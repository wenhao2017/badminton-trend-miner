from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as web
from app.config import settings


@pytest.fixture
def client(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    web.Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(web, "engine", engine)
    monkeypatch.setattr(settings, "WEB_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "WEB_AUTH_USERNAME", "test-user")
    monkeypatch.setattr(settings, "WEB_AUTH_PASSWORD", "test-password")
    monkeypatch.setattr(settings, "WEB_TURNSTILE_ENABLED", True)
    monkeypatch.setattr(settings, "WEB_TURNSTILE_SITE_KEY", "public-site-key")
    monkeypatch.setattr(settings, "WEB_TURNSTILE_SECRET_KEY", "private-secret-key")
    monkeypatch.setattr(settings, "WEB_TURNSTILE_HOSTNAME", "dashboard.example.com")
    # No lifespan startup: auth requests must not start the production scheduler.
    yield TestClient(web.app, follow_redirects=False)
    engine.dispose()


def test_login_page_exposes_only_public_key(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert 'data-sitekey="public-site-key"' in response.text
    assert 'data-action="dashboard_login"' in response.text
    assert "private-secret-key" not in response.text
    assert 'type="submit" disabled' in response.text


@pytest.mark.parametrize("token", ["", "x" * 2049])
def test_missing_or_oversized_token_cannot_login(client, token):
    response = client.post("/login", data={"username": "test-user", "password": "test-password", "cf-turnstile-response": token})
    assert "error=verification" in response.headers["location"]
    assert web.SESSION_COOKIE_NAME not in response.cookies


@pytest.mark.parametrize("verified, password, succeeds", [(False, "test-password", False), (True, "wrong", False), (True, "test-password", True)])
def test_login_requires_both_checks(client, monkeypatch, verified, password, succeeds):
    monkeypatch.setattr(web, "_verify_login_turnstile", AsyncMock(return_value=verified))
    response = client.post("/login", data={"username": "test-user", "password": password, "cf-turnstile-response": "token", "next": "//evil.example"})
    assert (web.SESSION_COOKIE_NAME in response.cookies) == succeeds
    if succeeds:
        assert response.headers["location"] == "/dashboard"
        assert "httponly" in response.headers["set-cookie"].lower()


@pytest.mark.parametrize("overrides, expected", [({}, True), ({"success": False}, False), ({"hostname": "other.example"}, False), ({"action": "other_form"}, False)])
@pytest.mark.asyncio
async def test_siteverify_checks_hostname_and_action(client, monkeypatch, overrides, expected):
    result = {"success": True, "hostname": "dashboard.example.com", "action": "dashboard_login", **overrides}
    post = AsyncMock(return_value=httpx.Response(200, json=result, request=httpx.Request("POST", "https://challenges.cloudflare.com/turnstile/v0/siteverify")))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    assert await web._verify_login_turnstile("token") is expected
    assert post.call_args.kwargs["data"] == {"secret": "private-secret-key", "response": "token"}


@pytest.mark.asyncio
async def test_network_failure_rejects_login(client, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectTimeout("unavailable")))
    assert await web._verify_login_turnstile("token") is False


def test_disabled_preserves_existing_login(client, monkeypatch):
    monkeypatch.setattr(settings, "WEB_TURNSTILE_ENABLED", False)
    page = client.get("/login")
    assert "challenges.cloudflare.com" not in page.text
    assert 'type="submit" disabled' not in page.text
    response = client.post("/login", data={"username": "test-user", "password": "test-password"})
    assert web.SESSION_COOKIE_NAME in response.cookies


def test_existing_sessions_and_health_do_not_require_challenge(client):
    client.cookies.set(web.SESSION_COOKIE_NAME, web._create_session_token("test-user"))
    assert client.get("/dashboard").status_code == 200
    assert client.get("/health").status_code == 200


def test_production_key_configuration_is_read_only_in_dashboard(client, monkeypatch):
    monkeypatch.setattr(settings, "WEB_MANAGE_SPORTRADAR_KEYS", False)
    client.cookies.set(web.SESSION_COOKIE_NAME, web._create_session_token("test-user"))
    assert client.post("/web/api/actions/keys-add", json={"keys": "ignored"}).status_code == 409
    assert client.post("/web/api/actions/keys-cleanup").status_code == 409
