from typing import Any, Dict

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.services.live_monitor import LiveMonitor
from app.config import settings
from loguru import logger
import os

class JobScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.monitor = LiveMonitor()
        from app.services.schedule_window import ScheduleWindow
        self.schedule_window = ScheduleWindow()
        from app.services.adaptive_polling import AdaptivePolling
        self.adaptive = AdaptivePolling()

    def _execute_live_monitor_job(self) -> None:
        """Runs the monitor updates cycle in a clean DB session."""
        if not settings.api_keys_list:
            return
        db: Session = SessionLocal()
        try:
            adaptive = getattr(self, "adaptive", None)
            accelerated = bool(settings.ENABLE_ADAPTIVE_LIVE_POLLING
                               and settings.OFFICIAL_S_ENABLED and adaptive is not None)
            baseline = not accelerated or adaptive.baseline_due()
            from zoneinfo import ZoneInfo
            from datetime import datetime
            
            tz_name = settings.DAILY_HISTORY_IMPORT_TIMEZONE or "Asia/Shanghai"
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("Asia/Shanghai")
                
            now_tz = datetime.now(tz)
            current_hour = now_tz.hour
            
            start_h = getattr(settings, "LIVE_MONITOR_START_HOUR", 0)
            end_h = getattr(settings, "LIVE_MONITOR_END_HOUR", 23)
            
            is_active = True
            if start_h <= end_h:
                if not (start_h <= current_hour <= end_h):
                    is_active = False
            else:  # spans midnight, e.g., 22 to 6
                if not (current_hour >= start_h or current_hour <= end_h):
                    is_active = False
                    
            if settings.LIVE_MONITOR_SCHEDULE_ENABLED:
                if not hasattr(self, "schedule_window"):
                    from app.services.schedule_window import ScheduleWindow
                    self.schedule_window = ScheduleWindow()
                is_active = self.schedule_window.active(db, self.monitor.live_client.api)

            if not is_active:
                if baseline:
                    if accelerated:
                        adaptive.mark_baseline()
                    res = self.monitor.finalize_tracked_matches(db)
                    logger.info(f"Outside active monitoring range; tracked-match cleanup results: {res}")
                return
                
            if baseline:
                if accelerated:
                    adaptive.mark_baseline()
                logger.info("Executing periodic live monitor run...")
                res = self.monitor.scan_and_update(db)
                if accelerated:
                    for mid in res.get("polled_match_ids", []):
                        adaptive.last_poll[mid] = adaptive.clock()
                logger.info(f"Live monitor run results: {res}")
            if accelerated:
                res = adaptive.run_extra(db, self.monitor)
                if res.get("extra_polls"):
                    logger.info("Adaptive live poll results: {}", res)
        except Exception as e:
            logger.error(f"Error running periodic monitor job: {e}")
        finally:
            db.close()

    def _execute_daily_historical_import(self) -> None:
        """Automatically imports yesterday's historical matches, generates training samples, rebuilds player profiles, and retrains ML models."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        from app.services.historical_importer import HistoricalImporter
        if not settings.api_keys_list:
            return
        yesterday = (datetime.now(ZoneInfo(settings.DAILY_HISTORY_IMPORT_TIMEZONE)) - timedelta(days=1)).date()
        with SessionLocal() as db:
            result = HistoricalImporter().import_date_range(db, yesterday.isoformat(), yesterday.isoformat())
            logger.info("Daily badminton import: {}", result)

    def _run_queued_job(self):
        from app.jobs.job_runner import JobRunner
        from app.jobs.job_service import JobService

        with SessionLocal() as db:
            job = JobService.claim_next_job(db, f"embedded-{os.getpid()}")
            job_id = job.id if job else None
        if job_id is not None:
            JobRunner.execute_job(job_id)

    def start(self) -> None:
        """Starts the periodic background scheduler."""
        if self.scheduler.running:
            self.scheduler.remove_all_jobs()

        if not (settings.ENABLE_JOB_WORKER or settings.ENABLE_LIVE_MONITOR or settings.ENABLE_DAILY_HISTORY_IMPORT):
            logger.info("Periodic jobs scheduler is disabled by configuration.")
            return

        if settings.ENABLE_JOB_WORKER:
            self.scheduler.add_job(self._run_queued_job, "interval", seconds=2, id="job_worker",
                                   replace_existing=True, max_instances=1, coalesce=True)

        if settings.ENABLE_LIVE_MONITOR:
            interval = settings.LIVE_MONITOR_INTERVAL_SECONDS
            if settings.ENABLE_ADAPTIVE_LIVE_POLLING and settings.OFFICIAL_S_ENABLED:
                interval = max(5, min(interval, settings.LIVE_POLL_FAST_SECONDS, settings.LIVE_POLL_WARM_SECONDS))
            logger.info("Scheduling live monitor tick every {} seconds; baseline {} seconds.",
                        interval, settings.LIVE_MONITOR_INTERVAL_SECONDS)
            self.scheduler.add_job(
                self._execute_live_monitor_job,
                "interval",
                seconds=interval,
                id="live_match_monitor",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )

        if settings.ENABLE_DAILY_HISTORY_IMPORT:
            logger.info(
                "Scheduling daily historical import at "
                f"{settings.DAILY_HISTORY_IMPORT_HOUR:02d}:{settings.DAILY_HISTORY_IMPORT_MINUTE:02d} "
                f"{settings.DAILY_HISTORY_IMPORT_TIMEZONE}."
            )
            self.scheduler.add_job(
                self._execute_daily_historical_import,
                "cron",
                hour=settings.DAILY_HISTORY_IMPORT_HOUR,
                minute=settings.DAILY_HISTORY_IMPORT_MINUTE,
                timezone=settings.DAILY_HISTORY_IMPORT_TIMEZONE,
                id="daily_historical_import",
                replace_existing=True,
            )

        if not self.scheduler.running:
            self.scheduler.start()

    def restart(self) -> None:
        """Restarts jobs using the current runtime settings."""
        if self.scheduler.running:
            self.scheduler.remove_all_jobs()
        self.start()

    def status(self) -> Dict[str, Any]:
        """Returns scheduler runtime state and configured jobs."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        
        tz_name = settings.DAILY_HISTORY_IMPORT_TIMEZONE or "Asia/Shanghai"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")
            
        now_tz = datetime.now(tz)
        server_time_str = now_tz.strftime("%Y-%m-%d %H:%M:%S")
        
        jobs = []
        if self.scheduler.running:
            for job in self.scheduler.get_jobs():
                jobs.append(
                    {
                        "id": job.id,
                        "name": job.name,
                        "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                        "trigger": str(job.trigger),
                    }
                )
        return {
            "running": self.scheduler.running,
            "server_time": server_time_str,
            "server_timezone": tz_name,
            "config": {
                "enable_live_monitor": settings.ENABLE_LIVE_MONITOR,
                "live_monitor_schedule_enabled": settings.LIVE_MONITOR_SCHEDULE_ENABLED,
                "live_monitor_interval_seconds": settings.LIVE_MONITOR_INTERVAL_SECONDS,
                "live_monitor_start_hour": getattr(settings, "LIVE_MONITOR_START_HOUR", 0),
                "live_monitor_end_hour": getattr(settings, "LIVE_MONITOR_END_HOUR", 23),
                "enable_daily_history_import": settings.ENABLE_DAILY_HISTORY_IMPORT,
                "daily_history_import_hour": settings.DAILY_HISTORY_IMPORT_HOUR,
                "daily_history_import_minute": settings.DAILY_HISTORY_IMPORT_MINUTE,
                "daily_history_import_timezone": settings.DAILY_HISTORY_IMPORT_TIMEZONE,
            },
            "jobs": jobs,
            "adaptive_polling": self.adaptive.status(),
            "monitoring_window": self.schedule_window.status(),
        }

    def shutdown(self) -> None:
        """Safely shuts down the scheduler."""
        if self.scheduler.running:
            logger.info("Shutting down periodic jobs scheduler...")
            self.scheduler.shutdown()
