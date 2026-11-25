"""A configured year-round competition allowlist is shared by signals and polling."""
from app.config import settings


def is_signal_excluded(match):
    allowed = {item.strip() for item in settings.SIGNAL_COMPETITION_IDS.split(",") if item.strip()}
    return bool(match and allowed and match.tournament_id not in allowed)

