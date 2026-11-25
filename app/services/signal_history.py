"""Compare a frozen signal against prior matches with similar rally states."""
from math import sqrt

from sqlalchemy import select

from app.db.models import Match, MatchLiveSnapshot, MatchPlayer, Player

MIN_MATCHES = 10


def build_signal_history(db, signal):
    match = db.get(Match, signal.match_id)
    snapshot = db.get(MatchLiveSnapshot, signal.snapshot_id) if signal.snapshot_id else None
    result = {"version": 2, "minimum_matches": MIN_MATCHES, "status": "unavailable", "reason": None,
              "cutoff": match.match_date.isoformat() + "Z" if match else None, "current": None,
              "candidate_matches": 0, "validated_matches": 0, "matched_matches": 0,
              "same_surface_matches": 0, "excluded": {}, "summary": None, "matches": []}
    if not match or not snapshot or not (snapshot.raw_snapshot_json or {}).get("features"):
        result["reason"] = "missing_signal_context"
        return result

    def view(s):
        games = [s.player_sets, s.opponent_sets]
        points = [s.player_points_in_game, s.opponent_points_in_game]
        return {"sets": games, "games": games, "points": points, "point": s.snapshot_point_index,
                "recent20": s.recent_20_points_win_rate,
                "leading": (games[0], points[0]) > (games[1], points[1]), "is_server": s.is_server}

    result["current"] = view(snapshot)
    eligible = list(db.scalars(select(Match).join(MatchPlayer, MatchPlayer.match_id == Match.id).where(
        MatchPlayer.player_id == signal.player_id, Match.last_rally_at < match.match_date,
        Match.timeline_complete.is_(True), Match.scoring_target == match.scoring_target,
    ).order_by(Match.match_date.desc()).limit(100)))
    cases = []
    for old in eligible:
        candidates = []
        for state in db.scalars(select(MatchLiveSnapshot).where(
            MatchLiveSnapshot.match_id == old.id, MatchLiveSnapshot.player_id == signal.player_id,
            MatchLiveSnapshot.game_no == snapshot.game_no, MatchLiveSnapshot.player_sets == snapshot.player_sets,
            MatchLiveSnapshot.opponent_sets == snapshot.opponent_sets
        )):
            if state.raw_snapshot_json.get("game_ended"):
                continue
            distance = (abs(state.player_points_in_game - snapshot.player_points_in_game)
                        + abs(state.opponent_points_in_game - snapshot.opponent_points_in_game))
            rate = abs(state.recent_20_points_win_rate - snapshot.recent_20_points_win_rate)
            if distance <= 4 and rate <= 0.15:
                candidates.append((distance, rate, state.snapshot_point_index, state))
        if candidates:
            anchor = min(candidates, key=lambda item: item[:3])[3]
            opponent = db.get(Player, anchor.opponent_id)
            cases.append({"match_id": old.id, "tournament": old.tournament, "surface": old.surface,
                          "date": old.match_date.isoformat() + "Z", "opponent": opponent.name if opponent else "",
                          "won": old.winner_player_id == signal.player_id, "anchor": view(anchor),
                          "caught_up": None, "same_surface": True, "final_score": old.final_score})
    count, wins = len(cases), sum(case["won"] for case in cases)
    rate = wins / count if count else None
    interval = None
    if count >= MIN_MATCHES:
        z = 1.95996398454
        denominator = 1 + z*z/count
        center = (rate + z*z/(2*count)) / denominator
        margin = z*sqrt(rate*(1-rate)/count + z*z/(4*count*count)) / denominator
        interval = [max(0, center-margin), min(1, center+margin)]
    result.update(status="available" if count >= MIN_MATCHES else "insufficient_sample",
                  candidate_matches=len(eligible), validated_matches=len(eligible), matched_matches=count,
                  same_surface_matches=count, matches=cases,
                  summary={"wins": wins, "losses": count-wins, "win_rate": rate if interval else None,
                           "win_rate_interval": interval})
    return result

