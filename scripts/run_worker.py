import os
import sys
import time
import socket
import signal
import logging

# Ensure PYTHONPATH allows loading app module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import SessionLocal
from app.jobs.job_service import JobService
from app.jobs.job_runner import JobRunner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("run_worker")

class Worker:
    def __init__(self):
        self.worker_name = f"worker-{socket.gethostname()}-{os.getpid()}"
        self.running = True
        
        signal.signal(signal.SIGINT, self.handle_exit)
        signal.signal(signal.SIGTERM, self.handle_exit)

    def handle_exit(self, signum, frame):
        logger.info("Termination signal received. Stopping worker loop...")
        self.running = False

    def start(self):
        logger.info(f"Starting badminton-trend-miner worker: {self.worker_name}")
        
        db = SessionLocal()
        try:
            recovered_count = JobService.recover_stale_jobs(db)
            if recovered_count > 0:
                logger.info(f"Recovered {recovered_count} stale running jobs on startup.")
        except Exception as e:
            logger.error(f"Failed to recover stale jobs on startup: {e}")
        finally:
            db.close()

        while self.running:
            db = SessionLocal()
            try:
                job = JobService.claim_next_job(db, self.worker_name)
                if job:
                    logger.info(f"Claimed job {job.id} of type '{job.job_type}'. Processing...")
                    db.close()
                    
                    JobRunner.execute_job(job.id)
                    
                    logger.info(f"Finished processing job {job.id}.")
                    continue
            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
            finally:
                # Safely close if session wasn't closed or was reopened
                try:
                    db.close()
                except Exception:
                    pass

            time.sleep(2.0)

        logger.info("Worker loop stopped. Exiting.")

if __name__ == "__main__":
    worker = Worker()
    worker.start()
