from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy import (
    Integer, String, Boolean, Float, DateTime, JSON, ForeignKey, func, Text, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class ApiCallLog(Base):
    __tablename__ = "api_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class ApiKeyUsage(Base):
    __tablename__ = "api_key_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    quota_limit: Mapped[int] = mapped_column(Integer, default=1000)
    quota_used: Mapped[int] = mapped_column(Integer, default=0)
    quota_remaining: Mapped[int] = mapped_column(Integer, default=1000)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_request_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

class RawProviderResponse(Base):
    __tablename__ = "raw_provider_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    body_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    source_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("idx_raw_provider_responses_lookup", "provider", "endpoint", "params_hash", unique=True),
    )

class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_player_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    hand: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    birth_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

class Match(Base):
    __tablename__ = "matches"

    __table_args__ = (
        Index("ix_matches_provider_status_date", "provider", "status", "match_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_match_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    tournament: Mapped[str] = mapped_column(String(255), nullable=False)
    tournament_id: Mapped[str] = mapped_column(String(100), nullable=False)
    tournament_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    match_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    surface: Mapped[str] = mapped_column(String(50), nullable=False) # hard, clay, grass, indoor
    indoor: Mapped[bool] = mapped_column(Boolean, default=False)
    round: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    competition_stage: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    best_of: Mapped[int] = mapped_column(Integer, default=3)
    scoring_target: Mapped[int] = mapped_column(Integer, default=21)
    timeline_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    timeline_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    last_rally_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    training_source_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    winner_player_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("players.id"), nullable=True)
    loser_player_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("players.id"), nullable=True)
    final_score: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="scheduled") # scheduled, live, completed, suspended
    
    # Sync status tracking
    last_summary_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_timeline_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_point_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    finalized_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_provider_fetch_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

