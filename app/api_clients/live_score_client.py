from sqlalchemy.orm import Session
from app.api_clients.badminton_api_base import BadmintonApiBase
from app.api_clients.sportradar_badminton_api import SportradarBadmintonApi
from loguru import logger

class LiveScoreClient:
    def __init__(self):
        logger.info("Initializing LiveScoreClient in SPORTRADAR mode")
        self.api: BadmintonApiBase = SportradarBadmintonApi()

    def get_live_matches(self, db: Session):
        """Fetches live matches summaries."""
        return self.api.get_live_summaries(db)

    def get_match_timeline(self, db: Session, match_id: str):
        """Fetches live point-by-point timeline."""
        return self.api.get_match_timeline(db, match_id)

    def get_match_summary(self, db: Session, match_id: str):
        """Fetches match summary."""
        return self.api.get_match_summary(db, match_id)
