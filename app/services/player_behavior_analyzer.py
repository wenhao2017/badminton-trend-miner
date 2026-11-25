from collections import Counter

from sqlalchemy import select

from app.db.models import Match, MatchPlayer, MatchPointEvent


class PlayerBehaviorAnalyzer:
    def analyze(self, db, player_id):
        matches = list(db.execute(select(Match.id, Match.winner_player_id, Match.scoring_target)
                                  .join(MatchPlayer, MatchPlayer.match_id == Match.id)
                                  .where(MatchPlayer.player_id == player_id, Match.timeline_complete.is_(True))
                                  .order_by(Match.match_date.desc()).limit(100)))
        counts = Counter()
        phases = {}
        for match_id, winner, target in matches:
            counts["matches"] += 1
            counts["match_wins"] += winner == player_id
            for point in db.scalars(select(MatchPointEvent).where(MatchPointEvent.match_id == match_id)):
                raw = point.raw_event_json
                won = point.point_winner_player_id == player_id
                counts["rallies"] += 1
                counts["won_rallies"] += won
                if point.is_game_point:
                    counts["pressure_rallies"] += 1
                    counts["pressure_wins"] += won
                start = max(raw["home_score_before"], raw["away_score_before"])
                phase = "开局" if start < target / 3 else "末段" if start >= target * 2 / 3 else "中段"
                bucket = phases.setdefault((target, phase), Counter())
                bucket["rallies"] += 1
                bucket["wins"] += won
        def rate(numerator, denominator):
            return counts[numerator] / counts[denominator] if counts[denominator] else None

        return {"status": "available" if matches else "insufficient_sample",
                "summary": {**counts, "match_win_rate": rate("match_wins", "matches"),
                            "rally_win_rate": rate("won_rallies", "rallies"),
                            "pressure_win_rate": rate("pressure_wins", "pressure_rallies")},
                "phase_rows": [{"scoring_target": target, "phase": phase, **count,
                                "win_rate": count["wins"] / count["rallies"]}
                               for (target, phase), count in sorted(phases.items())]}

