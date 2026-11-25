import base64
import hashlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
import httpx
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.config import settings
from app.db.session import get_db, engine
from app.db.base import Base
from app.db.models import Match, PlayerProfile, PlayerPhasePattern, PlayerSequencePattern, TrendSignalLog, MatchLiveSnapshot
from app.api_clients.sportradar_client import SportradarClient
from app.api_clients.sportradar_key_manager import SportradarKeyManager
from app.services.historical_importer import HistoricalImporter
from app.services.player_profile_builder import PlayerProfileBuilder
from app.services.player_pattern_miner import PlayerPatternMiner
from app.services.model_trainer import ModelTrainer
from app.services.backtest_engine import BacktestEngine
from app.services.live_monitor import LiveMonitor
from app.jobs.scheduler import JobScheduler
from app.utils.time_utils import get_utc_now
from app.web.dashboard import router as dashboard_router
from loguru import logger

SESSION_COOKIE_NAME = "badminton_dashboard_session"


class SchedulerConfigRequest(BaseModel):
    enable_live_monitor: bool
    live_monitor_interval_seconds: int = Field(ge=60, le=3600)
    live_monitor_start_hour: int = Field(ge=0, le=23, default=17)
    live_monitor_end_hour: int = Field(ge=0, le=23, default=5)
    enable_daily_history_import: bool
    daily_history_import_hour: int = Field(ge=0, le=23)
    daily_history_import_minute: int = Field(ge=0, le=59)
    daily_history_import_timezone: str = Field(min_length=1, max_length=64)

scheduler = JobScheduler()


@asynccontextmanager
async def lifespan(app):
    if settings.APP_ENV != "production":
        Base.metadata.create_all(bind=engine)
    if settings.ENABLE_JOB_WORKER:
        from app.db.session import SessionLocal
        from app.jobs.job_service import JobService
        with SessionLocal() as db:
            JobService.recover_stale_jobs(db, embedded_restart=True)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(
    lifespan=lifespan,
    title="Badminton Trend Miner REST API",
    description="Live scoring trend mining and automatic alert pipeline for badminton matches.",
    version="1.0.0"
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(dashboard_router)


def _protected_path(path: str) -> bool:
    protected_prefixes = (
        "/web/api",
        "/api",
        "/sportradar",
        "/profiles",
        "/players",
        "/patterns",
        "/models",
        "/backtest",
        "/signals",
        "/matches",
        "/monitor",
        "/docs",
        "/redoc",
        "/openapi.json",
    )
    return path in {"/", "/dashboard"} or path.startswith(protected_prefixes)


def _session_signature(payload: str) -> str:
    return hmac.new(
        settings.WEB_SESSION_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _create_session_token(username: str) -> str:
    expires_at = int(time.time()) + settings.WEB_SESSION_TTL_SECONDS
    payload = f"{username}:{expires_at}"
    signature = _session_signature(payload)
    raw = f"{payload}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _valid_session_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, expires_at_text, signature = raw.rsplit(":", 2)
        payload = f"{username}:{expires_at_text}"
        expected_signature = _session_signature(payload)
        expires_at = int(expires_at_text)
    except Exception:
        return False
    if username != settings.WEB_AUTH_USERNAME or expires_at < int(time.time()):
        return False
    return hmac.compare_digest(signature, expected_signature)


def _redirect_to_login(request: Request) -> RedirectResponse:
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    return RedirectResponse(url=f"/login?next={quote(next_path, safe='/?=&')}", status_code=303)


@app.middleware("http")
async def web_auth_middleware(request: Request, call_next):
    if not settings.WEB_AUTH_ENABLED or not _protected_path(request.url.path):
        return await call_next(request)
    if _valid_session_token(request.cookies.get(SESSION_COOKIE_NAME)):
        return await call_next(request)
    if request.url.path.startswith("/web/api") or request.url.path.startswith("/api"):
        return JSONResponse({"detail": "未登录，请先登录控制台。"}, status_code=401)
    return _redirect_to_login(request)


@app.get("/login", include_in_schema=False)
def login_page() -> HTMLResponse:
    page = Path("app/static/dashboard/login.html").read_text(encoding="utf-8")
    widget = ""
    if settings.WEB_TURNSTILE_ENABLED:
        widget = (
            '<div class="cf-turnstile" data-sitekey="'
            + escape(settings.WEB_TURNSTILE_SITE_KEY, quote=True)
            + '" data-action="dashboard_login" data-callback="turnstileReady"'
            + ' data-expired-callback="turnstileExpired" data-error-callback="turnstileError"'
            + ' data-language="zh-cn" data-size="flexible"></div>'
            '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>'
        )
        page = page.replace('type="submit">登录', 'type="submit" disabled>登录')
    return HTMLResponse(page.replace("<!-- TURNSTILE_WIDGET -->", widget), headers={"Cache-Control": "no-store"})


async def _verify_login_turnstile(token: str) -> bool:
    if not token or len(token) > 2048 or not settings.WEB_TURNSTILE_SECRET_KEY or not settings.WEB_TURNSTILE_HOSTNAME:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": settings.WEB_TURNSTILE_SECRET_KEY, "response": token},
            )
            response.raise_for_status()
            result = response.json()
        return (
            result.get("success") is True
            and result.get("hostname") == settings.WEB_TURNSTILE_HOSTNAME
            and result.get("action") == "dashboard_login"
        )
    except (httpx.HTTPError, ValueError, AttributeError):
        logger.warning("Dashboard Turnstile verification unavailable or invalid response")
        return False


@app.post("/login", include_in_schema=False)
async def login_submit(request: Request) -> RedirectResponse:
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body)
    username = form.get("username", [""])[0]
    password = form.get("password", [""])[0]
    next_path = form.get("next", ["/dashboard"])[0] or "/dashboard"
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/dashboard"

    if settings.WEB_TURNSTILE_ENABLED and not await _verify_login_turnstile(form.get("cf-turnstile-response", [""])[0]):
        return RedirectResponse(
            url=f"/login?error=verification&next={quote(next_path, safe='/?=&')}", status_code=303,
        )

    username_ok = secrets.compare_digest(username, settings.WEB_AUTH_USERNAME)
    password_ok = secrets.compare_digest(password, settings.WEB_AUTH_PASSWORD)
    if not (username_ok and password_ok):
        return RedirectResponse(
            url=f"/login?error=1&next={quote(next_path, safe='/?=&')}",
            status_code=303,
        )

    response = RedirectResponse(url=next_path, status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        _create_session_token(username),
        max_age=settings.WEB_SESSION_TTL_SECONDS,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="lax",
    )
    return response


