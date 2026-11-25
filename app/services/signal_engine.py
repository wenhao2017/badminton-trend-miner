from app.config import settings
from app.db.models import Match, TrendSignalLog
from app.rules.signal_levels import determine_match_signal_level
from app.services.bwf_candidate import POLICY, POLICY_RULE
from app.services.signal_admission import is_signal_excluded
from app.services.signal_recommendation import determine_signal_recommendation


LEGACY_RULE = "badminton-rally-consecutive3-v1"


def official_rule_version(match):
    if match.scoring_target == 21 and settings.OFFICIAL_S_21_POLICY == POLICY:
        return POLICY
    return LEGACY_RULE


def signal_entry_allowed(game_no, total_points, *, rule=None):
    return total_points >= (POLICY_RULE["start"] if rule == POLICY else settings.MIN_POINTS_FOR_SIGNAL)


class SignalEngine:
    def evaluate_snapshot(self, db, snapshot, model_win_prob=None, *, rule=None):
        """Grade a complete prefix with model strength and current score."""
        probability = (snapshot.match_control_score if model_win_prob is None else float(model_win_prob))
        trend = 0.80 * probability + 0.20 * snapshot.recent_20_points_win_rate
        risk = 1 - probability
        level = determine_match_signal_level(probability)
        if rule == POLICY:
            trend = probability
            level = ("S" if probability >= POLICY_RULE["threshold"] else "A" if probability >= .70
                     else "B" if probability >= .60 else "C" if probability >= .55 else "NONE")
        fusion = {"final_win_probability": probability, "final_trend_probability": trend,
                  "final_risk_score": risk, "model_win_probability": model_win_prob}
        return {"level": level, "match_level": level, "trend_level": level,
                "fusion": fusion, "behavior": {}, "context": {}, "patterns": []}

    def process_snapshot(self, db, snapshot, *, evaluation, official_confirmation):
        match = db.get(Match, snapshot.match_id)
        if (not settings.OFFICIAL_S_ENABLED or not match or match.status != "live"
                or not match.timeline_ready or is_signal_excluded(match)):
            return False, None
        raw = snapshot.raw_snapshot_json
        candidate = official_confirmation["version"] == POLICY
        minimum_history = POLICY_RULE["history"] if candidate else settings.MIN_PRIOR_MATCHES_FOR_S
        if raw["prior_matches"] < minimum_history:
            return False, None
        fusion = evaluation["fusion"]
        threshold = .70 if candidate else settings.S_CONFIRMATION_TREND_THRESHOLD
        if evaluation["level"] != "S" or fusion["final_trend_probability"] < threshold:
            return False, None
        signal = TrendSignalLog(
            match_id=match.id, official_match_id=match.id, player_id=snapshot.player_id,
            opponent_id=snapshot.opponent_id, snapshot_id=snapshot.id,
            signal_type="match_direction", signal_level="S",
            trend_confidence_score=fusion["final_trend_probability"],
            match_control_score=snapshot.match_control_score, momentum_score=snapshot.momentum_score,
            collapse_risk_score=fusion["final_risk_score"], model_probability=fusion["model_win_probability"],
            pattern_match_json={},
            reason_json={"official_s_confirmation": official_confirmation,
                         "recommendation": determine_signal_recommendation(snapshot, fusion, "S"),
                         "reasons": [f"模型整场胜率和连续{'两个' if candidate else '三个'}逐分状态达到确认阈值"],
                         "evaluation": {"target_label": "label_win_match"}},
            risk_json={"final_risk_score": fusion["final_risk_score"], "risks": []},
        )
        db.add(signal)
        db.flush()
        return True, signal

