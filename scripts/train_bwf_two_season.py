"""Fit, validate and save the offline 21-point women's singles candidate.

Run from the repository root with python -m scripts.train_bwf_two_season.
Input is the gzipped JSONL output of scripts/export_badminton_two_season.py.
"""

import argparse
from collections import Counter
from dataclasses import asdict, replace
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
import pickle
import platform

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from threadpoolctl import threadpool_limits

from app.services.bwf_candidate import paired_probabilities
from scripts.bwf_offline_features import LIVE_FORM, TRAJECTORY, dataset, load_matches, match_profiles, match_rows
from scripts.bwf_offline_replay import BASELINE, PRIMARY_POLLS, SENSITIVITY_POLLS, Rule, replay_inputs, simulate, summarize
from scripts.study_badminton_signals import FORM, PRIORS, PROFILE, SCORE, utc


FEATURES = {"score": SCORE, "score_profile": SCORE + PRIORS + PROFILE,
            "score_profile_form": SCORE + PRIORS + PROFILE + LIVE_FORM}
LEGACY_COLUMNS = SCORE + PRIORS + FORM
HGB = {"max_iter": 100, "max_leaf_nodes": 15, "l2_regularization": 1.0,
       "early_stopping": False, "random_state": 42}
LEGACY = {"n_estimators": 100, "max_depth": 5, "random_state": 42, "n_jobs": 2}
WINDOWS = {
    "evaluation_fit": ("2025-01-01", "2026-01-01"),
    "selection": ("2026-01-01", "2026-04-01"),
    "primary_test": ("2026-04-01", "2026-07-15"),
    "previous_selection_window": ("2026-07-15", "2026-10-05"),
    "previous_holdout_window": ("2026-10-05", "2027-01-01"),
}


def digest(path):
    with path.open("rb") as stream:
        return sha256(stream.read()).hexdigest()


def match_summary(matches):
    days = [match["scheduled"].date().isoformat() for match in matches]
    return {"matches": len(matches), "first_scheduled_date_utc": min(days), "last_scheduled_date_utc": max(days),
            "samples": sum(match["db_samples"] for match in matches),
            "rallies": sum(len(match["points"]) for match in matches),
            "last_rally_utc": max(match["finish"] for match in matches).isoformat(),
            "series": len({match["tournament_id"] for match in matches})}


def partitions(matches):
    parts = {}
    for name, (start, end) in WINDOWS.items():
        lo, hi = utc(start), utc(end)
        parts[name] = [match for match in matches if lo <= match["scheduled"] < hi and
                       lo <= match["start"] and match["finish"] < hi]
        if not parts[name]:
            raise ValueError(f"Empty partition {name}")
    assigned = [match["id"] for group in parts.values() for match in group]
    if len(assigned) != len(set(assigned)):
        raise ValueError("Overlapping match partitions")
    excluded = sorted({match["id"] for match in matches} - set(assigned))
    return parts, excluded


def fit(frame, cols, legacy=False):
    if legacy:
        return RandomForestClassifier(**LEGACY).fit(frame[cols], frame.won)
    weights = 1 / frame.groupby("match_id").match_id.transform("size")
    return HistGradientBoostingClassifier(**HGB).fit(frame[cols], frame.won, sample_weight=weights / weights.mean())


def predict(estimator, frame, cols, *, paired=True):
    probability = estimator.predict_proba(frame[cols])[:, 1]
    if paired:
        return paired_probabilities(probability)
    return np.clip(probability, 1e-6, 1 - 1e-6)


def probability_metrics(frame, probability):
    y = frame.won.to_numpy()
    data = pd.DataFrame({"match_id": frame.match_id, "rally": frame.rally, "won": y, "p": probability,
                         "brier": (probability - y) ** 2,
                         "log_loss": -np.where(y, np.log(probability), np.log1p(-probability))})
    metrics = data.groupby("match_id")[["brier", "log_loss"]].mean().mean().to_dict()
    metrics["opening_brier_12_to_24"] = data[data.rally <= 24].groupby("match_id").brier.mean().mean()
    reliability = []
    for lo, hi in zip((.50, .70, .85, .89, .92, .95), (.70, .85, .89, .92, .95, 1.0)):
        selected = data[data.p.ge(lo) & data.p.lt(hi)]
        per_match = selected.groupby("match_id")[["won", "p"]].mean().mean()
        reliability.append({"lower": lo, "upper": hi, "states": len(selected), "matches": selected.match_id.nunique(),
                            "probability": float(per_match.p) if len(selected) else None,
                            "observed_win_rate": float(per_match.won) if len(selected) else None})
    return {**metrics, "reliability_match_weighted": reliability}


