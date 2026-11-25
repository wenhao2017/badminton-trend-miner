import os
import sys

# Insert project parent to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import SessionLocal
from app.services.player_pattern_miner import PlayerPatternMiner
from loguru import logger

def main():
    db = SessionLocal()
    miner = PlayerPatternMiner()
    
    logger.info("Executing player pattern miner...")
    try:
        count = miner.mine_patterns(db)
        logger.info(f"Pattern mining complete. Saved {count} patterns.")
    except Exception as e:
        logger.error(f"Failed to mine player patterns: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
