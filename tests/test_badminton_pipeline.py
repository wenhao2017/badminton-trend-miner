from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app.api_clients.sportradar_badminton_api import SportradarBadmintonApi
from app.config import settings
from app.db.models import Match, MatchLiveSnapshot, MatchPlayerStat, MatchPointEvent, TrendModel, TrendSignalLog, TrendTrainingSample
from app.services.backtest_engine import BacktestEngine
from app.services.badminton_timeline import parse_timeline
from app.services.bwf_candidate import FEATURE_COLUMNS, MODEL_TYPE, POLICY
from app.services.official_s import model_for_match
from app.services.feature_engineering import FeatureExtractor
from app.services.historical_importer import HistoricalImporter
from app.services.live_monitor import LiveMonitor
from app.services.player_profile_builder import PlayerProfileBuilder
from app.services.score_display import latest_match_score
from app.services.sportradar_importer import SportradarImporter
from app.utils.sportradar_event import is_womens_singles_event


def fixture_match(day, event_id=1, target=21, live_rallies=None, winner="home"):
    left, right = ("home", "away") if winner == "home" else ("away", "home")
    sides = {"home": "sr:competitor:101", "away": "sr:competitor:102"}
    event = {
        "id": f"sr:sport_event:{event_id}", "start_time": day.isoformat(),
        "sport_event_context": {"sport": {"id": "sr:sport:31"},
                                "competition": {"id": "sr:competition:10", "name": "Test Open Super 300, WS",
                                                "type": "singles", "gender": "women"},
                                "stage": {"phase": "ws"}, "round": {"name": "round_of_32"}},
        "competitors": [{"qualifier": side, "name": side, "id": pid} for side, pid in sides.items()],
        "coverage": {"live": True},
    }
    points = []
    scores = []
    for game in (1, 2):
        points.append({"type": "period_start", "period_name": f"{game}{'st' if game == 1 else 'nd'}_set"})
        score = {"home": 0, "away": 0}
        sequence = [left, right] * 5 + [left] * (target - 5)
        if game == 1 and live_rallies is not None:
            sequence = sequence[:live_rallies]
        for side in sequence:
            score[side] += 1
            index = len(points)
            points.append({"id": index, "type": "score_change", "competitor": side,
                           "time": (day + timedelta(seconds=index)).isoformat(),
                           **{f"{key}_score": score[key] for key in sides}})
        if live_rallies is None:
            points.append({"id": len(points), "type": "period_score", "period": str(game),
                           **{f"{key}_score": score[key] for key in sides}})
        scores.append({"number": game, **{f"{key}_score": score[key] for key in sides}})
        if live_rallies is not None:
            break
    status = {"status": "live" if live_rallies is not None else "closed",
              f"{left}_score": 0 if live_rallies is not None else 2, f"{right}_score": 0,
              "period_scores": scores}
    if live_rallies is None:
        status["winner_id"] = sides[winner]
    return {"sport_event": event, "sport_event_status": status, "timeline": points}


@pytest.mark.parametrize(("year", "target"), [(2026, 21), (2027, 15)])
def test_import_replay_labels_and_profiles(db_session, year, target):
    payload = fixture_match(datetime(year, 11, 25, tzinfo=timezone.utc), target=target)
    importer = SportradarImporter()
    match = importer.import_match(db_session, payload)
    assert match.scoring_target == target
    assert match.tournament_level == "Super 300"
    assert importer.import_timeline(db_session, match.id, payload) == 2 * (target + 5)
    assert importer.import_timeline(db_session, match.id, payload) == 0
    assert match.timeline_complete and not match.timeline_ready
    assert db_session.query(MatchPointEvent).count() == 2 * (target + 5)
    first = db_session.query(MatchPointEvent).filter_by(point_index=1).one()
    second = db_session.query(MatchPointEvent).filter_by(point_index=2).one()
    assert not first.raw_event_json["serve_known"]
    assert second.server_player_id == first.point_winner_player_id
    assert latest_match_score(db_session, match.id, match.winner_player_id)["games"] == [2, 0]

    history = HistoricalImporter()
    history.build_training_data(db_session, match)
    history.build_training_data(db_session, match)
    samples = db_session.query(TrendTrainingSample).all()
    assert len(samples) == (2 * (target + 5) - settings.MIN_POINTS_FOR_SIGNAL) * 2
    assert sum(s.label_win_match for s in samples) * 2 == len(samples)
    assert all(s.feature_json["scoring_target"] == target for s in samples)
    assert all("first_serve_win_rate" not in s.feature_json for s in samples)
    assert db_session.query(MatchPlayerStat).count() == 2
    assert PlayerProfileBuilder().rebuild_profiles(db_session) == 2


