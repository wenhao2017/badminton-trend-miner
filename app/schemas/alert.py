from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any

class TrendSignalLogResponse(BaseModel):
    id: int
    match_id: int
    player_id: int
    opponent_id: int
    snapshot_id: Optional[int] = None
    signal_type: str
    signal_level: str
    trend_confidence_score: float
    match_control_score: float
    momentum_score: float
    collapse_risk_score: float
    model_probability: Optional[float] = None
    pattern_match_json: Dict[str, Any]
    reason_json: Dict[str, Any]
    risk_json: Dict[str, Any]
    final_result: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class AlertLogResponse(BaseModel):
    id: int
    match_id: int
    player_id: int
    signal_log_id: int
    alert_channel: str
    alert_title: str
    alert_body: str
    cooldown_key: str
    sent_success: bool
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
