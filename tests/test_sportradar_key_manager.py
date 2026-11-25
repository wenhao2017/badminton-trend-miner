import pytest
from app.api_clients.sportradar_key_manager import SportradarKeyManager, KeysExhaustedException
from app.db.models import ApiKeyUsage
from datetime import datetime, timedelta
from app.config import settings
from app.utils.security import hash_api_key


@pytest.fixture(autouse=True)
def isolated_key_config(tmp_path, monkeypatch):
    # Retirement writes .env: never allow tests to touch real credentials.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "SPORTRADAR_API_KEYS", "")
    monkeypatch.setattr(settings, "SPORTRADAR_KEY_TOTAL_QUOTA", 1000)

def test_key_manager_quota_rotation(db_session):
    manager = SportradarKeyManager()
    
    # Configure custom short keys list for testing
    manager.keys = ["test_key_1", "test_key_2"]
    
    # Get active key (should be key 1)
    k1 = manager.get_active_key(db_session)
    assert k1 == "test_key_1"
    
    # Consume all quota for key 1
    manager.record_usage(db_session, "test_key_1", increment=1000)
    
    # Get active key (should rotate to key 2)
    k2 = manager.get_active_key(db_session)
    assert k2 == "test_key_2"

def test_key_manager_cooldown(db_session):
    manager = SportradarKeyManager()
    manager.keys = ["test_key_1", "test_key_2"]
    
    # Put key 1 on cooldown
    manager.mark_cooldown(db_session, "test_key_1", minutes=5)
    
    # Should automatically yield key 2
    k = manager.get_active_key(db_session)
    assert k == "test_key_2"

def test_key_manager_exhaustion(db_session):
    manager = SportradarKeyManager()
    manager.keys = ["test_key_1"]
    
    manager.record_usage(db_session, "test_key_1", increment=1000)
    
    with pytest.raises(KeysExhaustedException):
        manager.get_active_key(db_session)


def test_key_manager_verification_and_cleanup(db_session, monkeypatch):
    manager = SportradarKeyManager()
    manager.keys = ["test_key_active", "test_key_exhausted"]
    
    # Create DB entry for active key with 1000 remaining
    usage_active = manager._get_or_create_usage(db_session, "test_key_active")
    assert usage_active.quota_remaining == 1000
    
    # Create DB entry for exhausted key with 0 remaining
    usage_exhausted = manager._get_or_create_usage(db_session, "test_key_exhausted")
    # Simulate a legacy exhausted row left by the old implementation.
    usage_exhausted.quota_used = 1000
    usage_exhausted.quota_remaining = 0
    db_session.commit()
    
    # Create an obsolete key in database that is not in settings manager.keys
    usage_obsolete = ApiKeyUsage(
        provider="sportradar",
        api_key_hash="some_old_obsolete_hash",
        quota_limit=1000,
        quota_used=0,
        quota_remaining=1000
    )
    db_session.add(usage_obsolete)
    db_session.commit()
    
    # Mock _remove_keys_from_env_file to avoid modifying actual .env during test
    removed_keys = []
    def mock_remove(keys_set):
        removed_keys.extend(keys_set)
    monkeypatch.setattr(manager, "_remove_keys_from_env_file", mock_remove)
    
    # Mock httpx.Client.get to return a mock response with status 200 to keep the key active
    import httpx
    class MockResponse:
        status_code = 200
        text = "ok"
    def mock_get(self_client, url, *args, **kwargs):
        return MockResponse()
    monkeypatch.setattr(httpx.Client, "get", mock_get)
    
    # Run cleanup
    res = manager.verify_and_cleanup_keys(db_session)
    
    # Assert obsolete key and exhausted key were deleted from database
    assert res["deleted_count"] == 2
    assert any("Obsolete" in d for d in res["details"])
    assert any("Exhausted" in d for d in res["details"])
    
    # Verify DB has only the active key left
    remaining = db_session.query(ApiKeyUsage).all()
    assert len(remaining) == 1
    assert remaining[0].api_key_hash == usage_active.api_key_hash
    
    # Verify exhausted key was requested to be removed from env file
    assert "test_key_exhausted" in removed_keys


def test_old_key_is_preferred_over_full_new_key(db_session):
    manager = SportradarKeyManager()
    manager.keys = ["new", "old"]
    old = manager._get_or_create_usage(db_session, "old")
    old.created_at = datetime.utcnow() - timedelta(days=20)
    old.quota_used = 930
    old.quota_remaining = 70
    db_session.commit()
    manager._get_or_create_usage(db_session, "new")
    assert manager.get_active_key(db_session) == "old"
    manager.record_usage(db_session, "old")
    assert manager.get_active_key(db_session) == "old"
    assert old.cooldown_until is None


