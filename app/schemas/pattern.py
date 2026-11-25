from pydantic import BaseModel
from datetime import datetime
from typing import Dict, Any

class PlayerPhasePatternResponse(BaseModel):
    id: int
    player_id: int
    surface: str
    set_no: int
    game_range: str
    condition_json: Dict[str, Any]
    event_type: str
    sample_count: int
    event_count: int
    event_rate: float
    baseline_rate: float
    lift_value: float
    confidence_score: float
    description: str
    updated_at: datetime

    class Config:
        from_attributes = True

class PlayerSequencePatternResponse(BaseModel):
    id: int
    player_id: int
    surface: str
    pattern_type: str
    trigger_condition_json: Dict[str, Any]
    next_event_type: str
    sample_count: int
    hit_count: int
    hit_rate: float
    baseline_rate: float
    lift_value: float
    confidence_score: float
    description: str
    updated_at: datetime

    class Config:
        from_attributes = True
