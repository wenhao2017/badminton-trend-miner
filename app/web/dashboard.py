from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, and_, case
from sqlalchemy.orm import Session

from app.db.models import (
    AlertLog,
    ApiKeyUsage,
    BacktestResult,
    BacktestRun,
    Match,
    MatchPointEvent,
    MatchLiveSnapshot,
    MatchPlayer,
    MatchPlayerStat,
    Player,
    PlayerMetricDistribution,
    PlayerPhasePattern,
    PlayerProfile,
    PlayerSequencePattern,
    ProcessedDate,
    TrendModel,
    TrendSignalLog,
    MatchSConfirmation,
)
from app.db.session import get_db
from app.config import settings
from app.services.live_monitor import LiveMonitor
from app.services.historical_importer import HistoricalImporter
from app.services.model_trainer import ModelTrainer
from app.services.player_behavior_analyzer import PlayerBehaviorAnalyzer
from app.services.player_pattern_miner import PlayerPatternMiner
from app.services.player_profile_builder import PlayerProfileBuilder
from app.services.signal_recommendation import match_direction_result
from app.services.signal_history_context import player_inactivity_warning
from app.services.score_display import snapshot_score_before, latest_match_score
from app.api_clients.sportradar_cache import SportradarCache
from app.utils.sportradar_event import tournament_level_label


router = APIRouter()


ACTIVE_MATCH_STATUSES = ["live", "suspended", "interrupted", "delayed"]
SCHEDULED_MATCH_STATUSES = ["not_started", "scheduled"]
COMPLETED_MATCH_STATUSES = ["ended", "completed", "closed"]


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Date must use YYYY-MM-DD format") from exc


def _date_range(start_date: str, end_date: str) -> List[str]:
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if end < start:
        raise HTTPException(status_code=400, detail="end_date must be greater than or equal to start_date")
    if (end - start).days > 62:
        raise HTTPException(status_code=400, detail="Date range cannot exceed 63 days")
    days = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return days


