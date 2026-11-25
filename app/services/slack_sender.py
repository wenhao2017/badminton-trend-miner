"""Slack Incoming Webhook delivery of the shared compact signal message."""
from html import escape, unescape
import re
from urllib.parse import urlparse

import httpx
from loguru import logger

from app.config import settings


class SlackSender:
    def send_message(self, text: str) -> bool:
        if not settings.ENABLE_SLACK_ALERT:
            logger.warning("Slack alerts disabled; message not sent")
            return False
        try:
            url = urlparse(settings.SLACK_WEBHOOK_URL)
            valid = (url.scheme == "https" and url.hostname in ("hooks.slack.com", "hooks.slack-gov.com")
                     and url.port in (None, 443) and not url.username and not url.password
                     and not url.query and not url.fragment
                     and re.fullmatch(r"/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", url.path))
        except ValueError:
            valid = False
        if not valid:
            logger.error("Slack webhook missing or invalid")
            return False
        # The shared formatter returns HTML-escaped plain text, not Slack markup.
        content = unescape(text).strip()
        if not content or len(content) > 3000:
            logger.error("Slack compact message is empty or exceeds the section text limit")
            return False
        payload = {
            "text": "<!channel>\n" + escape(content, quote=False),
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": "<!channel>", "verbatim": True}},
                {"type": "section", "text": {"type": "plain_text", "text": content, "emoji": True}},
            ],
            "unfurl_links": False,
            "unfurl_media": False,
        }
        try:
            with httpx.Client(timeout=10, trust_env=False) as client:
                response = client.post(settings.SLACK_WEBHOOK_URL, json=payload)
            if response.status_code == 200 and response.text.strip() == "ok":
                logger.info("Slack webhook confirmed message posted")
                return True
            logger.error("Slack webhook rejected notification (HTTP {})", response.status_code)
            return False
        except Exception as exc:
            # HTTP exceptions include request URLs, which contain the webhook secret.
            logger.error("Slack notification failed ({})", type(exc).__name__)
            return False