class MatchPlayer(Base):
    __tablename__ = "match_players"

    __table_args__ = (
        Index("ix_match_players_match_id", "match_id", "player_id", unique=True),
        Index("ix_match_players_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    opponent_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(20), nullable=False) # home / away
    seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ranking: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    result: Mapped[Optional[str]] = mapped_column(String(20), nullable=True) # won, lost
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

class MatchPointEvent(Base):
    __tablename__ = "match_point_events"

    __table_args__ = (
        Index("ix_match_point_events_match_id_point_index", "match_id", "point_index", unique=True),
        Index("ix_match_point_events_match_id_set_game", "match_id", "set_no", "game_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False)
    external_event_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    point_index: Mapped[int] = mapped_column(Integer, nullable=False)
    set_no: Mapped[int] = mapped_column(Integer, nullable=False)
    game_no: Mapped[int] = mapped_column(Integer, nullable=False)
    point_no_in_game: Mapped[int] = mapped_column(Integer, nullable=False)
    server_player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    receiver_player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    point_winner_player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    point_loser_player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    server_score_before: Mapped[str] = mapped_column(String(10), nullable=False) # 0, 15, 30, 40, A
    receiver_score_before: Mapped[str] = mapped_column(String(10), nullable=False)
    server_games_before: Mapped[int] = mapped_column(Integer, nullable=False)
    receiver_games_before: Mapped[int] = mapped_column(Integer, nullable=False)
    server_sets_before: Mapped[int] = mapped_column(Integer, nullable=False)
    receiver_sets_before: Mapped[int] = mapped_column(Integer, nullable=False)
    is_ace: Mapped[bool] = mapped_column(Boolean, default=False)
    is_double_fault: Mapped[bool] = mapped_column(Boolean, default=False)
    is_first_serve_in: Mapped[bool] = mapped_column(Boolean, default=True)
    is_first_serve_point_won: Mapped[bool] = mapped_column(Boolean, default=False)
    is_second_serve_point_won: Mapped[bool] = mapped_column(Boolean, default=False)
    is_break_point: Mapped[bool] = mapped_column(Boolean, default=False)
    is_game_point: Mapped[bool] = mapped_column(Boolean, default=False)
    is_set_point: Mapped[bool] = mapped_column(Boolean, default=False)
    is_match_point: Mapped[bool] = mapped_column(Boolean, default=False)
    is_tiebreak: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_event_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class MatchPlayerStat(Base):
    __tablename__ = "match_player_stats"

    __table_args__ = (
        Index("ix_match_player_stats_player_id", "player_id"),
        Index("ix_stats_match_player", "match_id", "player_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    opponent_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    aces: Mapped[int] = mapped_column(Integer, default=0)
    double_faults: Mapped[int] = mapped_column(Integer, default=0)
    first_serve_in: Mapped[int] = mapped_column(Integer, default=0)
    first_serve_total: Mapped[int] = mapped_column(Integer, default=0)
    first_serve_points_won: Mapped[int] = mapped_column(Integer, default=0)
    first_serve_points_total: Mapped[int] = mapped_column(Integer, default=0)
    second_serve_points_won: Mapped[int] = mapped_column(Integer, default=0)
    second_serve_points_total: Mapped[int] = mapped_column(Integer, default=0)
    service_points_won: Mapped[int] = mapped_column(Integer, default=0)
    service_points_total: Mapped[int] = mapped_column(Integer, default=0)
    return_points_won: Mapped[int] = mapped_column(Integer, default=0)
    return_points_total: Mapped[int] = mapped_column(Integer, default=0)
    break_points_saved: Mapped[int] = mapped_column(Integer, default=0)
    break_points_faced: Mapped[int] = mapped_column(Integer, default=0)
    break_points_converted: Mapped[int] = mapped_column(Integer, default=0)
    break_points_chances: Mapped[int] = mapped_column(Integer, default=0)
    total_points_won: Mapped[int] = mapped_column(Integer, default=0)
    total_points: Mapped[int] = mapped_column(Integer, default=0)
    raw_stats_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class MatchLiveSnapshot(Base):
    __tablename__ = "match_live_snapshots"

    __table_args__ = (
        Index("ix_match_live_snapshots_match_id", "match_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    opponent_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    snapshot_point_index: Mapped[int] = mapped_column(Integer, nullable=False)
    set_no: Mapped[int] = mapped_column(Integer, nullable=False)
    game_no: Mapped[int] = mapped_column(Integer, nullable=False)
    current_score_text: Mapped[str] = mapped_column(String(50), nullable=False)
    is_server: Mapped[bool] = mapped_column(Boolean, default=False)
    player_sets: Mapped[int] = mapped_column(Integer, default=0)
    opponent_sets: Mapped[int] = mapped_column(Integer, default=0)
    player_games: Mapped[int] = mapped_column(Integer, default=0)
    opponent_games: Mapped[int] = mapped_column(Integer, default=0)
    player_points_in_game: Mapped[int] = mapped_column(Integer, default=0)
    opponent_points_in_game: Mapped[int] = mapped_column(Integer, default=0)
    total_point_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    service_point_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    return_point_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    first_serve_in_rate: Mapped[float] = mapped_column(Float, default=0.0)
    first_serve_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    second_serve_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    break_point_create_count: Mapped[int] = mapped_column(Integer, default=0)
    break_point_save_rate: Mapped[float] = mapped_column(Float, default=0.0)
    recent_12_points_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    recent_20_points_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    recent_3_games_point_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    recent_5_games_point_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    recent_return_pressure_score: Mapped[float] = mapped_column(Float, default=0.0)
    service_game_pressure_score: Mapped[float] = mapped_column(Float, default=0.0)
    opponent_second_serve_attack_score: Mapped[float] = mapped_column(Float, default=0.0)
    win_profile_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    loss_profile_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    match_control_score: Mapped[float] = mapped_column(Float, default=0.0)
    momentum_score: Mapped[float] = mapped_column(Float, default=0.0)
    collapse_risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    trend_confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    raw_snapshot_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class PlayerProfile(Base):
    __tablename__ = "player_profiles"

    __table_args__ = (
        Index("ix_player_profiles_player_surface", "player_id", "surface"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    surface: Mapped[str] = mapped_column(String(50), nullable=False) # all, hard, clay, grass, indoor
    sample_matches: Mapped[int] = mapped_column(Integer, default=0)
    win_avg_first_serve_in_rate: Mapped[float] = mapped_column(Float, default=0.0)
    win_avg_first_serve_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    win_avg_second_serve_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    win_avg_service_points_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    win_avg_return_points_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    win_avg_break_point_convert_rate: Mapped[float] = mapped_column(Float, default=0.0)
    win_avg_break_point_save_rate: Mapped[float] = mapped_column(Float, default=0.0)
    win_avg_total_points_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    loss_avg_first_serve_in_rate: Mapped[float] = mapped_column(Float, default=0.0)
    loss_avg_first_serve_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    loss_avg_second_serve_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    loss_avg_service_points_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    loss_avg_return_points_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    loss_avg_break_point_convert_rate: Mapped[float] = mapped_column(Float, default=0.0)
    loss_avg_break_point_save_rate: Mapped[float] = mapped_column(Float, default=0.0)
    loss_avg_total_points_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

class PlayerMetricDistribution(Base):
    __tablename__ = "player_metric_distributions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    surface: Mapped[str] = mapped_column(String(50), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    result_type: Mapped[str] = mapped_column(String(20), nullable=False) # win / loss
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_value: Mapped[float] = mapped_column(Float, default=0.0)
    median_value: Mapped[float] = mapped_column(Float, default=0.0)
    std_value: Mapped[float] = mapped_column(Float, default=0.0)
    p25_value: Mapped[float] = mapped_column(Float, default=0.0)
    p75_value: Mapped[float] = mapped_column(Float, default=0.0)
    p90_value: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

class TrendTrainingSample(Base):
    __tablename__ = "trend_training_samples"

    __table_args__ = (
        Index("ix_trend_training_samples_match_id", "match_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    opponent_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    snapshot_point_index: Mapped[int] = mapped_column(Integer, nullable=False)
    surface: Mapped[str] = mapped_column(String(50), nullable=False)
    set_no: Mapped[int] = mapped_column(Integer, nullable=False)
    game_no: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    label_trend_continue_3_games: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    label_trend_continue_5_games: Mapped[bool] = mapped_column(Boolean, default=False)
    label_win_current_set: Mapped[bool] = mapped_column(Boolean, default=False)
    label_win_match: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class TrendModel(Base):
    __tablename__ = "trend_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scoring_target: Mapped[int] = mapped_column(Integer, default=21)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_label: Mapped[str] = mapped_column(String(100), nullable=False) # e.g. label_win_match
    feature_columns_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    metrics_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_path: Mapped[str] = mapped_column(String(255), nullable=False)
    trained_from_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    trained_to_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class TrendSignalLog(Base):
    __tablename__ = "trend_signal_logs"

    __table_args__ = (
        Index("ix_trend_signal_logs_match_id", "match_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False)
    # NULL for legacy/raw logs; DB-enforced once-per-match formal recommendation.
    official_match_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("matches.id"), nullable=True, unique=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    opponent_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    snapshot_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("match_live_snapshots.id"), nullable=True)
    signal_type: Mapped[str] = mapped_column(String(50), nullable=False) # direction player
    signal_level: Mapped[str] = mapped_column(String(10), nullable=False) # S, A, B, C, Risk
    trend_confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    match_control_score: Mapped[float] = mapped_column(Float, default=0.0)
    momentum_score: Mapped[float] = mapped_column(Float, default=0.0)
    collapse_risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    model_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pattern_match_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    reason_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    final_result: Mapped[Optional[str]] = mapped_column(String(50), nullable=True) # success, failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class MatchSConfirmation(Base):
    __tablename__ = "match_s_confirmations"

    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), primary_key=True)
    model_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_point_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    published_signal_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("trend_signal_logs.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

class AlertLog(Base):
    __tablename__ = "alert_logs"

    __table_args__ = (
        Index("ix_alert_logs_match_id", "match_id"),
        Index("ix_alert_logs_signal_log_id", "signal_log_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    signal_log_id: Mapped[int] = mapped_column(Integer, ForeignKey("trend_signal_logs.id"), nullable=False)
    alert_channel: Mapped[str] = mapped_column(String(50), nullable=False) # telegram, email
    alert_title: Mapped[str] = mapped_column(String(255), nullable=False)
    alert_body: Mapped[str] = mapped_column(Text, nullable=False)
    cooldown_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sent_success: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_from_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    dataset_to_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    config_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("backtest_runs.id"), nullable=False)
    signal_level: Mapped[str] = mapped_column(String(10), nullable=False) # S, A, B, C, Risk
    surface: Mapped[str] = mapped_column(String(50), nullable=False) # all, hard, clay, grass
    match_stage: Mapped[str] = mapped_column(String(50), nullable=False) # early, mid, late
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    hit_rate: Mapped[float] = mapped_column(Float, default=0.0)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    avg_trigger_game_no: Mapped[float] = mapped_column(Float, default=0.0)
    metrics_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class PlayerPhasePattern(Base):
    __tablename__ = "player_phase_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    surface: Mapped[str] = mapped_column(String(50), nullable=False)
    set_no: Mapped[int] = mapped_column(Integer, nullable=False)
    game_range: Mapped[str] = mapped_column(String(50), nullable=False) # e.g. "4-6"
    condition_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False) # e.g. broken, drop_3_games
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    event_rate: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_rate: Mapped[float] = mapped_column(Float, default=0.0)
    lift_value: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

class PlayerSequencePattern(Base):
    __tablename__ = "player_sequence_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    surface: Mapped[str] = mapped_column(String(50), nullable=False)
    pattern_type: Mapped[str] = mapped_column(String(100), nullable=False) # e.g. deuce_break, consecutive_points_dropped
    trigger_condition_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    next_event_type: Mapped[str] = mapped_column(String(100), nullable=False) # e.g. broken, collapse
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    hit_rate: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_rate: Mapped[float] = mapped_column(Float, default=0.0)
    lift_value: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

class ProcessedDate(Base):
    __tablename__ = "processed_dates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date_str: Mapped[str] = mapped_column(String(20), unique=True, nullable=False) # e.g. "2026-06-03"
    status: Mapped[str] = mapped_column(String(20), default="completed") # e.g. "completed", "failed"
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="queued", nullable=False)
    payload_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    progress_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    progress_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_job_runs_status_created", "status", "created_at"),
        Index("idx_job_runs_type_status", "job_type", "status"),
        Index("idx_job_runs_locked_at", "locked_at"),
    )



class PollBudget(Base):
    __tablename__ = "poll_budgets"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False)
