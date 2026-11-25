from datetime import datetime, timedelta
from contextlib import contextmanager
from pathlib import Path
import os
import threading
from typing import List
from dotenv import dotenv_values, set_key
from sqlalchemy.orm import Session
from app.config import settings
from app.db.models import ApiKeyUsage
from app.db.repositories import ApiKeyUsageRepository
from app.utils.security import hash_api_key, mask_key
from loguru import logger


_config_thread_lock = threading.RLock()


@contextmanager
def _key_config_lock():
    """Serialize key removals across app/worker processes as well as threads."""
    with _config_thread_lock:
        with open(".env.keys.lock", "a+b") as lock:
            if os.name == "nt":
                import msvcrt
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

class KeysExhaustedException(Exception):
    pass

class SportradarKeyManager:
    def __init__(self):
        self.repo = ApiKeyUsageRepository()

    @property
    def keys(self) -> List[str]:
        # Docker's environment stays stale after .env changes. The mounted file
        # is authoritative so a second process/restart cannot revive deleted keys.
        if Path(".env").exists():
            values = dotenv_values(".env", interpolate=False)
            if "SPORTRADAR_API_KEYS" in values:
                raw = values["SPORTRADAR_API_KEYS"] or ""
                return list(dict.fromkeys(k.strip() for k in raw.split(",") if k.strip()))
        return settings.api_keys_list

    @keys.setter
    def keys(self, value: List[str]) -> None:
        settings.SPORTRADAR_API_KEYS = ",".join(value)

    def _get_or_create_usage(self, db: Session, key: str) -> ApiKeyUsage:
        if key not in self.keys:
            raise KeysExhaustedException("Sportradar key is no longer configured.")
        key_hash = hash_api_key(key)
        usage = self.repo.get_by_key_hash(db, key_hash)
        if not usage:
            usage = ApiKeyUsage(
                provider="sportradar",
                api_key_hash=key_hash,
                quota_limit=settings.SPORTRADAR_KEY_TOTAL_QUOTA,
                quota_used=0,
                quota_remaining=settings.SPORTRADAR_KEY_TOTAL_QUOTA,
                cooldown_until=None,
                last_request_at=None
            )
            self.repo.create(db, usage)
        return usage

    def get_active_key(self, db: Session) -> str:
        """
        Retrieves the active API key that is not cooled down and has remaining quota,
        using first-registered keys first to avoid stranding older trial quota.
        Registration time is an age proxy, not a provider expiry timestamp.
        """
        keys = self.keys
        if not keys:
            raise KeysExhaustedException("No Sportradar API keys configured in settings.")

        now = datetime.utcnow()
        
        # Snapshot the list: retiring an exhausted key changes configuration.
        key_usages = []
        for key in keys:
            usage = self._get_or_create_usage(db, key)
            if usage.quota_remaining <= 0:
                self.mark_exhausted(db, key)
                continue
            key_usages.append((key, usage))
            
        key_usages.sort(
            key=lambda x: (
                x[1].created_at or datetime.max,
                x[1].id,
            )
        )
        
        for key, usage in key_usages:
            # Check cooldown
            if usage.cooldown_until and usage.cooldown_until > now:
                logger.debug(f"Key {mask_key(key)} is in cooldown until {usage.cooldown_until}")
                continue
                
            # Check quota
            if usage.quota_remaining <= 0:
                logger.debug(f"Key {mask_key(key)} has exhausted its quota limit.")
                continue
                
            return key
            
        raise KeysExhaustedException("All Sportradar API keys are exhausted or in cooldown!")

    def record_usage(self, db: Session, key: str, increment: int = 1) -> None:
        """Increments key call usage in database."""
        if key not in self.keys:
            return  # An in-flight request may finish after another process retired it.
        usage = self._get_or_create_usage(db, key)
        usage.quota_used += increment
        usage.quota_remaining = max(0, usage.quota_limit - usage.quota_used)
        usage.last_request_at = datetime.utcnow()
        self.repo.update(db, usage)
        if usage.quota_remaining <= 0:
            self.mark_exhausted(db, key)

    def mark_cooldown(self, db: Session, key: str, minutes: int = 5) -> None:
        """Sets a cooldown timer on the key when a 429 rate limit is encountered."""
        if key not in self.keys:
            return
        usage = self._get_or_create_usage(db, key)
        cooldown_time = datetime.utcnow() + timedelta(minutes=minutes)
        usage.cooldown_until = cooldown_time
        self.repo.update(db, usage)
        logger.warning(f"Key {mask_key(key)} has been cooled down until {cooldown_time}")
        
    def reset_quotas(self, db: Session) -> None:
        """Reset usage of all keys."""
        for key in self.keys:
            usage = self._get_or_create_usage(db, key)
            usage.quota_used = 0
            usage.quota_remaining = usage.quota_limit
            usage.cooldown_until = None
            self.repo.update(db, usage)
        logger.info("All API keys quota counts reset.")

    def mark_exhausted(self, db: Session, key: str) -> None:
        """Retire configuration before deleting usage, so it cannot reset to 1000."""
        usage = self.repo.get_by_key_hash(db, hash_api_key(key))
        if usage:
            usage.quota_remaining = 0
            usage.quota_used = usage.quota_limit
            self.repo.update(db, usage)
        # If persistence fails, propagate the error and retain the zero-quota row.
        self._remove_keys_from_env_file({key})
        if usage:
            db.delete(usage)
            db.commit()
        logger.info(f"Retired exhausted key {mask_key(key)} from configuration and usage records.")

    def verify_and_cleanup_keys(self, db: Session, force_remote: bool = False) -> dict:
        """
        Verify all API keys currently configured and in the database.
        Deletes exhausted or abnormal keys from the database, and physically
        removes them from the .env configuration file so they won't be reused.
        """
        import httpx
        
        # 1. Map configured raw keys to their hashes
        configured_keys_map = {hash_api_key(k): k for k in self.keys}
        
        # 2. Get all key usages from the database
        usages = db.query(ApiKeyUsage).filter(ApiKeyUsage.provider == "sportradar").all()
        
        deleted_keys_log = []
        keys_to_remove_from_config = set()
        rows_to_delete = []
        
        # Estimate expected consumption if remote check is requested
        if force_remote:
            active_keys_to_check = [u for u in usages if u.quota_remaining > 0 and configured_keys_map.get(u.api_key_hash)]
            estimated_cost = len(active_keys_to_check)
            logger.info(f"Running remote key cleanup. Expected API cost: {estimated_cost} calls.")
            
        for usage in usages:
            h = usage.api_key_hash
            raw_key = configured_keys_map.get(h)
            
            # Case A: Key is in DB but no longer in settings config (.env)
            if not raw_key:
                logger.info(f"Removing obsolete key hash {h[:12]}... from database (not in config).")
                rows_to_delete.append(usage)
                deleted_keys_log.append(f"Obsolete: {h[:12]}...")
                continue
                
            # Case B: Key is in config, check if it's already locally exhausted
            is_exhausted = False
            reason = ""
            if usage.quota_remaining <= 0:
                is_exhausted = True
                reason = "local quota exhausted"
            elif force_remote:
                # Case C: Key is not locally exhausted, but let's query Sportradar remotely to verify its status
                # We do a lightweight test request
                url = f"{settings.sportradar_base_url.rstrip('/')}/en/schedules/live/summaries.json"
                try:
                    with httpx.Client(timeout=10.0) as client:
                        resp = client.get(url, headers={"x-api-key": raw_key})
                        if resp.status_code in (401, 403) and "qps" not in resp.text.lower():
                            is_exhausted = True
                            reason = f"remote {resp.status_code} invalid or exhausted"
                        elif resp.status_code == 429:
                            # 429 can be rate limit or quota exceeded
                            if "quota" in resp.text.lower():
                                is_exhausted = True
                                reason = "remote 429 quota exhausted"
                except Exception as e:
                    logger.warning(f"Failed to remotely test key {mask_key(raw_key)}: {type(e).__name__}")
                    
            if is_exhausted:
                logger.warning(f"Key {mask_key(raw_key)} is exhausted/invalid ({reason}). Deleting from DB and removing from config...")
                rows_to_delete.append(usage)
                deleted_keys_log.append(f"Exhausted: {mask_key(raw_key)} ({reason})")
                keys_to_remove_from_config.add(raw_key)
                
        # Remove configuration first. A failed write must not reset usage on retry.
        if keys_to_remove_from_config:
            self._remove_keys_from_env_file(keys_to_remove_from_config)
        for usage in rows_to_delete:
            db.delete(usage)
        db.commit()
            
        return {
            "deleted_count": len(deleted_keys_log),
            "details": deleted_keys_log
        }

    def _remove_keys_from_env_file(self, keys_to_remove: set) -> None:
        with _key_config_lock():
            remaining = [k for k in self.keys if k not in keys_to_remove]
            value = ",".join(remaining)
            # set_key uses atomic replacement; readers never see a truncated file.
            # Also create an authoritative key list for env-only configurations.
            set_key(".env", "SPORTRADAR_API_KEYS", value, quote_mode="always")
            settings.SPORTRADAR_API_KEYS = value
