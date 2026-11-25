from sqlalchemy import delete, insert, select

from app.db.models import Match, MatchLiveSnapshot, MatchPointEvent, TrendSignalLog, TrendTrainingSample
from app.services.feature_engineering import FeatureExtractor
from app.services.signal_recommendation import COMPLETED_MATCH_STATUSES


class TrendLabeler:
    def generate_labels(self, db, match_id):
        match = db.get(Match, match_id)
        if (not match or not match.timeline_complete or not match.winner_player_id
                or match.status not in COMPLETED_MATCH_STATUSES):
            return 0
        protected = select(TrendSignalLog.snapshot_id).where(
            TrendSignalLog.match_id == match_id, TrendSignalLog.snapshot_id.is_not(None))
        snapshots = db.scalars(select(MatchLiveSnapshot).where(
            MatchLiveSnapshot.match_id == match_id,
            MatchLiveSnapshot.id.not_in(protected)))
        points = db.scalars(select(MatchPointEvent).where(MatchPointEvent.match_id == match_id))
        game_winners = {point.game_no: point.point_winner_player_id for point in points
                        if point.raw_event_json.get("game_ended")}
        extractor = FeatureExtractor()
        rows = [
            dict(match_id=match_id, player_id=s.player_id, opponent_id=s.opponent_id,
                 snapshot_point_index=s.snapshot_point_index, surface=match.surface, set_no=s.set_no,
                 game_no=s.game_no, feature_json=extractor.extract_features(s),
                 label_win_current_set=game_winners.get(s.game_no) == s.player_id,
                 label_win_match=match.winner_player_id == s.player_id)
            for s in snapshots if not s.raw_snapshot_json.get("match_ended")
        ]
        db.execute(delete(TrendTrainingSample).where(TrendTrainingSample.match_id == match_id))
        if rows:
            db.execute(insert(TrendTrainingSample), rows)
        db.commit()
        return len(rows)

