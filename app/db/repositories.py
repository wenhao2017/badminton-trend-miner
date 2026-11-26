from typing import List, Optional, Type, TypeVar, Generic
from sqlalchemy import select, update, delete
from sqlalchemy.orm import Session
from app.db.base import Base
from app.db.models import (
    ApiCallLog, ApiKeyUsage, Player, Match, MatchPlayer, MatchPointEvent,
    MatchPlayerStat, MatchLiveSnapshot, PlayerProfile, PlayerMetricDistribution,
    TrendTrainingSample, TrendModel, TrendSignalLog, AlertLog, BacktestRun,
    BacktestResult, PlayerPhasePattern, PlayerSequencePattern, ProcessedDate
)

T = TypeVar("T", bound=Base)

class BaseRepository(Generic[T]):
    def __init__(self, model: Type[T]):
        self.model = model

    def get(self, db: Session, id: int) -> Optional[T]:
        return db.get(self.model, id)

    def get_all(self, db: Session, skip: int = 0, limit: int = 100) -> List[T]:
        stmt = select(self.model).offset(skip).limit(limit)
        return list(db.scalars(stmt).all())

    def create(self, db: Session, obj: T) -> T:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def update(self, db: Session, obj: T) -> T:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def delete(self, db: Session, id: int) -> bool:
        obj = self.get(db, id)
        if obj:
            db.delete(obj)
            db.commit()
            return True
        return False

class PlayerRepository(BaseRepository[Player]):
    def __init__(self):
        super().__init__(Player)

    def get_by_external_id(self, db: Session, ext_id: str) -> Optional[Player]:
        stmt = select(Player).where(Player.external_player_id == ext_id)
        return db.scalars(stmt).first()

class MatchRepository(BaseRepository[Match]):
    def __init__(self):
        super().__init__(Match)

    def get_by_external_id(self, db: Session, ext_id: str) -> Optional[Match]:
        stmt = select(Match).where(Match.external_match_id == ext_id)
        return db.scalars(stmt).first()

    def get_live_matches(self, db: Session) -> List[Match]:
        stmt = select(Match).where(Match.status.in_(["live", "suspended", "interrupted", "delayed"]))
        return list(db.scalars(stmt).all())

class MatchPlayerRepository(BaseRepository[MatchPlayer]):
    def __init__(self):
        super().__init__(MatchPlayer)

    def get_by_match_and_player(self, db: Session, match_id: int, player_id: int) -> Optional[MatchPlayer]:
        stmt = select(MatchPlayer).where(
            MatchPlayer.match_id == match_id,
            MatchPlayer.player_id == player_id
        )
        return db.scalars(stmt).first()

class MatchPointEventRepository(BaseRepository[MatchPointEvent]):
    def __init__(self):
        super().__init__(MatchPointEvent)

    def get_points_by_match(self, db: Session, match_id: int) -> List[MatchPointEvent]:
        stmt = select(MatchPointEvent).where(MatchPointEvent.match_id == match_id).order_by(MatchPointEvent.point_index.asc())
        return list(db.scalars(stmt).all())

class MatchPlayerStatRepository(BaseRepository[MatchPlayerStat]):
    def __init__(self):
        super().__init__(MatchPlayerStat)

    def get_by_match_and_player(self, db: Session, match_id: int, player_id: int) -> Optional[MatchPlayerStat]:
        stmt = select(MatchPlayerStat).where(
            MatchPlayerStat.match_id == match_id,
            MatchPlayerStat.player_id == player_id
        )
        return db.scalars(stmt).first()

class ApiKeyUsageRepository(BaseRepository[ApiKeyUsage]):
    def __init__(self):
        super().__init__(ApiKeyUsage)

    def get_by_key_hash(self, db: Session, key_hash: str, *, refresh: bool = False) -> Optional[ApiKeyUsage]:
        stmt = select(ApiKeyUsage).where(ApiKeyUsage.api_key_hash == key_hash)
        if refresh:
            stmt = stmt.execution_options(populate_existing=True)
        return db.scalars(stmt).first()

class PlayerProfileRepository(BaseRepository[PlayerProfile]):
    def __init__(self):
        super().__init__(PlayerProfile)

    def get_by_player_and_surface(self, db: Session, player_id: int, surface: str) -> Optional[PlayerProfile]:
        stmt = select(PlayerProfile).where(
            PlayerProfile.player_id == player_id,
            PlayerProfile.surface == surface
        )
        return db.scalars(stmt).first()

class PlayerMetricDistributionRepository(BaseRepository[PlayerMetricDistribution]):
    def __init__(self):
        super().__init__(PlayerMetricDistribution)

    def get_distributions(self, db: Session, player_id: int, surface: str) -> List[PlayerMetricDistribution]:
        stmt = select(PlayerMetricDistribution).where(
            PlayerMetricDistribution.player_id == player_id,
            PlayerMetricDistribution.surface == surface
        )
        return list(db.scalars(stmt).all())

