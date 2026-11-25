import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import settings
from loguru import logger

class EmailSender:
    def __init__(self):
        self.host = settings.SMTP_HOST
        self.port = settings.SMTP_PORT
        self.user = settings.SMTP_USER
        self.password = settings.SMTP_PASSWORD
        self.from_addr = settings.SMTP_FROM
        self.enabled = settings.ENABLE_EMAIL_ALERT

    def send_email(self, to_addr: str, subject: str, body_html: str) -> bool:
        """
        Sends HTML formatted email to recipient.
        If disabled, logs content locally.
        """
        if not self.enabled:
            logger.info(f"[MOCK EMAIL ALERT] To: {to_addr} | Subject: {subject}\nHTML Body:\n{body_html[:300]}...")
            return True

        if not all([self.host, self.port, self.from_addr]):
            logger.error("SMTP config incomplete: host, port, or from_address missing.")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = to_addr

        part = MIMEText(body_html, "html", "utf-8")
        msg.attach(part)

        try:
            with smtplib.SMTP(self.host, self.port) as server:
                server.ehlo()
                if self.port == 587:
                    server.starttls()
                    server.ehlo()
                if self.user and self.password:
                    server.login(self.user, self.password)
                server.sendmail(self.from_addr, [to_addr], msg.as_string())
            logger.info(f"Email sent successfully to {to_addr}")
            return True
        except Exception as e:
            logger.error(f"SMTP error sending email to {to_addr}: {e}")
            return False
        
    def send_alert(self, subject: str, html_body: str) -> bool:
        """Sends an urgent alert email to the configured email recipient."""
        return self.send_email(settings.ALERT_EMAIL_TO, subject, html_body)
