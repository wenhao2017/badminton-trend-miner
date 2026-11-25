import json
from sqlalchemy.orm import Session
from typing import Dict, Any
from app.config import settings
from app.api_clients.badminton_api_base import BadmintonApiBase
from app.api_clients.sportradar_client import SportradarClient

class SportradarBadmintonApi(BadmintonApiBase):
    def __init__(self):
        self.client = SportradarClient()

    def _get_path(self, template: str, **kwargs) -> str:
        """Helper to interpolate settings locale, format, and identifiers."""
        # Clean placeholders like /{locale}/schedules/{date}/summaries.{format}
        locale = settings.SPORTRADAR_LOCALE
        fmt = settings.SPORTRADAR_FORMAT
        
        path = template.replace("{locale}", locale).replace("{format}", fmt)
        for key, val in kwargs.items():
            path = path.replace(f"{{{key}}}", str(val))
        return path

    def get_daily_schedule(self, db: Session, date: str) -> Dict[str, Any]:
        template = settings.SPORTRADAR_DAILY_SCHEDULE_ENDPOINT
        path = self._get_path(template, date=date)
        entries = []
        seen = set()
        while True:
            query = {"offset": len(entries), "limit": 200}
            page = json.loads(self.client.get(db, path, params=query, ttl_seconds=900,
                                              max_cache_age_seconds=900))
            rows = page.get("summaries")
            if not isinstance(rows, list):
                raise ValueError("Badminton daily summaries response has no summaries array")
            ids = {item.get("sport_event", {}).get("id") for item in rows}
            if rows and ids <= seen:
                raise ValueError("Badminton daily summaries pagination repeated a page")
            entries.extend(rows)
            seen.update(ids)
            if len(rows) < 200:
                return {"summaries": entries}

    def get_live_summaries(self, db: Session) -> Dict[str, Any]:
        template = settings.SPORTRADAR_LIVE_SUMMARIES_ENDPOINT
        path = self._get_path(template)
        # Live queries should use shorter cache TTL to stay fresh
        res_str = self.client.get(db, path, ttl_seconds=settings.SPORTRADAR_LIVE_CACHE_TTL_SECONDS)
        return json.loads(res_str)

    def _get_ttl(self, db: Session, match_id: str) -> int:
        """Helper to determine cache TTL. Uses 30 days for ended/closed matches, 20s for live matches."""
        try:
            from app.db.models import Match
            match_obj = db.query(Match).filter(Match.external_match_id == match_id).first()
            if match_obj and (
                match_obj.status in ["cancelled", "abandoned", "retired", "walkover"]
                or (match_obj.status in ["ended", "closed", "completed"] and match_obj.winner_player_id)
            ):
                return settings.SPORTRADAR_HISTORY_CACHE_TTL_SECONDS
        except Exception:
            pass
        return settings.SPORTRADAR_LIVE_CACHE_TTL_SECONDS

    def get_match_summary(self, db: Session, match_id: str) -> Dict[str, Any]:
        template = settings.SPORTRADAR_MATCH_SUMMARY_ENDPOINT
        path = self._get_path(template, match_id=match_id)
        ttl = self._get_ttl(db, match_id)
        res_str = self.client.get(db, path, ttl_seconds=ttl)
        return json.loads(res_str)

    def get_match_timeline(self, db: Session, match_id: str, *, refresh=False) -> Dict[str, Any]:
        template = settings.SPORTRADAR_MATCH_TIMELINE_ENDPOINT
        path = self._get_path(template, match_id=match_id)
        ttl = self._get_ttl(db, match_id)
        res_str = self.client.get(db, path, ttl_seconds=ttl, max_cache_age_seconds=0 if refresh else None)
        return json.loads(res_str)

    def get_player_profile(self, db: Session, player_id: str) -> Dict[str, Any]:
        template = settings.SPORTRADAR_PLAYER_PROFILE_ENDPOINT
        path = self._get_path(template, player_id=player_id)
        res_str = self.client.get(db, path, ttl_seconds=settings.SPORTRADAR_HISTORY_CACHE_TTL_SECONDS)
        return json.loads(res_str)

    def get_tournament_profile(self, db: Session, tournament_id: str) -> Dict[str, Any]:
        template = settings.SPORTRADAR_TOURNAMENT_PROFILE_ENDPOINT
        path = self._get_path(template, tournament_id=tournament_id)
        res_str = self.client.get(db, path, ttl_seconds=settings.SPORTRADAR_HISTORY_CACHE_TTL_SECONDS)
        return json.loads(res_str)
