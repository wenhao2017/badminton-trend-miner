from collections import defaultdict

from sqlalchemy import select

from app.config import settings
from app.db.models import Match, MatchLiveSnapshot, PlayerPhasePattern, PlayerSequencePattern


class PlayerPatternMiner:
    def mine_patterns(self, db):
        observations = defaultdict(dict)
        baseline = defaultdict(dict)
        query = select(MatchLiveSnapshot, Match.winner_player_id, Match.scoring_target).join(
            Match, Match.id == MatchLiveSnapshot.match_id).where(Match.timeline_complete.is_(True)).order_by(
                MatchLiveSnapshot.snapshot_point_index)
        for snapshot, winner, target in db.execute(query).yield_per(500):
            raw = snapshot.raw_snapshot_json
            if raw.get("game_ended") or not raw.get("features"):
                continue
            player, game = snapshot.player_id, snapshot.game_no
            won = winner == player
            baseline[player, target][snapshot.match_id] = won
            phase = raw["match_stage"]
            gap = snapshot.player_points_in_game - snapshot.opponent_points_in_game
            direction = "leading" if gap >= 3 else "trailing" if gap <= -3 else "close"
            observations[player, target, game, phase, direction][snapshot.match_id] = won

        db.query(PlayerPhasePattern).delete()
        db.query(PlayerSequencePattern).delete()
        count = 0
        for (player, target, game, phase, direction), samples in observations.items():
            if len(samples) < settings.PATTERN_MIN_SAMPLE_COUNT:
                continue
            reference = baseline[player, target]
            wins = sum(samples.values())
            rate, base = wins / len(samples), sum(reference.values()) / len(reference)
            if abs(rate - base) < settings.PATTERN_MIN_LIFT:
                continue
            db.add(PlayerPhasePattern(
                player_id=player, surface=f"{target}-point", set_no=game, game_range=phase,
                condition_json={"score_state": direction, "phase": phase}, event_type="match_win",
                sample_count=len(samples), event_count=wins, event_rate=rate, baseline_rate=base,
                lift_value=rate - base, confidence_score=len(samples) / (len(samples) + 10),
                description=f"第 {game} 局 {phase} / {direction}：{wins}/{len(samples)} 场赢下整场"))
            count += 1
        db.commit()
        return count