@app.get("/logout", include_in_schema=False)
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response

# 1. Health and Quotas
@app.get("/health", tags=["system"])
def health_check():
    """Returns application status and environment information."""
    from sqlalchemy.exc import SQLAlchemyError

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT id FROM matches LIMIT 1")).first()
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="SQLite schema unavailable.") from None
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "env": settings.APP_ENV
    }

@app.get("/api/quota", tags=["system"])
def get_api_quota(db: Session = Depends(get_db)):
    """Retrieves Sportradar trial API Key usage and limits statistics."""
    from app.db.models import ApiKeyUsage
    usages = db.query(ApiKeyUsage).all()
    return [
        {
            "id": u.id,
            "provider": u.provider,
            "api_key_hash": u.api_key_hash,
            "quota_limit": u.quota_limit,
            "quota_used": u.quota_used,
            "quota_remaining": u.quota_remaining,
            "cooldown_until": u.cooldown_until.isoformat() if u.cooldown_until else None,
            "last_request_at": u.last_request_at.isoformat() if u.last_request_at else None
        }
        for u in usages
    ]


@app.get("/web/api/scheduler", tags=["system"])
def get_scheduler_status():
    """Returns runtime scheduler status and current configuration."""
    return {**scheduler.status(), "web_manage_sportradar_keys": settings.WEB_MANAGE_SPORTRADAR_KEYS}


@app.post("/web/api/actions/scheduler-config", tags=["system"])
def update_scheduler_config(payload: SchedulerConfigRequest):
    """Updates in-process scheduler configuration and restarts scheduled jobs."""
    settings.ENABLE_LIVE_MONITOR = payload.enable_live_monitor
    settings.LIVE_MONITOR_INTERVAL_SECONDS = payload.live_monitor_interval_seconds
    settings.LIVE_MONITOR_START_HOUR = payload.live_monitor_start_hour
    settings.LIVE_MONITOR_END_HOUR = payload.live_monitor_end_hour
    settings.ENABLE_DAILY_HISTORY_IMPORT = payload.enable_daily_history_import
    settings.DAILY_HISTORY_IMPORT_HOUR = payload.daily_history_import_hour
    settings.DAILY_HISTORY_IMPORT_MINUTE = payload.daily_history_import_minute
    settings.DAILY_HISTORY_IMPORT_TIMEZONE = payload.daily_history_import_timezone
    scheduler.restart()
    return scheduler.status()

