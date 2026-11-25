import time
from threading import Lock
import httpx
import hashlib
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.config import settings
from app.api_clients.sportradar_key_manager import SportradarKeyManager, KeysExhaustedException
from app.api_clients.sportradar_rate_limiter import SportradarRateLimiter
from app.api_clients.sportradar_cache import SportradarCache
from app.db.models import ApiCallLog
from app.db.repositories import BaseRepository
from app.utils.security import hash_api_key, mask_key
from loguru import logger

class SportradarClient:
    def __init__(self):
        self.key_manager = SportradarKeyManager()
        self.rate_limiter = SportradarRateLimiter()
        self.cache = SportradarCache()
        self.log_repo = BaseRepository(ApiCallLog)

    def _generate_params_hash(self, params: Dict[str, Any]) -> str:
        """Keep cached data for trial, production and custom base URLs separate."""
        sorted_items = sorted(params.items())
        serialized = "&".join(f"{k}={v}" for k, v in sorted_items)
        cache_scope = settings.sportradar_base_url.rstrip("/")
        return hashlib.sha256(f"{cache_scope}?{serialized}".encode("utf-8")).hexdigest()

    _breaker_lock = Lock()
    _failures = 0
    _retry_after = 0.0

    @classmethod
    def _check_circuit_breaker(cls):
        with cls._breaker_lock:
            if time.monotonic() < cls._retry_after:
                raise RuntimeError("Sportradar circuit breaker is open.")

    @classmethod
    def _record_circuit_breaker_failure(cls):
        with cls._breaker_lock:
            cls._failures += 1
            if cls._failures >= 5:
                cls._retry_after = time.monotonic() + 60

    @classmethod
    def _reset_circuit_breaker(cls):
        with cls._breaker_lock:
            cls._failures = 0
            cls._retry_after = 0.0

    def get(self, db: Session, endpoint: str, params: Optional[Dict[str, Any]] = None, ttl_seconds: int = 86400, max_cache_age_seconds: Optional[int] = None) -> str:
        """
        Executes a GET request on Sportradar endpoint with caching, QPS throttling, and key rotation.
        """
        if params is None:
            params = {}

        params_hash = self._generate_params_hash(params)
        
        # 1. Memory cache check
        cached_val = self.cache.get(endpoint, params_hash) if max_cache_age_seconds is None else None
        if cached_val is not None:
            # Save a log representing the cache hit
            log = ApiCallLog(
                provider="sportradar",
                endpoint=endpoint,
                params_hash=params_hash,
                api_key_hash="cache_hit",
                status_code=200,
                cache_hit=True,
                retry_count=0,
                error_message=None
            )
            self.log_repo.create(db, log)
            return cached_val

        # 1.5 DB Persistent Cache Check
        from app.db.models import RawProviderResponse
        from datetime import datetime, timedelta
        import json
        
        db_cache = db.query(RawProviderResponse).filter(
            RawProviderResponse.provider == "sportradar",
            RawProviderResponse.endpoint == endpoint,
            RawProviderResponse.params_hash == params_hash
        ).first()
        
        now_dt = datetime.utcnow()
        if (settings.SPORTRADAR_ENABLE_CACHE and db_cache and db_cache.expires_at > now_dt and db_cache.status_code == 200
                and (max_cache_age_seconds is None or
                     (now_dt - db_cache.fetched_at).total_seconds() < max_cache_age_seconds)):
            logger.info(f"Database persistent cache HIT for endpoint: {endpoint}")
            # Restore to the memory cache for faster subsequent hits
            serialized = json.dumps(db_cache.body_json)
            self.cache.set(endpoint, params_hash, serialized,
                           min(ttl_seconds, (db_cache.expires_at - now_dt).total_seconds()))
            
            # Log as cache hit
            log = ApiCallLog(
                provider="sportradar",
                endpoint=endpoint,
                params_hash=params_hash,
                api_key_hash="db_cache_hit",
                status_code=200,
                cache_hit=True,
                retry_count=0,
                error_message=None
            )
            self.log_repo.create(db, log)
            return serialized

        # 2. Key loop & call
        retries = 0
        max_retries = settings.SPORTRADAR_MAX_RETRIES
        backoff_sec = settings.SPORTRADAR_BACKOFF_SECONDS

        # Check circuit breaker
        self._check_circuit_breaker()

        start_time = time.time()
        timeout_budget = 60.0  # 60s maximum budget
        max_attempts = max(6, len(self.key_manager.keys) * 2)
        total_attempts = 0

        while True:
            # Enforce budget
            elapsed = time.time() - start_time
            if elapsed >= timeout_budget:
                raise Exception(f"Sportradar request budget of {timeout_budget}s exceeded. Aborting.")

            if total_attempts >= max_attempts:
                raise Exception(f"Sportradar request failed. Max attempts limit ({max_attempts}) reached across rotated keys.")

            total_attempts += 1

            try:
                api_key = self.key_manager.get_active_key(db)
            except KeysExhaustedException as e:
                logger.error("No active API keys available to fulfill request.")
                raise e

            # Send the key in the HTTPS header.
            req_params = params.copy()
            key_hash = hash_api_key(api_key)

            # Enforce QPS limit
            self.rate_limiter.acquire(api_key)

            url = f"{settings.sportradar_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
            logger.info(f"Sending request to: {endpoint} using key {mask_key(api_key)} (attempt {total_attempts})")

            status_code = 0
            error_msg = None
            response_text = ""

            try:
                # Optional accelerated polls have a durable cap on actual requests,
                # including retries. Cache hits above do not consume this budget.
                from app.api_clients.request_budget import reserve_optional_request
                reserve_optional_request()
                with httpx.Client(timeout=settings.SPORTRADAR_REQUEST_TIMEOUT_SECONDS) as client:
                    resp = client.get(url, params=req_params, headers={"x-api-key": api_key})
                    status_code = resp.status_code
                    response_text = resp.text

                # Check status
                if status_code == 200:
                    # Successful request - Record and write cache
                    self.key_manager.record_usage(db, api_key)
                    self.cache.set(endpoint, params_hash, response_text, ttl_seconds)
                    self._reset_circuit_breaker()
                    
                    # Save/Update DB Cache
                    try:
                        response_json = json.loads(response_text)
                    except Exception:
                        response_json = {"raw": response_text}
                    payload_hash = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
                    expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)
                    
                    from sqlalchemy.exc import IntegrityError
                    try:
                        if db_cache:
                            db_cache.payload_hash = payload_hash
                            db_cache.body_json = response_json
                            db_cache.fetched_at = datetime.utcnow()
                            db_cache.expires_at = expires_at
                            db_cache.status_code = status_code
                            db_cache.source_key_hash = key_hash
                        else:
                            new_cache = RawProviderResponse(
                                provider="sportradar",
                                endpoint=endpoint,
                                params_hash=params_hash,
                                payload_hash=payload_hash,
                                body_json=response_json,
                                expires_at=expires_at,
                                status_code=status_code,
                                source_key_hash=key_hash
                            )
                            db.add(new_cache)
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        db_cache = db.query(RawProviderResponse).filter(
                            RawProviderResponse.provider == "sportradar",
                            RawProviderResponse.endpoint == endpoint,
                            RawProviderResponse.params_hash == params_hash
                        ).first()
                        if db_cache:
                            db_cache.payload_hash = payload_hash
                            db_cache.body_json = response_json
                            db_cache.fetched_at = datetime.utcnow()
                            db_cache.expires_at = expires_at
                            db_cache.status_code = status_code
                            db_cache.source_key_hash = key_hash
                            db.commit()
                        else:
                            raise
                    
                    log = ApiCallLog(
                        provider="sportradar",
                        endpoint=endpoint,
                        params_hash=params_hash,
                        api_key_hash=key_hash,
                        status_code=status_code,
                        cache_hit=False,
                        retry_count=retries,
                        error_message=None
                    )
                    self.log_repo.create(db, log)
                    return response_text

                elif status_code == 401 or status_code == 403:
                    if "qps" in response_text.lower():
                        logger.warning(f"Key {mask_key(api_key)} hit QPS rate limit (403). Cooling down key...")
                        self.key_manager.mark_cooldown(db, api_key, minutes=1)
                        time.sleep(2.0)
                    else:
                        # Key is invalid, unauthorized, or quota exhausted
                        logger.warning(f"Key {mask_key(api_key)} returned status {status_code} ({response_text[:50]}). Marking as EXHAUSTED.")
                        self.key_manager.mark_exhausted(db, api_key)
                    retries = 0
                    continue

                elif status_code == 429:
                    # Rate Limit or Quota Exceeded
                    is_quota = "quota" in response_text.lower()
                    if is_quota:
                        logger.warning(f"Key {mask_key(api_key)} has exhausted its quota (429). Marking as EXHAUSTED.")
                        self.key_manager.mark_exhausted(db, api_key)
                    else:
                        logger.warning(f"Key {mask_key(api_key)} hit 429 rate limit. Cooling down key...")
                        self.key_manager.mark_cooldown(db, api_key, minutes=1)
                        time.sleep(2.0)
                    retries = 0
                    continue

                elif 500 <= status_code < 600:
                    error_msg = f"Server Error: {status_code}. Response: {response_text[:200]}"
                    logger.error(error_msg)

                else:
                    # 4xx Client Errors (except 401, 403, 429)
                    error_msg = f"Client Error {status_code}: {response_text[:200]}"
                    logger.error(error_msg)
                    log = ApiCallLog(
                        provider="sportradar",
                        endpoint=endpoint,
                        params_hash=params_hash,
                        api_key_hash=key_hash,
                        status_code=status_code,
                        cache_hit=False,
                        retry_count=retries,
                        error_message=error_msg
                    )
                    self.log_repo.create(db, log)
                    self.key_manager.record_usage(db, api_key)
                    raise Exception(error_msg)

            except httpx.RequestError as e:
                status_code = 599 # custom code for network failures
                error_msg = f"RequestError: {str(e)}"
                logger.error(error_msg)

            # Record failed log entry
            log = ApiCallLog(
                provider="sportradar",
                endpoint=endpoint,
                params_hash=params_hash,
                api_key_hash=key_hash,
                status_code=status_code,
                cache_hit=False,
                retry_count=retries,
                error_message=error_msg
            )
            self.log_repo.create(db, log)
            self.key_manager.record_usage(db, api_key)

            # Implement Retry logic
            if retries < max_retries:
                retries += 1
                sleep_dur = backoff_sec * (2 ** (retries - 1))
                logger.info(f"Retrying in {sleep_dur} seconds... (Retry {retries}/{max_retries})")
                time.sleep(sleep_dur)
            else:
                # Max retries exceeded for current key, let's mark cooldown and try next key
                logger.warning(f"Max retries hit for key {mask_key(api_key)}. Cooling down and trying next key...")
                self.key_manager.mark_cooldown(db, api_key, minutes=2)
                self._record_circuit_breaker_failure()
                retries = 0 # reset retries for next key
                continue
