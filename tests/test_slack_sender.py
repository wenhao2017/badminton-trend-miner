from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.config import settings
from app.services import slack_sender as slack
from app.services.alert_service import AlertService


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SLACK_ALERT", True)
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/TEST/CHANNEL/secret")


def mock_response(monkeypatch, status=200, text="ok"):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = httpx.Response(status, text=text)
    monkeypatch.setattr(slack.httpx, "Client", Mock(return_value=client))
    return client


def test_slack_mentions_channel_explicitly_without_parsing_mentions_in_body(configured, monkeypatch):
    client = mock_response(monkeypatch)
    assert slack.SlackSender().send_message("🎾 WTA 信号提醒\n方向：A &amp; B &lt;@U123&gt; &lt;!here&gt;\n详细信息请到网站查看")
    payload = client.post.call_args.kwargs["json"]
    assert payload["blocks"][0]["text"] == {
        "type": "mrkdwn", "text": "<!channel>", "verbatim": True}
    assert payload["blocks"][1]["text"] == {
        "type": "plain_text", "text": "🎾 WTA 信号提醒\n方向：A & B <@U123> <!here>\n详细信息请到网站查看", "emoji": True}
    assert payload["text"].startswith("<!channel>\n")
    assert payload["text"].count("<!channel>") == 1
    assert "<@U123>" not in payload["text"]
    assert "<!here>" not in payload["text"]
    assert payload["unfurl_links"] is False and payload["unfurl_media"] is False


@pytest.mark.parametrize("status,text", [(200, "false"), (200, "<html>proxy error</html>"), (403, "action_prohibited"), (429, "rate_limited")])
def test_http_200_alone_does_not_mean_slack_posted(configured, monkeypatch, status, text):
    mock_response(monkeypatch, status, text)
    assert not slack.SlackSender().send_message("signal")


@pytest.mark.parametrize("url", ["", "http://hooks.slack.com/services/A/B/C", "https://evil.example/services/A/B/C", "https://hooks.slack.com.evil.example/services/A/B/C", "https://user:secret@hooks.slack.com/services/A/B/C", "https://hooks.slack.com/services/A/B/C?token=extra"])
def test_invalid_webhook_never_makes_request(configured, monkeypatch, url):
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", url)
    factory = Mock()
    monkeypatch.setattr(slack.httpx, "Client", factory)
    assert not slack.SlackSender().send_message("signal")
    factory.assert_not_called()


def test_disabled_slack_is_not_reported_as_sent(configured, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SLACK_ALERT", False)
    assert not slack.SlackSender().send_message("signal")


def test_request_failure_does_not_leak_webhook_secret(configured, monkeypatch):
    client = mock_response(monkeypatch)
    client.post.side_effect = httpx.ConnectError(settings.SLACK_WEBHOOK_URL)
    lines = []
    sink = slack.logger.add(lambda message: lines.append(str(message)))
    try:
        assert not slack.SlackSender().send_message("signal")
    finally:
        slack.logger.remove(sink)
    assert "hooks.slack.com" not in "".join(lines)
    assert "/secret" not in "".join(lines)


@pytest.mark.parametrize("ok,channel", [(True, "slack"), (False, "telegram-fallback")])
def test_slack_route_and_telegram_fallback_are_recorded(configured, monkeypatch, ok, channel):
    monkeypatch.setattr(settings, "PRIMARY_ALERT_CHANNEL", "slack")
    monkeypatch.setattr(settings, "FALLBACK_ALERT_CHANNEL", "telegram")
    monkeypatch.setattr(settings, "ENABLE_TELEGRAM_ALERT", True)
    service = AlertService()
    service.slack.send_message = Mock(return_value=ok)
    service.telegram.send_message = Mock(return_value=True)
    service._format_telegram_message = Mock(return_value="compact signal")
    service._format_email_body = Mock(return_value="email")
    service.alert_repo = Mock()
    sig = SimpleNamespace(id=123, match_id=1, player_id=2, signal_level="S")
    assert service.trigger_alert(Mock(), sig)
    assert service.alert_repo.create.call_args.args[1].alert_channel == channel
    if ok:
        service.telegram.send_message.assert_not_called()
    else:
        service.telegram.send_message.assert_called_once_with("compact signal")


def test_multiple_slack_webhooks_receive_same_payload(configured, monkeypatch):
    url1 = "https://hooks.slack.com/services/T1/B1/secret1"
    url2 = "https://hooks.slack.com/services/T2/B2/secret2"
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", f" {url1} , {url2} , {url1} ")
    client = mock_response(monkeypatch)
    assert slack.SlackSender().send_message("🏸 BWF 信号提醒")
    assert client.post.call_count == 2
    first_call, second_call = client.post.call_args_list
    assert first_call.args[0] == url1
    assert second_call.args[0] == url2
    assert first_call.kwargs["json"] == second_call.kwargs["json"]


def test_multiple_slack_webhooks_continue_when_one_fails_without_leaking_secret(configured, monkeypatch):
    url1 = "https://hooks.slack.com/services/T1/B1/secret1"
    url2 = "https://hooks.slack.com/services/T2/B2/secret2"
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", f"{url1},{url2}")
    client = mock_response(monkeypatch)
    client.post.side_effect = [httpx.ConnectError(url1), httpx.Response(200, text="ok")]
    lines = []
    sink = slack.logger.add(lambda message: lines.append(str(message)))
    try:
        assert slack.SlackSender().send_message("signal")
    finally:
        slack.logger.remove(sink)
    assert client.post.call_count == 2
    assert "hooks.slack.com" not in "".join(lines)
    assert "secret1" not in "".join(lines)
    assert "secret2" not in "".join(lines)


def test_mixed_valid_and_invalid_webhooks_sends_to_valid_only(configured, monkeypatch):
    valid_url = "https://hooks.slack.com/services/T2/B2/secret2"
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", f"https://evil.example/services/A/B/C,{valid_url}")
    client = mock_response(monkeypatch)
    assert slack.SlackSender().send_message("signal")
    client.post.assert_called_once()
    assert client.post.call_args.args[0] == valid_url
