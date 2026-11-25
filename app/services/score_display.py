from app.db.models import MatchPointEvent


def latest_match_score(db, match_id, first_player_id):
    point = db.query(MatchPointEvent).filter_by(match_id=match_id).order_by(
        MatchPointEvent.point_index.desc()).first()
    if not point:
        return None
    raw = point.raw_event_json
    home_first = first_player_id == raw.get("home_player_id")
    order = ("home", "away") if home_first else ("away", "home")
    games = raw["games_after"] if home_first else raw["games_after"][::-1]
    return {"set_no": point.game_no, "game_no": point.game_no, "sets": games, "games": games,
            "points": [str(raw[f"{side}_score"]) for side in order],
            "set_ended": raw["game_ended"], "point_index": point.point_index}


def snapshot_score_before(db, snapshot):
    return (snapshot.raw_snapshot_json or {}).get("score_before")

