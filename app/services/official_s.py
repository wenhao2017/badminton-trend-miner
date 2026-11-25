"""Model-backed confirmation and one immutable publication per match."""
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.config import settings
from app.db.models import Match, MatchLiveSnapshot, MatchPlayer, MatchSConfirmation, TrendModel, TrendSignalLog
from app.services.bwf_candidate import MODEL_TYPE, POLICY, POLICY_RULE, profile_cutoff
from app.services.signal_admission import is_signal_excluded
from app.services.signal_engine import LEGACY_RULE, official_rule_version, signal_entry_allowed

RULE_VERSION = LEGACY_RULE


def advance_confirmation(previous, event, rows, *, rule=RULE_VERSION):
    state = deepcopy(previous or {})
    index = event.point_index
    last = state.get("last_index", 0)
    if index <= last:
        return state
    if last and index != last + 1:
        state["players"] = {}
    players = state.setdefault("players", {})
    count = POLICY_RULE["run"] if rule == POLICY else 3
    threshold = .70 if rule == POLICY else settings.S_CONFIRMATION_TREND_THRESHOLD
    for pid, row in rows.items():
        other = next(value for other_id, value in rows.items() if other_id != pid)
        item = players.setdefault(str(pid), {"run": [], "confirmation": None})
        if row["level"] not in ("S", "A") or other["level"] in ("S", "A"):
            item["run"], item["confirmation"] = [], None
        elif row["level"] != "S":
            item["run"] = []
        elif event.raw_event_json.get("type") == "rally":
            item["run"] = (item["run"] + [index])[-count:]
        if len(item["run"]) == count and row["level"] == "S" and row["trend"] >= threshold:
            item["confirmation"] = {"point": index, "support_points": list(item["run"]), "trend": row["trend"],
                                    "source_time": event.raw_event_json.get("time")}
            state.setdefault("first_qualifications", {}).setdefault(str(pid), deepcopy(item["confirmation"]))
        item["latest"] = {**row, "point": index, "source_time": event.raw_event_json.get("time")}
    state.update(last_index=index, rule_version=rule)
    return state


def can_publish(state, player_id):
    item = (state.get("players") or {}).get(str(player_id), {})
    latest = item.get("latest") or {}
    threshold = .70 if state.get("rule_version") == POLICY else settings.S_CONFIRMATION_TREND_THRESHOLD
    return bool(item.get("confirmation") and latest.get("level") == "S" and latest["trend"] >= threshold)


def model_for_match(db, match, *, cutoff=None):
    rule = official_rule_version(match)
    family = TrendModel.model_type == MODEL_TYPE if rule == POLICY else TrendModel.model_type != MODEL_TYPE
    safe_cutoff = cutoff or match.match_date.replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(TrendModel).where(
        TrendModel.target_label == "label_win_match",
        TrendModel.scoring_target == match.scoring_target,
        TrendModel.trained_to_date < safe_cutoff, family,
    ).order_by(TrendModel.id.desc()).limit(1))


def evaluate_rallies(db, match, points, players, replay, predictor, engine, model, *, after=0, cutoff=None):
    rule = official_rule_version(match)
    profiles = None
    batches, snapshots = [], []
    for point in points:
        if point.point_index <= after or not signal_entry_allowed(point.game_no, point.point_index, rule=rule):
            continue
        if rule == POLICY and profiles is None:
            cutoff = cutoff or profile_cutoff(match.match_date, points[0].raw_event_json.get("time"))
            profiles = replay.candidate_history.profiles(db, cutoff, players)
        pair = [replay.generate_snapshot_for_point(db, match.id, point.point_index, points, pid, opponent,
                                                   candidate_profiles=profiles)
                for pid, opponent in (players, players[::-1])]
        if pair[0].raw_snapshot_json["match_ended"]:
            break
        batches.append((point, pair))
        snapshots.extend(pair)
    if not snapshots:
        return
    probabilities = predictor.predict_batch(db, snapshots, model_record=model)
    if probabilities is None:
        return
    for index, (point, pair) in enumerate(batches):
        graded = {s.player_id: engine.evaluate_snapshot(db, s, probabilities[2 * index + j], rule=rule)
                  for j, s in enumerate(pair)}
        rows = {pid: {"level": value["level"], "trend": value["fusion"]["final_trend_probability"],
                      "probability": value["fusion"]["final_win_probability"]}
                for pid, value in graded.items()}
        yield point, pair, graded, rows


