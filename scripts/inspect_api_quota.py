import os
import sys

# Insert project parent to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import SessionLocal
from app.db.models import ApiKeyUsage
from loguru import logger

def main():
    db = SessionLocal()
    
    logger.info("Inspecting Sportradar API Keys usage metadata...")
    try:
        usages = db.query(ApiKeyUsage).all()
        if not usages:
            print("No API Key usages recorded in database yet.")
        else:
            print(f"{'Key Hash':<16} | {'Used':<6} | {'Remaining':<10} | {'Cooldown Until':<20} | {'Last Request'}")
            print("-" * 75)
            for u in usages:
                cooldown_str = u.cooldown_until.isoformat() if u.cooldown_until else "Active"
                last_req = u.last_request_at.isoformat() if u.last_request_at else "Never"
                print(f"{u.api_key_hash[:12]}... | {u.quota_used:<6} | {u.quota_remaining:<10} | {cooldown_str:<20} | {last_req}")
    except Exception as e:
        logger.error(f"Failed to inspect keys usage: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
