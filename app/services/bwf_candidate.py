"""21-point model contract and causal player histories shared with research."""

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import text

from app.services.feature_engineering import FeatureExtractor


MODEL_TYPE = "BWF21HGB"
POLICY = "bwf21-r30-s2-p95-v1"
POLICY_RULE = {"start": 30, "run": 2, "threshold": .95, "trend": "probability", "history": 10}
PROFILE_COLUMNS = ["elo_gap", "recent_match_gap", "historical_close_rally_gap", "first_game_rally_gap"]
FEATURE_COLUMNS = FeatureExtractor.feature_columns[:9] + PROFILE_COLUMNS + FeatureExtractor.feature_columns[9:] + ["recent20"]


class PlayerHistory:
    def __init__(self):
        self.matches = defaultdict(list)
        self.rating = defaultdict(lambda: 1500.0)

    def record_stats(self, players, winner, stats):
        home, away = players
        expected = 1 / (1 + 10 ** ((self.rating[away] - self.rating[home]) / 400))
        delta = 24 * ((winner == home) - expected)
        self.rating[home] += delta
        self.rating[away] -= delta
        for player in players:
            self.matches[player].append((winner == player, *stats[player]))

    def record(self, match):
        points = match["points"]
        first_end = next(i for i, point in enumerate(points, 1) if point[4])
        first = points[:first_end]
        close = [current for prior, current in zip(points, points[1:])
                 if not prior[4] and min(prior[1], prior[2]) >= 15 and abs(prior[1] - prior[2]) <= 3]
        stats = {player: (sum(point[0] == player for point in points), len(points),
                          sum(point[0] == player for point in close), len(close),
                          sum(point[0] == player for point in first), len(first))
                 for player in match["players"]}
        self.record_stats(match["players"], match["winner_player_id"], stats)

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


def profile_gaps(own, other):
    return {"elo_gap": (own["elo"] - other["elo"]) / 400,
            "recent_match_gap": own["recent"] - other["recent"],
            "historical_close_rally_gap": own["close"] - other["close"],
            "first_game_rally_gap": own["first"] - other["first"]}


def paired_probabilities(probabilities):
    import numpy as np

    pair = np.asarray(probabilities).reshape(-1, 2)
    first = (pair[:, 0] + 1 - pair[:, 1]) / 2
    return np.clip(np.column_stack((first, 1 - first)).ravel(), 1e-6, 1 - 1e-6)


def start_markers(events):
    return [(event["type"], event["time"]) for event in events
            if event.get("type") in {"match_started", "match_start", "sport_event_started", "period_start"}
            and isinstance(event.get("time"), str)
            and (event["type"] != "period_start" or str(event.get("period")) == "1"
                 or event.get("period_name") in ("1st_game", "1st_set"))]


def utc_naive(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def profile_cutoff(scheduled, first_rally, markers=()):
    scheduled, first = utc_naive(scheduled), utc_naive(first_rally)
    earliest = min(scheduled, first) if first else scheduled
    starts = [utc_naive(time) for _, time in markers]
    trusted = [start for start in starts if start and first and start <= first]
    return (min(earliest, *trusted) if trusted else earliest.replace(hour=0, minute=0, second=0, microsecond=0))


SUMMARY = text("""
    WITH eligible AS (
        SELECT id, winner_player_id, last_rally_at FROM matches
        WHERE provider = 'sportradar' AND scoring_target = 21 AND timeline_complete = 1
          AND status IN ('closed', 'completed', 'ended') AND winner_player_id IS NOT NULL
          AND match_date >= '2025-01-01' AND last_rally_at >= :start AND last_rally_at < :end
    ), scores AS (
        SELECT p.match_id, p.point_winner_player_id AS player_id, COUNT(*) AS rallies,
               SUM(p.game_no = 1) AS first_game,
               SUM(CASE WHEN json_extract(p.raw_event_json, '$.home_score_before') >= 15
                    AND json_extract(p.raw_event_json, '$.away_score_before') >= 15
                    AND ABS(json_extract(p.raw_event_json, '$.home_score_before')
                          - json_extract(p.raw_event_json, '$.away_score_before')) <= 3
                   THEN 1 ELSE 0 END) AS close
        FROM eligible AS e JOIN match_point_events AS p ON p.match_id = e.id
        GROUP BY p.match_id, p.point_winner_player_id
    )
    SELECT e.id, e.winner_player_id,
           h.player_id AS home, COALESCE(hs.rallies, 0) AS home_rallies,
           COALESCE(hs.first_game, 0) AS home_first, COALESCE(hs.close, 0) AS home_close,
           a.player_id AS away, COALESCE(aws.rallies, 0) AS away_rallies,
           COALESCE(aws.first_game, 0) AS away_first, COALESCE(aws.close, 0) AS away_close
    FROM eligible AS e
    JOIN match_players AS h ON h.match_id = e.id AND h.side = 'home'
    JOIN match_players AS a ON a.match_id = e.id AND a.side = 'away'
    LEFT JOIN scores AS hs ON hs.match_id = e.id AND hs.player_id = h.player_id
    LEFT JOIN scores AS aws ON aws.match_id = e.id AND aws.player_id = a.player_id
    ORDER BY e.last_rally_at, e.id
""")


def _history_rows(db, start, end):
    return db.execute(SUMMARY, {"start": start.isoformat(" "), "end": end.isoformat(" ")}).all()


def _record_rows(history, rows):
    for row in rows:
        total = row.home_rallies + row.away_rallies
        if not total:
            continue
        first, close = row.home_first + row.away_first, row.home_close + row.away_close
        history.record_stats((row.home, row.away), row.winner_player_id, {
            row.home: (row.home_rallies, total, row.home_close, close, row.home_first, first),
            row.away: (row.away_rallies, total, row.away_close, close, row.away_first, first),
        })


class CandidateProfiles:
    def __init__(self):
        self.day = None
        self.history = None

    def profiles(self, db, cutoff, players):
        day = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.day != day:
            history = PlayerHistory()
            _record_rows(history, _history_rows(db, datetime(2025, 1, 1), day))
            self.day, self.history = day, history
        history = self.history
        if cutoff > day:
            rows = _history_rows(db, day, cutoff)
            if rows:
                history = deepcopy(history)
                _record_rows(history, rows)
        return {player: history.profile(player) for player in players}
