import re


def get_event_context(event: dict) -> dict:
    return event.get("sport_event_context") or {}


def get_event_round(event: dict) -> str | None:
    value = event.get("round") or get_event_context(event).get("round")
    if isinstance(value, dict):
        value = value.get("name")
    return value.strip() if isinstance(value, str) and value.strip() else None


def get_event_competition_stage(event: dict) -> str | None:
    round_name = (get_event_round(event) or "").lower()
    phase = (get_event_context(event).get("stage") or {}).get("phase")
    if phase == "qualification" or round_name.startswith("qualif"):
        return "qualification"
    if round_name:
        return "main_draw"
    return None


def get_event_category(event: dict) -> dict:
    return get_event_context(event).get("category") or {}


def get_event_season(event: dict) -> dict:
    return get_event_context(event).get("season") or {}


def get_event_tournament(event: dict) -> dict:
    return get_event_context(event).get("competition") or {}


def get_event_tournament_level(event: dict) -> str | None:
    competition = get_event_tournament(event)
    label = str(competition.get("level") or competition.get("name") or "")
    match = re.search(r"\b(?:super\s*)?(1000|750|500|300|100)\b", label, re.I)
    return f"Super {match[1]}" if match else None


def is_singles_event(event: dict) -> bool:
    context = get_event_context(event)
    competition = get_event_tournament(event)
    stage = context.get("stage") or {}
    phase = str(stage.get("phase") or "").lower()
    discipline = str(competition.get("type") or "").lower()
    return (discipline == "singles" or (not discipline and phase in {"ws", "ms", "women_single"})) and not any(
        competitor.get("players") for competitor in event.get("competitors") or []
    )


def is_womens_singles_event(event: dict) -> bool:
    context = get_event_context(event)
    competition = get_event_tournament(event)
    sport = context.get("sport") or {}
    if sport.get("id") not in {None, "sr:sport:31"} or not is_singles_event(event):
        return False
    gender = str(competition.get("gender") or "").lower()
    phase = str((context.get("stage") or {}).get("phase") or "").lower()
    return gender in {"women", "female"} or (not gender and phase in {"ws", "women_single"})