@app.post("/api/cache/clear", tags=["system"])
def clear_api_cache():
    """Clears all cached API response payloads from the in-process cache."""
    from app.api_clients.sportradar_cache import SportradarCache
    cache = SportradarCache()
    count = cache.clear()
    return {"message": f"Successfully cleared {count} keys from cache."}

@app.post("/sportradar/import-history", tags=["import"])
def import_sportradar_history(start_date: str, end_date: str, women_only: bool = True, force: bool = False, db: Session = Depends(get_db)):
    """
    Imports historical match and point data from Sportradar for a date range (YYYY-MM-DD).
    """
    importer = HistoricalImporter()
    try:
        summary = importer.import_date_range(db, start_date, end_date, women_only, force)
        return summary
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 3. Profiles
@app.post("/profiles/rebuild", tags=["analytics"])
def rebuild_profiles(db: Session = Depends(get_db)):
    """
    Rebuilds badminton player profiles and metrics distribution tables.
    """
    builder = PlayerProfileBuilder()
    try:
        count = builder.rebuild_profiles(db)
        return {"message": f"Successfully rebuilt profiles for {count} players."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/players/{player_id}/profile", tags=["analytics"])
def get_player_profile_details(player_id: int, db: Session = Depends(get_db)):
    """Retrieves computed win/loss profile vectors for a player."""
    profiles = db.query(PlayerProfile).filter(PlayerProfile.player_id == player_id).all()
    if not profiles:
        raise HTTPException(status_code=404, detail="Player profiles not found.")
    return profiles

# 4. Patterns
@app.post("/patterns/mine", tags=["patterns"])
def mine_player_patterns(db: Session = Depends(get_db)):
    """
    Scans point events and mines stage-specific play-by-play patterns.
    """
    miner = PlayerPatternMiner()
    try:
        count = miner.mine_patterns(db)
        return {"message": f"Successfully mined {count} patterns across all players."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/players/{player_id}/patterns", tags=["patterns"])
def get_player_patterns(player_id: int, db: Session = Depends(get_db)):
    """Retrieves mined stage and sequence patterns for a player."""
    phase = db.query(PlayerPhasePattern).filter(PlayerPhasePattern.player_id == player_id).all()
    seq = db.query(PlayerSequencePattern).filter(PlayerSequencePattern.player_id == player_id).all()
    return {
        "phase_patterns": phase,
        "sequence_patterns": seq
    }

# 5. Model Training & Backtesting
@app.post("/models/train", tags=["ml"])
def train_prediction_model(target_label: str = "label_win_match", scoring_target: int | None = None, db: Session = Depends(get_db)):
    """
    Trains a prediction model using generated DB trend training samples.
    """
    trainer = ModelTrainer()
    try:
        model_rec = trainer.train_model(db, target_label, scoring_target=scoring_target)
        if model_rec:
            return {
                "message": "Model trained successfully.",
                "model_id": model_rec.id,
                "metrics": model_rec.metrics_json
            }
        else:
            raise HTTPException(status_code=500, detail="Model training did not return a valid record.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/backtest/run", tags=["ml"])
def run_backtest_simulation(run_name: str, start_date: str, end_date: str, db: Session = Depends(get_db)):
    """
    Simulates historical match events to backtest accuracy of trend alerts.
    """
    backtester = BacktestEngine()
    try:
        run_record = backtester.run_backtest(db, run_name, start_date, end_date)
        return {
            "message": "Backtest run complete.",
            "run_id": run_record.id,
            "metrics": run_record.metrics_json
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/backtest/results", tags=["ml"])
def get_backtest_results(db: Session = Depends(get_db)):
    """Retrieves previous backtest runs logs and breakdown statistics."""
    from app.db.models import BacktestRun
    runs = db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).all()
    return runs

# 6. Live Monitor and Signals
@app.get("/signals/recent", tags=["live"])
def get_recent_signals(limit: int = 50, db: Session = Depends(get_db)):
    """Retrieves recent signal alerts generated by the system."""
    signals = db.query(TrendSignalLog).order_by(TrendSignalLog.created_at.desc()).limit(limit).all()
    return signals

@app.get("/matches/live", tags=["live"])
def get_live_matches_list(db: Session = Depends(get_db)):
    """Lists currently active live matches under monitoring."""
    matches = db.query(Match).filter(Match.status.in_(["live", "suspended", "interrupted", "delayed"])).all()
    return matches

@app.post("/monitor/run-once", tags=["live"])
def trigger_live_monitor_scan(db: Session = Depends(get_db)):
    """
    Manually triggers one loop iteration of live monitor scanning.
    """
    monitor = LiveMonitor()
    try:
        res = monitor.scan_and_update(db)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/matches/{match_id}/snapshots", tags=["live"])
def get_match_snapshots(match_id: int, db: Session = Depends(get_db)):
    """Retrieves point-by-point live trend snapshots generated for a match."""
    snaps = db.query(MatchLiveSnapshot).filter(MatchLiveSnapshot.match_id == match_id).order_by(MatchLiveSnapshot.snapshot_point_index.asc()).all()
    return snaps

@app.post("/web/api/actions/history-sync", tags=["jobs"])
def enqueue_history_sync(start_date: str, end_date: str, women_only: bool = True, force: bool = False, db: Session = Depends(get_db)):
    from app.jobs.job_service import JobService
    payload = {
        "start_date": start_date,
        "end_date": end_date,
        "women_only": women_only,
        "force": force
    }
    dedupe_key = f"history_sync-{start_date}-{end_date}"
    job = JobService.enqueue(db, "history_sync", payload, dedupe_key)
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "dedupe_key": job.dedupe_key,
        "created_at": job.created_at
    }

@app.post("/web/api/actions/profiles-rebuild", tags=["jobs"])
def enqueue_profiles_rebuild(db: Session = Depends(get_db)):
    from app.jobs.job_service import JobService
    payload = {}
    dedupe_key = "profiles_rebuild"
    job = JobService.enqueue(db, "profiles_rebuild", payload, dedupe_key)
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "dedupe_key": job.dedupe_key,
        "created_at": job.created_at
    }

