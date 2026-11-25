import pytest
from datetime import datetime, timedelta
from app.db.models import JobRun
from app.jobs.job_service import JobService, JobContext
from app.jobs.job_runner import JobRunner
from app.jobs.handlers import HANDLERS

class NonClosingSession:
    def __init__(self, session):
        self.session = session
    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self.session, name)
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

def test_job_lifecycle(db_session):
    def sample_handler(payload, context):
        context.log("Running sample job")
        context.update_progress(50, 100, "Processing")
        if payload.get("should_fail"):
            raise ValueError("Failure injection")
        return {"processed": True}
        
    HANDLERS["sample_job"] = sample_handler

    payload = {"should_fail": False}
    job = JobService.enqueue(db_session, "sample_job", payload, dedupe_key="sample_dedupe")
    assert job.id is not None
    assert job.status == "queued"
    assert job.payload_json == payload
    assert job.dedupe_key == "sample_dedupe"

    duplicate_job = JobService.enqueue(db_session, "sample_job", payload, dedupe_key="sample_dedupe")
    assert duplicate_job.id == job.id

    claimed = JobService.claim_next_job(db_session, "test-worker")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.locked_by == "test-worker"
    assert claimed.locked_at is not None

    assert JobService.claim_next_job(db_session, "test-worker") is None

    # Patch SessionLocal to return NonClosingSession(db_session)
    import app.jobs.job_runner
    import app.jobs.job_service
    orig_session = app.jobs.job_runner.SessionLocal
    app.jobs.job_runner.SessionLocal = lambda: NonClosingSession(db_session)
    app.jobs.job_service.SessionLocal = lambda: NonClosingSession(db_session)
    try:
        JobRunner.execute_job(job.id)
    finally:
        app.jobs.job_runner.SessionLocal = orig_session
        app.jobs.job_service.SessionLocal = orig_session
    
    db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.result_json == {"processed": True}
    assert job.progress_current == 100
    assert job.progress_message == "Completed successfully."

def test_job_failure(db_session):
    def sample_handler(payload, context):
        raise ValueError("Failed intentionally")
        
    HANDLERS["failed_sample_job"] = sample_handler

    job = JobService.enqueue(db_session, "failed_sample_job", {}, dedupe_key="fail_dedupe")
    claimed = JobService.claim_next_job(db_session, "test-worker")
    assert claimed is not None

    import app.jobs.job_runner
    import app.jobs.job_service
    orig_session = app.jobs.job_runner.SessionLocal
    app.jobs.job_runner.SessionLocal = lambda: NonClosingSession(db_session)
    app.jobs.job_service.SessionLocal = lambda: NonClosingSession(db_session)
    try:
        JobRunner.execute_job(job.id)
    finally:
        app.jobs.job_runner.SessionLocal = orig_session
        app.jobs.job_service.SessionLocal = orig_session
    
    db_session.refresh(job)
    assert job.status == "failed"
    assert "Failed intentionally" in job.error_message

def test_job_cancellation(db_session):
    def cancellable_handler(payload, context):
        for i in range(10):
            if context.should_cancel():
                context.log("Cancelled")
                return {"interrupted": True}
        return {"processed": True}

    HANDLERS["cancellable_job"] = cancellable_handler

    job = JobService.enqueue(db_session, "cancellable_job", {})
    claimed = JobService.claim_next_job(db_session, "test-worker")
    assert claimed is not None

    import app.jobs.job_runner
    import app.jobs.job_service
    orig_session = app.jobs.job_runner.SessionLocal
    app.jobs.job_runner.SessionLocal = lambda: NonClosingSession(db_session)
    app.jobs.job_service.SessionLocal = lambda: NonClosingSession(db_session)
    try:
        success = JobService.cancel_job(db_session, job.id)
        assert success is True
        
        db_session.refresh(job)
        assert job.status == "cancelled"

        JobRunner.execute_job(job.id)
        db_session.refresh(job)
        assert job.status == "cancelled"
    finally:
        app.jobs.job_runner.SessionLocal = orig_session
        app.jobs.job_service.SessionLocal = orig_session

def test_recover_stale_jobs(db_session):
    stale_time = datetime.utcnow() - timedelta(minutes=20)
    job = JobRun(
        job_type="sample_job",
        status="running",
        payload_json={},
        locked_by="dead-worker",
        locked_at=stale_time,
        started_at=stale_time,
        created_at=stale_time,
        updated_at=stale_time
    )
    db_session.add(job)
    db_session.commit()

    count = JobService.recover_stale_jobs(db_session, max_lock_minutes=15)
    assert count == 1

    db_session.refresh(job)
    assert job.status == "queued"
    assert job.locked_by is None
    assert job.locked_at is None
