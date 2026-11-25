"""Slack Incoming Webhook delivery of the shared compact signal message."""
from html import escape, unescape
import re
from urllib.parse import urlparse

import httpx
from loguru import logger

from app.config import settings


class SlackSender:
    @staticmethod
    def _is_valid_webhook_url(raw_url: str) -> bool:
        try:
            url = urlparse(raw_url)
            return bool(
                url.scheme == "https"
                and url.hostname in ("hooks.slack.com", "hooks.slack-gov.com")
                and url.port in (None, 443)
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and re.fullmatch(r"/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", url.path)
            )
        except ValueError:
            return False

    @classmethod
    def _parse_webhook_urls(cls, raw: str) -> list[str]:
        candidates = [item.strip() for item in re.split(r"[,\r\n]+", raw or "") if item.strip()]
        valid_urls: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not cls._is_valid_webhook_url(candidate):
                logger.error("Slack webhook missing or invalid")
                continue
            if candidate not in seen:
                seen.add(candidate)
                valid_urls.append(candidate)
        return valid_urls

    def send_message(self, text: str) -> bool:
        if not settings.ENABLE_SLACK_ALERT:
            logger.warning("Slack alerts disabled; message not sent")
            return False
        webhook_urls = self._parse_webhook_urls(settings.SLACK_WEBHOOK_URL)
        if not webhook_urls:
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
        succeeded = 0
        total = len(webhook_urls)
        with httpx.Client(timeout=10, trust_env=False) as client:
            for index, webhook_url in enumerate(webhook_urls, start=1):
                try:
                    response = client.post(webhook_url, json=payload)
                    if response.status_code == 200 and response.text.strip() == "ok":
                        succeeded += 1
                    else:
                        logger.error(
                            "Slack webhook #{}/{} rejected notification (HTTP {})",
                            index,
                            total,
                            response.status_code,
                        )
                except Exception as exc:
                    # HTTP exceptions include request URLs, which contain the webhook secret.
                    logger.error(
                        "Slack webhook #{}/{} notification failed ({})",
                        index,
                        total,
                        type(exc).__name__,
                    )
        if succeeded:
            logger.info("Slack webhook confirmed message posted ({}/{})", succeeded, total)
            return True
        return False
