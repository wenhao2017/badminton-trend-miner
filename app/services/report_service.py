from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db.models import TrendSignalLog, ApiKeyUsage, Match
from app.services.email_sender import EmailSender
from loguru import logger

class ReportService:
    def __init__(self):
        self.email_sender = EmailSender()

    def generate_daily_report(self, db: Session) -> str:
        """
        Gathers match logs and API quotas for the past 24 hours,
        generating a detailed HTML summary report.
        """
        day_ago = datetime.utcnow() - timedelta(days=1)
        
        # 1. Fetch signal logs
        signals = db.query(TrendSignalLog).filter(TrendSignalLog.created_at >= day_ago).all()
        
        # 2. Fetch API key usage logs
        usages = db.query(ApiKeyUsage).all()
        
        # Format HTML
        signal_rows = []
        for s in signals:
            status = s.final_result or "Ongoing/Unknown"
            signal_rows.append(
                f"<tr>"
                f"<td>{s.created_at.strftime('%H:%M:%S')}</td>"
                f"<td>{s.match_id}</td>"
                f"<td>{s.signal_level}</td>"
                f"<td>{s.trend_confidence_score:.2f}</td>"
                f"<td>{s.collapse_risk_score:.2f}</td>"
                f"<td>{status}</td>"
                f"</tr>"
            )
        
        usage_rows = []
        for u in usages:
            usage_rows.append(
                f"<tr>"
                f"<td>{u.api_key_hash[:8]}...</td>"
                f"<td>{u.quota_used}</td>"
                f"<td>{u.quota_remaining}</td>"
                f"<td>{u.cooldown_until or 'No cooldown'}</td>"
                f"</tr>"
            )

        html_body = (
            f"<h2>Badminton Trend Miner - Daily Recap Report</h2>"
            f"<p>Report generated at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>"
            f"<h3>Today's Signals Triggered</h3>"
            f"<table border='1' cellpadding='5' style='border-collapse: collapse;'>"
            f"<thead><tr>"
            f"<th>Time</th><th>Match ID</th><th>Level</th><th>Confidence</th><th>Risk</th><th>Outcome</th>"
            f"</tr></thead>"
            f"<tbody>"
            f"{''.join(signal_rows) if signal_rows else '<tr><td colspan=6>No signals triggered today.</td></tr>'}"
            f"</tbody></table>"
            f"<h3>API Quota & Health Status</h3>"
            f"<table border='1' cellpadding='5' style='border-collapse: collapse;'>"
            f"<thead><tr>"
            f"<th>Key Hash</th><th>Calls Used</th><th>Remaining Quota</th><th>Cooldown Info</th>"
            f"</tr></thead>"
            f"<tbody>"
            f"{''.join(usage_rows) if usage_rows else '<tr><td colspan=4>No active API keys found.</td></tr>'}"
            f"</tbody></table>"
        )
        return html_body

    def email_daily_report(self, db: Session, recipient_email: str) -> bool:
        """Generates and emails the daily report HTML to recipient."""
        html_report = self.generate_daily_report(db)
        subject = f"🏸 BWF Trend Miner Daily Report - {datetime.utcnow().strftime('%Y-%m-%d')}"
        return self.email_sender.send_email(recipient_email, subject, html_report)
