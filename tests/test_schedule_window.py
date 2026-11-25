from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from app.services.schedule_window import ScheduleWindow


def at(hour, day=10):
    return datetime(2026, 11, day, hour, tzinfo=timezone.utc)

def entry(start, status="not_started", end=None):
    return {"sport_event": {"start_time": start.isoformat()},
            "sport_event_status": {"status": status, "end_time": end.isoformat() if end else None}}

def test_whole_day_span_without_tail(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DAILY_HISTORY_IMPORT_TIMEZONE", "UTC")
    w=ScheduleWindow()
    w.entries=[entry(at(4), "closed", at(5)), entry(at(22), "closed", at(23))]
    assert not w.evaluate(at(3))
    assert w.evaluate(at(4))
    assert w.evaluate(at(15))
    assert not w.evaluate(at(23))
    assert not w.evaluate(at(1,11))
    assert not w.evaluate(at(3,11))

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