@app.post("/web/api/actions/models-train", tags=["jobs"])
def enqueue_models_train(target_label: str = "label_win_match", db: Session = Depends(get_db)):
    from app.jobs.job_service import JobService
    payload = {"target_label": target_label}
    dedupe_key = f"models_train-{target_label}"
    job = JobService.enqueue(db, "models_train", payload, dedupe_key)
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "dedupe_key": job.dedupe_key,
        "created_at": job.created_at
    }

@app.get("/web/api/jobs", tags=["jobs"])
def get_jobs_list(limit: int = 50, db: Session = Depends(get_db)):
    from app.db.models import JobRun
    jobs = db.query(JobRun).order_by(JobRun.created_at.desc()).limit(limit).all()
    return [{
        "id": j.id,
        "job_type": j.job_type,
        "status": j.status,
        "progress_current": j.progress_current,
        "progress_total": j.progress_total,
        "progress_message": j.progress_message,
        "created_at": j.created_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
        "error_message": j.error_message,
        "result_json": j.result_json,
    } for j in jobs]

@app.get("/web/api/jobs/{job_id}", tags=["jobs"])
def get_job_detail(job_id: int, db: Session = Depends(get_db)):
    from app.db.models import JobRun
    job = db.query(JobRun).filter(JobRun.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "payload_json": job.payload_json,
        "result_json": job.result_json,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "progress_message": job.progress_message,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_message": job.error_message,
        "locked_by": job.locked_by,
        "locked_at": job.locked_at,
    }

@app.post("/web/api/jobs/{job_id}/cancel", tags=["jobs"])
def cancel_job_run(job_id: int, db: Session = Depends(get_db)):
    from app.jobs.job_service import JobService
    success = JobService.cancel_job(db, job_id)
    if not success:
        raise HTTPException(status_code=400, detail="Job cannot be cancelled or is not in a cancellable state")
    return {"message": f"Job {job_id} cancellation requested."}
