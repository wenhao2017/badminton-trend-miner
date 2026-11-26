import os
from typing import List, Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_ENV: str = "production"
    LOG_LEVEL: str = "INFO"
    WEB_PUBLIC_BASE_URL: str = ""
    WEB_AUTH_ENABLED: bool = True
    WEB_MANAGE_SPORTRADAR_KEYS: bool = True
    WEB_AUTH_USERNAME: str = "admin"
    WEB_AUTH_PASSWORD: str = "change_this_password"
    WEB_SESSION_SECRET: str = "change_this_long_random_secret"
    WEB_SESSION_TTL_SECONDS: int = 86400
    WEB_TURNSTILE_ENABLED: bool = False
    WEB_TURNSTILE_SITE_KEY: str = ""
    WEB_TURNSTILE_SECRET_KEY: str = ""
    WEB_TURNSTILE_HOSTNAME: str = ""

    DATABASE_URL: str = "sqlite:///data/badminton.sqlite3"

    # Sportradar Config
    SPORTRADAR_BASE_URL: str = ""

    @property
    def sportradar_base_url(self) -> str:
        return self.SPORTRADAR_BASE_URL or f"https://api.sportradar.com/badminton/{self.SPORTRADAR_ACCESS_LEVEL}/v2"
    SPORTRADAR_ACCESS_LEVEL: Literal["trial", "production"] = "trial"
    SPORTRADAR_LOCALE: str = "en"
    SPORTRADAR_FORMAT: Literal["json"] = "json"
    SPORTRADAR_API_KEYS: str = ""
    SPORTRADAR_KEY_SOURCE: Literal["dotenv", "environment"] = "dotenv"
    SPORTRADAR_DAILY_SCHEDULE_ENDPOINT: str = "/{locale}/schedules/{date}/summaries.{format}"
    SPORTRADAR_LIVE_SUMMARIES_ENDPOINT: str = "/{locale}/schedules/live/summaries.{format}"
    SPORTRADAR_MATCH_SUMMARY_ENDPOINT: str = "/{locale}/sport_events/{match_id}/summary.{format}"
    SPORTRADAR_MATCH_TIMELINE_ENDPOINT: str = "/{locale}/sport_events/{match_id}/timeline.{format}"
    SPORTRADAR_PLAYER_PROFILE_ENDPOINT: str = "/{locale}/competitors/{player_id}/profile.{format}"
    SPORTRADAR_TOURNAMENT_PROFILE_ENDPOINT: str = "/{locale}/competitions/{tournament_id}/info.{format}"

    SPORTRADAR_KEY_TOTAL_QUOTA: int = 1000
    SPORTRADAR_KEY_QPS: int = 1
    SPORTRADAR_REQUEST_TIMEOUT_SECONDS: int = 15
    SPORTRADAR_MAX_RETRIES: int = 3
    SPORTRADAR_BACKOFF_SECONDS: float = 2.0
    SPORTRADAR_ENABLE_CACHE: bool = True
    SPORTRADAR_CACHE_TTL_SECONDS: int = 86400
    SPORTRADAR_LIVE_CACHE_TTL_SECONDS: int = 20
    SPORTRADAR_HISTORY_CACHE_TTL_SECONDS: int = 2592000

    # Alerts Channel Config
    PRIMARY_ALERT_CHANNEL: str = "telegram"
    FALLBACK_ALERT_CHANNEL: str = "email"

    # Telegram
    ENABLE_TELEGRAM_ALERT: bool = False
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Slack Incoming Webhook
    ENABLE_SLACK_ALERT: bool = False
    SLACK_WEBHOOK_URL: str = ""

    # Email
    ENABLE_EMAIL_ALERT: bool = False
    ENABLE_DAILY_EMAIL_REPORT: bool = False
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    ALERT_EMAIL_TO: str = ""

    # Live Monitor Parameters
    ENABLE_LIVE_MONITOR: bool = False
    LIVE_MONITOR_INTERVAL_SECONDS: int = 60
    LIVE_SIGNAL_MAX_AGE_SECONDS: int = Field(default=60, ge=5)
    ENABLE_ADAPTIVE_LIVE_POLLING: bool = False
    LIVE_POLL_WARM_SECONDS: int = Field(default=60, ge=20, le=180)
    LIVE_POLL_FAST_SECONDS: int = Field(default=20, ge=10, le=60)
    LIVE_POLL_MAX_MATCHES_PER_TICK: int = Field(default=2, ge=1, le=5)
    LIVE_POLL_EXTRA_REQUESTS_PER_DAY: int = Field(default=1000, ge=0)
    LIVE_POLL_PUBLISHED_INTERVAL_SECONDS: int = Field(default=900, ge=180)
    LIVE_MONITOR_SCHEDULE_ENABLED: bool = True
    LIVE_MONITOR_START_HOUR: int = 17
    LIVE_MONITOR_END_HOUR: int = 5
    ENABLE_DAILY_HISTORY_IMPORT: bool = False
    DAILY_HISTORY_IMPORT_HOUR: int = 2
    DAILY_HISTORY_IMPORT_MINUTE: int = 0
    DAILY_HISTORY_IMPORT_TIMEZONE: str = "Asia/Shanghai"
    ALERT_COOLDOWN_MINUTES: int = 15
    # Enabled explicitly at rollout; raw grades remain diagnostics, not recommendations.
    OFFICIAL_S_ENABLED: bool = False
    OFFICIAL_S_21_POLICY: Literal["legacy", "bwf21-r30-s2-p95-v1"] = "legacy"

    # Rules
    MIN_POINTS_FOR_SIGNAL: int = Field(default=12, ge=3)
    S_CONFIRMATION_TREND_THRESHOLD: float = Field(default=0.70, gt=0.5, lt=1.0)
    SIGNAL_COMPETITION_IDS: str = ""
    SCORING_TARGET_OVERRIDE: int | None = None
    MODEL_DIRECTORY: str = "models"

    @field_validator("SCORING_TARGET_OVERRIDE")
    @classmethod
    def check_scoring_target(cls, value):
        if value not in (None, 15, 21):
            raise ValueError("Scoring target must be 15 or 21.")
        return value

    MIN_PRIOR_MATCHES_FOR_S: int = Field(default=10, ge=0)

    # Thresholds
    S_LEVEL_THRESHOLD: float = 0.90
    A_LEVEL_THRESHOLD: float = 0.82
    B_LEVEL_THRESHOLD: float = 0.72
    C_LEVEL_THRESHOLD: float = 0.65
    MAX_RISK_FOR_ALERT: float = 0.30
    MATCH_S_LEVEL_THRESHOLD: float = 0.85
    MATCH_A_LEVEL_THRESHOLD: float = 0.70
    MATCH_B_LEVEL_THRESHOLD: float = 0.60
    MATCH_C_LEVEL_THRESHOLD: float = 0.55

    # Pattern mining
    PATTERN_MIN_SAMPLE_COUNT: int = 10
    PATTERN_MIN_LIFT: float = 0.10
    PATTERN_MIN_CONFIDENCE: float = 0.65

    # Model training
    MODEL_MIN_TRAINING_SAMPLES: int = 50
    MODEL_MAX_TRAINING_SAMPLES: int = Field(default=0, ge=0)  # 0 uses all eligible samples
    ENABLE_JOB_WORKER: bool = True

    @property
    def api_keys_list(self) -> List[str]:
        if self.SPORTRADAR_KEY_SOURCE == "environment":
            return list(dict.fromkeys(key.strip() for key in self.SPORTRADAR_API_KEYS.split(",") if key.strip()))
        import time
        global _cached_keys, _cached_raw_keys_str, _last_fetch_time
        now = time.time()
        
        # 1. If SPORTRADAR_API_KEYS has been updated in-memory (e.g. by dashboard or key manager),
        # invalidate the cache immediately.
        if _cached_raw_keys_str != self.SPORTRADAR_API_KEYS:
            _cached_keys = None
            
        # 2. If keys have been explicitly modified in-memory from the startup value
        # (e.g. during tests or custom in-memory updates), prioritize the in-memory value
        # and do not attempt to read the .env file from disk.
        is_modified_in_memory = (self.SPORTRADAR_API_KEYS != _initial_api_keys_str)
        
        if is_modified_in_memory:
            keys_str = self.SPORTRADAR_API_KEYS
            if not keys_str:
                return []
            return [k.strip() for k in keys_str.split(",") if k.strip()]
            
        # 3. If cache is valid and we fetched it less than 10 seconds ago, return cached keys.
        if _cached_keys is not None and (now - _last_fetch_time < 10.0):
            return _cached_keys

        # 4. Read from .env file on disk to see if it changed externally (e.g. by add_remote_keys.py)
        env_keys = None
        env_path = ".env"
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip().startswith("SPORTRADAR_API_KEYS="):
                            val = line.split("=", 1)[1].strip()
                            if val.startswith('"') and val.endswith('"'):
                                val = val[1:-1]
                            elif val.startswith("'") and val.endswith("'"):
                                val = val[1:-1]
                            env_keys = val
                            break
            except Exception:
                pass

        keys_str = env_keys if env_keys is not None else self.SPORTRADAR_API_KEYS
        if not keys_str:
            _cached_keys = []
        else:
            _cached_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
            
        _cached_raw_keys_str = self.SPORTRADAR_API_KEYS
        _last_fetch_time = now
        return _cached_keys

_cached_keys = None
_cached_raw_keys_str = None
_last_fetch_time = 0.0

settings = Settings()
_initial_api_keys_str = settings.SPORTRADAR_API_KEYS
