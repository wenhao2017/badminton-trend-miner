import httpx
from app.config import settings
from loguru import logger

class TelegramSender:
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.enabled = settings.ENABLE_TELEGRAM_ALERT

    def send_message(self, text: str) -> bool:
        """
        Sends formatted text message to the specified Telegram Chat.
        If disabled, logs the message contents locally.
        """
        if not self.enabled:
            logger.info(f"[MOCK TELEGRAM ALERT] Text content:\n{text}")
            return True

        if not self.bot_token or not self.chat_id:
            logger.error("Telegram enabled but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing.")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    logger.info("Telegram alert sent successfully.")
                    return True
                else:
                    logger.error(f"Telegram API error {resp.status_code}: {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"Failed to send Telegram request: {e}")
            return False
