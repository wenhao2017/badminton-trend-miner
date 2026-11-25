from abc import ABC, abstractmethod
from sqlalchemy.orm import Session
from typing import Dict, Any

class BadmintonApiBase(ABC):
    @abstractmethod
    def get_daily_schedule(self, db: Session, date: str) -> Dict[str, Any]:
        """Fetch matches scheduled for a specific date (YYYY-MM-DD)."""
        pass

    @abstractmethod
    def get_live_summaries(self, db: Session) -> Dict[str, Any]:
        """Fetch list of all currently live tennis matches."""
        pass

    @abstractmethod
    def get_match_summary(self, db: Session, match_id: str) -> Dict[str, Any]:
        """Fetch full summary for a specific match, including players and final results."""
        pass

    @abstractmethod
    def get_match_timeline(self, db: Session, match_id: str, *, refresh=False) -> Dict[str, Any]:
        """Fetch play-by-play events (point timeline) for a match."""
        pass

    @abstractmethod
    def get_player_profile(self, db: Session, player_id: str) -> Dict[str, Any]:
        """Fetch detailed information about a player (hand, country, rankings)."""
        pass

    @abstractmethod
    def get_tournament_profile(self, db: Session, tournament_id: str) -> Dict[str, Any]:
        """Fetch detailed profile of a tournament."""
        pass
