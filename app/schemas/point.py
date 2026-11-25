from pydantic import BaseModel
from typing import Optional, Dict, Any

class MatchPointEventBase(BaseModel):
    point_index: int
    set_no: int
    game_no: int
    point_no_in_game: int
    server_player_id: int
    receiver_player_id: int
    point_winner_player_id: int
    point_loser_player_id: int
    server_score_before: str
    receiver_score_before: str
    server_games_before: int
    receiver_games_before: int
    server_sets_before: int
    receiver_sets_before: int
    is_ace: bool
    is_double_fault: bool
    is_first_serve_in: bool
    is_first_serve_point_won: bool
    is_second_serve_point_won: bool
    is_break_point: bool
    is_game_point: bool
    is_set_point: bool
    is_match_point: bool
    is_tiebreak: bool

class MatchPointEventCreate(MatchPointEventBase):
    match_id: int
    external_event_id: Optional[str] = None
    raw_event_json: Dict[str, Any]

class MatchPointEventResponse(MatchPointEventBase):
    id: int
    match_id: int
    external_event_id: Optional[str] = None

    class Config:
        from_attributes = True
