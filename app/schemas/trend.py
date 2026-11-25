from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any, List

class MatchLiveSnapshotResponse(BaseModel):
    id: int
    match_id: int
    player_id: int
    opponent_id: int
    snapshot_point_index: int
    set_no: int
    game_no: int
    current_score_text: str
    is_server: bool
    player_sets: int
    opponent_sets: int
    player_games: int
    opponent_games: int
    player_points_in_game: int
    opponent_points_in_game: int
    
    total_point_win_rate: float
    service_point_win_rate: float
    return_point_win_rate: float
    first_serve_in_rate: float
    first_serve_win_rate: float
    second_serve_win_rate: float
    break_point_create_count: int
    break_point_save_rate: float
    
    recent_12_points_win_rate: float
    recent_20_points_win_rate: float
    recent_3_games_point_win_rate: float
    recent_5_games_point_win_rate: float
    recent_return_pressure_score: float
    service_game_pressure_score: float
    opponent_second_serve_attack_score: float
    
    win_profile_similarity: float
    loss_profile_similarity: float
    match_control_score: float
    momentum_score: float
    collapse_risk_score: float
    trend_confidence_score: float
    created_at: datetime

    class Config:
        from_attributes = True

class TrendModelResponse(BaseModel):
    id: int
    model_name: str
    model_type: str
    target_label: str
    feature_columns_json: Dict[str, Any]
    metrics_json: Dict[str, Any]
    model_path: str
    trained_from_date: datetime
    trained_to_date: datetime
    created_at: datetime

    class Config:
        from_attributes = True