def paired_brier_delta(frame, reference, variant):
    values = frame.assign(delta=(variant - frame.won) ** 2 - (reference - frame.won) ** 2)
    values = values.groupby("match_id").delta.mean().to_numpy()
    rng = np.random.default_rng(42)
    samples = rng.choice(values, (2000, len(values)), replace=True).mean(axis=1)
    return {"variant_minus_reference": float(values.mean()), "match_bootstrap_95": np.quantile(samples, [.025, .975]).tolist()}


def grid_rules(trend):
    return [Rule(start, run, threshold, trend) for start, run, threshold in
            product((12, 16, 20, 24, 30), (1, 2, 3), (.85, .89, .92, .95))]


def rule_result(inputs, rule, polls, *, picks=False):
    emitted = simulate(inputs, rule, polls)
    immediate, by_poll = summarize(emitted, len(inputs))
    return {"rule": asdict(rule), "immediate": immediate,
            "polled": [{"poll": asdict(poll), **metrics} for poll, metrics in by_poll.items()],
            **({"picks": {"immediate": emitted[None], "primary_poll_phase_0": emitted[PRIMARY_POLLS[0]]}} if picks else {})}


def select_rule(rows):
    viable = [row for row in rows if all(poll["coverage"] >= .25 and
              poll["remaining_at_publication_p25_p50_p75"][1] >= 25 for poll in row["polled"])]
    if not viable:
        raise ValueError("No rule met the selection coverage and lead-time floors")
    best = max(viable, key=lambda row: (min(p["wilson_95"][0] for p in row["polled"]),
                                       min(p["remaining_at_publication_p25_p50_p75"][1] for p in row["polled"])))
    return Rule(**best["rule"])


def risk_audit(frame, predictions, leader_model, leader_cols, path_model, path_cols, selected):
    ahead = frame[frame.score_ahead].copy()
    binary = 1 - ahead.won.to_numpy()
    have_game = ahead.games_lead.to_numpy() > 0
    general = 1 - predictions[selected][frame.score_ahead.to_numpy()]
    leader = 1 - predict(leader_model, ahead, leader_cols, paired=False)
    paths = 1 - predict(path_model, frame, path_cols)[frame.score_ahead.to_numpy()]
    result = {}
    for label, keep in (("all_ahead", np.ones(len(ahead), dtype=bool)), ("game_ahead", have_game),
                        ("points_ahead_in_tied_games", ~have_game)):
        subset = ahead.iloc[keep].assign(won=binary[keep])
        result[label] = {
            "states": len(subset), "matches": int(subset.match_id.nunique()),
            "matches_with_losing_leader": int(subset.loc[subset.won.eq(1), "match_id"].nunique()),
            "observed_loss_match_weighted": subset.groupby("match_id").won.mean().mean(),
            "complement": probability_metrics(subset, general[keep])["brier"],
            "global_with_lead_trajectory": probability_metrics(subset, paths[keep])["brier"],
            "specialized_leader": probability_metrics(subset, leader[keep])["brier"],
            "trajectory_delta": paired_brier_delta(subset, general[keep], paths[keep]),
            "specialized_delta": paired_brier_delta(subset, general[keep], leader[keep]),
        }
    return result


def trend_audit(frame, probabilities, inputs, candidate):
    recent = frame.recent20.to_numpy()
    profile = probabilities["score_profile"]
    form = probabilities["score_profile_form"]
    form_delta = form - profile
    recent_delta = recent - profile
    experiments = {}
    for label, rule in (("current_threshold", BASELINE), ("candidate_threshold", candidate)):
        experiments[label] = {}
        for name in ("score_profile", "score_profile_form"):
            probability = probabilities[name]
            blend = .80 * probability + .20 * recent
            grades = (frame.rally.to_numpy() >= rule.start) & (frame.prior.to_numpy() >= rule.history)
            grades &= (probability >= rule.threshold) & (probability.reshape(-1, 2)[:, ::-1].ravel() < .70)
            experiments[label][name] = {
                "otherwise_eligible_s_states": int(grades.sum()),
                "states_below_blended_trend_0_70": int((grades & (blend < .70)).sum()),
                "results": {mode: rule_result(inputs[name], replace(rule, trend=mode), PRIMARY_POLLS)
                            for mode in ("blend", "probability")},
            }
    return {
        "pearson_form_model_increment_vs_recent20_minus_profile_probability": float(np.corrcoef(form_delta, recent_delta)[0, 1]),
        "average_absolute_model_increment": float(np.abs(form_delta).mean()),
        "average_absolute_external_blend_increment_on_form_model": float((.20 * np.abs(recent - form)).mean()),
        "experiments": experiments,
    }


