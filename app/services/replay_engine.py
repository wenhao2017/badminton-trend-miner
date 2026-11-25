from itertools import takewhile

from sqlalchemy import select

from app.config import settings
from app.db.models import Match, MatchLiveSnapshot, MatchPlayer, MatchPlayerStat, TrendSignalLog
from app.db.repositories import MatchPointEventRepository
from app.services.bwf_candidate import CandidateProfiles, profile_gaps
from app.services.signal_recommendation import COMPLETED_MATCH_STATUSES


class ReplayEngine:
    def __init__(self):
        self.point_repo = MatchPointEventRepository()
        self._history_match_id = None
        self._history_cache = {}
        self.candidate_history = CandidateProfiles()

    def _prior(self, db, match, player_id):
        if self._history_match_id != match.id:
            self._history_cache.clear()
            self._history_match_id = match.id
        if player_id not in self._history_cache:
            rows = db.execute(
                select(Match.winner_player_id, Match.last_rally_at,
                       MatchPlayerStat.total_points_won, MatchPlayerStat.total_points)
                .join(MatchPlayer, MatchPlayer.match_id == Match.id)
                .outerjoin(MatchPlayerStat, (MatchPlayerStat.match_id == Match.id)
                           & (MatchPlayerStat.player_id == MatchPlayer.player_id))
                .where(MatchPlayer.player_id == player_id, Match.status.in_(COMPLETED_MATCH_STATUSES),
                       Match.winner_player_id.is_not(None), Match.last_rally_at < match.match_date,
                       Match.timeline_complete.is_(True), Match.scoring_target == match.scoring_target)
                .order_by(Match.match_date.desc()).limit(40)
            ).all()
            wins = sum(winner == player_id for winner, *_ in rows)
            won = sum(row.total_points_won or 0 for row in rows)
            total = sum(row.total_points or 0 for row in rows)
            self._history_cache[player_id] = ((wins + 3) / (len(rows) + 6),
                                               (won + 20) / (total + 40), len(rows))
        return self._history_cache[player_id]

    def generate_snapshot_for_point(self, db, match_id, current_point_idx, points, player_id, opponent_id,
                                    *, candidate_profiles=None, **_):
        match = db.get(Match, match_id)
        prefix = list(takewhile(lambda p: p.point_index <= current_point_idx, points))
        if not prefix or prefix[-1].point_index != current_point_idx:
            raise ValueError("Snapshot requires a continuous rally prefix")
        point = prefix[-1]
        raw = point.raw_event_json
        own = 0 if raw["home_player_id"] == player_id else 1
        rival = 1 - own
        after = [raw["home_score"], raw["away_score"]]
        games = raw["games_after"]
        wins = [p.point_winner_player_id == player_id for p in prefix]

        def rate(window):
            return sum(window) / len(window) if window else 0.5

        if candidate_profiles is None:
            prior_win, prior_rally, prior_count = self._prior(db, match, player_id)
            opponent_prior, _, opponent_count = self._prior(db, match, opponent_id)
        else:
            profile, opponent = candidate_profiles[player_id], candidate_profiles[opponent_id]
            prior_win, prior_rally, prior_count = profile["win"], profile["rally"], profile["matches"]
            opponent_prior, opponent_count = opponent["win"], opponent["matches"]
        short = rate(wins[-8:])
        long = rate(wins[-16:])
        recent20 = rate(wins[-20:])
        streak = 0
        for won in reversed(wins):
            if won != wins[-1]:
                break
            streak += 1
        target = match.scoring_target
        gap = (after[own] - after[rival]) / target
        game_lead = games[own] - games[rival]
        pressure = [p.point_winner_player_id == player_id for p in prefix if p.is_game_point]
        features = {
            "scoring_target": target,
            "game_no": point.game_no,
            "games_lead": game_lead,
            "game_score_fraction": max(after) / target,
            "point_margin_fraction": gap,
            "is_deciding_game": point.game_no == match.best_of,
            "prior_match_win_rate": prior_win,
            "opponent_prior_match_win_rate": opponent_prior,
            "prior_rally_win_rate": prior_rally,
            "total_rally_win_rate": rate(wins),
            "recent_8_rallies_win_rate": short,
            "recent_16_rallies_win_rate": long,
            "recent_rally_deviation": long - prior_rally,
            "signed_streak": streak if wins[-1] else -streak,
            "pressure_rally_win_rate": (sum(pressure) + 2) / (len(pressure) + 4),
        }
        if candidate_profiles is not None:
            features.update(profile_gaps(profile, opponent), recent20=recent20)
        control = min(0.98, max(0.02, 0.5 + 0.22 * game_lead + 0.30 * gap))
        serving = [p.point_winner_player_id == player_id for p in prefix
                   if p.raw_event_json.get("serve_known") and p.server_player_id == player_id]
        receiving = [p.point_winner_player_id == player_id for p in prefix
                     if p.raw_event_json.get("serve_known") and p.receiver_player_id == player_id]
        return MatchLiveSnapshot(
            match_id=match_id, player_id=player_id, opponent_id=opponent_id,
            snapshot_point_index=current_point_idx, set_no=point.game_no, game_no=point.game_no,
            current_score_text=f"{games[own]}-{games[rival]} · {after[own]}-{after[rival]}",
            player_sets=games[own], opponent_sets=games[rival],
            player_games=games[own], opponent_games=games[rival],
            player_points_in_game=after[own], opponent_points_in_game=after[rival],
            is_server=bool(raw.get("serve_known") and point.server_player_id == player_id),
            total_point_win_rate=rate(wins), service_point_win_rate=rate(serving),
            return_point_win_rate=rate(receiving), recent_12_points_win_rate=rate(wins[-12:]),
            recent_20_points_win_rate=recent20, match_control_score=control,
            momentum_score=short, collapse_risk_score=1 - long, trend_confidence_score=control,
            raw_snapshot_json={
                "features": features, "prior_matches": prior_count, "opponent_prior_matches": opponent_count,
                "match_stage": "early" if max(after) < target / 3 else "late" if max(after) >= target * 2 / 3 else "mid",
                "serve_known": raw["serve_known"], "source_time": raw.get("time"),
                "game_ended": raw["game_ended"], "match_ended": max(games) >= match.best_of // 2 + 1,
                "score_before": {"game_no": point.game_no, "set_no": point.game_no,
                                 "games": [raw["games_before"][own], raw["games_before"][rival]],
                                 "points": [str(raw[f"{side}_score_before"]) for side in
                                            (("home", "away") if own == 0 else ("away", "home"))]},
            },
        )

    def replay_match(self, db, match_id, on_snapshot=None):
        match = db.get(Match, match_id)
        if not match or not match.timeline_complete or match.status not in COMPLETED_MATCH_STATUSES:
            return 0
        players = list(db.scalars(select(MatchPlayer.player_id).where(MatchPlayer.match_id == match_id)))
        if len(players) != 2:
            return 0
        points = self.point_repo.get_points_by_match(db, match_id)
        protected = select(TrendSignalLog.snapshot_id).where(
            TrendSignalLog.match_id == match_id, TrendSignalLog.snapshot_id.is_not(None))
        db.query(MatchLiveSnapshot).filter(MatchLiveSnapshot.match_id == match_id,
                                          MatchLiveSnapshot.id.not_in(protected)).delete(synchronize_session=False)
        count = 0
        for point in points[settings.MIN_POINTS_FOR_SIGNAL - 1:]:
            for player, opponent in (players, players[::-1]):
                snapshot = self.generate_snapshot_for_point(db, match_id, point.point_index, points, player, opponent)
                if snapshot.raw_snapshot_json["match_ended"]:
                    continue
                db.add(snapshot)
                if on_snapshot:
                    on_snapshot(snapshot)
                count += 1
        db.commit()
        return count