class MatchLiveSnapshotRepository(BaseRepository[MatchLiveSnapshot]):
    def __init__(self):
        super().__init__(MatchLiveSnapshot)

    def get_snapshots_by_match(self, db: Session, match_id: int) -> List[MatchLiveSnapshot]:
        stmt = select(MatchLiveSnapshot).where(MatchLiveSnapshot.match_id == match_id).order_by(MatchLiveSnapshot.snapshot_point_index.asc())
        return list(db.scalars(stmt).all())

class LightweightSample:
    __slots__ = ("created_at", "match_id", "match_date", "last_rally_at", "feature_json", "label_win_match",
                 "label_trend_continue_3_games", "label_trend_continue_5_games", "label_win_current_set")

    def __init__(self, row):
        for name in self.__slots__:
            setattr(self, name, getattr(row, name))

class TrendTrainingSampleRepository(BaseRepository[TrendTrainingSample]):
    def __init__(self):
        super().__init__(TrendTrainingSample)

    def get_samples_for_training(self, db: Session, limit: Optional[int] = None, *, scoring_target=None) -> List[LightweightSample]:
        stmt = select(
            TrendTrainingSample.created_at,
            TrendTrainingSample.match_id,
            Match.match_date,
            Match.last_rally_at,
            TrendTrainingSample.feature_json,
            (TrendTrainingSample.player_id == Match.winner_player_id).label("label_win_match"),
            TrendTrainingSample.label_trend_continue_3_games,
            TrendTrainingSample.label_trend_continue_5_games,
            TrendTrainingSample.label_win_current_set,
        ).join(Match, Match.id == TrendTrainingSample.match_id).where(
            Match.provider == "sportradar",
            Match.status.in_(["ended", "closed", "completed"]),
            Match.winner_player_id.is_not(None),
        )
        if scoring_target is not None:
            stmt = stmt.where(Match.scoring_target == scoring_target, Match.timeline_complete.is_(True),
                              Match.last_rally_at.is_not(None))
        if limit:
            stmt = stmt.order_by(Match.match_date.desc(), TrendTrainingSample.id.desc()).limit(limit)
            rows = db.execute(stmt).all()
            rows = list(rows)
            rows.reverse()
        else:
            stmt = stmt.order_by(Match.match_date.asc(), TrendTrainingSample.id.asc())
            rows = db.execute(stmt).all()

        return [LightweightSample(row) for row in rows]

class TrendModelRepository(BaseRepository[TrendModel]):
    def __init__(self):
        super().__init__(TrendModel)

    def get_latest_model(self, db: Session, target_label: str) -> Optional[TrendModel]:
        stmt = select(TrendModel).where(TrendModel.target_label == target_label).order_by(TrendModel.created_at.desc())
        return db.scalars(stmt).first()

class TrendSignalLogRepository(BaseRepository[TrendSignalLog]):
    def __init__(self):
        super().__init__(TrendSignalLog)

    def get_recent_signals(self, db: Session, limit: int = 50) -> List[TrendSignalLog]:
        stmt = select(TrendSignalLog).order_by(TrendSignalLog.created_at.desc()).limit(limit)
        return list(db.scalars(stmt).all())

class AlertLogRepository(BaseRepository[AlertLog]):
    def __init__(self):
        super().__init__(AlertLog)

    def get_by_cooldown_key(self, db: Session, cooldown_key: str) -> List[AlertLog]:
        stmt = select(AlertLog).where(AlertLog.cooldown_key == cooldown_key)
        return list(db.scalars(stmt).all())

class PlayerPhasePatternRepository(BaseRepository[PlayerPhasePattern]):
    def __init__(self):
        super().__init__(PlayerPhasePattern)

    def get_patterns_by_player(self, db: Session, player_id: int) -> List[PlayerPhasePattern]:
        stmt = select(PlayerPhasePattern).where(PlayerPhasePattern.player_id == player_id)
        return list(db.scalars(stmt).all())

class PlayerSequencePatternRepository(BaseRepository[PlayerSequencePattern]):
    def __init__(self):
        super().__init__(PlayerSequencePattern)

    def get_patterns_by_player(self, db: Session, player_id: int) -> List[PlayerSequencePattern]:
        stmt = select(PlayerSequencePattern).where(PlayerSequencePattern.player_id == player_id)
        return list(db.scalars(stmt).all())

class BacktestRunRepository(BaseRepository[BacktestRun]):
    def __init__(self):
        super().__init__(BacktestRun)

class BacktestResultRepository(BaseRepository[BacktestResult]):
    def __init__(self):
        super().__init__(BacktestResult)

class ProcessedDateRepository(BaseRepository[ProcessedDate]):
    def __init__(self):
        super().__init__(ProcessedDate)

    def get_by_date(self, db: Session, date_str: str) -> Optional[ProcessedDate]:
        stmt = select(ProcessedDate).where(ProcessedDate.date_str == date_str)
        return db.scalars(stmt).first()
