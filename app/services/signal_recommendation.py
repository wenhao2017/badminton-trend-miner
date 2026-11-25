from __future__ import annotations

from typing import Any, Dict, Optional

from app.db.models import Match, MatchLiveSnapshot, TrendSignalLog, TrendTrainingSample


SIGNAL_TARGETS: Dict[str, Dict[str, str]] = {
    "match": {
        "label": "整场比赛",
        "target_label": "label_win_match",
    },
}

COMPLETED_MATCH_STATUSES = frozenset({"ended", "completed", "closed"})
PREMATCH_STATUSES = frozenset({"scheduled", "not_started"})
PAUSED_MATCH_STATUSES = frozenset({"suspended", "interrupted", "delayed"})
ACTIVE_MATCH_STATUSES = PAUSED_MATCH_STATUSES | {"live"}
SPECIAL_MATCH_RESULTS = {"retired": "retired", "walkover": "walkover",
                         "cancelled": "abandoned", "canceled": "abandoned", "abandoned": "abandoned",
                         "defaulted": "walkover", "disqualified": "walkover"}
TERMINAL_MATCH_STATUSES = COMPLETED_MATCH_STATUSES | SPECIAL_MATCH_RESULTS.keys()


def provider_match_status(status_json: Dict[str, Any]) -> str:
    """Preserve explicit terminal outcomes; interruptions are resumable."""
    status = status_json.get("status", "scheduled")
    detail = status_json.get("match_status")
    reason = status_json.get("winning_reason")
    if status in TERMINAL_MATCH_STATUSES or detail in SPECIAL_MATCH_RESULTS:
        if detail in SPECIAL_MATCH_RESULTS:
            return detail
        if reason in ("retirement", "retired"):
            return "retired"
        if reason in ("walkover", "defaulted", "disqualified"):
            return "walkover"
    if status not in TERMINAL_MATCH_STATUSES:
        if detail in PAUSED_MATCH_STATUSES:
            return detail
        if detail == "pause":
            return "suspended"
        if detail == "start_delayed":
            return "delayed"
        if status == "started":
            return "live"
    return status


def signal_settlement_result(signal: TrendSignalLog, match: Optional[Match]) -> Optional[str]:
    """Resolve full-match direction from match facts, never training labels."""
    if not match or match.status not in TERMINAL_MATCH_STATUSES:
        return None
    is_hit = evaluate_match_direction_hit(signal, match)
    if is_hit is not None:
        return "success" if is_hit else "failed"
    return SPECIAL_MATCH_RESULTS.get(match.status)


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def determine_signal_recommendation(
    snapshot: MatchLiveSnapshot,
    fusion_result: Dict[str, Any],
    signal_level: str,
) -> Dict[str, Any]:
    trend_probability = float(fusion_result.get("final_trend_probability") or 0.0)
    win_probability = float(fusion_result.get("final_win_probability") or 0.0)
    risk = float(fusion_result.get("final_risk_score") or 0.0)
    match_control = float(snapshot.match_control_score or 0.0)
    momentum = float(snapshot.momentum_score or 0.0)

    horizon = "match"
    confidence = win_probability
    basis = "整场方向推荐"

    target = SIGNAL_TARGETS[horizon]
    return {
        "horizon": horizon,
        "label": target["label"],
        "target_label": target["target_label"],
        "confidence": round(_clip(confidence), 3),
        "basis": basis,
    }


def signal_recommendation_from_log(signal: TrendSignalLog) -> Dict[str, Any]:
    reason_json = signal.reason_json or {}
    recommendation = reason_json.get("recommendation") or {}
    horizon = recommendation.get("horizon")
    if horizon in SIGNAL_TARGETS:
        target = SIGNAL_TARGETS[horizon]
        return {
            "horizon": horizon,
            "label": recommendation.get("label") or target["label"],
            "target_label": recommendation.get("target_label") or target["target_label"],
            "confidence": recommendation.get("confidence"),
            "basis": recommendation.get("basis"),
        }

    target_label = (reason_json.get("evaluation") or {}).get("target_label")
    for key, target in SIGNAL_TARGETS.items():
        if target["target_label"] == target_label:
            return {
                "horizon": key,
                "label": target["label"],
                "target_label": target["target_label"],
                "confidence": recommendation.get("confidence"),
                "basis": recommendation.get("basis"),
            }

    target = SIGNAL_TARGETS["match"]
    return {
        "horizon": "match",
        "label": target["label"],
        "target_label": target["target_label"],
        "confidence": recommendation.get("confidence"),
        "basis": recommendation.get("basis") or "历史信号未记录推荐周期，按旧规则回退",
    }


def evaluate_signal_hit(signal: TrendSignalLog, sample: Optional[TrendTrainingSample]) -> Optional[bool]:
    if not sample:
        return None
    recommendation = signal_recommendation_from_log(signal)
    target_label = recommendation["target_label"]
    if not hasattr(sample, target_label):
        target_label = SIGNAL_TARGETS["match"]["target_label"]
    return bool(getattr(sample, target_label))


def evaluate_match_direction_hit(signal: TrendSignalLog, match: Optional[Match]) -> Optional[bool]:
    # Providers may name a winner for retirements/walkovers. That is not a
    # normally completed match and must not turn a void signal into a hit/miss.
    if not match or match.status in SPECIAL_MATCH_RESULTS or not match.winner_player_id:
        return None
    return match.winner_player_id == signal.player_id


def match_direction_result(signal: TrendSignalLog, match: Optional[Match]) -> str:
    is_hit = evaluate_match_direction_hit(signal, match)
    if is_hit is not None:
        return "won_match" if is_hit else "lost_match"
    if match and match.status not in TERMINAL_MATCH_STATUSES:
        return "unknown"
    if match and match.status in SPECIAL_MATCH_RESULTS:
        return SPECIAL_MATCH_RESULTS[match.status]
    if signal.final_result in {"retired", "walkover", "abandoned"}:
        return signal.final_result
    # A completed match with missing result is a data gap, not a retirement.
    if match and match.status in COMPLETED_MATCH_STATUSES:
        return "result_pending"
    return "unknown"
