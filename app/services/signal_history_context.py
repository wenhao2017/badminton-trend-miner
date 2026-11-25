"""Pre-match inactivity evidence shared by dashboard and notifications."""
from datetime import timedelta, timezone
from sqlalchemy import and_, or_
from app.db.models import Match, MatchPlayer, MatchPointEvent


def player_inactivity_warning(db, player_id, match):
    if not match or not match.match_date:
        return None
    played_points = db.query(MatchPointEvent.id).filter(MatchPointEvent.match_id == Match.id).exists()
    previous = db.query(Match).join(MatchPlayer, MatchPlayer.match_id == Match.id).filter(
        MatchPlayer.player_id == player_id,
        Match.id != match.id,
        Match.match_date < match.match_date,
        Match.status.notin_(["scheduled", "not_started", "cancelled", "walkover"]),
        or_(MatchPlayer.result.is_(None), MatchPlayer.result != "walkover"),
        or_(played_points, and_(
            Match.status.in_(["ended", "closed", "completed", "retired"]),
            Match.final_score.isnot(None), Match.final_score.notin_(["", "0-0", "0-0, 0-0"]),
        )),
    ).order_by(Match.match_date.desc(), Match.id.desc()).first()
    if not previous:
        return None
    gap = match.match_date - previous.match_date
    if gap <= timedelta(weeks=3):
        return None
    return {"days": round(gap.total_seconds() / 86400), "last_match_date": previous.match_date.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
            "label": "超3周无比赛记录"}
