from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.db.models import BacktestResult, BacktestRun, Match, MatchPlayer
from app.services.bwf_candidate import POLICY, POLICY_RULE
from app.services.official_s import advance_confirmation, can_publish, evaluate_rallies, model_for_match
from app.services.replay_engine import ReplayEngine
from app.services.signal_admission import is_signal_excluded
from app.services.signal_engine import SignalEngine, official_rule_version
from app.services.signal_recommendation import COMPLETED_MATCH_STATUSES
from app.services.trend_predictor import TrendPredictor


class BacktestEngine:
    def run_backtest(self, db, run_name, start_date, end_date):
        start = datetime.combine(date.fromisoformat(start_date), datetime.min.time())
        end = datetime.combine(date.fromisoformat(end_date), datetime.min.time())
        if start > end:
            raise ValueError("Start date must not be after end date.")
        matches = db.scalars(select(Match).where(
            Match.match_date >= start, Match.match_date < end + timedelta(days=1),
            Match.timeline_complete.is_(True), Match.status.in_(COMPLETED_MATCH_STATUSES),
        ).order_by(Match.match_date)).all()
        replay, predictor, engine = ReplayEngine(), TrendPredictor(), SignalEngine()
        hits, qualified, evaluated, max_streak, loss_streak = 0, 0, 0, 0, 0
        first_rallies, cases = [], []
        for match in matches:
            if is_signal_excluded(match):
                continue
            model = model_for_match(db, match)
            if not model:
                continue
            players = list(db.scalars(select(MatchPlayer.player_id).where(
                MatchPlayer.match_id == match.id).order_by(MatchPlayer.side.desc())))
            if len(players) != 2:
                continue
            points = replay.point_repo.get_points_by_match(db, match.id)
            rule = official_rule_version(match)
            minimum_prior = POLICY_RULE["history"] if rule == POLICY else settings.MIN_PRIOR_MATCHES_FOR_S
            state = {}
            for point, pair, _, rows in evaluate_rallies(db, match, points, players, replay, predictor, engine, model):
                state = advance_confirmation(state, point, rows, rule=rule)
                candidates = [snap for snap in pair if can_publish(state, snap.player_id)
                              and snap.raw_snapshot_json["prior_matches"] >= minimum_prior]
                if len(candidates) != 1:
                    continue
                candidate = candidates[0]
                hit = candidate.player_id == match.winner_player_id
                hits += hit
                qualified += 1
                first_rallies.append(point.point_index)
                loss_streak = 0 if hit else loss_streak + 1
                max_streak = max(max_streak, loss_streak)
                cases.append({"match_id": match.id, "model_id": model.id, "rally": point.point_index,
                              "remaining_rallies": len(points) - point.point_index, "won": hit})
                break
            if state:
                evaluated += 1
        rate = hits / qualified if qualified else 0.0
        result = {"total": qualified, "hits": hits, "hit_rate": rate, "max_consecutive_losses": max_streak,
                  "direction_total": qualified, "direction_hits": hits, "direction_match_win_rate": rate}
        run = BacktestRun(run_name=run_name, dataset_from_date=start, dataset_to_date=end,
                          config_json={"start_date": start_date, "end_date": end_date,
                                       "scope": "point_replay_no_polling_latency"},
                          metrics_json={"total_matches": len(matches), "evaluated_matches": evaluated,
                                        "total_signals_triggered": qualified, "breakdown": {"S": result},
                                        "coverage": qualified / evaluated if evaluated else 0,
                                        "avg_trigger_rally": sum(first_rallies) / qualified if qualified else None,
                                        "cases": cases})
        db.add(run)
        db.flush()
        db.add(BacktestResult(backtest_run_id=run.id, signal_level="S", surface="all", match_stage="all",
                              sample_count=qualified, hit_count=hits, hit_rate=rate,
                              max_consecutive_losses=max_streak, avg_trigger_game_no=0, metrics_json=result))
        db.commit()
        return run

