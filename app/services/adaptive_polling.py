"""Accelerate selected unpublished directions without accelerating global discovery."""
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy.dialects.sqlite import insert
from app.db.session import SessionLocal

from app.api_clients.request_budget import PollBudgetExceeded, optional_poll_budget
from app.config import settings
from app.db.models import Match, MatchSConfirmation, PollBudget, TrendSignalLog
from app.services.signal_admission import is_signal_excluded


def candidate_interval(state, now):
    """Return the best eligible tier; an opposed A/S direction never accelerates."""
    players = (state or {}).get("players", {})
    best = None
    for pid, item in players.items():
        latest = item.get("latest") or {}
        if latest.get("level") not in ("A", "S"):
            continue
        others = [v.get("latest") or {} for other, v in players.items() if other != pid]
        if len(others) != 1 or others[0].get("level") in ("A", "S"):
            continue
        try:
            source = datetime.fromisoformat(latest["source_time"].replace("Z", "+00:00"))
            age = (now - source).total_seconds()
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if not -60 <= age <= settings.LIVE_MONITOR_INTERVAL_SECONDS * 2:
            continue
        run = item.get("run") or []
        imminent = latest["level"] == "S" and (len(run) >= 2 or item.get("confirmation"))
        interval = (settings.LIVE_POLL_FAST_SECONDS if imminent
                    else settings.LIVE_POLL_WARM_SECONDS)
        best = min(best, interval) if best is not None else interval
    return best


class AdaptivePolling:
    def __init__(self, session_factory=SessionLocal, clock=time.monotonic):
        self.session_factory = session_factory
        self.clock = clock
        self.next_baseline = 0.0
        self.last_poll = {}

    def baseline_due(self):
        return self.clock() >= self.next_baseline

    def mark_baseline(self):
        self.next_baseline = self.clock() + settings.LIVE_MONITOR_INTERVAL_SECONDS

    def reserve(self):
        limit = settings.LIVE_POLL_EXTRA_REQUESTS_PER_DAY
        if limit <= 0:
            return False
        day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        try:
            with self.session_factory() as db:
                statement = insert(PollBudget).values(day=day, used=1)
                result = db.execute(statement.on_conflict_do_update(
                    index_elements=[PollBudget.day], set_={"used": PollBudget.used + 1},
                    where=PollBudget.used < limit,
                ))
                db.commit()
                return result.rowcount == 1
        except Exception as exc:
            logger.warning("Adaptive polling budget unavailable: {}", type(exc).__name__)
            return False

    def candidates(self, db, now):
        published = db.query(TrendSignalLog.id).filter(
            TrendSignalLog.match_id == Match.id,
            TrendSignalLog.signal_level == "S",
        ).exists()
        rows = db.query(Match, MatchSConfirmation).join(
            MatchSConfirmation, MatchSConfirmation.match_id == Match.id,
        ).filter(Match.provider == "sportradar", Match.status == "live",
                 MatchSConfirmation.published_signal_id.is_(None), ~published).all()
        return [(match.id, interval) for match, record in rows
                if not is_signal_excluded(match)
                and (interval := candidate_interval(record.state_json, now)) is not None]

    def run_extra(self, db, monitor):
        now = self.clock()
        candidates = self.candidates(db, datetime.now(timezone.utc))
        active = {mid for mid, _ in candidates}
        for mid in list(self.last_poll):
            if mid not in active:
                del self.last_poll[mid]
        eligible = []
        for mid, interval in candidates:
            last = self.last_poll.setdefault(mid, now)
            if now - last >= interval:
                eligible.append((interval, last, mid))
        polled = 0
        for _, _, mid in sorted(eligible)[:settings.LIVE_POLL_MAX_MATCHES_PER_TICK]:
            # Count failed attempts in scheduling too; do not hammer an unavailable upstream.
            self.last_poll[mid] = self.clock()
            try:
                with optional_poll_budget(self.reserve):
                    monitor.poll_candidate(db, mid)
                polled += 1
            except PollBudgetExceeded:
                db.rollback()
                break
            except Exception as exc:
                db.rollback()
                logger.warning("Adaptive poll failed for match {}: {}", mid, type(exc).__name__)
        return {"extra_polls": polled, "candidates": len(candidates)}

    def status(self):
        used = 0
        try:
            with self.session_factory() as db:
                day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
                row = db.get(PollBudget, day)
                used = row.used if row else 0
        except Exception:
            used = None
        return {"enabled": settings.ENABLE_ADAPTIVE_LIVE_POLLING,
                "warm_seconds": settings.LIVE_POLL_WARM_SECONDS,
                "fast_seconds": settings.LIVE_POLL_FAST_SECONDS,
                "extra_requests_today": used,
                "extra_requests_per_day": settings.LIVE_POLL_EXTRA_REQUESTS_PER_DAY,
                "max_matches_per_tick": settings.LIVE_POLL_MAX_MATCHES_PER_TICK}
