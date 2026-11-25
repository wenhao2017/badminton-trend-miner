from unittest.mock import Mock, patch

import pytest

from app.config import settings
from app.jobs.scheduler import JobScheduler


@pytest.mark.parametrize("hour, active", [(0, True), (3, True), (4, False), (8, False), (10, False), (11, True), (23, True)])
def test_scheduler_routes_off_hours_to_cleanup_only(monkeypatch, hour, active):
    monkeypatch.setattr(settings, "SPORTRADAR_API_KEYS", "fixture-key")
    monkeypatch.setattr(settings, "LIVE_MONITOR_START_HOUR", 11)
    monkeypatch.setattr(settings, "LIVE_MONITOR_END_HOUR", 3)
    monkeypatch.setattr(settings, "DAILY_HISTORY_IMPORT_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setattr(settings, "LIVE_MONITOR_SCHEDULE_ENABLED", False)
    session = Mock()
    monkeypatch.setattr("app.jobs.scheduler.SessionLocal", Mock(return_value=session))
    obj = JobScheduler.__new__(JobScheduler)
    obj.monitor = Mock()
    with patch("datetime.datetime") as clock:
        clock.now.return_value.hour = hour
        obj._execute_live_monitor_job()
    if active:
        obj.monitor.scan_and_update.assert_called_once_with(session)
        obj.monitor.finalize_tracked_matches.assert_not_called()
    else:
        obj.monitor.finalize_tracked_matches.assert_called_once_with(session)
        obj.monitor.scan_and_update.assert_not_called()
    session.close.assert_called_once()
