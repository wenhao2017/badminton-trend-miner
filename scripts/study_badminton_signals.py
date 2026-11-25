"""Evaluate women's singles features and S confirmation against unseen matches.

Input is a gzip JSONL export of complete matches and their normalized rallies.
Run --export-db on a SQLite backup to produce that input.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
from itertools import product
import json
import math
from pathlib import Path
import sqlite3


SCORE = ["scoring_target", "game_no", "games_lead", "game_score_fraction",
         "point_margin_fraction", "is_deciding_game"]
PRIORS = ["prior_match_win_rate", "opponent_prior_match_win_rate", "prior_rally_win_rate"]
FORM = ["total_rally_win_rate", "recent_8_rallies_win_rate", "recent_16_rallies_win_rate",
        "recent_rally_deviation", "signed_streak", "pressure_rally_win_rate"]
PROFILE = ["elo_gap", "recent_match_gap", "historical_close_rally_gap", "first_game_rally_gap"]


def export_matches(source, output):
    """Read complete singles and only the score fields needed for replay."""
    with sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        matches = db.execute(
            "SELECT id, match_date, last_rally_at, scoring_target, best_of, winner_player_id,"
            " tournament, tournament_id, tournament_level, competition_stage FROM matches"
            " WHERE timeline_complete AND status IN ('closed','completed','ended') ORDER BY match_date, id"
        ).fetchall()
        with gzip.open(output, "wt", encoding="utf-8") as out:
            for match in matches:
                records = db.execute(
                    "SELECT point_winner_player_id, raw_event_json, is_game_point FROM match_point_events"
                    " WHERE match_id=? ORDER BY point_index", (match["id"],)
                )
                points = []
                players = None
                for winner, payload, pressure in records:
                    raw = json.loads(payload)
                    players = [raw["home_player_id"], raw["away_player_id"]]
                    points.append([winner, raw["home_score"], raw["away_score"], raw["games_after"],
                                   raw["game_ended"], raw.get("time"), pressure, raw.get("serve_known", False)])
                if not points or players is None or match["winner_player_id"] not in players:
                    raise ValueError(f"Complete match {match['id']} has no usable result or rallies")
                out.write(json.dumps({**dict(match), "players": players, "points": points}) + "\n")


def utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class PlayerHistory:
    def __init__(self):
        self.matches = defaultdict(list)
        self.rating = defaultdict(lambda: 1500.0)

    def record(self, match):
        home, away = match["players"]
        winner = match["winner_player_id"]
        expected = 1 / (1 + 10 ** ((self.rating[away] - self.rating[home]) / 400))
        delta = 24 * ((winner == home) - expected)
        self.rating[home] += delta
        self.rating[away] -= delta
        points = match["points"]
        first_end = next(i for i, point in enumerate(points, 1) if point[4])
        first = points[:first_end]
        close = [current for prior, current in zip(points, points[1:])
                 if not prior[4] and min(prior[1], prior[2]) >= 15 and abs(prior[1] - prior[2]) <= 3]
        for player in match["players"]:
            self.matches[player].append((
                winner == player, sum(point[0] == player for point in points), len(points),
                sum(point[0] == player for point in close), len(close),
                sum(point[0] == player for point in first), len(first),
            ))

    def profile(self, player):
        history = self.matches[player][-40:]
        wins, rallies, total, close, close_total, first, first_total = (
            sum(group) for group in zip(*history)
        ) if history else (0, 0, 0, 0, 0, 0, 0)
        recent = history[-12:]
        return {
            "matches": len(history),
            "win": (wins + 3) / (len(history) + 6),
            "rally": (rallies + 20) / (total + 40),
            "elo": self.rating[player],
            "recent": (sum(item[0] for item in recent) + 3) / (len(recent) + 6),
            "close": (close + 15) / (close_total + 30),
            "first": (first + 20) / (first_total + 40),
        }


def replay(matches):
    history = PlayerHistory()
    finished = iter(sorted(matches, key=lambda match: (utc(match["last_rally_at"]), match["id"])))
    previous = next(finished, None)
    for match in sorted(matches, key=lambda match: (utc(match["match_date"]), match["id"])):
        start = utc(match["match_date"])
        while previous is not None and utc(previous["last_rally_at"]) < start:
            history.record(previous)
            previous = next(finished, None)
        players = match["players"]
        points = match["points"]
        profiles = [history.profile(player) for player in players]
        for side, player in enumerate(players):
            rival = 1 - side
            own, other = profiles[side], profiles[rival]
            running = [0]
            streak = pressure_count = pressure_wins = 0
            for index, (winner, home, away, games, ended, _, pressure, _) in enumerate(points[:-1], 1):
                won = winner == player
                running.append(running[-1] + won)
                streak = (max(streak, 0) + 1) if won else (min(streak, 0) - 1)
                pressure_count += bool(pressure)
                pressure_wins += bool(pressure and won)
                if index < 8:
                    continue
                scores = home, away
                game = sum(games) + 1 - bool(ended)
                recent = {window: (running[-1] - running[max(index-window,0)]) / min(index,window)
                          for window in (8, 16, 20)}
                yield {
                    "match_id": match["id"], "rally": index, "side": side,
                    "day": start.date().isoformat(), "finish_day": utc(match["last_rally_at"]).date().isoformat(),
                    "tournament": match["tournament"], "event_time": int(utc(points[index-1][5]).timestamp()),
                    "final_time": int(utc(points[-1][5]).timestamp()),
                    "remaining": len(points) - index, "prior": own["matches"], "recent20": recent[20],
                    "won": int(match["winner_player_id"] == player),
                    "scoring_target": match["scoring_target"], "game_no": game,
                    "games_lead": games[side] - games[rival],
                    "game_score_fraction": max(scores) / match["scoring_target"],
                    "point_margin_fraction": (scores[side] - scores[rival]) / match["scoring_target"],
                    "is_deciding_game": game == match["best_of"],
                    "prior_match_win_rate": own["win"], "opponent_prior_match_win_rate": other["win"],
                    "prior_rally_win_rate": own["rally"], "total_rally_win_rate": running[-1] / index,
                    "recent_8_rallies_win_rate": recent[8], "recent_16_rallies_win_rate": recent[16],
                    "recent_rally_deviation": recent[16] - own["rally"],
                    "signed_streak": streak, "pressure_rally_win_rate": (pressure_wins + 2) / (pressure_count + 4),
                    "elo_gap": (own["elo"] - other["elo"]) / 400,
                    "recent_match_gap": own["recent"] - other["recent"],
                    "historical_close_rally_gap": own["close"] - other["close"],
                    "first_game_rally_gap": own["first"] - other["first"],
                }


def evaluations(df, probabilities):
    df = df.assign(probability=probabilities).sort_values(["match_id", "rally", "side"])
    return [
        (group["rally"].to_numpy()[::2], group["remaining"].to_numpy()[::2],
         group["game_no"].to_numpy()[::2], group["probability"].to_numpy().reshape(-1, 2),
         group["recent20"].to_numpy().reshape(-1, 2), group["prior"].to_numpy()[:2],
         group["won"].to_numpy()[:2], group["event_time"].to_numpy()[::2], int(group["final_time"].iat[0]))
        for _, group in df.groupby("match_id", sort=False)
    ]


def wilson(hits, count):
    if not count:
        return [0.0, 1.0]
    rate, z = hits / count, 1.96
    center = rate + z * z / (2 * count)
    delta = z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count))
    return [(center - delta) / (1 + z * z / count), (center + delta) / (1 + z * z / count)]


def observed_states(event_times, final_time, *, interval, phase, delivery_delay):
    """Find which rally is latest at each poll while the match is still open."""
    import numpy as np

    event_available = event_times + delivery_delay
    first_poll = ((event_available[0] - phase + interval - 1) // interval) * interval + phase
    publishable = np.zeros(len(event_times), dtype=bool)
    for poll in range(first_poll, final_time + delivery_delay, interval):
        index = np.searchsorted(event_available, poll, side="right") - 1
        if index >= 0 and poll - event_times[index] <= 60:
            publishable[index] = True
    return publishable


def evaluate_policy(matches, rule, *, poll=None):
    import numpy as np

    start, consecutive, threshold, prior = rule
    emitted = []
    for rallies, remaining, games, probability, recent, history, won, timestamps, final_time in matches:
        publishable = True if poll is None else observed_states(timestamps, final_time, **poll)
        trend = 0.8 * probability + 0.2 * recent
        qualified = []
        for side in (0, 1):
            if history[side] < prior:
                continue
            clean = (probability[:, side] >= 0.70) & (probability[:, 1-side] < 0.70)
            grade = clean & (probability[:, side] >= threshold) & (rallies >= start)
            window = np.convolve(grade, np.ones(consecutive, dtype=int), mode="full")[:len(grade)]
            eligible = grade & (trend[:, side] >= 0.70)
            confirmed_at = np.maximum.accumulate(np.where((window == consecutive) & eligible, rallies, 0))
            reset_at = np.maximum.accumulate(np.where(~clean, rallies, 0))
            can_publish = eligible & (confirmed_at > reset_at) & publishable
            indices = np.flatnonzero(can_publish)
            if len(indices):
                index = indices[0]
                qualified.append((rallies[index], won[side], remaining[index], games[index]))
        if qualified:
            emitted.append(min(qualified, key=lambda row: row[0]))
    hits = sum(int(item[1]) for item in emitted)
    n = len(emitted)
    return {
        "count": n, "hits": hits, "hit_rate": hits / n if n else None, "wilson_95": wilson(hits, n),
        "coverage": n / len(matches), "median_trigger": float(np.median([item[0] for item in emitted])) if n else None,
        "median_remaining": float(np.median([item[2] for item in emitted])) if n else None,
        "game1": sum(int(item[3] == 1) for item in emitted),
        "remaining_at_least_20": sum(int(item[2] >= 20) for item in emitted),
    }


def run_study(path, first_cut, second_cut, through):
    import numpy as np
    import pandas as pd
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier

    with gzip.open(path, "rt", encoding="utf-8") as inp:
        all_matches = [json.loads(line) for line in inp]
    all_matches = [match for match in all_matches if through is None or utc(match["match_date"]) <= utc(through)]
    matches = [match for match in all_matches if match["scoring_target"] == 21]
    if any(len(match["points"]) < 9 for match in matches):
        raise ValueError("A complete match has too few rallies")
    dataset = pd.DataFrame(replay(matches))
    groups = {
        "train": dataset[(dataset.day < first_cut) & (dataset.finish_day < first_cut)],
        "validation": dataset[(dataset.day >= first_cut) & (dataset.day < second_cut)
                              & (dataset.finish_day < second_cut)].copy(),
        "test": dataset[dataset.day >= second_cut].copy(),
    }
    result = {"scoring_rule_counts": dict(Counter(m["scoring_target"] for m in all_matches)),
              "serve_known": sum(bool(p[7]) for m in matches for p in m["points"]),
              "rallies": sum(len(m["points"]) for m in matches),
              "splits": {name: {"matches": int(frame.match_id.nunique()), "from": frame.day.min(),
                                "to": frame.day.max(), "tournaments": int(frame.tournament.nunique())}
                         for name, frame in groups.items()}}
    if any(frame.empty for frame in groups.values()):
        raise ValueError("Train, validation and test must each contain completed matches")
    train = groups["train"].query("rally >= 12")
    weights = 1 / train.groupby("match_id").match_id.transform("size")
    features = {"score_rf": SCORE, "score_prior_rf": SCORE + PRIORS, "rally_rf": SCORE + PRIORS + FORM,
                "profile_rf": SCORE + PRIORS + FORM + PROFILE, "profile_boost": SCORE + PRIORS + FORM + PROFILE}
    metrics, probabilities = {}, {}
    for name, columns in features.items():
        model = (HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=15, random_state=42)
                 if name == "profile_boost" else
                 RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, n_jobs=2))
        model.fit(train[columns], train.won, sample_weight=weights.to_numpy() / weights.mean())
        metrics[name], probabilities[name] = {}, {}
        for phase in ("validation", "test"):
            frame = groups[phase]
            p = np.clip(model.predict_proba(frame[columns])[:, 1], 1e-6, 1-1e-6)
            probabilities[name][phase] = p
            block = frame.assign(brier=(p - frame.won)**2, logloss=-np.where(frame.won, np.log(p), np.log1p(-p)))
            metrics[name][phase] = {
                "brier": block.query("rally >= 12").groupby("match_id").brier.mean().mean(),
                "logloss": block.query("rally >= 12").groupby("match_id").logloss.mean().mean(),
                "opening_brier": block.query("12 <= rally <= 24").groupby("match_id").brier.mean().mean(),
            }
    result["models"] = metrics
    winner = min(metrics, key=lambda name: metrics[name]["validation"]["brier"])
    result["selected_model"] = winner
    validation = evaluations(groups["validation"], probabilities[winner]["validation"])
    test = evaluations(groups["test"], probabilities[winner]["test"])
    rules = list(product((8, 12, 16, 20, 24), (1, 2, 3, 4), (0.85, 0.89, 0.92, 0.95), (5, 10)))
    metrics_validation = {rule: evaluate_policy(validation, rule) for rule in rules}
    candidates = [rule for rule, metric in metrics_validation.items()
                  if rule[0] >= 12 and metric["coverage"] >= .25 and metric["median_remaining"] >= 25]
    selected = max(candidates, key=lambda rule: (metrics_validation[rule]["wilson_95"][0],
                                                 metrics_validation[rule]["median_remaining"])) if candidates else None
    result["selected_policy"] = selected
    result["policy_grid_validation"] = [{"rule": rule, **metrics_validation[rule]} for rule in rules]
    baseline = (12, 3, 0.85, 10)
    compare = [(start, 3, .85, 10) for start in (8, 12, 16, 20, 24)] + [(12, n, .85, 10) for n in (1, 2, 4)]
    if selected is not None:
        opening, _, confidence, history = selected
        compare.extend((opening, n, confidence, history) for n in (1, 2, 3, 4))
    poll = {"interval": 60, "phase": 0, "delivery_delay": 15}
    result["poll_assumptions"] = poll
    result["holdout_rules"] = [{"rule": rule, "validation": metrics_validation[rule],
                                "test": evaluate_policy(test, rule), "test_poll": evaluate_policy(test, rule, poll=poll)}
                               for rule in dict.fromkeys(compare)]
    result["original_features_holdout"] = evaluate_policy(
        evaluations(groups["test"], probabilities["rally_rf"]["test"]), baseline
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="gzip JSONL export path")
    parser.add_argument("--export-db", type=Path, help="export from this read-only SQLite file and exit")
    parser.add_argument("--output", type=Path, help="write full JSON study results here")
    parser.add_argument("--train-before", default="2026-07-15")
    parser.add_argument("--test-from", default="2026-10-05")
    parser.add_argument("--through-utc", help="include matches scheduled through this timestamp, inclusive")
    args = parser.parse_args()
    if args.export_db:
        export_matches(args.export_db, args.input)
        return
    if args.train_before >= args.test_from:
        parser.error("--train-before must precede --test-from")
    result = run_study(args.input, args.train_before, args.test_from, args.through_utc)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "policy_grid_validation"}, indent=2))


if __name__ == "__main__":
    main()

