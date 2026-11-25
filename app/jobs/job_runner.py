import logging
import traceback
from app.db.session import SessionLocal
from app.db.models import JobRun
from app.jobs.job_service import JobService, JobContext
from app.jobs.handlers import HANDLERS

logger = logging.getLogger(__name__)

class JobRunner:
    @staticmethod
    def execute_job(job_id: int) -> None:
        db = SessionLocal()
        job = db.query(JobRun).filter(JobRun.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found in database.")
            db.close()
            return

        job_type = job.job_type
        payload = job.payload_json
        
        handler = HANDLERS.get(job_type)
        if not handler:
            error_msg = f"No handler registered for job type: {job_type}"
            logger.error(error_msg)
            JobService.mark_failure(db, job_id, error_msg)
            db.close()
            return

        db.close()

        context = JobContext(job_id)
        logger.info(f"Running job {job_id} ({job_type})...")
        
        try:
            result = handler(payload, context)
            
            final_db = SessionLocal()
            try:
                current_job = final_db.query(JobRun).filter(JobRun.id == job_id).first()
                if current_job and current_job.status == "cancelled":
                    logger.info(f"Job {job_id} was cancelled. Skipping mark success.")
                else:
                    JobService.mark_success(final_db, job_id, result or {})
            finally:
                final_db.close()
                
        except Exception as e:
            logger.error(f"Job {job_id} failed with exception: {e}\n{traceback.format_exc()}")
            final_db = SessionLocal()
            try:
                current_job = final_db.query(JobRun).filter(JobRun.id == job_id).first()
                if current_job and current_job.status == "cancelled":
                    logger.info(f"Job {job_id} was cancelled. Skipping mark failure.")
                else:
                    JobService.mark_failure(final_db, job_id, str(e))
            finally:
                final_db.close()
