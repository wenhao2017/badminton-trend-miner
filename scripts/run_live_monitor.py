import os
import sys

# Insert project parent to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import SessionLocal
from app.services.live_monitor import LiveMonitor
from loguru import logger

def main():
    db = SessionLocal()
    monitor = LiveMonitor()
    
    logger.info("Executing manual live monitor check...")
    try:
        res = monitor.scan_and_update(db)
        logger.info(f"Monitor check completed. Results: {res}")
    except Exception as e:
        logger.error(f"Failed to scan live matches: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