def test_gap_disables_confirmation_and_subsequent_clean_feed_recovers(db_session):
    payload = fixture_match(datetime(2026, 11, 25, tzinfo=timezone.utc), live_rallies=16)
    importer = SportradarImporter()
    match = importer.import_match(db_session, payload)
    importer.import_timeline(db_session, match.id, payload)
    gap = deepcopy(payload)
    gap["timeline"].pop(6)
    with pytest.raises(ValueError, match="Nonconsecutive"):
        parse_timeline(gap, 21)
    importer.import_timeline(db_session, match.id, gap)
    assert not match.timeline_ready and db_session.query(MatchPointEvent).count() == 16
    importer.import_timeline(db_session, match.id, payload)
    assert match.timeline_ready


def test_confirm_once_and_settle_with_frozen_reference(db_session, monkeypatch):
    monkeypatch.setattr(settings, "SPORTRADAR_API_KEYS", "fixture-key")
    monkeypatch.setattr(settings, "OFFICIAL_S_ENABLED", True)
    monkeypatch.setattr(settings, "MIN_PRIOR_MATCHES_FOR_S", 0)
    day = datetime.now(timezone.utc) - timedelta(seconds=30)
    monkeypatch.setattr(settings, "SCORING_TARGET_OVERRIDE", 21)
    live = fixture_match(day, live_rallies=16)
    db_session.add(TrendModel(model_name="test", model_type="test", target_label="label_win_match",
                              scoring_target=21, feature_columns_json={"columns": FeatureExtractor.feature_columns},
                              metrics_json={}, model_path="test", trained_from_date=datetime(2024, 1, 1),
                              trained_to_date=day.replace(tzinfo=None) - timedelta(days=3)))
    db_session.commit()
    monitor = LiveMonitor()
    monitor.live_client = Mock()
    monitor.live_client.get_live_matches.return_value = {"summaries": [live]}
    monitor.live_client.get_match_timeline.return_value = live
    monitor.predictor.predict_batch = lambda db, snapshots, **kwargs: [
        0.95 if s.player_points_in_game > s.opponent_points_in_game else 0.05 for s in snapshots]
    monitor.scan_and_update(db_session)
    match = db_session.query(Match).one()
    signal = db_session.query(TrendSignalLog).one()
    frozen = deepcopy(signal.reason_json)
    assert signal.official_match_id == match.id and signal.final_result is None
    monitor.scan_and_update(db_session)
    assert db_session.query(TrendSignalLog).count() == 1

    closed = fixture_match(day)
    monitor.live_client.get_match_timeline.return_value = closed
    monitor.live_client.get_live_matches.return_value = {"summaries": [closed]}
    monitor.scan_and_update(db_session)
    db_session.refresh(signal)
    assert signal.final_result == "success" and signal.reason_json == frozen
    assert db_session.get(MatchLiveSnapshot, signal.snapshot_id) is not None
    assert db_session.query(TrendTrainingSample).count() > 0

    monkeypatch.setattr("app.services.trend_predictor.TrendPredictor.predict_batch",
                        lambda self, *args, **kwargs: monitor.predictor.predict_batch(*args, **kwargs))
    run = BacktestEngine().run_backtest(db_session, "fixture", day.date().isoformat(), day.date().isoformat())
    assert run.metrics_json["total_signals_triggered"] == 1
    assert db_session.query(TrendSignalLog).count() == 1


def test_status_only_terminal_void_without_rallies(db_session, monkeypatch):
    monkeypatch.setattr(settings, "SPORTRADAR_API_KEYS", "fixture-key")
    payload = fixture_match(datetime(2026, 11, 25, tzinfo=timezone.utc), live_rallies=15)
    payload["sport_event_status"].update(status="closed", match_status="retired", winner_id="sr:competitor:101")
    monitor = LiveMonitor()
    monitor.live_client = Mock()
    monitor.live_client.get_live_matches.return_value = {"summaries": [payload]}
    monitor.scan_and_update(db_session)
    assert db_session.query(Match).one().status == "retired"
    assert db_session.query(MatchPointEvent).count() == 0
    monitor.live_client.get_match_timeline.assert_not_called()