def test_old_key_returns_after_actual_cooldown(db_session):
    manager = SportradarKeyManager()
    manager.keys = ["old", "new"]
    old = manager._get_or_create_usage(db_session, "old")
    old.created_at = datetime.utcnow() - timedelta(days=10)
    manager.mark_cooldown(db_session, "old")
    assert manager.get_active_key(db_session) == "new"
    old.cooldown_until = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert manager.get_active_key(db_session) == "old"


def test_last_call_removes_key_and_preserves_other_config(db_session, tmp_path):
    env = tmp_path / ".env"
    env.write_text('OTHER_SETTING=keep\nSPORTRADAR_API_KEYS="old,new"\n', encoding="utf-8")
    manager = SportradarKeyManager()
    manager.record_usage(db_session, "old", increment=999)
    manager.record_usage(db_session, "old")
    assert manager.repo.get_by_key_hash(db_session, hash_api_key("old")) is None
    assert manager.keys == ["new"]
    assert "OTHER_SETTING=keep" in env.read_text(encoding="utf-8")
    # Simulate a fresh process with stale Docker environment variables.
    settings.SPORTRADAR_API_KEYS = "old,new"
    other_process = SportradarKeyManager()
    assert other_process.keys == ["new"]
    # Completion/retry of an in-flight request must not resurrect the old row.
    other_process.record_usage(db_session, "old")
    other_process.mark_cooldown(db_session, "old")
    assert other_process.repo.get_by_key_hash(db_session, hash_api_key("old")) is None
    assert other_process.get_active_key(db_session) == "new"


def test_remote_exhaustion_removes_key_idempotently(db_session):
    manager = SportradarKeyManager()
    manager.keys = ["old", "new"]
    manager._get_or_create_usage(db_session, "old")
    manager.mark_exhausted(db_session, "old")
    manager.mark_exhausted(db_session, "old")
    assert manager.keys == ["new"]
    assert manager.repo.get_by_key_hash(db_session, hash_api_key("old")) is None


def test_legacy_zero_quota_is_cleaned_on_selection(db_session):
    manager = SportradarKeyManager()
    manager.keys = ["old", "new"]
    old = manager._get_or_create_usage(db_session, "old")
    old.quota_remaining = 0
    db_session.commit()
    assert manager.get_active_key(db_session) == "new"
    assert manager.repo.get_by_key_hash(db_session, hash_api_key("old")) is None
    assert manager.keys == ["new"]


def test_removing_last_key_does_not_fall_back_to_stale_environment(db_session):
    manager = SportradarKeyManager()
    manager.keys = ["last"]
    manager.record_usage(db_session, "last", increment=1000)
    settings.SPORTRADAR_API_KEYS = "last"
    assert manager.keys == []
    assert db_session.query(ApiKeyUsage).count() == 0
    with pytest.raises(KeysExhaustedException):
        manager.get_active_key(db_session)


def test_failed_config_write_retains_zero_usage_for_retry(db_session, monkeypatch):
    manager = SportradarKeyManager()
    manager.keys = ["old", "new"]
    def fail_write(*args, **kwargs):
        raise OSError("read-only config")
    with monkeypatch.context() as patch:
        patch.setattr("app.api_clients.sportradar_key_manager.set_key", fail_write)
        with pytest.raises(OSError):
            manager.record_usage(db_session, "old", increment=1000)
    old = manager.repo.get_by_key_hash(db_session, hash_api_key("old"))
    assert old is not None
    assert old.quota_remaining == 0
    assert manager.get_active_key(db_session) == "new"
    assert manager.repo.get_by_key_hash(db_session, hash_api_key("old")) is None


@pytest.mark.parametrize("status, body", [(403, "QPS limit exceeded"), (429, "rate limit exceeded")])
def test_remote_cleanup_does_not_delete_rate_limited_keys(db_session, monkeypatch, status, body):
    manager = SportradarKeyManager()
    manager.keys = ["limited"]
    manager._get_or_create_usage(db_session, "limited")
    class Response:
        status_code = status
        text = body
    monkeypatch.setattr("httpx.Client.get", lambda *args, **kwargs: Response())
    result = manager.verify_and_cleanup_keys(db_session, force_remote=True)
    assert result["deleted_count"] == 0
    assert manager.keys == ["limited"]
    assert db_session.query(ApiKeyUsage).count() == 1

