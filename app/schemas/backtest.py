from pydantic import BaseModel
from datetime import datetime
from typing import Dict, Any, List

class BacktestRunResponse(BaseModel):
    id: int
    run_name: str
    dataset_from_date: datetime
    dataset_to_date: datetime
    config_json: Dict[str, Any]
    metrics_json: Dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True

class BacktestResultResponse(BaseModel):
    id: int
    backtest_run_id: int
    signal_level: str
    surface: str
    match_stage: str
    sample_count: int
    hit_count: int
    hit_rate: float
    max_consecutive_losses: int
    avg_trigger_game_no: float
    metrics_json: Dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True