def test_candidate_21_point_publication_and_model_isolation(db_session, monkeypatch):
    monkeypatch.setattr(settings, "OFFICIAL_S_ENABLED", True)
    monkeypatch.setattr(settings, "OFFICIAL_S_21_POLICY", POLICY)
    day = datetime.now(timezone.utc) - timedelta(seconds=36)
    importer = SportradarImporter()
    for i in range(10):
        history = fixture_match(day - timedelta(days=i + 2), event_id=100 + i)
        previous = importer.import_match(db_session, history)
        importer.import_timeline(db_session, previous.id, history)
    saved = TrendModel(model_name="candidate", model_type=MODEL_TYPE, target_label="label_win_match",
                       scoring_target=21, feature_columns_json={"columns": FEATURE_COLUMNS},
                       metrics_json={}, model_path="fixture", trained_from_date=datetime(2025, 1, 1),
                       trained_to_date=day.replace(tzinfo=None) - timedelta(days=2))
    db_session.add(saved)
    db_session.commit()

    closed = fixture_match(day)
    monitor = LiveMonitor()
    def predict(db, snapshots, *, model_record):
        assert model_record.id == saved.id
        for snapshot in snapshots:
            assert snapshot.raw_snapshot_json["prior_matches"] == 10
            assert set(FEATURE_COLUMNS) == set(snapshot.raw_snapshot_json["features"])
        return [.95 if snapshot.player_sets == 1 else .05 for snapshot in snapshots]
    monitor.predictor.predict_batch = predict

    for total in (29, 30, 31, 32):
        payload = deepcopy(closed)
        points = [event for event in payload["timeline"] if event["type"] == "score_change"]
        last = points[total - 1]
        payload["timeline"] = payload["timeline"][:payload["timeline"].index(last) + 1]
        payload["sport_event_status"] = {
            "status": "live", "home_score": 1, "away_score": 0,
            "period_scores": [closed["sport_event_status"]["period_scores"][0],
                              {"number": 2, "home_score": last["home_score"], "away_score": last["away_score"]}],
        }
        match = importer.import_match(db_session, payload)
        importer.import_timeline(db_session, match.id, payload)
        monitor._live_points(db_session, match)
        assert db_session.query(TrendSignalLog).count() == int(total >= 31)
    signal = db_session.query(TrendSignalLog).one()
    assert signal.reason_json["official_s_confirmation"]["confirmation"]["support_points"] == [30, 31]
    assert signal.reason_json["official_s_confirmation"]["published_point"] == 31
    assert signal.trend_confidence_score == .95
    assert signal.collapse_risk_score == pytest.approx(.05)

    monkeypatch.setattr(settings, "SCORING_TARGET_OVERRIDE", 15)
    another = importer.import_match(db_session, fixture_match(day, event_id=70, target=15))
    assert model_for_match(db_session, another) is None


def test_womens_singles_and_daily_summary_pagination(db_session):
    event = fixture_match(datetime(2026, 11, 25, tzinfo=timezone.utc))["sport_event"]
    assert is_womens_singles_event(event)
    event["sport_event_context"]["competition"]["type"] = "doubles"
    assert not is_womens_singles_event(event)
    api = SportradarBadmintonApi()
    import json
    page = lambda start, stop: json.dumps({"summaries": [{"sport_event": {"id": str(i)}} for i in range(start, stop)]})
    api.client.get = Mock(side_effect=[page(0, 200), page(200, 205)])
    assert len(api.get_daily_schedule(db_session, "2026-11-25")["summaries"]) == 205
    call = api.client.get.call_args
    assert call.args[1] == "/en/schedules/2026-11-25/summaries.json"
    assert call.kwargs["params"] == {"offset": 200, "limit": 200}



def test_sportradar_v2_request_uses_https_and_key_header(db_session, monkeypatch):
    import httpx

    monkeypatch.setattr(settings, "SPORTRADAR_API_KEYS", "fixture-key")
    monkeypatch.setattr(settings, "SPORTRADAR_ACCESS_LEVEL", "production")
    monkeypatch.setattr(settings, "SPORTRADAR_BASE_URL", "")
    api = SportradarBadmintonApi()
    original_client = httpx.Client

    def respond(request):
        assert str(request.url) == (
            "https://api.sportradar.com/badminton/production/v2/en/sport_events/sr:sport_event:501/timeline.json"
        )
        assert request.headers["x-api-key"] == "fixture-key"
        return httpx.Response(200, json={"timeline": []})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr("app.api_clients.sportradar_client.httpx.Client",
                        lambda **kwargs: original_client(transport=transport, **kwargs))
    assert api.get_match_timeline(db_session, "sr:sport_event:501", refresh=True) == {"timeline": []}


@pytest.mark.parametrize("target, cap", [(15, 21), (21, 30)])
def test_win_by_two_and_scoring_cap(target, cap):
    from app.utils.badminton_score import game_won

    assert game_won(target, target - 2, target)
    assert not game_won(target, target - 1, target)
    assert not game_won(cap - 1, cap - 2, target)
    assert game_won(cap, cap - 1, target)


def test_provider_period_rallies_ignore_match_total_events(db_session):
    payload = fixture_match(datetime(2026, 1, 6, tzinfo=timezone.utc))
    timeline = []
    game = 0
    for item in payload["timeline"]:
        event = item.copy()
        if event["type"] == "period_start":
            game += 1
        if event["type"] == "score_change":
            if event["home_score"] == 21:
                timeline.append({"type": "score_change", "home_score": game, "away_score": 0,
                                 "competitor": "home", "time": event["time"]})
            event.update(type="period_score", period=str(game))
        timeline.append(event)
    payload["timeline"] = timeline

    importer = SportradarImporter()
    match = importer.import_match(db_session, payload)
    assert importer.import_timeline(db_session, match.id, payload) == 52
    assert match.timeline_complete
