import os
import sys

# Insert project parent to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import SessionLocal
from app.services.player_profile_builder import PlayerProfileBuilder
from loguru import logger

def main():
    db = SessionLocal()
    builder = PlayerProfileBuilder()
    
    logger.info("Executing player profile builder update...")
    try:
        count = builder.rebuild_profiles(db)
        logger.info(f"Rebuild completed. Profiled {count} players.")
    except Exception as e:
        logger.error(f"Failed to rebuild player profiles: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
