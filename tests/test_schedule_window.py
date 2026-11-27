from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from app.services.schedule_window import ScheduleWindow


def at(hour, day=10):
    return datetime(2026, 11, day, hour, tzinfo=timezone.utc)

def entry(start, status="not_started", end=None):
    return {"sport_event": {"start_time": start.isoformat()},
            "sport_event_status": {"status": status, "end_time": end.isoformat() if end else None}}

def test_per_match_window_sleeps_during_cross_continent_gaps(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DAILY_HISTORY_IMPORT_TIMEZONE", "Asia/Shanghai")
    w = ScheduleWindow()
    # Early-morning European match finished by 04:05 CST (20:05 UTC prev day),
    # next Asian match starts at 15:10 CST (07:10 UTC).
    w.entries = [
        entry(datetime(2026, 11, 26, 16, 10, tzinfo=timezone.utc), "closed",
              datetime(2026, 11, 26, 17, 0, tzinfo=timezone.utc)),
        entry(datetime(2026, 11, 27, 7, 10, tzinfo=timezone.utc), "not_started"),
    ]
    # Active during the earlier match interval
    assert w.evaluate(datetime(2026, 11, 26, 16, 30, tzinfo=timezone.utc))
    # Sleeping during the long empty gap (09:45 CST = 01:45 UTC)
    assert not w.evaluate(datetime(2026, 11, 27, 1, 45, tzinfo=timezone.utc))
    # Sleeping 21 minutes before next match
    assert not w.evaluate(datetime(2026, 11, 27, 6, 49, tzinfo=timezone.utc))
    # Wakes up 20 minutes before next match (14:50 CST = 06:50 UTC)
    assert w.evaluate(datetime(2026, 11, 27, 6, 50, tzinfo=timezone.utc))
    # Remains active if start time passed and match is still not finished
    assert w.evaluate(datetime(2026, 11, 27, 7, 30, tzinfo=timezone.utc))

def test_delay_never_closes_but_finish_has_no_tail():
    w=ScheduleWindow()
    w.entries=[entry(at(4), "delayed")]
    assert w.evaluate(at(18))
    w.entries=[entry(at(4), "closed")]
    assert not w.evaluate(at(18))
    assert not w.evaluate(at(20))
    assert not w.evaluate(at(22))

def test_tracked_match_survives_empty_schedule():
    w=ScheduleWindow()
    assert w.evaluate(at(4), tracked=True)
    assert not w.evaluate(at(6))
    assert not w.evaluate(at(8))

def test_schedule_error_keeps_discovery_on_and_throttles_retry():
    w=ScheduleWindow(); api=Mock()
    api.get_daily_schedule.side_effect=RuntimeError("unavailable")
    assert w.active(Mock(),api,at(4))
    assert w.active(Mock(),api,at(4))
    assert api.get_daily_schedule.call_count==1
    assert w.status()["error"]=="RuntimeError"

def test_schedule_filters_singles_and_reloads_three_utc_dates(db_session):
    w=ScheduleWindow(); api=Mock()
    valid=entry(at(4))
    valid["sport_event"].update(id="one",sport_event_context={"sport":{"id":"sr:sport:31"},"competition":{"type":"singles","gender":"women"}})
    api.get_daily_schedule.return_value={"summaries":[valid]}
    assert w.active(db_session,api,at(5))
    assert api.get_daily_schedule.call_count==3
    assert len(w.entries)==1
    w.active(db_session,api,at(5))
    assert api.get_daily_schedule.call_count==3

def test_scheduler_automatic_mode_overrides_fixed_hours(monkeypatch):
    from app.jobs.scheduler import JobScheduler
    from app.config import settings
    monkeypatch.setattr(settings,"SPORTRADAR_API_KEYS","fixture-key")
    monkeypatch.setattr(settings,"LIVE_MONITOR_SCHEDULE_ENABLED",True)
    monkeypatch.setattr(settings,"ENABLE_ADAPTIVE_LIVE_POLLING",False)
    session=Mock()
    monkeypatch.setattr("app.jobs.scheduler.SessionLocal",Mock(return_value=session))
    obj=JobScheduler.__new__(JobScheduler);obj.monitor=Mock();obj.schedule_window=Mock()
    obj.schedule_window.active.return_value=True
    obj._execute_live_monitor_job()
    obj.monitor.scan_and_update.assert_called_once_with(session)
    obj.monitor.finalize_tracked_matches.assert_not_called()


def test_schedule_cache_age_rejects_old_db_and_redis_values(db_session):
    from datetime import timedelta
    import pytest
    from app.api_clients.sportradar_client import SportradarClient
    from app.db.models import RawProviderResponse
    c=SportradarClient.__new__(SportradarClient)
    c.cache=Mock();c.cache.get.return_value="stale redis"
    c.log_repo=Mock()
    c._check_circuit_breaker=Mock(side_effect=RuntimeError("network boundary"))
    now=datetime.now(timezone.utc).replace(tzinfo=None)
    record=RawProviderResponse(provider="sportradar",endpoint="/schedule",params_hash=c._generate_params_hash({}),
        body_json={"summaries":[]},fetched_at=now-timedelta(hours=1),expires_at=now+timedelta(hours=23),
        status_code=200,source_key_hash="test",payload_hash="test")
    db_session.add(record);db_session.commit()
    with pytest.raises(RuntimeError,match="network boundary"):
        c.get(db_session,"/schedule",max_cache_age_seconds=900)
    c.cache.get.assert_not_called()
    record.fetched_at=now
    db_session.commit()
    assert "summaries" in c.get(db_session,"/schedule",max_cache_age_seconds=900)



def test_empty_live_feed_still_cleans_tracked_match(db_session, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "SPORTRADAR_API_KEYS", "fixture-key")
    from app.services.live_monitor import LiveMonitor
    from app.db.models import Match
    m=Match(external_match_id="empty-feed-test", provider="test", tournament="Test",tournament_id="test",
            match_date=at(4).replace(tzinfo=None),surface="hard",status="live")
    db_session.add(m);db_session.commit()
    monitor=LiveMonitor.__new__(LiveMonitor)
    monitor.live_client=Mock()
    monitor.live_client.get_live_matches.return_value={"summaries":[]}
    monitor.importer=Mock()
    monitor.scan_and_update(db_session)
    monitor.live_client.get_match_summary.assert_called_once_with(db_session,"empty-feed-test")


def test_cross_midnight_unfinished_match_stays_active_until_finished():
    w=ScheduleWindow()
    w.entries=[entry(at(23), "live")]
    assert w.evaluate(at(4,11))
    w.entries=[entry(at(23), "closed", at(4,11))]
    assert not w.evaluate(at(4,11))
    assert not w.evaluate(at(5,11))
    assert w.status()["tail_hours"] == 0


def test_schedule_sync_populates_upcoming_matches_for_live_board(db_session, monkeypatch):
    from app.config import settings
    from app.web.dashboard import web_live_matches
    monkeypatch.setattr(settings, "DAILY_HISTORY_IMPORT_TIMEZONE", "Asia/Shanghai")
    w = ScheduleWindow()
    api = Mock()
    scheduled_match = {
        "sport_event": {
            "id": "sr:sport_event:77212834",
            "start_time": "2026-11-27T07:10:00+00:00",
            "sport_event_context": {
                "sport": {"id": "sr:sport:31"},
                "competition": {"id": "sr:competition:27256", "name": "Syed Modi India International, WS", "type": "singles", "gender": "women", "level": "super 300"},
                "round": {"name": "quarterfinal"},
            },
            "competitors": [
                {"id": "sr:competitor:1", "name": "Gunji, Riko", "qualifier": "home"},
                {"id": "sr:competitor:2", "name": "Mir, Tasnim", "qualifier": "away"},
            ],
        },
        "sport_event_status": {"status": "not_started", "match_status": "not_started"},
    }
    api.get_daily_schedule.return_value = {"summaries": [scheduled_match]}
    now = datetime(2026, 11, 27, 1, 45, tzinfo=timezone.utc)
    assert not w.active(db_session, api, now)
    board = web_live_matches(date="2026-11-27", db=db_session)
    assert len(board["matches"]) == 1
    assert board["matches"][0]["status"] == "not_started"
    assert board["matches"][0]["match_date"] == "2026-11-27T07:10:00Z"


def test_dst_offsets_and_utc_serialization():
    from app.services.schedule_window import parse_time
    from app.web.dashboard import _dt, _local_day_utc_range
    # Summer CEST (+02:00) vs Winter CET (+01:00) normalize to exact UTC instants
    assert parse_time("2026-07-10T14:00:00+02:00") == datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    assert parse_time("2026-11-27T14:00:00+01:00") == datetime(2026, 11, 27, 13, 0, tzinfo=timezone.utc)
    # Naive UTC datetimes from SQLite always serialize with Z suffix
    assert _dt(datetime(2026, 11, 27, 7, 10, 0)) == "2026-11-27T07:10:00Z"
    assert _dt(datetime(2026, 7, 10, 14, 0, tzinfo=timezone(timedelta(hours=2)))) == "2026-07-10T12:00:00Z"
    # Asia/Shanghai day range is strictly 16:00Z previous day to 16:00Z current day in both summer & winter
    _, s_win, e_win = _local_day_utc_range("2026-11-27")
    assert (s_win, e_win) == (datetime(2026, 11, 26, 16, 0), datetime(2026, 11, 27, 16, 0))
    _, s_sum, e_sum = _local_day_utc_range("2026-07-10")
    assert (s_sum, e_sum) == (datetime(2026, 7, 9, 16, 0), datetime(2026, 7, 10, 16, 0))
