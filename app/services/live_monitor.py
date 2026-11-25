from datetime import datetime, timedelta, timezone
from threading import Lock

from loguru import logger
from sqlalchemy import func

from app.api_clients.live_score_client import LiveScoreClient
from app.config import settings
from app.db.models import Match, MatchLiveSnapshot, MatchPlayer, TrendSignalLog
from app.services.alert_service import AlertService
from app.services.bwf_candidate import start_markers
from app.services.historical_importer import HistoricalImporter
from app.services.official_s import process_official_batch
from app.services.replay_engine import ReplayEngine
from app.services.signal_admission import is_signal_excluded
from app.services.signal_engine import SignalEngine, signal_entry_allowed
from app.services.signal_recommendation import (
    ACTIVE_MATCH_STATUSES, COMPLETED_MATCH_STATUSES, TERMINAL_MATCH_STATUSES,
    provider_match_status, signal_settlement_result,
)
from app.services.sportradar_importer import SportradarImporter
from app.services.trend_predictor import TrendPredictor
from app.utils.sportradar_event import is_womens_singles_event

_scan_lock = Lock()


def fresh_live_point(point):
    raw = point.raw_event_json or {}
    value = raw.get("time")
    if not isinstance(value, str):
        return False
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return False
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        return -30 <= age <= settings.LIVE_SIGNAL_MAX_AGE_SECONDS
    except ValueError:
        return False


