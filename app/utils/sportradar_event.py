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


TARGET_WORLD_TOUR_LEVELS = frozenset({
    "Super 1000",
    "Super 750",
    "Super 500",
    "Super 300",
    "World Tour Finals",
})

# Sportradar daily/live summaries omit competition.level; map known BWF World Tour WS competition IDs.
BWF_WORLD_TOUR_COMPETITION_LEVELS: dict[str, str] = {
    # Super 1000
    "sr:competition:25339": "Super 1000",  # Malaysia Open, WS
    "sr:competition:27510": "Super 1000",  # All England Open, Women, Singles
    "sr:competition:27947": "Super 1000",  # Indonesia Open, Women Singles
    "sr:competition:26468": "Super 1000",  # China Open, WS
    # Super 750
    "sr:competition:23739": "Super 750",   # India Open, Women, Singles
    "sr:competition:25607": "Super 750",   # Singapore Open, Women, Singles
    "sr:competition:19742": "Super 750",   # Japan Open, Women, Singles
    "sr:competition:24307": "Super 750",   # LI-NING China Masters, WS
    "sr:competition:20584": "Super 750",   # Denmark Open, WS
    "sr:competition:20646": "Super 750",   # French Open, WS
    # Super 500
    "sr:competition:27959": "Super 500",   # Indonesia Masters, WS
    "sr:competition:25501": "Super 500",   # Thailand Open (WT), Women Singles
    "sr:competition:23451": "Super 500",   # Malaysia Masters, Women, Singles
    "sr:competition:21510": "Super 500",   # Hong Kong Open, WS
    "sr:competition:26670": "Super 500",   # Korea Open, Women, Singles
    "sr:competition:41262": "Super 500",   # Arctic Open, WS
    "sr:competition:27020": "Super 500",   # Hylo Open, WS
    "sr:competition:41624": "Super 500",   # Kumamoto Masters Japan, WS
    "sr:competition:24678": "Super 500",   # Australian Open, Women Singles
    # Super 300
    "sr:competition:23415": "Super 300",   # Thailand Masters, WS
    "sr:competition:23995": "Super 300",   # German Open, Women Singles
    "sr:competition:24275": "Super 300",   # Orleans Masters, Women, Singles
    "sr:competition:23891": "Super 300",   # Swiss Open, WS
    "sr:competition:26800": "Super 300",   # Chinese Taipei Open (WT), Women Singles
    "sr:competition:25151": "Super 300",   # US Open, Women Singles
    "sr:competition:25305": "Super 300",   # Canada Open, WS
    "sr:competition:27010": "Super 300",   # Macau Open, WS
    "sr:competition:22014": "Super 300",   # Korea Masters, Women, WS
    "sr:competition:27256": "Super 300",   # Syed Modi India International, WS
    # World Tour Finals
    "sr:competition:23251": "World Tour Finals",  # World Tour Finals, WS
}


def get_event_tournament_level(event: dict) -> str | None:
    competition = get_event_tournament(event)
    level = str(competition.get("level") or "").strip()
    tier = re.fullmatch(r"(?:super\s*)?(1000|750|500|300|100)", level, re.I)
    if tier:
        return f"Super {tier[1]}"
    return (tournament_level_label(None, level, None)
            or tournament_level_label(None, competition.get("name"), competition.get("id")))


def tournament_level_label(level: str | None, tournament: str | None,
                           competition_id: str | None) -> str | None:
    if level and level.strip():
        return level.strip()
    name = tournament or ""
    tier = re.search(r"\bsuper\s*(1000|750|500|300|100)\b", name, re.I)
    if tier:
        return f"Super {tier[1]}"
    if re.search(r"\bworld tour finals\b", name, re.I):
        return "World Tour Finals"
    if competition_id and competition_id in BWF_WORLD_TOUR_COMPETITION_LEVELS:
        return BWF_WORLD_TOUR_COMPETITION_LEVELS[competition_id]
    return None


def is_target_world_tour_level(level: str | None) -> bool:
    return bool(level and level in TARGET_WORLD_TOUR_LEVELS)


def is_target_world_tour_event(event: dict) -> bool:
    return is_target_world_tour_level(get_event_tournament_level(event))


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

