"""Write a minimal JSONL research export from a read-only SQLite backup."""

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3


COMPLETE = """provider = 'sportradar' AND timeline_complete
    AND status IN ('closed','completed','ended') AND winner_player_id IS NOT NULL
    AND last_rally_at IS NOT NULL"""
START_TYPES = {"match_started", "match_start", "sport_event_started", "period_start"}


def export(source):
    uri = Path(source).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite backup failed quick_check")
        matches = db.execute(f"""
            SELECT id, external_match_id, match_date, last_rally_at, scoring_target,
                   best_of, tournament, tournament_id, winner_player_id, last_point_count
            FROM matches WHERE {COMPLETE} AND match_date >= '2025-01-01' AND match_date < '2027-01-01'
            ORDER BY match_date, id
        """).fetchall()
        wanted = {match["external_match_id"] for match in matches}
        starts = {}
        event_types = Counter()
        for (body,) in db.execute("SELECT body_json FROM raw_provider_responses WHERE endpoint LIKE '%/timeline.json'"):
            payload = json.loads(body)
            external_id = (payload.get("sport_event") or {}).get("id")
            if external_id not in wanted:
                continue
            timeline = payload.get("timeline") or []
            event_types.update(event.get("type", "") for event in timeline)
            events = starts.setdefault(external_id, set())
            events.update((event["type"], event["time"]) for event in timeline
                          if event.get("type") in START_TYPES and isinstance(event.get("time"), str)
                          and (event["type"] != "period_start" or
                               str(event.get("period")) == "1" or event.get("period_name") in ("1st_game", "1st_set")))
        old_limit = db.execute(f"""
            SELECT substr(match_date, 1, 4) AS year, COUNT(*) AS samples,
                   COUNT(DISTINCT match_id) AS matches, MIN(date(match_date)) AS first_day,
                   MAX(date(match_date)) AS last_day
              FROM (
                SELECT m.match_date, s.match_id FROM trend_training_samples AS s
                JOIN matches AS m ON m.id = s.match_id
                WHERE {COMPLETE} AND m.scoring_target = 21
                ORDER BY m.match_date DESC, s.id DESC LIMIT 30000
              ) GROUP BY year
        """).fetchall()
        yield {"source_backup": Path(source).parent.name, "old_limit": [dict(row) for row in old_limit],
               "registered_models": db.execute("SELECT COUNT(*) FROM trend_models").fetchone()[0],
               "formal_signals": db.execute("SELECT COUNT(*) FROM trend_signal_logs WHERE official_match_id IS NOT NULL").fetchone()[0],
               "scoring_target_status_counts": [dict(row) for row in db.execute("""
                    SELECT scoring_target, substr(match_date, 1, 4) AS year, status,
                           timeline_complete, COUNT(*) AS matches FROM matches
                    WHERE match_date >= '2025-01-01' AND match_date < '2027-01-01'
                    GROUP BY scoring_target, year, status, timeline_complete
                """)], "timeline_event_types": dict(event_types)}
        for match in matches:
            match_id = match["id"]
            players = dict(db.execute("SELECT side, player_id FROM match_players WHERE match_id = ?", (match_id,)))
            if set(players) != {"home", "away"}:
                raise ValueError(f"Unexpected player sides for {match_id}")
            players = [players["home"], players["away"]]
            points = []
            for index, game, winner, pressure, payload in db.execute("""
                SELECT point_index, game_no, point_winner_player_id, is_game_point, raw_event_json
                FROM match_point_events WHERE match_id = ? ORDER BY point_index
            """, (match_id,)):
                raw = json.loads(payload)
                if (index != len(points) + 1 or raw.get("type") != "rally"
                        or [raw["home_player_id"], raw["away_player_id"]] != players):
                    raise ValueError(f"Invalid normalized rally {match_id}/{index}")
                points.append([winner, raw["home_score"], raw["away_score"], raw["games_after"],
                               raw["game_ended"], raw.get("time"), pressure, raw.get("serve_known", False), game])
            rows = list(db.execute("""
                SELECT player_id, snapshot_point_index FROM trend_training_samples WHERE match_id = ?
            """, (match_id,)))
            observed = {tuple(row) for row in rows}
            expected = {(player, index) for player in players for index in range(12, len(points))}
            yield {**dict(match), "players": players, "points": points, "db_samples": len(rows),
                   "db_sample_duplicates": len(rows) - len(observed),
                   "db_sample_missing": len(expected - observed), "db_sample_extra": len(observed - expected),
                   "start_markers": sorted(starts.get(match["external_match_id"], []))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup")
    for row in export(parser.parse_args().backup):
        print(json.dumps(row, ensure_ascii=True, separators=(",", ":")))
