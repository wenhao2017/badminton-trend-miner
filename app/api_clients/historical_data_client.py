from sqlalchemy.orm import Session
from app.api_clients.badminton_api_base import BadmintonApiBase
from app.api_clients.sportradar_badminton_api import SportradarBadmintonApi
from loguru import logger

class HistoricalDataClient:
    def __init__(self):
        logger.info("Initializing HistoricalDataClient in SPORTRADAR mode")
        self.api: BadmintonApiBase = SportradarBadmintonApi()

    def get_schedule_for_date(self, db: Session, date_str: str):
        """Fetches match schedule for a specific date."""
        return self.api.get_daily_schedule(db, date_str)

    def get_match_summary(self, db: Session, match_id: str):
        """Fetches full match summary."""
        return self.api.get_match_summary(db, match_id)

    def get_player_profile(self, db: Session, player_id: str):
        """Fetches competitor details."""
        return self.api.get_player_profile(db, player_id)

    def get_match_timeline(self, db: Session, match_id: str, *, refresh=False):
        """Fetches full play-by-play events."""
        return self.api.get_match_timeline(db, match_id, refresh=refresh)
