"""Normalize a full Badminton v2 timeline into consecutive, confirmed rallies."""
from dataclasses import dataclass

from app.utils.badminton_score import game_won, other_side

SIDES = ("home", "away")
POINT_TYPES = {"score_change", "period_score"}


@dataclass(frozen=True)
class Rally:
    event: dict
    game: int
    before: tuple[int, int]
    after: tuple[int, int]
    games_before: tuple[int, int]
    games_after: tuple[int, int]
    winner: str
    server: str | None
    game_ended: bool


def _period(event, fallback):
    number = event.get("period")
    if type(number) is int and number > 0:
        return number
    if isinstance(number, str) and number.isascii() and number.isdecimal() and int(number) > 0:
        return int(number)
    name = event.get("period_name") or ""
    if name.endswith(("_set", "_game")) and name[0:1].isdigit():
        return int(name[0])
    return fallback


def parse_timeline(payload: dict, target: int, best_of: int = 3) -> list[Rally]:
    events = payload.get("timeline")
    if not isinstance(events, list):
        raise ValueError("Timeline missing")

    # Badminton v2 can report rallies as period_score and game totals as
    # score_change. A nonterminal period score identifies that representation.
    # A closing-only period_score can still accompany score_change rallies.
    period_reports_rallies = any(
        event.get("type") == "period_score"
        and all(type(event.get(f"{side}_score")) is int for side in SIDES)
        and 0 < max(event["home_score"], event["away_score"]) < target
        for event in events
    )
    rows = []
    scores: dict[int, tuple[int, int]] = {}
    games = [0, 0]
    period, server = 1, None
    seen = {}
    needed = best_of // 2 + 1

    for event in events:
        event_id = event.get("id")
        if event_id is not None:
            if event_id in seen:
                if seen[event_id] != event:
                    raise ValueError("Conflicting repeated event")
                continue
            seen[event_id] = event
        kind = event.get("type")
        if kind == "period_start":
            period = _period(event, period)
        if kind not in POINT_TYPES or (period_reports_rallies and kind == "score_change"):
            continue
        game = _period(event, period)
        if (kind == "period_score" and event.get("period") is None
                and event.get("period_name") is None and rows and rows[-1].game_ended):
            game = rows[-1].game
        score = tuple(event.get(f"{side}_score") for side in SIDES)
        if any(type(value) is not int or value < 0 for value in score):
            raise ValueError("Missing or invalid rally score")
        before = scores.get(game, (0, 0))
        if score == before:
            continue
        if game != sum(games) + 1 or max(games) >= needed:
            raise ValueError("Unexpected game number")
        step = tuple(after - previous for after, previous in zip(score, before))
        if step not in {(1, 0), (0, 1)}:
            raise ValueError("Nonconsecutive rally scores")
        winner_index = step.index(1)
        winner = SIDES[winner_index]
        if event.get("competitor") in SIDES and event["competitor"] != winner:
            raise ValueError("Scoring side conflicts with score")
        declared_server = event.get("server")
        serving = declared_server if declared_server in SIDES else server
        if server and serving != server:
            raise ValueError("Serving side conflicts with previous rally")
        games_before = tuple(games)
        ended = game_won(score[winner_index], score[1 - winner_index], target)
        games[winner_index] += ended
        rows.append(Rally(event, game, before, score, games_before, tuple(games),
                          winner, serving, ended))
        scores[game] = score
        server = winner

    status = payload.get("sport_event_status") or {}
    for index, official_period in enumerate(status.get("period_scores") or [], 1):
        score = tuple(official_period.get(f"{side}_score") for side in SIDES)
        if all(type(value) is int for value in score):
            if scores.get(official_period.get("number") or index, (0, 0)) != score:
                raise ValueError("Timeline does not cover the provider game score")
    reported_games = tuple(status.get(f"{side}_score") for side in SIDES)
    if rows and all(type(value) is int for value in reported_games) and tuple(games) != reported_games:
        raise ValueError("Timeline does not cover the provider match score")
    return rows


def to_point(match_id, index, rally, players, target):
    from app.db.models import MatchPointEvent

    # Store ordering even when the first server is unknown. Analyses only use
    # server/receiver semantics if the explicit serve_known flag is true.
    first = rally.server or "home"
    second = other_side(first)
    a, b = SIDES.index(first), SIDES.index(second)
    cap = 21 if target == 15 else 30
    point_for = tuple(game_won(value + 1, rival, target) and value < cap
                      for value, rival in zip(rally.before, reversed(rally.before)))
    return MatchPointEvent(
        match_id=match_id, external_event_id=str(rally.event["id"]) if "id" in rally.event else None,
        point_index=index, set_no=rally.game, game_no=rally.game,
        point_no_in_game=sum(rally.after), server_player_id=players[first],
        receiver_player_id=players[second], point_winner_player_id=players[rally.winner],
        point_loser_player_id=players[other_side(rally.winner)],
        server_score_before=str(rally.before[a]), receiver_score_before=str(rally.before[b]),
        server_games_before=rally.games_before[a], receiver_games_before=rally.games_before[b],
        server_sets_before=rally.games_before[a], receiver_sets_before=rally.games_before[b],
        is_first_serve_in=False, is_game_point=any(point_for), is_set_point=any(point_for),
        is_match_point=any(flag and games == 1 for flag, games in zip(point_for, rally.games_before)),
        raw_event_json={**rally.event, "source_type": rally.event["type"], "type": "rally",
                        "server": rally.server, "serve_known": rally.server is not None,
                        "home_score_before": rally.before[0], "away_score_before": rally.before[1],
                        "home_score": rally.after[0], "away_score": rally.after[1],
                        "games_before": list(rally.games_before), "games_after": list(rally.games_after),
                        "game_ended": rally.game_ended},
    )

