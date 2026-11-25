from pydantic import BaseModel
from typing import Optional

class PlayerBase(BaseModel):
    external_player_id: str
    name: str
    country: Optional[str] = None
    hand: Optional[str] = None
    birth_date: Optional[str] = None

class PlayerCreate(PlayerBase):
    provider: str

class PlayerResponse(PlayerBase):
    id: int
    provider: str

    class Config:
        from_attributes = True

class PlayerProfileResponse(BaseModel):
    player_id: int
    surface: str
    sample_matches: int
    win_avg_first_serve_in_rate: float
    win_avg_first_serve_win_rate: float
    win_avg_second_serve_win_rate: float
    win_avg_service_points_win_rate: float
    win_avg_return_points_win_rate: float
    win_avg_break_point_convert_rate: float
    win_avg_break_point_save_rate: float
    win_avg_total_points_win_rate: float
    
    loss_avg_first_serve_in_rate: float
    loss_avg_first_serve_win_rate: float
    loss_avg_second_serve_win_rate: float
    loss_avg_service_points_win_rate: float
    loss_avg_return_points_win_rate: float
    loss_avg_break_point_convert_rate: float
    loss_avg_break_point_save_rate: float
    loss_avg_total_points_win_rate: float

    class Config:
        from_attributes = True