def _display_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.DAILY_HISTORY_IMPORT_TIMEZONE or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def _local_day_utc_range(date_str: Optional[str] = None) -> tuple[str, datetime, datetime]:
    tz = _display_timezone()
    local_date = _parse_date(date_str).date() if date_str else datetime.now(tz).date()
    local_start = datetime.combine(local_date, time.min, tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    return (
        local_date.strftime("%Y-%m-%d"),
        local_start.astimezone(timezone.utc).replace(tzinfo=None),
        local_end.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _dt(value: Any, *, naive_as_utc: bool = False) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            if naive_as_utc:
                value = value.replace(tzinfo=timezone.utc)
            else:
                return value.isoformat()
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _round(value: Any, digits: int = 3) -> float:
    try:
        return round(float(value or 0), digits)
    except (TypeError, ValueError):
        return 0.0


def _rate(numerator: Any, denominator: Any) -> Optional[float]:
    try:
        den = float(denominator or 0)
        if den <= 0:
            return None
        return _round(float(numerator or 0) / den)
    except (TypeError, ValueError):
        return None


def _pagination_params(page: int, per_page: int, max_per_page: int = 100) -> tuple[int, int]:
    page = max(int(page or 1), 1)
    per_page = min(max(int(per_page or 25), 1), max_per_page)
    return page, per_page


def _pagination_meta(total: int, page: int, per_page: int) -> Dict[str, int]:
    total_pages = max((int(total) + per_page - 1) // per_page, 1)
    return {
        "page": page,
        "per_page": per_page,
        "total": int(total),
        "total_pages": total_pages,
        "offset": (page - 1) * per_page,
    }


def _player(db: Session, player_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not player_id:
        return None
    player = db.get(Player, player_id)
    if not player:
        return None
    return {
        "id": player.id,
        "name": player.name,
        "country": player.country,
        "hand": player.hand,
        "birth_date": player.birth_date,
    }


def _match(db: Session, match_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not match_id:
        return None
    match = db.get(Match, match_id)
    if not match:
        return None
    players = db.query(MatchPlayer).filter(MatchPlayer.match_id == match.id).order_by(
        case((MatchPlayer.side == "home", 0), (MatchPlayer.side == "away", 1), else_=2),
        MatchPlayer.id,
    ).all()
    return {
        "id": match.id,
        "external_match_id": match.external_match_id,
        "tournament": match.tournament,
        "tournament_id": match.tournament_id,
        "tournament_level": tournament_level_label(match.tournament_level, match.tournament, match.tournament_id),
        "match_date": _dt(match.match_date, naive_as_utc=True),
        "surface": match.surface,
        "round": match.round,
        "competition_stage": match.competition_stage,
        "status": match.status,
        "final_score": match.final_score,
        "winner_player_id": match.winner_player_id,
        "players": [
            {
                "side": mp.side,
                "result": mp.result,
                "seed": mp.seed,
                "ranking": mp.ranking,
                "player": _player(db, mp.player_id),
            }
            for mp in players
        ],
    }


def _snapshot(snapshot: Optional[MatchLiveSnapshot]) -> Optional[Dict[str, Any]]:
    if not snapshot:
        return None
    return {
        "id": snapshot.id,
        "match_id": snapshot.match_id,
        "player_id": snapshot.player_id,
        "opponent_id": snapshot.opponent_id,
        "snapshot_point_index": snapshot.snapshot_point_index,
        "set_no": snapshot.set_no,
        "game_no": snapshot.game_no,
        "current_score_text": snapshot.current_score_text,
        "is_server": snapshot.is_server,
        "serve_known": (snapshot.raw_snapshot_json or {}).get("serve_known", False),
        "player_sets": snapshot.player_sets,
        "opponent_sets": snapshot.opponent_sets,
        "player_games": snapshot.player_games,
        "opponent_games": snapshot.opponent_games,
        "total_point_win_rate": _round(snapshot.total_point_win_rate),
        "service_point_win_rate": _round(snapshot.service_point_win_rate),
        "return_point_win_rate": _round(snapshot.return_point_win_rate),
        "first_serve_in_rate": _round(snapshot.first_serve_in_rate),
        "first_serve_win_rate": _round(snapshot.first_serve_win_rate),
        "second_serve_win_rate": _round(snapshot.second_serve_win_rate),
        "break_point_create_count": snapshot.break_point_create_count,
        "break_point_save_rate": _round(snapshot.break_point_save_rate),
        "recent_12_points_win_rate": _round(snapshot.recent_12_points_win_rate),
        "recent_20_points_win_rate": _round(snapshot.recent_20_points_win_rate),
        "recent_3_games_point_win_rate": _round(snapshot.recent_3_games_point_win_rate),
        "recent_5_games_point_win_rate": _round(snapshot.recent_5_games_point_win_rate),
        "recent_return_pressure_score": _round(snapshot.recent_return_pressure_score),
        "service_game_pressure_score": _round(snapshot.service_game_pressure_score),
        "opponent_second_serve_attack_score": _round(snapshot.opponent_second_serve_attack_score),
        "win_profile_similarity": _round(snapshot.win_profile_similarity),
        "loss_profile_similarity": _round(snapshot.loss_profile_similarity),
        "match_control_score": _round(snapshot.match_control_score),
        "momentum_score": _round(snapshot.momentum_score),
        "collapse_risk_score": _round(snapshot.collapse_risk_score),
        "trend_confidence_score": _round(snapshot.trend_confidence_score),
        "raw_snapshot_json": snapshot.raw_snapshot_json or {},
        "created_at": _dt(snapshot.created_at),
    }


def _signal_inactivity_warning(db: Session, signal: TrendSignalLog, match: Optional[Match]) -> Optional[Dict[str, Any]]:
    """Display-only warning, measured before this match rather than as of today."""
    if signal.signal_level != "S" or not match or not match.match_date:
        return None
    return player_inactivity_warning(db, signal.player_id, match)


def _signal(db: Session, signal: TrendSignalLog) -> Dict[str, Any]:
    match = db.get(Match, signal.match_id)
    direction_result = match_direction_result(signal, match)
    admission = db.get(MatchSConfirmation, signal.match_id) if signal.official_match_id else None
    latest = ((admission.state_json or {}).get("players", {}).get(str(signal.player_id), {}).get("latest", {}) if admission else {})
    weakening = bool(latest and latest.get("level") != "S" and match and match.status == "live")
    notice = "优势已减弱，原推荐不变" if weakening else None
    if match and match.status == "live" and latest.get("source_time"):
        try:
            source_time = datetime.fromisoformat(latest["source_time"].replace("Z", "+00:00"))
            if (datetime.now(timezone.utc)-source_time).total_seconds() > settings.LIVE_MONITOR_INTERVAL_SECONDS*2:
                notice = "数据更新延迟，原推荐不变"
        except (ValueError, TypeError):
            notice = "数据时间待核查，原推荐不变"
    return {
        "id": signal.id,
        "match_id": signal.match_id,
        "match_status": match.status if match else None,
        "player": _player(db, signal.player_id),
        "opponent": _player(db, signal.opponent_id),
        "snapshot_id": signal.snapshot_id,
        "signal_type": signal.signal_type,
        "signal_level": signal.signal_level,
        "is_official_s": signal.official_match_id is not None,
        "current_notice": notice,
        "trigger": (signal.reason_json or {}).get("official_s_confirmation"),
        "inactivity_warning": _signal_inactivity_warning(db, signal, match),
        "tournament": match.tournament if match else None,
        "tournament_level": (tournament_level_label(match.tournament_level, match.tournament, match.tournament_id)
                             if match else None),
        "competition_stage": match.competition_stage if match else None,
        "round": match.round if match else None,
        "trend_confidence_score": _round(signal.trend_confidence_score),
        "match_control_score": _round(signal.match_control_score),
        "momentum_score": _round(signal.momentum_score),
        "collapse_risk_score": _round(signal.collapse_risk_score),
        "model_probability": _round(signal.model_probability),
        "pattern_match_json": signal.pattern_match_json or {},
        "reason_json": signal.reason_json or {},
        "risk_json": signal.risk_json or {},
        "final_result": signal.final_result,
        "direction_match_result": direction_result,
        "direction_match_won": direction_result == "won_match",
        "created_at": _dt(signal.created_at),
    }


def _snapshot_series(snapshots: List[MatchLiveSnapshot]) -> List[Dict[str, Any]]:
    return [
        {
            "snapshot_point_index": s.snapshot_point_index,
            "player_id": s.player_id,
            "trend_confidence_score": _round(s.trend_confidence_score),
            "match_control_score": _round(s.match_control_score),
            "momentum_score": _round(s.momentum_score),
            "collapse_risk_score": _round(s.collapse_risk_score),
            "recent_20_points_win_rate": _round(s.recent_20_points_win_rate),
            "win_profile_similarity": _round(s.win_profile_similarity),
            "loss_profile_similarity": _round(s.loss_profile_similarity),
            "score": s.current_score_text,
        }
        for s in snapshots
    ]


def _snapshot_series_by_player(db: Session, snapshots: List[MatchLiveSnapshot]) -> List[Dict[str, Any]]:
    grouped: Dict[int, List[MatchLiveSnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(snapshot.player_id, []).append(snapshot)
    return [
        {
            "player": _player(db, player_id),
            "points": _snapshot_series(rows),
        }
        for player_id, rows in grouped.items()
    ]


def _latest_snapshots(db: Session, snapshots: List[MatchLiveSnapshot]) -> List[Dict[str, Any]]:
    latest: Dict[int, MatchLiveSnapshot] = {}
    for snapshot in snapshots:
        current = latest.get(snapshot.player_id)
        if current is None or snapshot.snapshot_point_index >= current.snapshot_point_index:
            latest[snapshot.player_id] = snapshot
    return [
        {
            "player": _player(db, player_id),
            "snapshot": {**_snapshot(snapshot), "score_before": snapshot_score_before(db, snapshot)},
        }
        for player_id, snapshot in latest.items()
    ]


def _player_stat(db: Session, stat: MatchPlayerStat) -> Dict[str, Any]:
    return {
        "player": _player(db, stat.player_id),
        "opponent": _player(db, stat.opponent_id),
        "aces": stat.aces,
        "double_faults": stat.double_faults,
        "first_serve_in": stat.first_serve_in,
        "first_serve_total": stat.first_serve_total,
        "first_serve_in_rate": _rate(stat.first_serve_in, stat.first_serve_total),
        "first_serve_points_won": stat.first_serve_points_won,
        "first_serve_points_total": stat.first_serve_points_total,
        "first_serve_win_rate": _rate(stat.first_serve_points_won, stat.first_serve_points_total),
        "second_serve_points_won": stat.second_serve_points_won,
        "second_serve_points_total": stat.second_serve_points_total,
        "second_serve_win_rate": _rate(stat.second_serve_points_won, stat.second_serve_points_total),
        "service_points_won": stat.service_points_won,
        "service_points_total": stat.service_points_total,
        "service_point_win_rate": _rate(stat.service_points_won, stat.service_points_total),
        "return_points_won": stat.return_points_won,
        "return_points_total": stat.return_points_total,
        "return_point_win_rate": _rate(stat.return_points_won, stat.return_points_total),
        "break_points_saved": stat.break_points_saved,
        "break_points_faced": stat.break_points_faced,
        "break_point_save_rate": _rate(stat.break_points_saved, stat.break_points_faced),
        "break_points_converted": stat.break_points_converted,
        "break_points_chances": stat.break_points_chances,
        "break_point_convert_rate": _rate(stat.break_points_converted, stat.break_points_chances),
        "total_points_won": stat.total_points_won,
        "total_points": stat.total_points,
        "total_point_win_rate": _rate(stat.total_points_won, stat.total_points),
    }


def _point_event(db: Session, point: MatchPointEvent) -> Dict[str, Any]:
    return {
        "point_index": point.point_index,
        "set_no": point.set_no,
        "game_no": point.game_no,
        "point_no_in_game": point.point_no_in_game,
        "server": _player(db, point.server_player_id) if point.raw_event_json.get("serve_known") else None,
        "home_score_before": point.raw_event_json.get("home_score_before"),
        "away_score_before": point.raw_event_json.get("away_score_before"),
        "receiver": _player(db, point.receiver_player_id),
        "winner": _player(db, point.point_winner_player_id),
        "server_score_before": point.server_score_before,
        "receiver_score_before": point.receiver_score_before,
        "server_games_before": point.server_games_before,
        "receiver_games_before": point.receiver_games_before,
        "server_sets_before": point.server_sets_before,
        "receiver_sets_before": point.receiver_sets_before,
        "is_ace": point.is_ace,
        "is_double_fault": point.is_double_fault,
        "is_break_point": point.is_break_point,
        "is_game_point": point.is_game_point,
        "is_set_point": point.is_set_point,
        "is_match_point": point.is_match_point,
        "is_tiebreak": point.is_tiebreak,
    }


def _game_timeline(db: Session, points: List[MatchPointEvent]) -> List[Dict[str, Any]]:
    games: Dict[tuple[int, int], List[MatchPointEvent]] = {}
    for point in points:
        games.setdefault((point.set_no, point.game_no), []).append(point)

    timeline = []
    for (set_no, game_no), game_points in sorted(games.items()):
        rows = sorted(game_points, key=lambda p: p.point_index)
        last = rows[-1]
        timeline.append(
            {
                "set_no": set_no,
                "game_no": game_no,
                "point_count": len(rows),
                "server": _player(db, last.server_player_id),
                "winner": _player(db, last.point_winner_player_id) if last.raw_event_json.get("game_ended") else None,
                "score": f"{last.raw_event_json.get('home_score')}-{last.raw_event_json.get('away_score')}",
                "was_break": last.point_winner_player_id != last.server_player_id,
                "had_break_point": any(p.is_break_point for p in rows),
                "had_set_point": any(p.is_set_point for p in rows),
                "had_match_point": any(p.is_match_point for p in rows),
                "last_point_index": last.point_index,
                "score_before": f"{last.server_score_before}-{last.receiver_score_before}",
            }
        )
    return timeline


@router.get("/", include_in_schema=False)
@router.get("/dashboard", include_in_schema=False)
def dashboard_page() -> FileResponse:
    path = Path(__file__).resolve().parents[1] / "static" / "dashboard" / "index.html"
    return FileResponse(path)


@router.get("/web/api/overview")
def web_overview(db: Session = Depends(get_db)) -> Dict[str, Any]:
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    recent_signals = (
        _dashboard_signals(db).order_by(TrendSignalLog.created_at.desc()).limit(12).all()
    )
    level_rows = (
        db.query(TrendSignalLog.signal_level, func.count(TrendSignalLog.id))
        .filter(TrendSignalLog.official_match_id.is_not(None) if settings.OFFICIAL_S_ENABLED else True)
        .group_by(TrendSignalLog.signal_level)
        .all()
    )
    latest_backtest = db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).first()
    latest_models = db.query(TrendModel).order_by(TrendModel.created_at.desc()).limit(4).all()
    quota = db.query(ApiKeyUsage).order_by(ApiKeyUsage.id.asc()).all()
    live_matches = db.query(Match).filter(Match.status.in_(ACTIVE_MATCH_STATUSES)).order_by(Match.match_date.desc()).all()
    return {
        "generated_at": _dt(now),
        "counts": {
            "live_matches": len(live_matches),
            "signals_24h": _dashboard_signals(db).filter(TrendSignalLog.created_at >= day_ago).count(),
            "total_signals": _dashboard_signals(db).count(),
            "tracked_players": db.query(Player).count(),
            "completed_matches": db.query(Match).filter(Match.status.in_(COMPLETED_MATCH_STATUSES)).count(),
        },
        "level_distribution": [{"level": lvl or "unknown", "count": count} for lvl, count in level_rows],
        "recent_signals": [_signal(db, sig) for sig in recent_signals],
        "live_matches": [_match(db, m.id) for m in live_matches],
        "quota": [
            {
                "id": q.id,
                "api_key_hash": q.api_key_hash[:8],
                "quota_limit": q.quota_limit,
                "quota_used": q.quota_used,
                "quota_remaining": q.quota_remaining,
                "cooldown_until": _dt(q.cooldown_until),
                "last_request_at": _dt(q.last_request_at),
            }
            for q in quota
        ],
        "latest_backtest": {
            "id": latest_backtest.id,
            "run_name": latest_backtest.run_name,
            "metrics_json": latest_backtest.metrics_json or {},
            "created_at": _dt(latest_backtest.created_at),
        }
        if latest_backtest
        else None,
        "latest_models": [
            {
                "id": m.id,
                "model_name": m.model_name,
                "target_label": m.target_label,
                "metrics_json": m.metrics_json or {},
                "created_at": _dt(m.created_at),
            }
            for m in latest_models
        ],
    }


def _dashboard_signals(db, scope=None):
    query = db.query(TrendSignalLog)
    if scope == "legacy":
        first = db.query(func.min(TrendSignalLog.id)).filter(
            TrendSignalLog.signal_level == "S", TrendSignalLog.official_match_id.is_(None)).group_by(TrendSignalLog.match_id)
        return query.filter(TrendSignalLog.id.in_(first))
    if scope == "official" or (scope is None and settings.OFFICIAL_S_ENABLED):
        return query.filter(TrendSignalLog.official_match_id.is_not(None))
    if scope not in (None, "raw"):
        raise HTTPException(status_code=400, detail="Unknown signal scope")
    return query


def _official_summary(db):
    normal = and_(Match.status.in_(COMPLETED_MATCH_STATUSES), Match.winner_player_id.is_not(None))
    special = Match.status.in_(["retired", "walkover", "cancelled", "abandoned"])
    row = db.query(func.count(TrendSignalLog.id),
        func.sum(case((and_(normal, Match.winner_player_id == TrendSignalLog.player_id), 1), else_=0)),
        func.sum(case((and_(normal, Match.winner_player_id != TrendSignalLog.player_id), 1), else_=0)),
        func.sum(case((special, 1), else_=0))).join(Match, Match.id == TrendSignalLog.match_id).filter(
            TrendSignalLog.official_match_id.is_not(None)).one()
    total, correct, wrong, excluded = (int(v or 0) for v in row)
    return {"total": total, "correct": correct, "wrong": wrong, "special": excluded,
            "pending": total-correct-wrong-excluded, "accuracy": correct/(correct+wrong) if correct+wrong else None}


@router.get("/web/api/signals")
def web_signals(
    limit: Optional[int] = None,
    page: int = 1,
    per_page: int = 25,
    level: Optional[str] = None,
    scope: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if limit is not None and per_page == 25:
        per_page = limit
    page, per_page = _pagination_params(page, per_page, 500)
    query = _dashboard_signals(db, scope)
    if level:
        query = query.filter(TrendSignalLog.signal_level == level)
    total = query.count()
    rows = (
        query.order_by(TrendSignalLog.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {"signals": [_signal(db, sig) for sig in rows], "pagination": _pagination_meta(total, page, per_page),
            "summary": _official_summary(db) if scope == "official" or (scope is None and settings.OFFICIAL_S_ENABLED) else None}


@router.get("/web/api/signals/pushed")
def web_pushed_signals(
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    page, per_page = _pagination_params(page, per_page, 500)
    if settings.OFFICIAL_S_ENABLED:
        return web_signals(page=page, per_page=per_page, scope="official", db=db)
    
    # Query all signals, ordered by match_id and id to easily track transitions
    all_signals = db.query(TrendSignalLog).order_by(
        TrendSignalLog.match_id.asc(),
        TrendSignalLog.id.asc()
    ).all()
    
    pushed_signals = []
    prev_sig_by_match = {}
    
    for sig in all_signals:
        match_id = sig.match_id
        prev_sig = prev_sig_by_match.get(match_id)
        
        # Determine if this signal should be pushed
        should_push = False
        if sig.signal_level in ["S", "A"]:
            should_push = True
        elif sig.signal_level in ["B", "C"]:
            if prev_sig and prev_sig.signal_level in ["S", "A"]:
                should_push = True
                
        if should_push:
            pushed_signals.append(sig)
            
        prev_sig_by_match[match_id] = sig
        
    # Now sort by created_at descending (newest first)
    pushed_signals.sort(key=lambda s: s.created_at, reverse=True)
    
    total = len(pushed_signals)
    offset = (page - 1) * per_page
    paginated_rows = pushed_signals[offset : offset + per_page]
    
    return {
        "signals": [_signal(db, sig) for sig in paginated_rows],
        "pagination": _pagination_meta(total, page, per_page)
    }



@router.get("/web/api/signals/{signal_id}/history-comparison")
def web_signal_history_comparison(signal_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.services.signal_history import build_signal_history
    signal = db.get(TrendSignalLog, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return build_signal_history(db, signal)


@router.get("/web/api/signals/{signal_id}")
def web_signal_detail(signal_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    signal = db.get(TrendSignalLog, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    snapshot = db.get(MatchLiveSnapshot, signal.snapshot_id) if signal.snapshot_id else None
    snapshots = (
        db.query(MatchLiveSnapshot)
        .filter(MatchLiveSnapshot.match_id == signal.match_id)
        .order_by(MatchLiveSnapshot.snapshot_point_index.asc())
        .all()
    )
    points = (
        db.query(MatchPointEvent)
        .filter(MatchPointEvent.match_id == signal.match_id)
        .order_by(MatchPointEvent.point_index.asc())
        .all()
    )
    stats = (
        db.query(MatchPlayerStat)
        .filter(MatchPlayerStat.match_id == signal.match_id)
        .all()
    )
    match_signals = (
        _dashboard_signals(db)
        .filter(TrendSignalLog.match_id == signal.match_id)
        .order_by(TrendSignalLog.created_at.desc())
        .limit(100)
        .all()
    )
    alerts = db.query(AlertLog).filter(AlertLog.signal_log_id == signal.id).order_by(AlertLog.created_at.desc()).all()
    return {
        "signal": _signal(db, signal),
        "match": _match(db, signal.match_id),
        "snapshot": _snapshot(snapshot),
        "series": _snapshot_series(snapshots),
        "series_by_player": _snapshot_series_by_player(db, snapshots),
        "latest_snapshots": _latest_snapshots(db, snapshots),
        "stats": [_player_stat(db, stat) for stat in stats],
        "recent_points": [_point_event(db, point) for point in points[-24:]],
        "game_timeline": _game_timeline(db, points)[-36:],
        "summary": {
            "total_points": len(points),
            "snapshots_count": len(snapshots),
            "last_updated_at": _dt(snapshots[-1].created_at) if snapshots else None,
        },
        "signals": [_signal(db, sig) for sig in match_signals],
        "alerts": [
            {
                "id": a.id,
                "alert_channel": a.alert_channel,
                "alert_title": a.alert_title,
                "sent_success": a.sent_success,
                "error_message": a.error_message,
                "created_at": _dt(a.created_at),
            }
            for a in alerts
        ],
    }


@router.get("/web/api/matches/live")
def web_live_matches(date: Optional[str] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    date_label, day_start, day_end = _local_day_utc_range(date)
    # Today's board also keeps earlier unfinished matches visible across midnight.
    day_filter = and_(Match.match_date >= day_start, Match.match_date < day_end)
    if date_label == _local_day_utc_range()[0]:
        day_filter = or_(day_filter, and_(Match.match_date < day_start,
                                        Match.status.in_(ACTIVE_MATCH_STATUSES)))
    matches = (
        db.query(Match)
        .filter(day_filter)
        .order_by(Match.tournament.asc(), Match.match_date.asc(), Match.id.asc())
        .all()
    )
    status_rows = (
        db.query(Match.status, func.count(Match.id))
        .filter(day_filter)
        .group_by(Match.status)
        .all()
    )
    payload = []
    for match in matches:
        snapshots = (
            db.query(MatchLiveSnapshot)
            .filter(MatchLiveSnapshot.match_id == match.id)
            .order_by(MatchLiveSnapshot.snapshot_point_index.asc())
            .all()
        )
        item = _match(db, match.id)
        first_player = next((mp["player"] for mp in item["players"] if mp["player"]), None)
        item["latest_score"] = latest_match_score(db, match.id, first_player["id"]) if first_player else None
        item["carried_over"] = match.match_date < day_start
        item["latest_snapshots"] = _latest_snapshots(db, snapshots)
        item["signals_count"] = db.query(TrendSignalLog).filter(TrendSignalLog.match_id == match.id).count()
        payload.append(item)
    return {
        "date": date_label,
        "timezone": settings.DAILY_HISTORY_IMPORT_TIMEZONE or "Asia/Shanghai",
        "matches": payload,
        "status_counts": {status or "unknown": count for status, count in status_rows},
        "active_statuses": ACTIVE_MATCH_STATUSES,
        "scheduled_statuses": SCHEDULED_MATCH_STATUSES,
        "completed_statuses": COMPLETED_MATCH_STATUSES,
    }


@router.get("/web/api/matches/{match_id}")
def web_match_detail(match_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    match = db.get(Match, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    snapshots = (
        db.query(MatchLiveSnapshot)
        .filter(MatchLiveSnapshot.match_id == match_id)
        .order_by(MatchLiveSnapshot.snapshot_point_index.asc())
        .all()
    )
    points = (
        db.query(MatchPointEvent)
        .filter(MatchPointEvent.match_id == match_id)
        .order_by(MatchPointEvent.point_index.asc())
        .all()
    )
    stats = (
        db.query(MatchPlayerStat)
        .filter(MatchPlayerStat.match_id == match_id)
        .all()
    )
    signals = (
        _dashboard_signals(db)
        .filter(TrendSignalLog.match_id == match_id)
        .order_by(TrendSignalLog.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "match": _match(db, match_id),
        "snapshots": [_snapshot(s) for s in snapshots[-80:]],
        "series": _snapshot_series(snapshots),
        "series_by_player": _snapshot_series_by_player(db, snapshots),
        "latest_snapshots": _latest_snapshots(db, snapshots),
        "stats": [_player_stat(db, stat) for stat in stats],
        "recent_points": [_point_event(db, point) for point in points[-24:]],
        "game_timeline": _game_timeline(db, points)[-36:],
        "summary": {
            "total_points": len(points),
            "snapshots_count": len(snapshots),
            "signals_count": len(signals),
            "last_updated_at": _dt(snapshots[-1].created_at) if snapshots else _dt(match.updated_at),
        },
        "signals": [_signal(db, sig) for sig in signals],
    }


@router.get("/web/api/players")
def web_players(
    search: str = "",
    limit: Optional[int] = None,
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if limit is not None and per_page == 25:
        per_page = limit
    page, per_page = _pagination_params(page, per_page, 200)
    query = db.query(Player)
    if search:
        query = query.filter(Player.name.ilike(f"%{search}%"))
    total = query.count()
    players = (
        query.order_by(Player.name.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {"players": [_player(db, p.id) for p in players], "pagination": _pagination_meta(total, page, per_page)}


def _player_historical_matches(db: Session, player_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    match_players = (
        db.query(MatchPlayer)
        .join(Match, Match.id == MatchPlayer.match_id)
        .filter(MatchPlayer.player_id == player_id)
        .order_by(Match.match_date.desc(), Match.id.desc())
        .limit(limit)
        .all()
    )
    match_ids = [mp.match_id for mp in match_players]
    if not match_ids:
        return []

    point_counts = {
        match_id: count
        for match_id, count in (
            db.query(MatchPointEvent.match_id, func.count(MatchPointEvent.id))
            .filter(MatchPointEvent.match_id.in_(match_ids))
            .group_by(MatchPointEvent.match_id)
            .all()
        )
    }
    matches = {m.id: m for m in db.query(Match).filter(Match.id.in_(match_ids)).all()}

    rows = []
    for mp in match_players:
        match = matches.get(mp.match_id)
        if not match:
            continue
        point_count = int(point_counts.get(match.id, 0))
        rows.append(
            {
                "id": match.id,
                "external_match_id": match.external_match_id,
                "tournament": match.tournament,
                "tournament_level": tournament_level_label(match.tournament_level, match.tournament, match.tournament_id),
                "match_date": _dt(match.match_date, naive_as_utc=True),
                "surface": match.surface,
                "round": match.round,
                "status": match.status,
                "final_score": match.final_score,
                "result": mp.result or ("won" if match.winner_player_id == player_id else "lost" if match.loser_player_id == player_id else None),
                "opponent": _player(db, mp.opponent_id),
                "ranking": mp.ranking,
                "seed": mp.seed,
                "point_count": point_count,
                "used_for_behavior": point_count > 0,
            }
        )
    return rows


def _latest_profiles_by_surface(profiles: List[PlayerProfile]) -> List[PlayerProfile]:
    by_surface: Dict[str, PlayerProfile] = {}
    for profile in profiles:
        current = by_surface.get(profile.surface)
        if current is None or (profile.updated_at, profile.id or 0) > (current.updated_at, current.id or 0):
            by_surface[profile.surface] = profile
    surface_order = {"all": 0, "hard": 1, "clay": 2, "grass": 3, "indoor": 4}
    return sorted(by_surface.values(), key=lambda p: (surface_order.get(p.surface, 99), p.surface))


@router.get("/web/api/players/{player_id}")
def web_player_detail(player_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    profiles = db.query(PlayerProfile).filter(PlayerProfile.player_id == player_id).all()
    distributions = db.query(PlayerMetricDistribution).filter(PlayerMetricDistribution.player_id == player_id).all()
    phase_patterns = db.query(PlayerPhasePattern).filter(PlayerPhasePattern.player_id == player_id).all()
    sequence_patterns = db.query(PlayerSequencePattern).filter(PlayerSequencePattern.player_id == player_id).all()
    behavior = PlayerBehaviorAnalyzer().analyze(db, player_id)
    historical_matches = _player_historical_matches(db, player_id)
    signals = (
        _dashboard_signals(db)
        .filter(TrendSignalLog.player_id == player_id)
        .order_by(TrendSignalLog.created_at.desc())
        .limit(50)
        .all()
    )
    return {
        "player": _player(db, player_id),
        "profiles": [
            {
                "surface": p.surface,
                "sample_matches": p.sample_matches,
                "win": {
                    "first_serve_in_rate": _round(p.win_avg_first_serve_in_rate),
                    "first_serve_win_rate": _round(p.win_avg_first_serve_win_rate),
                    "second_serve_win_rate": _round(p.win_avg_second_serve_win_rate),
                    "service_points_win_rate": _round(p.win_avg_service_points_win_rate),
                    "return_points_win_rate": _round(p.win_avg_return_points_win_rate),
                    "break_point_convert_rate": _round(p.win_avg_break_point_convert_rate),
                    "break_point_save_rate": _round(p.win_avg_break_point_save_rate),
                    "total_points_win_rate": _round(p.win_avg_total_points_win_rate),
                },
                "loss": {
                    "first_serve_in_rate": _round(p.loss_avg_first_serve_in_rate),
                    "first_serve_win_rate": _round(p.loss_avg_first_serve_win_rate),
                    "second_serve_win_rate": _round(p.loss_avg_second_serve_win_rate),
                    "service_points_win_rate": _round(p.loss_avg_service_points_win_rate),
                    "return_points_win_rate": _round(p.loss_avg_return_points_win_rate),
                    "break_point_convert_rate": _round(p.loss_avg_break_point_convert_rate),
                    "break_point_save_rate": _round(p.loss_avg_break_point_save_rate),
                    "total_points_win_rate": _round(p.loss_avg_total_points_win_rate),
                },
                "updated_at": _dt(p.updated_at),
            }
            for p in _latest_profiles_by_surface(profiles)
        ],
        "distributions": [
            {
                "surface": d.surface,
                "metric_name": d.metric_name,
                "result_type": d.result_type,
                "sample_count": d.sample_count,
                "avg_value": _round(d.avg_value),
                "median_value": _round(d.median_value),
                "std_value": _round(d.std_value),
                "p25_value": _round(d.p25_value),
                "p75_value": _round(d.p75_value),
                "p90_value": _round(d.p90_value),
            }
            for d in distributions
        ],
        "patterns": {
            "phase": [
                {
                    "id": p.id,
                    "surface": p.surface,
                    "set_no": p.set_no,
                    "game_range": p.game_range,
                    "event_type": p.event_type,
                    "sample_count": p.sample_count,
                    "event_rate": _round(p.event_rate),
                    "baseline_rate": _round(p.baseline_rate),
                    "lift_value": _round(p.lift_value),
                    "confidence_score": _round(p.confidence_score),
                    "description": p.description,
                }
                for p in phase_patterns
            ],
            "sequence": [
                {
                    "id": p.id,
                    "surface": p.surface,
                    "pattern_type": p.pattern_type,
                    "next_event_type": p.next_event_type,
                    "sample_count": p.sample_count,
                    "hit_rate": _round(p.hit_rate),
                    "baseline_rate": _round(p.baseline_rate),
                    "lift_value": _round(p.lift_value),
                    "confidence_score": _round(p.confidence_score),
                    "description": p.description,
                }
                for p in sequence_patterns
            ],
        },
        "behavior": behavior,
        "historical_matches": historical_matches,
        "signals": [_signal(db, sig) for sig in signals],
    }


@router.get("/web/api/backtests")
def web_backtests(
    runs_page: int = 1,
    runs_per_page: int = 10,
    results_page: int = 1,
    results_per_page: int = 25,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    runs_page, runs_per_page = _pagination_params(runs_page, runs_per_page, 50)
    results_page, results_per_page = _pagination_params(results_page, results_per_page, 100)
    runs_query = db.query(BacktestRun)
    results_query = db.query(BacktestResult)
    runs_total = runs_query.count()
    results_total = results_query.count()
    runs = (
        runs_query.order_by(BacktestRun.created_at.desc())
        .offset((runs_page - 1) * runs_per_page)
        .limit(runs_per_page)
        .all()
    )
    results = (
        results_query.order_by(BacktestResult.created_at.desc())
        .offset((results_page - 1) * results_per_page)
        .limit(results_per_page)
        .all()
    )
    return {
        "runs": [
            {
                "id": r.id,
                "run_name": r.run_name,
                "dataset_from_date": _dt(r.dataset_from_date),
                "dataset_to_date": _dt(r.dataset_to_date),
                "metrics_json": r.metrics_json or {},
                "created_at": _dt(r.created_at),
            }
            for r in runs
        ],
        "results": [
            {
                "id": r.id,
                "backtest_run_id": r.backtest_run_id,
                "signal_level": r.signal_level,
                "surface": r.surface,
                "match_stage": r.match_stage,
                "sample_count": r.sample_count,
                "hit_count": r.hit_count,
                "hit_rate": _round(r.hit_rate),
                "max_consecutive_losses": r.max_consecutive_losses,
                "avg_trigger_game_no": _round(r.avg_trigger_game_no),
                "metrics_json": r.metrics_json or {},
            }
            for r in results
        ],
        "runs_pagination": _pagination_meta(runs_total, runs_page, runs_per_page),
        "results_pagination": _pagination_meta(results_total, results_page, results_per_page),
    }


@router.get("/web/api/history-sync")
def web_history_sync(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    today = datetime.utcnow().date()
    if not end_date:
        end_date = today.strftime("%Y-%m-%d")
    if not start_date:
        start_date = (today - timedelta(days=13)).strftime("%Y-%m-%d")

    days = _date_range(start_date, end_date)
    processed = {
        row.date_str: row
        for row in db.query(ProcessedDate)
        .filter(ProcessedDate.date_str.in_(days))
        .all()
    }

    rows = []
    for date_str in days:
        day_start = _parse_date(date_str)
        day_end = day_start + timedelta(days=1)
        matches_query = db.query(Match).filter(
            Match.match_date >= day_start,
            Match.match_date < day_end,
        )
        status_rows = (
            db.query(Match.status, func.count(Match.id))
            .filter(Match.match_date >= day_start, Match.match_date < day_end)
            .group_by(Match.status)
            .all()
        )
        rec = processed.get(date_str)
        rows.append(
            {
                "date": date_str,
                "processed_status": rec.status if rec else "not_synced",
                "processed_match_count": rec.match_count if rec else 0,
                "processed_at": _dt(rec.created_at) if rec else None,
                "db_match_count": matches_query.count(),
                "completed_match_count": matches_query.filter(Match.status.in_(["ended", "completed", "closed"])).count(),
                "status_counts": {status or "unknown": count for status, count in status_rows},
            }
        )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "dates": rows,
    }


def history_sync_task(start_date: str, end_date: str, women_only: bool, force: bool) -> None:
    from app.db.session import SessionLocal
    from loguru import logger

    db = SessionLocal()
    try:
        if force:
            days = _date_range(start_date, end_date)
            deleted = (
                db.query(ProcessedDate)
                .filter(ProcessedDate.date_str.in_(days))
                .delete(synchronize_session=False)
            )
            db.commit()
            logger.info(f"Web Action: cleared {deleted} processed date markers for forced history sync.")

        summary = HistoricalImporter().import_date_range(db, start_date, end_date, women_only=women_only, force=force)
        logger.info(f"Web Action: history sync finished: {summary}")
    except Exception as exc:
        logger.error(f"Web Action: history sync failed for {start_date}..{end_date}: {exc}", exc_info=True)
    finally:
        db.close()


@router.post("/web/api/actions/history-sync")
def web_action_history_sync(
    start_date: str,
    end_date: str,
    women_only: bool = True,
    force: bool = False,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    from app.jobs.job_service import JobService
    _date_range(start_date, end_date)
    payload = {
        "start_date": start_date,
        "end_date": end_date,
        "women_only": women_only,
        "force": force
    }
    dedupe_key = f"history_sync-{start_date}-{end_date}"
    job = JobService.enqueue(db, "history_sync", payload, dedupe_key)
    return {
        "status": job.status,
        "job_id": job.id,
        "start_date": start_date,
        "end_date": end_date,
        "women_only": women_only,
        "force": force,
    }


@router.post("/web/api/actions/monitor-run-once")
def web_action_monitor_run_once(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return LiveMonitor().scan_and_update(db)


@router.post("/web/api/actions/cache-clear")
def web_action_cache_clear() -> Dict[str, Any]:
    count = SportradarCache().clear()
    return {"cleared": count}


from pydantic import BaseModel

class AddKeysRequest(BaseModel):
    keys: str


@router.post("/web/api/actions/keys-cleanup")
def web_action_keys_cleanup(force_remote: bool = False, db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not settings.WEB_MANAGE_SPORTRADAR_KEYS:
        raise HTTPException(status_code=409, detail="API key 由服务器配置管理。")
    from app.api_clients.sportradar_key_manager import SportradarKeyManager
    manager = SportradarKeyManager()
    try:
        result = manager.verify_and_cleanup_keys(db, force_remote=force_remote)
        return result
    except Exception as e:
        return {"error": str(e)}


@router.post("/web/api/actions/keys-add")
def web_action_keys_add(payload: AddKeysRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not settings.WEB_MANAGE_SPORTRADAR_KEYS:
        raise HTTPException(status_code=409, detail="API key 由服务器配置管理。")
    from app.api_clients.sportradar_key_manager import SportradarKeyManager
    from loguru import logger
    
    new_keys = []
    for k in payload.keys.split("\n"):
        k = k.strip().strip('",\',')
        if k:
            new_keys.append(k)
    if not new_keys:
        return {"error": "No valid keys entered."}
        
    manager = SportradarKeyManager()
    
    # Add them to .env file
    env_path = ".env"
    import os
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                
            new_lines = []
            keys_updated = False
            for line in lines:
                if line.startswith("SPORTRADAR_API_KEYS="):
                    val = line.split("=", 1)[1].strip()
                    quotes = ""
                    if val.startswith('"') and val.endswith('"'):
                        quotes = '"'
                        val = val[1:-1]
                    elif val.startswith("'") and val.endswith("'"):
                        quotes = "'"
                        val = val[1:-1]
                        
                    current_keys = [k.strip() for k in val.split(",") if k.strip()]
                    
                    for nk in new_keys:
                        if nk not in current_keys:
                            current_keys.append(nk)
                            
                    new_line = f"SPORTRADAR_API_KEYS={quotes}{','.join(current_keys)}{quotes}\n"
                    new_lines.append(new_line)
                    keys_updated = True
                else:
                    new_lines.append(line)
                    
            if not keys_updated:
                new_lines.append(f"SPORTRADAR_API_KEYS={','.join(new_keys)}\n")
                
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
                
        except Exception as e:
            logger.error(f"Failed to write new keys to env file: {e}")
            return {"error": f"Failed to save keys to configuration file: {e}"}
            
    # Reload in settings and key manager
    all_keys = list(manager.keys)
    for nk in new_keys:
        if nk not in all_keys:
            all_keys.append(nk)
            
    settings.SPORTRADAR_API_KEYS = ",".join(all_keys)
    manager.keys = settings.api_keys_list
    
    # Create database records for the new keys
    added_keys_masked = []
    for k in new_keys:
        usage = manager._get_or_create_usage(db, k)
        from app.utils.security import mask_key
        added_keys_masked.append(mask_key(k))
        
    return {
        "status": "success",
        "added_count": len(new_keys),
        "keys": added_keys_masked
    }


@router.post("/web/api/actions/profiles-rebuild")
def web_action_profiles_rebuild(db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.jobs.job_service import JobService
    payload = {}
    dedupe_key = "profiles_rebuild"
    job = JobService.enqueue(db, "profiles_rebuild", payload, dedupe_key)
    return {"status": job.status, "job_id": job.id}


@router.post("/web/api/actions/patterns-mine")
def web_action_patterns_mine(db: Session = Depends(get_db)) -> Dict[str, Any]:
    count = PlayerPatternMiner().mine_patterns(db)
    return {"patterns_mined": count}


@router.post("/web/api/actions/models-train")
def web_action_models_train(target_label: str = "label_win_match", scoring_target: int | None = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.jobs.job_service import JobService
    payload = {"target_label": target_label, "scoring_target": scoring_target}
    dedupe_key = f"models_train-{target_label}-{scoring_target}"
    job = JobService.enqueue(db, "models_train", payload, dedupe_key)
    return {"status": job.status, "job_id": job.id}


def reprocess_all_historical_data_task():
    from app.db.session import SessionLocal
    from app.db.models import Match
    from app.services.replay_engine import ReplayEngine
    from app.services.trend_labeler import TrendLabeler
    from loguru import logger
    
    db = SessionLocal()
    try:
        logger.info("Web Action: Background task starting historical data reprocessing...")
        matches = db.query(Match).filter(Match.status.in_(["ended", "completed", "closed"])).all()
        logger.info(f"Web Action: Found {len(matches)} matches to reprocess.")
        
        replay_engine = ReplayEngine()
        labeler = TrendLabeler()
        
        for idx, match in enumerate(matches):
            try:
                replay_engine.replay_match(db, match.id)
                labeler.generate_labels(db, match.id)
            except Exception as match_err:
                logger.error(f"Web Action: Failed to replay/label match {match.id}: {match_err}")
                
        logger.info("Web Action: Rebuilding player profiles...")
        PlayerProfileBuilder().rebuild_profiles(db)
        
        logger.info("Web Action: Mining player patterns...")
        PlayerPatternMiner().mine_patterns(db)
        
        logger.info("Web Action: Retraining ML models...")
        trainer = ModelTrainer()
        trainer.train_model(db, "label_win_match")
        
        logger.info("Web Action: Resolving final results for all signals...")
        try:
            # Reset all results first to ensure recalculation is fresh
            db.query(TrendSignalLog).update({TrendSignalLog.final_result: None})
            db.commit()
            
            # Use LiveMonitor to resolve them in bulk
            from app.services.live_monitor import LiveMonitor
            LiveMonitor().resolve_pending_signals(db)
        except Exception as e_res_all:
            logger.error(f"Web Action: Failed to resolve signal results during reprocessing: {e_res_all}")
            
        logger.info("Web Action: Background reprocessing complete!")
    except Exception as e:
        logger.error(f"Web Action: Reprocessing task failed: {e}")
    finally:
        db.close()


@router.post("/web/api/actions/historical-reprocess")
def web_action_historical_reprocess(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    background_tasks.add_task(reprocess_all_historical_data_task)
    return {"status": "enqueued", "message": "Historical reprocessing has started in the background. Check backend logs for progress."}
