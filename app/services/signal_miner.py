from sqlalchemy.orm import Session
from app.db.models import TrendSignalLog, Match, TrendTrainingSample, MatchLiveSnapshot
from app.services.signal_recommendation import (
    evaluate_match_direction_hit,
    evaluate_signal_hit,
    signal_recommendation_from_log,
)
from loguru import logger
from typing import Dict, Any

class SignalMiner:
    def mine_signals_performance(self, db: Session) -> Dict[str, Any]:
        """
        Scans DB signal logs, links them to training samples/outcomes,
        and aggregates hit rate metrics per level and surface.
        """
        signals = db.query(TrendSignalLog).all()
        if not signals:
            logger.warning("No signal logs found to mine.")
            return {"message": "No data"}

        summary = {}
        for sig in signals:
            # Try to find corresponding training sample to get actual label values
            match = db.query(Match).filter(Match.id == sig.match_id).first()
            if not match:
                continue

            match_direction_hit = evaluate_match_direction_hit(sig, match)
            
            # Find set winner (check if set was won by player)
            # Find training sample matching snapshot id
            sample = db.query(TrendTrainingSample).filter(
                TrendTrainingSample.match_id == sig.match_id,
                TrendTrainingSample.player_id == sig.player_id,
                TrendTrainingSample.snapshot_point_index == db.query(MatchLiveSnapshot.snapshot_point_index).filter(MatchLiveSnapshot.id == sig.snapshot_id).scalar_subquery()
            ).first()

            recommended_hit = evaluate_signal_hit(sig, sample)
            recommendation = signal_recommendation_from_log(sig)
            horizon = recommendation["horizon"]
            
            level = sig.signal_level
            surface = match.surface
            
            if level not in summary:
                summary[level] = {
                    "total": 0,
                    "match_won": 0,
                    "match_direction_total": 0,
                    "match_direction_won": 0,
                    "recommended_total": 0,
                    "recommended_hit": 0,
                    "by_horizon": {},
                }
            
            summary[level]["total"] += 1
            if match_direction_hit is not None:
                summary[level]["match_direction_total"] += 1
                summary[level]["match_direction_won"] += int(match_direction_hit)
            if match_direction_hit:
                summary[level]["match_won"] += 1
            if recommended_hit is not None:
                summary[level]["recommended_total"] += 1
                summary[level]["recommended_hit"] += int(recommended_hit)
                by_horizon = summary[level]["by_horizon"].setdefault(horizon, {"total": 0, "hits": 0})
                by_horizon["total"] += 1
                by_horizon["hits"] += int(recommended_hit)

        # Format results
        results = {}
        for lvl, counts in summary.items():
            results[lvl] = {
                "count": counts["total"],
                "match_win_rate": float(counts["match_won"]) / counts["total"],
                "match_direction_win_rate": (
                    float(counts["match_direction_won"]) / counts["match_direction_total"]
                    if counts["match_direction_total"]
                    else 0.0
                ),
                "match_direction_sample_count": counts["match_direction_total"],
                "recommended_hit_rate": (
                    float(counts["recommended_hit"]) / counts["recommended_total"]
                    if counts["recommended_total"]
                    else 0.0
                ),
                "recommended_sample_count": counts["recommended_total"],
                "by_horizon": {
                    horizon: {
                        "count": horizon_counts["total"],
                        "hit_rate": (
                            float(horizon_counts["hits"]) / horizon_counts["total"]
                            if horizon_counts["total"]
                            else 0.0
                        ),
                    }
                    for horizon, horizon_counts in counts["by_horizon"].items()
                },
            }

        logger.info(f"Signal mining complete: {results}")
        return results