def json_write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run(source, output, model_path):
    metadata, all_matches = load_matches(source)
    matches = [match for match in all_matches if match["scoring_target"] == 21]
    index = {match["id"]: match for match in matches}
    frame, profiles = dataset(matches)
    split, excluded = partitions(matches)
    frames = {name: frame[frame.match_id.isin(match["id"] for match in group)]
              for name, group in split.items()}
    train = frames.pop("evaluation_fit")
    selection = frames["selection"]
    estimators = {name: fit(train, cols) for name, cols in FEATURES.items()}
    selection_probs = {name: predict(estimator, selection, FEATURES[name]) for name, estimator in estimators.items()}
    selection_metrics = {name: probability_metrics(selection, probabilities)
                         for name, probabilities in selection_probs.items()}
    selected = min(selection_metrics, key=lambda name: selection_metrics[name]["brier"])
    # Keep the external short-term blend only when the chosen estimator excludes explicit live form.
    trend = "probability" if selected == "score_profile_form" else "blend"
    selection_inputs = replay_inputs(selection, selection_probs[selected], index, PRIMARY_POLLS)
    rules = grid_rules(trend)
    selection_grid = [rule_result(selection_inputs, rule, PRIMARY_POLLS) for rule in rules]
    candidate = select_rule(selection_grid)
    print(f"Locked on selection: {selected}, {candidate}", flush=True)

    legacy = fit(train, LEGACY_COLUMNS, legacy=True)
    path_cols = FEATURES[selected] + TRAJECTORY
    path_model = fit(train, path_cols)
    leader_model = fit(train[train.score_ahead], path_cols)
    polls = (*PRIMARY_POLLS, *SENSITIVITY_POLLS)
    prior_holdout_rules = [Rule(start, 3, .85) for start in (8, 12, 16, 20, 24)]
    prior_holdout_rules += [Rule(12, run, .85) for run in (1, 2, 4)]
    prior_holdout_rules += [Rule(12, run, .92, history=5) for run in (1, 2, 3, 4)]
    result = {
        "source": {**metadata, "export_sha256": digest(source)},
        "target": 21, "scoring_target_export_counts": dict(Counter(str(match["scoring_target"]) for match in all_matches)),
        "timing": {"start_evidence": dict(Counter(match["start_evidence"] for match in matches)),
                   "poll_replay_unusable": sum(not match["poll_usable"] for match in matches)},
        "splits": {name: {"window_utc": WINDOWS[name], **match_summary(group)} for name, group in split.items()},
        "boundary_match_ids_excluded_from_evaluation": excluded,
        "full_fit_by_season": {str(year): match_summary([match for match in matches if match["scheduled"].year == year])
                               for year in (2025, 2026)},
        "full_fit": match_summary(matches), "features": FEATURES,
        "selected_model": selected, "selected_rule": asdict(candidate),
        "selection_criteria": ("Minimum match-weighted Brier. Among rules with at least 25% coverage and median "
                               "25 actual rallies left in each primary poll phase, maximize the minimum Wilson "
                               "lower bound then the minimum median actual rallies left across phases."),
        "previous_holdout_rules": [asdict(rule) for rule in prior_holdout_rules],
        "selection_rule_grid": selection_grid, "evaluation": {},
    }

    season_2026 = [match for match in matches if match["scheduled"].year == 2026]
    carried = [profile for match in season_2026 for profile in profiles[match["id"]]]
    result["profile_audit"] = {
        "2026_player_match_sides": len(carried),
        "2026_sides_with_any_completed_2025_history": sum(p["prior_previous_season"] > 0 for p in carried),
        "2026_matches_with_both_sides_having_2025_history": sum(
            all(p["prior_previous_season"] > 0 for p in profiles[match["id"]]) for match in season_2026),
        "2026_sides_eligible_at_10": sum(p["career_matches"] >= 10 for p in carried),
        "2026_sides_eligible_only_after_2025_carryover": sum(
            p["career_matches"] >= 10 > p["career_matches"] - p["prior_previous_season"] for p in carried),
    }
    reset = pd.DataFrame(row for match, prior in match_profiles(season_2026) for row in match_rows(match, prior))
    reset = reset.set_index(["match_id", "rally", "side"])
    for name, sample in frames.items():
        print(f"Evaluating {name}: {sample.match_id.nunique()} matches", flush=True)
        probabilities = (selection_probs if name == "selection" else
                         {key: predict(estimator, sample, FEATURES[key]) for key, estimator in estimators.items()})
        reset_sample = reset.loc[pd.MultiIndex.from_frame(sample[["match_id", "rally", "side"]])]
        reset_probs = predict(estimators["score_profile"], reset_sample, FEATURES["score_profile"])
        inputs = {key: replay_inputs(sample, p, index, polls) for key, p in probabilities.items()}
        legacy_probs = predict(legacy, sample, LEGACY_COLUMNS, paired=False)
        reference = replay_inputs(sample, legacy_probs, index, polls)
        chosen = inputs[selected]
        block = {
            "models": {key: probability_metrics(sample, p) for key, p in probabilities.items()},
            "legacy_rf": probability_metrics(sample, legacy_probs),
            "profile_vs_score": paired_brier_delta(sample, probabilities["score"], probabilities["score_profile"]),
            "form_vs_profile": paired_brier_delta(sample, probabilities["score_profile"], probabilities["score_profile_form"]),
            "reset_2025_history_vs_carried": paired_brier_delta(sample, probabilities["score_profile"], reset_probs),
            "probability_pair_max_sum_error": float(np.max(np.abs(probabilities[selected].reshape(-1, 2).sum(1) - 1))),
            "legacy_pair_abs_sum_error_mean": float(np.mean(np.abs(legacy_probs.reshape(-1, 2).sum(1) - 1))),
            "leading_loss": risk_audit(sample, probabilities, leader_model, path_cols,
                                       path_model, path_cols, selected),
            "production_reference_legacy_rf": rule_result(reference, BASELINE, polls, picks=True),
            "current_rule_on_selected_model": rule_result(chosen, BASELINE, polls, picks=True),
            "candidate": rule_result(chosen, candidate, polls, picks=True),
            "previous_study_candidate_on_selected_model": rule_result(
                chosen, Rule(12, 1, .92, history=5), PRIMARY_POLLS),
            "trend_ablation": trend_audit(sample, probabilities, inputs, candidate),
        }
        if name in ("primary_test", "previous_holdout_window"):
            block["policy_grid"] = [rule_result(chosen, rule, PRIMARY_POLLS) for rule in rules]
        result["evaluation"][name] = block

    final = fit(frame, FEATURES[selected])
    manifest = {"model": selected, "scoring_target": 21, "label": "label_win_match",
                "feature_columns": FEATURES[selected], "estimator_params": HGB,
                "fit": result["full_fit"], "fit_by_season": result["full_fit_by_season"],
                "source_sha256": result["source"]["export_sha256"],
                "row_weights": "equal total weight per match",
                "pair_transform": "home=(raw_home+1-raw_away)/2; away=1-home; clip to [1e-6, 1-1e-6]",
                "rule": asdict(candidate), "python": platform.python_version(),
                "sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__,
                "threadpool_limit": 2}
    probe = frame.iloc[:24]
    with model_path.open("wb") as stream:
        pickle.dump({"estimator": final, "manifest": manifest}, stream, protocol=5)
    with model_path.open("rb") as stream:
        loaded = pickle.load(stream)
    if not np.array_equal(predict(final, probe, FEATURES[selected]),
                          predict(loaded["estimator"], probe, loaded["manifest"]["feature_columns"])):
        raise ValueError("Saved model failed probability round-trip")
    result["artifact"] = {**manifest, "model_sha256": digest(model_path), "round_trip_rows": len(probe)}
    json_write(model_path.with_suffix(".manifest.json"), result["artifact"])
    json_write(output, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.output, args.model):
        path.parent.mkdir(parents=True, exist_ok=True)
    with threadpool_limits(limits=2):
        run(args.source, args.output, args.model)


if __name__ == "__main__":
    main()
