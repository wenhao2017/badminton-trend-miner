"""Competition eligibility shared by schedule window, live polling, live board, and signals."""
from app.config import settings
from app.utils.sportradar_event import (
    get_event_tournament,
    is_target_world_tour_event,
    is_target_world_tour_level,
    tournament_level_label,
)


def _allowed_competition_ids() -> set[str]:
    return {item.strip() for item in settings.SIGNAL_COMPETITION_IDS.split(",") if item.strip()}


def is_event_excluded(event: dict | None) -> bool:
    if not event:
        return True
    allowed = _allowed_competition_ids()
    if allowed:
        comp_id = get_event_tournament(event).get("id")
        return comp_id not in allowed
    return not is_target_world_tour_event(event)


def is_signal_excluded(match) -> bool:
    if not match:
        return False
    allowed = _allowed_competition_ids()
    if allowed:
        return match.tournament_id not in allowed
    level = tournament_level_label(match.tournament_level, match.tournament, match.tournament_id)
    return not is_target_world_tour_level(level)

