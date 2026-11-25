from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List
from app.schemas.player import PlayerResponse

class MatchBase(BaseModel):
    external_match_id: str
    tournament: str
    tournament_id: str
    tournament_level: Optional[str] = None
    match_date: datetime
    surface: str
    indoor: bool
    round: Optional[str] = None
    best_of: int

class MatchCreate(MatchBase):
    provider: str

class MatchResponse(MatchBase):
    id: int
    provider: str
    winner_player_id: Optional[int] = None
    loser_player_id: Optional[int] = None
    final_score: Optional[str] = None
    status: str

    class Config:
        from_attributes = True

class MatchPlayerResponse(BaseModel):
    match_id: int
    player_id: int
    opponent_id: int
    side: str
    seed: Optional[int] = None
    ranking: Optional[int] = None
    result: Optional[str] = None

    class Config:
        from_attributes = True
