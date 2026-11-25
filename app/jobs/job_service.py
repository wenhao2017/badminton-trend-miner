from datetime import datetime, timedelta
import logging
from typing import Any, Dict, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db.models import JobRun
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

class JobContext:
    def __init__(self, job_id: int, db_session_factory=SessionLocal):
        self.job_id = job_id
        self.db_session_factory = db_session_factory

    def update_progress(self, current: int, total: int = 100, message: Optional[str] = None):
        db = self.db_session_factory()
        try:
            db.query(JobRun).filter(JobRun.id == self.job_id).update({
                "progress_current": current,
                "progress_total": total,
                "progress_message": message,
                "updated_at": datetime.utcnow()
            })
            db.commit()
        except Exception as e:
            logger.error(f"Failed to update progress for job {self.job_id}: {e}")
            db.rollback()
        finally:
            db.close()

    def should_cancel(self) -> bool:
        db = self.db_session_factory()
        try:
            job = db.query(JobRun).filter(JobRun.id == self.job_id).first()
            if job and job.status == "cancelled":
                return True
            return False
        except Exception:
            return False
        finally:
            db.close()

    def log(self, message: str):
        logger.info(f"[Job {self.job_id}] {message}")


class JobService:
    @staticmethod
    def enqueue(db: Session, job_type: str, payload: dict, dedupe_key: Optional[str] = None) -> JobRun:
        db.execute(text("BEGIN IMMEDIATE"))
        force = payload.get("force", False)
        if dedupe_key and not force:
            existing = db.query(JobRun).filter(
                JobRun.dedupe_key == dedupe_key,
                JobRun.status.in_(["queued", "running"])
            ).first()
            if existing:
                logger.info(f"Job of type {job_type} with dedupe_key {dedupe_key} already exists in state {existing.status}. Skipping enqueue.")
                db.commit()
                return existing

        prior = db.query(JobRun.id).filter_by(dedupe_key=dedupe_key).first() if dedupe_key else None
        actual_dedupe_key = dedupe_key
        if (force or prior) and dedupe_key:
            actual_dedupe_key = f"{dedupe_key}-force-{datetime.utcnow().timestamp()}"

        new_job = JobRun(
            job_type=job_type,
            status="queued",
            payload_json=payload,
            dedupe_key=actual_dedupe_key,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(new_job)
        db.commit()
        db.refresh(new_job)
        logger.info(f"Enqueued job {new_job.id} of type {job_type} (dedupe_key: {actual_dedupe_key})")
        return new_job

    @staticmethod
    def claim_next_job(db: Session, worker_name: str) -> Optional[JobRun]:
        db.execute(text("BEGIN IMMEDIATE"))
        job = db.query(JobRun).filter_by(status="queued").order_by(JobRun.created_at, JobRun.id).first()
        if job:
            job.status = "running"
            job.locked_by = worker_name
            job.locked_at = datetime.utcnow()
            job.started_at = datetime.utcnow()
            job.progress_current = 0
            job.progress_total = 100
            job.progress_message = "Starting job..."
            job.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(job)
            logger.info(f"Worker {worker_name} locked job {job.id} of type {job.job_type}")
            return job
        db.commit()
        return None

    @staticmethod
    def mark_success(db: Session, job_id: int, result: dict) -> None:
        job = db.query(JobRun).filter(JobRun.id == job_id).first()
        if job:
            job.status = "succeeded"
            job.result_json = result
            job.finished_at = datetime.utcnow()
            job.progress_current = job.progress_total
            job.progress_message = "Completed successfully."
            job.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Job {job_id} marked as succeeded.")

    @staticmethod
    def mark_failure(db: Session, job_id: int, error_message: str) -> None:
        job = db.query(JobRun).filter(JobRun.id == job_id).first()
        if job:
            job.status = "failed"
            job.error_message = error_message
            job.finished_at = datetime.utcnow()
            job.progress_message = "Failed."
            job.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Job {job_id} marked as failed. Error: {error_message}")

    @staticmethod
    def cancel_job(db: Session, job_id: int) -> bool:
        job = db.query(JobRun).filter(JobRun.id == job_id).first()
        if job:
            if job.status in ["queued", "running"]:
                job.status = "cancelled"
                job.finished_at = datetime.utcnow()
                job.progress_message = "Cancelled by user."
                job.updated_at = datetime.utcnow()
                db.commit()
                logger.info(f"Job {job_id} marked as cancelled.")
                return True
        return False

    @staticmethod
    def recover_stale_jobs(db: Session, max_lock_minutes: int = 15, *, embedded_restart=False) -> int:
        threshold = datetime.utcnow() - timedelta(minutes=max_lock_minutes)
        expired = JobRun.locked_at < threshold
        if embedded_restart:
            expired = expired | JobRun.locked_by.startswith("embedded-")
        stale_jobs = db.query(JobRun).filter(JobRun.status == "running", expired).all()
        count = 0
        for job in stale_jobs:
            job.status = "queued"
            job.locked_by = None
            job.locked_at = None
            job.started_at = None
            job.progress_current = 0
            job.progress_message = "Recovered from stale running state."
            job.updated_at = datetime.utcnow()
            count += 1
        if count > 0:
            db.commit()
            logger.info(f"Recovered {count} stale running jobs back to queued state.")
        return count
