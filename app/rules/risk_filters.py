from app.config import settings

def is_risk_suppressed(set_no: int, game_no: int, total_points: int) -> bool:
    """
    Evaluates whether the alert should be suppressed due to early-game risk filters.
    """
    if total_points < settings.MIN_POINTS_FOR_SIGNAL:
        return True
    
    if set_no == 1 and game_no <= settings.MIN_GAMES_FOR_SIGNAL:
        return True
        
    return False

def evaluate_collapse_risk(risk_score: float) -> bool:
    """Returns True if the collapse risk score exceeds configured thresholds."""
    return risk_score > settings.MAX_RISK_FOR_ALERT
