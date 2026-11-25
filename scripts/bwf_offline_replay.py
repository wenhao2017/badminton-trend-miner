"""Replay formal S over all normalized prefixes and optionally batched polls."""

from dataclasses import dataclass

import numpy as np

from scripts.study_badminton_signals import wilson


@dataclass(frozen=True)
class Rule:
    start: int
    run: int
    threshold: float
    trend: str = "blend"
    history: int = 10


@dataclass(frozen=True)
class Poll:
    interval: int
    phase: int
    delivery: int
    max_age: int = 60


PRIMARY_POLLS = tuple(Poll(60, phase, 15) for phase in (0, 15, 30, 45))
SENSITIVITY_POLLS = (Poll(60, 0, 0), Poll(60, 0, 30), Poll(30, 0, 15), Poll(120, 0, 15))
BASELINE = Rule(12, 3, 0.85)


def poll_states(timestamps, poll):
    """Earliest fresh poll for each prefix that was the latest known to the client."""
    result = np.full(len(timestamps) - 1, np.nan)
    if poll is None:
        return timestamps[:-1].copy()
    earliest = int(np.ceil((timestamps[0] + poll.delivery - poll.phase) / poll.interval))
    ticks = np.arange(earliest * poll.interval + poll.phase, timestamps[-1], poll.interval, dtype=float)
    latest = np.searchsorted(timestamps + poll.delivery, ticks, side="right") - 1
    for tick, index in zip(ticks, latest):
        if index >= 0 and tick - timestamps[index] <= poll.max_age and np.isnan(result[index]):
            result[index] = tick
    return result


def replay_inputs(frame, probabilities, matches, polls):
    result = []
    for match_id, block in frame.assign(probability=probabilities).groupby("match_id", sort=False):
        match = matches[match_id]
        timestamps = np.array([t.timestamp() for t in match["timestamps"][11:]]) if match["poll_usable"] else None
        rally = block.rally.to_numpy()[::2]
        if not np.array_equal(rally, np.arange(12, len(match["points"]))):
            raise ValueError(f"Incomplete candidate prefixes for {match_id}")
        delivery = {poll: poll_states(timestamps, poll) if timestamps is not None else
                    np.full(len(rally), np.nan) for poll in (None, *polls)}
        result.append({
            "match_id": int(match_id), "rally": rally, "timestamps": timestamps, "polls": delivery,
            "probability": block.probability.to_numpy().reshape(-1, 2),
            "recent20": block.recent20.to_numpy().reshape(-1, 2), "prior": block.prior.to_numpy()[:2],
            "won": block.won.to_numpy()[:2], "players": match["players"], "total": len(match["points"]),
        })
    return result


def publishable(rallies, probability, recent, rule):
    trend = (0.80 * probability + 0.20 * recent) if rule.trend == "blend" else probability
    # A preserves a previous confirmation. Any deeper drop or opponent A/S clears it.
    clean = (probability >= .70) & (probability[:, ::-1] < .70)
    grade = clean & (probability >= rule.threshold) & (rallies[:, None] >= rule.start)
    eligible = grade & (trend >= .70)
    output = np.zeros(grade.shape, dtype=bool)
    for side in (0, 1):
        consecutive = np.convolve(grade[:, side], np.ones(rule.run, dtype=int), mode="full")[:len(grade)]
        confirmed = np.maximum.accumulate(np.where((consecutive == rule.run) & eligible[:, side], rallies, 0))
        reset = np.maximum.accumulate(np.where(~clean[:, side], rallies, 0))
        output[:, side] = eligible[:, side] & (confirmed > reset)
    return output


def simulate(inputs, rule, polls):
    emitted = {poll: [] for poll in (None, *polls)}
    for match in inputs:
        eligible = publishable(match["rally"], match["probability"], match["recent20"], rule)
        eligible &= match["prior"] >= rule.history
        indices, sides = np.where(eligible)
        if not len(indices):
            continue
        times = match["timestamps"]
        for poll in emitted:
            seen = match["polls"][poll]
            positions = np.flatnonzero(np.isfinite(seen[indices])) if poll is not None else [0]
            if not len(positions):
                continue
            index, side = indices[positions[0]], sides[positions[0]]
            rally = int(match["rally"][index])
            at = float(seen[index]) if np.isfinite(seen[index]) else None
            actual = (int(np.searchsorted(times, at, side="right") + 11)
                      if poll is not None else rally)
            emitted[poll].append({
                "match_id": match["match_id"], "player_id": match["players"][side],
                "hit": bool(match["won"][side]), "published_rally": rally, "actual_rally": actual,
                "remaining_at_snapshot": match["total"] - rally, "remaining_at_publication": match["total"] - actual,
                "probability": float(match["probability"][index, side]), "published_at_utc_epoch": at,
                "source_age_seconds": at - float(times[index]) if at is not None else None,
            })
    return emitted


def metrics(picks, total, immediate=None):
    def quantiles(key):
        values = [row[key] for row in picks if row[key] is not None]
        return np.quantile(values, [0.25, 0.5, 0.75]).tolist() if values else None

    hits, count = sum(row["hit"] for row in picks), len(picks)
    result = {
        "matches": total, "signals": count, "hits": hits, "hit_rate": hits / count if count else None,
        "coverage": count / total, "wilson_95": wilson(hits, count),
        "published_rally_p25_p50_p75": quantiles("published_rally"),
        "actual_rally_p25_p50_p75": quantiles("actual_rally"),
        "remaining_at_snapshot_p25_p50_p75": quantiles("remaining_at_snapshot"),
        "remaining_at_publication_p25_p50_p75": quantiles("remaining_at_publication"),
        "source_age_seconds_p25_p50_p75": quantiles("source_age_seconds"),
        "at_least_20_rallies_remaining": sum(row["remaining_at_publication"] >= 20 for row in picks),
    }
    if immediate is not None:
        reference = {row["match_id"]: row for row in immediate}
        delays = [row["published_at_utc_epoch"] - reference[row["match_id"]]["published_at_utc_epoch"]
                  for row in picks]
        result.update(
            lost_vs_immediate=len(immediate) - len(picks),
            changed_direction_vs_immediate=sum(row["player_id"] != reference[row["match_id"]]["player_id"] for row in picks),
            delay_after_immediate_seconds_p25_p50_p75=np.quantile(delays, [0.25, 0.5, 0.75]).tolist() if delays else None,
        )
    return result


def summarize(emitted, total):
    immediate = metrics(emitted[None], total)
    by_poll = {poll: metrics(emitted[poll], total, emitted[None]) for poll in emitted if poll is not None}
    return immediate, by_poll
