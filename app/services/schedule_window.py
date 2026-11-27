"""Schedule-driven discovery window. Times are UTC; failed schedules fail open."""
from datetime import datetime, time, timedelta, timezone
from time import monotonic
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.models import Match
from app.services.signal_admission import is_event_excluded
from app.utils.sportradar_event import is_womens_singles_event, is_singles_event

TERMINAL = {"ended", "closed", "completed", "cancelled", "abandoned", "retired", "walkover"}
ACTIVE = {"live", "suspended", "interrupted", "delayed"}
PRE_MATCH_LEAD_MINUTES = 20


def parse_time(value):
    if not value:
        return None
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _display_timezone():
    from app.config import settings
    try:
        return ZoneInfo(settings.DAILY_HISTORY_IMPORT_TIMEZONE or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def _has_two_sided_competitors(event):
    competitors = event.get("competitors") or []
    return (
        len(competitors) == 2
        and {c.get("qualifier") for c in competitors} == {"home", "away"}
        and len({c.get("id") for c in competitors if c.get("id")}) == 2
    )


class ScheduleWindow:
    def __init__(self):
        self.entries = []
        self.refreshed = None
        self.error = None
        self.refresh_clock = float("-inf")

    def _sync_schedule_matches(self, db, now):
        """Upsert today's and upcoming scheduled women's singles matches for the live board."""
        if not isinstance(db, Session):
            return 0
        from app.services.sportradar_importer import SportradarImporter

        tz = _display_timezone()
        today_local = now.astimezone(tz).date()
        today_start_utc = datetime.combine(today_local, time.min, tzinfo=tz).astimezone(timezone.utc)

        importer = SportradarImporter()
        synced = 0
        for item in self.entries:
            event = item.get("sport_event") or {}
            ext_id = event.get("id")
            if not ext_id or not _has_two_sided_competitors(event):
                continue
            start = parse_time(event.get("start_time"))
            if not start:
                continue
            existing = importer.match_repo.get_by_external_id(db, ext_id)
            if start < today_start_utc and existing is None:
                continue
            try:
                importer.import_match(db, item)
                synced += 1
            except Exception as exc:
                db.rollback()
                logger.warning("Failed syncing schedule match {}: {}", ext_id, exc)
        return synced

    def active(self, db, api, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
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
                        if is_womens_singles_event(event) and is_singles_event(event) and not is_event_excluded(event):
                            if not parse_time(event.get("start_time")):
                                raise ValueError("Schedule entry has no valid start time")
                            entries[event["id"]] = item
                self.entries = list(entries.values())
                self.refreshed, self.error = now, None
                self._sync_schedule_matches(db, now)
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
        if tracked:
            return True
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        lead = timedelta(minutes=PRE_MATCH_LEAD_MINUTES)
        for item in self.entries:
            event, status_obj = item["sport_event"], item.get("sport_event_status", {})
            status = status_obj.get("status")
            start = parse_time(event.get("start_time"))
            if not start or status in {"cancelled", "walkover"}:
                continue
            if status in ACTIVE:
                return True
            if status not in TERMINAL:
                if now >= start - lead:
                    return True
                continue
            end = parse_time(status_obj.get("end_time"))
            if end and start <= now < end:
                return True
        return False

    def status(self):
        return {"mode": "daily_schedule", "refresh_minutes": 15,
                "pre_match_minutes": PRE_MATCH_LEAD_MINUTES,
                "tail_hours": 0, "last_refresh": self.refreshed.isoformat() if self.refreshed else None,
                "schedule_matches": len(self.entries), "error": self.error}
