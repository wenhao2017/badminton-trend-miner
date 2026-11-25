from app.config import settings


def determine_trend_signal_level(confidence_score: float, collapse_risk_score: float) -> str:
    """Classifies the short-term trend tier."""
    if confidence_score >= settings.S_LEVEL_THRESHOLD and collapse_risk_score <= 0.20:
        return "S"
    elif confidence_score >= settings.A_LEVEL_THRESHOLD and collapse_risk_score <= settings.MAX_RISK_FOR_ALERT:
        return "A"
    elif confidence_score >= settings.B_LEVEL_THRESHOLD:
        if collapse_risk_score > settings.MAX_RISK_FOR_ALERT:
            return "Risk"
        return "B"
    elif confidence_score >= settings.C_LEVEL_THRESHOLD:
        return "C"
        
    return "NONE"


def determine_match_signal_level(win_probability: float) -> str:
    """Classifies the primary full-match direction tier."""
    if win_probability >= settings.MATCH_S_LEVEL_THRESHOLD:
        return "S"
    if win_probability >= settings.MATCH_A_LEVEL_THRESHOLD:
        return "A"
    if win_probability >= settings.MATCH_B_LEVEL_THRESHOLD:
        return "B"
    if win_probability >= settings.MATCH_C_LEVEL_THRESHOLD:
        return "C"
    return "NONE"


def determine_signal_level(confidence_score: float, collapse_risk_score: float) -> str:
    """Backward-compatible alias for trend signal classification."""
    return determine_trend_signal_level(confidence_score, collapse_risk_score)
