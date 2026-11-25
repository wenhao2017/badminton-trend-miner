"""Schedule-driven discovery window. Times are UTC; failed schedules fail open."""
from datetime import datetime, timedelta, timezone
from time import monotonic
from loguru import logger
from sqlalchemy import select
from app.db.models import Match
from app.utils.sportradar_event import is_womens_singles_event, is_singles_event

TERMINAL = {"ended", "closed", "completed", "cancelled", "abandoned", "retired", "walkover"}

def parse_time(value):
    if not value:
        return None
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class ScheduleWindow:
    def __init__(self):
        self.entries = []
        self.refreshed = None
        self.error = None
        self.refresh_clock = float("-inf")

    def active(self, db, api, now=None):
        now = now or datetime.now(timezone.utc)
        if monotonic() - self.refresh_clock >= 900:
            self.refresh_clock = monotonic()
            try:
                entries = {}
                for offset in (-1, 0, 1):
                    date = (now + timedelta(days=offset)).date().isoformat()
                    data = api.get_daily_schedule(db, date)
                    if "summaries" not in data and "schedules" not in data:
                        raise ValueError("Schedule response has no entries field")
                    for item in data.get("summaries", data.get("schedules", [])):
                        event = item.get("sport_event", {})
                        if is_womens_singles_event(event) and is_singles_event(event):
                            if not parse_time(event.get("start_time")):
                                raise ValueError("Schedule entry has no valid start time")
                            entries[event["id"]] = item
                self.entries = list(entries.values())
                self.refreshed, self.error = now, None
            except Exception as exc:
                self.error = str(type(exc).__name__)
                logger.warning("Schedule refresh failed; keeping live discovery enabled: {}", self.error)
        if self.error:
            return True
        # Existing live/suspended matches survive midnight and schedule changes.
        tracked = db.scalars(select(Match).where(Match.status.in_(
            ["live", "suspended", "interrupted", "delayed"]))).first()
        return self.evaluate(now, tracked is not None)

    def evaluate(self, now, tracked=False):
        from zoneinfo import ZoneInfo
        from app.config import settings
        tz = ZoneInfo(settings.DAILY_HISTORY_IMPORT_TIMEZONE or "Asia/Shanghai")
        days, unfinished = {}, False
        for item in self.entries:
            event, status = item["sport_event"], item.get("sport_event_status", {})
            start = parse_time(event.get("start_time"))
            if not start or status.get("status") in {"cancelled", "walkover"}:
                continue
            end = parse_time(status.get("end_time"))
            day = start.astimezone(tz).date()
            days.setdefault(day, []).append((start, (end or start)))
            if start <= now and status.get("status") not in TERMINAL:
                unfinished = True
        if tracked or unfinished:
            return True
        return any(min(s for s, e in pairs) <= now < max(e for s, e in pairs)
                   for pairs in days.values())

    def status(self):
        return {"mode": "daily_schedule", "refresh_minutes": 15,
                "tail_hours": 0, "last_refresh": self.refreshed.isoformat() if self.refreshed else None,
                "schedule_matches": len(self.entries), "error": self.error}