class LiveMonitor:
    def __init__(self):
        self.live_client = LiveScoreClient()
        self.importer = SportradarImporter()
        self.replay_engine = ReplayEngine()
        self.predictor = TrendPredictor()
        self.signal_engine = SignalEngine()
        self.alert_service = AlertService()

    def _settle_match_signals(self, db, match):
        resolved = 0
        for signal in db.query(TrendSignalLog).filter_by(match_id=match.id, final_result=None):
            result = signal_settlement_result(signal, match)
            if result is not None:
                signal.final_result = result
                resolved += 1
        db.commit()
        return resolved

    def _finalize(self, db, match, timeline=None):
        self._settle_match_signals(db, match)
        if match.status not in COMPLETED_MATCH_STATUSES:
            return
        if match.timeline_complete and match.training_source_hash == match.last_timeline_hash:
            return
        if timeline is None:
            timeline = self.live_client.get_match_timeline(db, match.external_match_id)
        if timeline.get("sport_event") and provider_match_status(timeline.get("sport_event_status") or {}) in TERMINAL_MATCH_STATUSES:
            match = self.importer.import_match(db, timeline)
            self._settle_match_signals(db, match)
        self.importer.import_timeline(db, match.id, timeline)
        HistoricalImporter().build_training_data(db, match)

    def _live_points(self, db, match, *, markers=()):
        points = self.replay_engine.point_repo.get_points_by_match(db, match.id)
        if not points or not match.timeline_ready:
            return False
        sent = False
        if settings.OFFICIAL_S_ENABLED and not is_signal_excluded(match):
            signal = process_official_batch(db, match.id, self.replay_engine, self.predictor, self.signal_engine,
                                            observed_at=datetime.now(timezone.utc).isoformat(), start_markers=markers)
            if signal and (settings.ENABLE_TELEGRAM_ALERT or settings.ENABLE_EMAIL_ALERT or settings.ENABLE_SLACK_ALERT):
                sent = self.alert_service.trigger_alert(db, signal)
        # Keep current score and metrics visible when publication is disabled,
        # the model is missing, or this match has already published.
        point = points[-1]
        latest = db.query(func.max(MatchLiveSnapshot.snapshot_point_index)).filter_by(match_id=match.id).scalar() or 0
        if point.point_index > latest and signal_entry_allowed(point.game_no, point.point_index):
            players = [row.player_id for row in db.query(MatchPlayer).filter_by(match_id=match.id)]
            if len(players) == 2:
                pair = [self.replay_engine.generate_snapshot_for_point(db, match.id, point.point_index, points, pid, opp)
                        for pid, opp in (players, players[::-1])]
                db.add_all(pair)
                db.commit()
        return sent

    def _poll(self, db, match):
        timeline = self.live_client.get_match_timeline(db, match.external_match_id)
        payload_status = provider_match_status(timeline.get("sport_event_status") or {})
        if payload_status in TERMINAL_MATCH_STATUSES:
            if timeline.get("sport_event"):
                match = self.importer.import_match(db, timeline)
            else:
                match = self.importer.import_match(db, self.live_client.get_match_summary(db, match.external_match_id))
            if match.status in TERMINAL_MATCH_STATUSES:
                self._finalize(db, match, timeline)
            return False
        if payload_status != "live":
            return False
        self.importer.import_timeline(db, match.id, timeline)
        return self._live_points(db, match, markers=start_markers(timeline.get("timeline") or []))

    def poll_candidate(self, db, match_id):
        with _scan_lock:
            match = db.get(Match, match_id)
            if match and match.status == "live" and not is_signal_excluded(match):
                return self._poll(db, match)
        return False

    def _finalize_tracked(self, db, active_ids):
        due = datetime.utcnow() - timedelta(minutes=10)
        pending = db.query(TrendSignalLog.id).filter(
            TrendSignalLog.match_id == Match.id, TrendSignalLog.final_result.is_(None)).exists()
        rows = db.query(Match).filter(
            Match.status.in_(ACTIVE_MATCH_STATUSES) | pending
            | (Match.status.in_(COMPLETED_MATCH_STATUSES) & Match.training_source_hash.is_(None)
               & (Match.match_date >= datetime.utcnow() - timedelta(days=2))),
            Match.last_provider_fetch_at.is_(None) | (Match.last_provider_fetch_at <= due),
        ).order_by(Match.last_provider_fetch_at.asc().nullsfirst()).limit(20).all()
        count = 0
        for match in rows:
            if match.external_match_id in active_ids:
                continue
            try:
                match.last_provider_fetch_at = datetime.utcnow()
                db.commit()
                if match.status in TERMINAL_MATCH_STATUSES:
                    self._settle_match_signals(db, match)
                if match.status not in TERMINAL_MATCH_STATUSES or not match.winner_player_id:
                    match = self.importer.import_match(db, self.live_client.get_match_summary(db, match.external_match_id))
                if match.status in TERMINAL_MATCH_STATUSES:
                    self._finalize(db, match)
                    count += 1
            except Exception as exc:
                db.rollback()
                logger.warning("Could not finalize a tracked match: {}", exc)
        return {"finalized": count}

    def finalize_tracked_matches(self, db):
        if not settings.api_keys_list:
            return {"status": "disabled_no_api_key", "finalized": 0}
        with _scan_lock:
            return self._finalize_tracked(db, set())

    def scan_and_update(self, db):
        if not settings.api_keys_list:
            return {"status": "disabled_no_api_key", "processed_matches": 0, "alerts_fired": 0}
        if not _scan_lock.acquire(blocking=False):
            return {"status": "busy", "processed_matches": 0, "alerts_fired": 0}
        try:
            payload = self.live_client.get_live_matches(db)
            if not isinstance(payload.get("summaries"), list):
                raise ValueError("Badminton live summaries response has no summaries array")
            active, polled, errors, alerts = set(), [], 0, 0
            for summary in payload["summaries"]:
                event = summary.get("sport_event") or {}
                if not is_womens_singles_event(event) or not event.get("id"):
                    continue
                if event["id"] in active:
                    continue
                active.add(event["id"])
                try:
                    match = self.importer.import_match(db, summary)
                    if match.status in TERMINAL_MATCH_STATUSES:
                        self._finalize(db, match)
                    elif match.status == "live" and (event.get("coverage") or {}).get("live") not in (False, "false"):
                        alerts += int(self._poll(db, match))
                    polled.append(match.id)
                except Exception as exc:
                    db.rollback()
                    errors += 1
                    logger.warning("Could not process {}: {}", event["id"], exc)
            return {"status": "partial" if errors else "success", "processed_matches": len(polled),
                    "polled_match_ids": polled, "failed_matches": errors, "alerts_fired": alerts,
                    **self._finalize_tracked(db, active)}
        finally:
            _scan_lock.release()

