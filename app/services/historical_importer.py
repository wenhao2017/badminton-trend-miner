from datetime import date, datetime, timedelta, timezone

from loguru import logger

from app.api_clients.historical_data_client import HistoricalDataClient
from app.config import settings
from app.db.models import Match, ProcessedDate, TrendSignalLog
from app.services.replay_engine import ReplayEngine
from app.services.signal_recommendation import COMPLETED_MATCH_STATUSES, TERMINAL_MATCH_STATUSES, provider_match_status, signal_settlement_result
from app.services.sportradar_importer import SportradarImporter
from app.services.trend_labeler import TrendLabeler
from app.utils.sportradar_event import is_womens_singles_event


class HistoricalImporter:
    def __init__(self):
        self.client = HistoricalDataClient()
        self.importer = SportradarImporter()

    def import_match_by_id(self, db, match_id, force=False, *, women_only=True):
        predicate = is_womens_singles_event
        existing = db.query(Match).filter_by(external_match_id=match_id).first()
        if not force and existing and existing.timeline_complete and existing.training_source_hash == existing.last_timeline_hash:
            return True
        summary = self.client.get_match_summary(db, match_id)
        if not predicate(summary.get("sport_event") or {}):
            return False
        match = self.importer.import_match(db, summary)
        if match.status in TERMINAL_MATCH_STATUSES:
            for signal in db.query(TrendSignalLog).filter_by(match_id=match.id, final_result=None):
                signal.final_result = signal_settlement_result(signal, match)
            db.commit()
        if match.status in COMPLETED_MATCH_STATUSES:
            timeline = self.client.get_match_timeline(db, match_id, refresh=force)
            self.importer.import_timeline(db, match.id, timeline)
            if match.timeline_complete:
                self.build_training_data(db, match)
            elif timeline.get("timeline"):
                return False
        return match.status in TERMINAL_MATCH_STATUSES

    def build_training_data(self, db, match):
        if not match.timeline_complete or match.training_source_hash == match.last_timeline_hash:
            return
        ReplayEngine().replay_match(db, match.id)
        TrendLabeler().generate_labels(db, match.id)
        match.training_source_hash = match.last_timeline_hash
        db.commit()

    def import_date_range(self, db, start_date_str, end_date_str, women_only=True, force=False):
        if not women_only:
            raise ValueError("This database is scoped to women’s singles.")
        if not settings.api_keys_list:
            raise ValueError("Set SPORTRADAR_API_KEYS before requesting historical data.")
        start, end = date.fromisoformat(start_date_str), date.fromisoformat(end_date_str)
        if start > end:
            raise ValueError("Start date must not be after the end date.")
        result = dict(start_date=start_date_str, end_date=end_date_str, total_matches_found=0,
                      imported_successfully=0, failed_imports=0, skipped_dates_count=0)
        predicate = is_womens_singles_event

        for offset in range((end - start).days + 1):
            day = (start + timedelta(days=offset)).isoformat()
            # The completed-date marker is for the default women's singles scope.
            marker = db.query(ProcessedDate).filter_by(date_str=day).first()
            if women_only and not force and marker and marker.status == "completed":
                result["skipped_dates_count"] += 1
                continue
            payload = self.client.get_schedule_for_date(db, day)
            rows = payload["summaries"]
            eligible = {event["id"]: row for row in rows
                        if (event := row.get("sport_event") or {}).get("id") and predicate(event)}
            pending = False
            failures = 0
            result["total_matches_found"] += len(eligible)
            for event_id, row in sorted(eligible.items(), key=lambda item: item[1]["sport_event"].get("start_time") or ""):
                if provider_match_status(row.get("sport_event_status") or {}) not in TERMINAL_MATCH_STATUSES:
                    pending = True
                    continue
                try:
                    if self.import_match_by_id(db, event_id, force, women_only=women_only):
                        result["imported_successfully"] += 1
                    else:
                        failures += 1
                except Exception as exc:
                    logger.warning("Could not import {}: {}", event_id, exc)
                    db.rollback()
                    failures += 1
            result["failed_imports"] += failures
            if women_only:
                if marker is None:
                    marker = ProcessedDate(date_str=day)
                    db.add(marker)
                marker.status = "partial" if pending or failures or date.fromisoformat(day) >= datetime.now(timezone.utc).date() else "completed"
                marker.match_count = len(eligible) - failures
                db.commit()
        return result

