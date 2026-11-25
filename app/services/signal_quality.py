"""Trigger-time evidence only. These fields do not change signal admission."""
from sqlalchemy import case, func
from sqlalchemy.orm import Session
from app.db.models import Match, MatchPlayer, MatchPointEvent


def signal_quality_evidence(db: Session, match: Match | None, player_id: int, opponent_id: int) -> dict:
    players = {str(pid): {"completed_matches": None, "same_surface_matches": None,
                          "matches_with_points": None} for pid in (player_id, opponent_id)}
    if match and match.match_date:
        has_points = db.query(MatchPointEvent.id).filter(MatchPointEvent.match_id == Match.id).exists()
        rows = db.query(
            MatchPlayer.player_id,
            func.count(func.distinct(Match.id)),
            func.count(func.distinct(case((Match.surface == match.surface, Match.id)))),
            func.count(func.distinct(case((has_points, Match.id)))),
        ).join(Match, Match.id == MatchPlayer.match_id).filter(
            MatchPlayer.player_id.in_([player_id, opponent_id]),
            Match.id != match.id, Match.match_date < match.match_date,
            Match.status.in_(["ended", "completed", "closed"]),
        ).group_by(MatchPlayer.player_id).all()
        # No rows means zero history, not missing data.
        for pid in players:
            players[pid] = {"completed_matches": 0, "same_surface_matches": 0, "matches_with_points": 0}
        for pid, matches, surface, points in rows:
            players[str(pid)] = {"completed_matches": matches, "same_surface_matches": surface,
                                 "matches_with_points": points}
    return {"version": 1, "mode": "observe_only",
            "competition_stage": match.competition_stage if match else None,
            "round": match.round if match else None,
            "history_before_match_date": match.match_date.isoformat() if match and match.match_date else None,
            "player": players[str(player_id)], "opponent": players[str(opponent_id)]}
