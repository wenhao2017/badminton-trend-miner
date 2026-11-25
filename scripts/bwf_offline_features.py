"""Causal, two-sided features for the women's singles offline candidate."""

from collections import Counter, defaultdict
import gzip
import json

import pandas as pd

from app.services.bwf_candidate import PlayerHistory, profile_cutoff, profile_gaps
from app.utils.badminton_score import game_won
from scripts.study_badminton_signals import FORM, utc


LIVE_FORM = FORM + ["recent20"]
TRAJECTORY = ["peak_score_advantage", "drawdown_from_peak", "ever_led_game"]


def load_matches(path):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        metadata = json.loads(next(source))
        matches = [json.loads(line) for line in source]
    if len({match["id"] for match in matches}) != len(matches):
        raise ValueError("Duplicate match ID in snapshot")
    for match in matches:
        validate_match(match)
    return metadata, matches


def validate_match(match):
    points = match["points"]
    match_id = match["id"]
    target = match["scoring_target"]
    needed = match["best_of"] // 2 + 1
    players = match["players"]
    if len(points) != match["last_point_count"] or len(set(players)) != 2:
        raise ValueError(f"Invalid count or sides in match {match_id}")
    games, scores = [0, 0], [0, 0]
    for index, point in enumerate(points, 1):
        winner, home, away, games_after, ended, *_ = point
        side = players.index(winner)
        if point[8] != sum(games) + 1 or max(games) >= needed:
            raise ValueError(f"Invalid game in {match_id}/{index}")
        scores[side] += 1
        game_over = game_won(scores[side], scores[1 - side], target)
        games[side] += game_over
        if scores != [home, away] or bool(ended) != game_over or games != games_after:
            raise ValueError(f"Invalid score progression in {match_id}/{index}")
        if game_over:
            scores = [0, 0]
    if max(games) != needed or players[games.index(needed)] != match["winner_player_id"]:
        raise ValueError(f"Invalid match winner in {match_id}")
    if target == 21 and any(match[key] for key in ("db_sample_duplicates", "db_sample_missing", "db_sample_extra")):
        raise ValueError(f"Database training key mismatch for {match_id}")

    match["scheduled"] = utc(match["match_date"])
    match["finish"] = utc(match["last_rally_at"])
    timestamps = [utc(point[5]) if point[5] else None for point in points]
    if timestamps[-1] != match["finish"]:
        raise ValueError(f"Last-rally time mismatch in {match_id}")
    first = min(time for time in timestamps if time is not None)
    start_events = [utc(time) for _, time in match["start_markers"] if utc(time) <= first]
    # A scheduled time is not proof of actual start. Without a start marker,
    # exclude same-day outcomes using the earlier UTC date in the two records.
    match["start"] = profile_cutoff(match["scheduled"], first, match["start_markers"]).replace(tzinfo=first.tzinfo)
    match["start_evidence"] = "provider_marker" if start_events else "utc_day_floor"
    match["poll_usable"] = all(time is not None for time in timestamps) and all(
        left <= right for left, right in zip(timestamps, timestamps[1:])
    )
    match["timestamps"] = timestamps
    if match["finish"] <= match["start"]:
        raise ValueError(f"Nonpositive match interval in {match_id}")


def match_profiles(matches):
    history = PlayerHistory()
    prior_seasons = defaultdict(Counter)
    completed = iter(sorted(matches, key=lambda match: (match["finish"], match["id"])))
    previous = next(completed, None)
    for match in sorted(matches, key=lambda match: (match["start"], match["id"])):
        while previous is not None and previous["finish"] < match["start"]:
            history.record(previous)
            for player in previous["players"]:
                prior_seasons[player][previous["scheduled"].year] += 1
            previous = next(completed, None)
        season = match["scheduled"].year
        profiles = []
        for player in match["players"]:
            profile = history.profile(player)
            profile["prior_previous_season"] = prior_seasons[player][season - 1]
            profile["career_matches"] = sum(prior_seasons[player].values())
            profiles.append(profile)
        yield match, profiles


def match_rows(match, profiles):
    points, players = match["points"], match["players"]
    target = match["scoring_target"]
    totals = [0]
    pressure_count = pressure_home = streak = 0
    peak = [0.0, 0.0]
    led_game = [False, False]
    for index, point in enumerate(points[:-1], 1):
        winner, home, away, games, _, _, pressure, _, game = point
        home_won = winner == players[0]
        totals.append(totals[-1] + home_won)
        streak = max(streak, 0) + 1 if home_won else min(streak, 0) - 1
        pressure_count += bool(pressure)
        pressure_home += bool(pressure and home_won)
        margin = home - away
        recent = {window: (totals[-1] - totals[max(index - window, 0)]) / min(index, window)
                  for window in (8, 16, 20)}
        for side in (0, 1):
            rival = 1 - side
            direction = 1 - 2 * side
            own, other = profiles[side], profiles[rival]
            game_lead = games[side] - games[rival]
            point_margin = direction * margin / target
            # One game takes precedence over any point margin within the current game.
            advantage = 2 * game_lead + point_margin
            peak[side] = max(peak[side], advantage)
            led_game[side] |= game_lead > 0
            if index < 12:
                continue
            recent8, recent16, recent20 = (recent[n] if side == 0 else 1 - recent[n] for n in (8, 16, 20))
            won_count = totals[-1] if side == 0 else index - totals[-1]
            pressure_wins = pressure_home if side == 0 else pressure_count - pressure_home
            yield {
                "match_id": match["id"], "rally": index, "side": side, "prior": own["matches"],
                "won": int(match["winner_player_id"] == players[side]),
                "score_ahead": advantage > 0, "recent20": recent20,
                "scoring_target": target, "game_no": game, "games_lead": game_lead,
                "game_score_fraction": max(home, away) / target,
                "point_margin_fraction": point_margin, "is_deciding_game": game == match["best_of"],
                "prior_match_win_rate": own["win"], "opponent_prior_match_win_rate": other["win"],
                "prior_rally_win_rate": own["rally"], **profile_gaps(own, other),
                "total_rally_win_rate": won_count / index,
                "recent_8_rallies_win_rate": recent8, "recent_16_rallies_win_rate": recent16,
                "recent_rally_deviation": recent16 - own["rally"], "signed_streak": direction * streak,
                "pressure_rally_win_rate": (pressure_wins + 2) / (pressure_count + 4),
                "peak_score_advantage": peak[side], "drawdown_from_peak": peak[side] - advantage,
                "ever_led_game": led_game[side],
            }


def dataset(matches):
    if {match["scoring_target"] for match in matches} != {21}:
        raise ValueError("Candidate training only supports homogeneous 21-point data")
    rows = []
    profile_audit = {}
    for match, profiles in match_profiles(matches):
        profile_audit[match["id"]] = profiles
        rows.extend(match_rows(match, profiles))
    frame = pd.DataFrame(rows)
    if len(frame) != sum(match["db_samples"] for match in matches):
        raise ValueError("Rebuilt row total differs from SQLite training keys")
    return frame, profile_audit