def process_official_batch(db, match_id, replay, predictor, engine, *, observed_at=None, start_markers=()):
    from app.services.live_monitor import fresh_live_point

    # SQLite acquires its write reservation before looking up the publication.
    db.execute(text("BEGIN IMMEDIATE"))
    try:
        match = db.get(Match, match_id, populate_existing=True)
        if (not match or not match.timeline_ready or match.status != "live"
                or is_signal_excluded(match)):
            db.commit()
            return None
        points = replay.point_repo.get_points_by_match(db, match_id)
        record = db.get(MatchSConfirmation, match_id)
        published = bool((record and record.published_signal_id) or db.scalar(
            select(TrendSignalLog.id).where(TrendSignalLog.official_match_id == match_id)))
        if not points:
            db.commit()
            return None
        rule = official_rule_version(match)
        cutoff = (profile_cutoff(match.match_date, points[0].raw_event_json.get("time"), start_markers)
                  if rule == POLICY else None)
        model = model_for_match(db, match, cutoff=cutoff)
        players = list(db.scalars(select(MatchPlayer.player_id).where(
            MatchPlayer.match_id == match_id).order_by(MatchPlayer.side.desc())))
        if model is None or len(players) != 2:
            db.commit()
            return None
        if record is None:
            record = MatchSConfirmation(match_id=match_id, last_point_index=0, state_json={})
            db.add(record)
        if record.model_id != model.id or record.state_json.get("rule_version") != rule:
            record.last_point_index, record.state_json, record.model_id = 0, {}, model.id

        latest = None
        state = record.state_json
        for point, snapshots, evaluated, rows in evaluate_rallies(
                db, match, points, players, replay, predictor, engine, model, after=record.last_point_index, cutoff=cutoff):
            state = advance_confirmation(state, point, rows, rule=rule)
            for snapshot in snapshots:
                snapshot.raw_snapshot_json["official_s_raw_grade"] = rows[snapshot.player_id]
            latest = point, snapshots, evaluated
        signal = None
        if latest:
            point, snapshots, evaluated = latest
            record.state_json, record.last_point_index = state, state["last_index"]
            record.updated_at = datetime.utcnow()
            protected = select(TrendSignalLog.snapshot_id).where(
                TrendSignalLog.match_id == match_id, TrendSignalLog.snapshot_id.is_not(None))
            db.query(MatchLiveSnapshot).filter(
                MatchLiveSnapshot.match_id == match_id, MatchLiveSnapshot.id.not_in(protected),
                MatchLiveSnapshot.snapshot_point_index == point.point_index,
            ).delete(synchronize_session=False)
            db.add_all(snapshots)
            if not published and point.point_index == points[-1].point_index and fresh_live_point(point):
                candidates = [s for s in snapshots if can_publish(state, s.player_id)]
                if len(candidates) == 1:
                    chosen = candidates[0]
                    confirmation = state["players"][str(chosen.player_id)]["confirmation"]
                    evidence = {"version": rule, "model_id": model.id, "confirmation": confirmation,
                                "published_point": point.point_index, "published_score": chosen.current_score_text,
                                "publication_trend": evaluated[chosen.player_id]["fusion"]["final_trend_probability"],
                                "frozen_direction": True,
                                "timing": {"observed_at": observed_at, "published_source_time": confirmation["source_time"],
                                           "evaluated_at": datetime.now(timezone.utc).isoformat(),
                                           "first_qualification": state["first_qualifications"][str(chosen.player_id)]}}
                    db.flush()
                    _, signal = engine.process_snapshot(db, chosen, evaluation=evaluated[chosen.player_id],
                                                        official_confirmation=evidence)
                    if signal:
                        record.published_signal_id = signal.id
        db.commit()
        return signal
    except Exception:
        db.rollback()
        raise

