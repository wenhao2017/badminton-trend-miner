from html import escape
from sqlalchemy.orm import Session
from app.db.models import TrendSignalLog, AlertLog, Match, Player, MatchLiveSnapshot
from app.db.repositories import AlertLogRepository
from app.services.telegram_sender import TelegramSender
from app.services.slack_sender import SlackSender
from app.services.email_sender import EmailSender
from app.services.signal_history_context import player_inactivity_warning
from app.services.score_display import snapshot_score_before
from app.config import settings
from loguru import logger

class AlertService:
    def __init__(self):
        self.telegram = TelegramSender()
        self.slack = SlackSender()
        self.email = EmailSender()
        self.alert_repo = AlertLogRepository()

    def _zh_surface(self, surface: str | None) -> str:
        return {
            "hard": "硬地",
            "clay": "红土",
            "grass": "草地",
            "indoor": "室内",
            "all": "全部场地",
        }.get(surface or "", surface or "未知")

    def _zh_level(self, level: str | None) -> str:
        if level == "Risk":
            return "风险"
        return level or "未知"

    def _format_telegram_message(self, db: Session, sig: TrendSignalLog, is_warning_alert: bool = False) -> str:
        """One compact HTML-safe body shared by Telegram, Weixin and email."""
        match = db.query(Match).filter(Match.id == sig.match_id).first()
        player = db.query(Player).filter(Player.id == sig.player_id).first()
        opponent = db.query(Player).filter(Player.id == sig.opponent_id).first()
        snapshot = None
        if sig.snapshot_id:
            snapshot = db.query(MatchLiveSnapshot).filter(MatchLiveSnapshot.id == sig.snapshot_id).first()

        rounds = {
            "qualification_round_1": "资格赛首轮", "qualification_round_2": "资格赛次轮",
            "qualification_final": "资格赛决轮", "round_of_128": "128强", "round_of_64": "64强",
            "round_of_32": "32强", "round_of_16": "16强", "quarterfinal": "八强",
            "semifinal": "半决赛", "final": "决赛", "round_robin": "小组赛",
        }
        tournament = (match.tournament or "未知赛事") if match else "未知赛事"
        if match and match.round:
            tournament += f"（{rounds.get(match.round, match.round)}）"
        if match and match.surface:
            tournament += f"· {self._zh_surface(match.surface)}"
        player_name = player.name if player else "未知球员"
        opponent_name = opponent.name if opponent else "未知球员"
        score = "暂无比分"
        score_label = "触发快照比分"
        if snapshot:
            score = snapshot.current_score_text
            score_label = "触发分后局分 · 小分（按推荐方向在前）"
        title = "⚠️ BWF 降级防守预警" if is_warning_alert else "🏸 BWF 信号提醒"
        risk = "未知" if sig.collapse_risk_score is None else f"{sig.collapse_risk_score:.3f}"
        warnings = []
        for pid, name in ((sig.player_id, player_name), (sig.opponent_id, opponent_name)):
            warning = player_inactivity_warning(db, pid, match)
            if warning:
                warnings.append(f"⚠️ {name}：超3周无比赛记录（距上场约{warning['days']}天）")
        inactivity_text = "".join(line + "\n" for line in warnings)
        text = (
            f"{title}\n"
            "━━━━━━━━━━━━━━\n"
            f"🏆 赛事：{tournament}\n\n"
            f"⚔️ 对阵：{player_name} vs {opponent_name}\n"
            f"🎯 方向：{player_name}\n\n"
            f"📊 {score_label}：{score}\n\n"
            f"⚡ 信号等级：〔 {self._zh_level(sig.signal_level)} 〕\n"
            f"📉 风险值：{risk}（评分，非输球概率）\n"
            f"{inactivity_text}"
            "━━━━━━━━━━━━━━\n"
            "详细信息请到网站查看"
        )
        return escape(text, quote=False)


    def _format_email_body(self, db: Session, sig: TrendSignalLog, is_warning_alert: bool = False) -> str:
        """Formats the HTML alert body for email dispatch."""
        tele_msg = self._format_telegram_message(db, sig, is_warning_alert)
        # Convert newlines to breaks for HTML email
        email_html = f"<html><body>{tele_msg.replace(chr(10), '<br>')}</body></html>"
        return email_html

    def trigger_alert(self, db: Session, sig: TrendSignalLog) -> bool:
        """
        Sends an alert through configured channels.
        Uses the configured primary and fallback channels.
        Keeps signal eligibility and de-escalation delivery rules unchanged.
        """
        cooldown_key = f"match_{sig.match_id}_player_{sig.player_id}_lvl_{sig.signal_level}"
        
        # Determine if we should alert and if it is a warning
        should_alert = False
        is_warning_alert = False
        prev_sig = None
        
        if sig.signal_level in ["S", "A"]:
            should_alert = True
        else:
            # Check for de-escalation warning (from S/A to B/C)
            prev_sig = db.query(TrendSignalLog).filter(
                TrendSignalLog.match_id == sig.match_id,
                TrendSignalLog.id < sig.id
            ).order_by(TrendSignalLog.id.desc()).first()
            if prev_sig and prev_sig.signal_level in ["S", "A"]:
                # Check if we successfully sent an alert for the previous S/A signal
                prev_alert = db.query(AlertLog).filter(
                    AlertLog.signal_log_id == prev_sig.id,
                    AlertLog.sent_success == True
                ).first()
                if prev_alert:
                    should_alert = True
                    is_warning_alert = True

        # Format Alert
        telegram_body = self._format_telegram_message(db, sig, is_warning_alert)
        email_body = self._format_email_body(db, sig, is_warning_alert)
        
        if is_warning_alert:
            subject = f"⚠️ BWF 降级防守预警：{prev_sig.signal_level} 级 ➡️ {sig.signal_level} 级，比赛 {sig.match_id}"
        else:
            subject = f"🏸 BWF 信号：{self._zh_level(sig.signal_level)} 级，比赛 {sig.match_id}"

        # 1. Primary Channel
        sent_success = False
        channel_used = settings.PRIMARY_ALERT_CHANNEL
        error_msg = None

        if should_alert:
            def dispatch(channel, fallback=False):
                if channel == "slack":
                    return self.slack.send_message(telegram_body)
                if channel == "telegram":
                    if fallback and not settings.ENABLE_TELEGRAM_ALERT:
                        return False
                    return self.telegram.send_message(telegram_body)
                if channel == "email":
                    if fallback and not settings.ENABLE_EMAIL_ALERT:
                        return False
                    return self.email.send_alert(subject, email_body)
                logger.error("Unknown alert channel: {}", channel)
                return False

            sent_success = dispatch(settings.PRIMARY_ALERT_CHANNEL)
            fallback = settings.FALLBACK_ALERT_CHANNEL
            if not sent_success and fallback and fallback != settings.PRIMARY_ALERT_CHANNEL:
                channel_used = f"{fallback}-fallback"
                sent_success = dispatch(fallback, fallback=True)
            if not sent_success:
                error_msg = "Alert delivery failed; check channel configuration."
        else:
            logger.info(f"Skipping alert: level {sig.signal_level} is not S or A, and no de-escalation warning triggered.")
            error_msg = f"Skipped: level {sig.signal_level} is not S or A"

        # Write alert log
        alert_log = AlertLog(
            match_id=sig.match_id,
            player_id=sig.player_id,
            signal_log_id=sig.id,
            alert_channel=channel_used,
            alert_title=subject,
            alert_body=telegram_body,
            cooldown_key=cooldown_key,
            sent_success=sent_success,
            error_message=error_msg
        )
        self.alert_repo.create(db, alert_log)
        
        return sent_success
