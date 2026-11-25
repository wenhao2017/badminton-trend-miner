"""Evaluate on held-out match dates, then refit the deployment estimator."""

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.metrics import brier_score_loss, log_loss, accuracy_score, f1_score, precision_score, recall_score, roc_auc_score


def _evaluate(model, X: pd.DataFrame, y: pd.Series) -> dict[str, float]:
    predictions = model.predict(X)
    positive_class = np.flatnonzero(model.classes_ == 1)
    probabilities = (
        model.predict_proba(X)[:, positive_class[0]]
        if len(positive_class) else np.zeros(len(X))
    )
    auc = float(roc_auc_score(y, probabilities)) if y.nunique() == 2 else 0.5
    return {
        "brier_score": float(brier_score_loss(y, probabilities)),
        "log_loss": float(log_loss(y, probabilities, labels=[0, 1])),
        "accuracy": float(accuracy_score(y, predictions)),
        "precision": float(precision_score(y, predictions, zero_division=0)),
        "recall": float(recall_score(y, predictions, zero_division=0)),
        "f1_score": float(f1_score(y, predictions, zero_division=0)),
        "roc_auc": auc if math.isfinite(auc) else 0.5,
    }


def evaluate_and_refit(model, X: pd.DataFrame, y: pd.Series, match_dates, *, baselines=None) -> tuple[Any, dict[str, Any]]:
    """Return a full-data model; metrics belong to a separate holdout estimator."""
    if len(X) != len(y) or len(X) != len(match_dates) or not len(X):
        raise ValueError("Features, labels and match dates must have the same nonzero length.")
    X, y = X.reset_index(drop=True), y.reset_index(drop=True)
    dates = pd.to_datetime(pd.Series(list(match_dates)), utc=True, errors="coerce").dt.tz_convert(None)
    if dates.isna().any():
        raise ValueError("Every training sample must have a valid match date.")
    if y.nunique() < 2:
        raise ValueError("Training labels must contain both outcome classes.")
    order = dates.sort_values(kind="stable").index
    X, y, dates = X.loc[order], y.loc[order], dates.loc[order]
    days = dates.dt.normalize()
    counts = days.value_counts().sort_index()
    if len(counts) < 3:
        raise ValueError("At least three match dates are required for independent train/validation/test splits.")
    cumulative = counts.cumsum()
    train_day_index = min(int(cumulative.searchsorted(len(X) * 0.70)), len(counts) - 3)
    valid_day_index = min(
        max(int(cumulative.searchsorted(len(X) * 0.85)), train_day_index + 1), len(counts) - 2
    )
    train_mask = days <= counts.index[train_day_index]
    valid_mask = (days > counts.index[train_day_index]) & (days <= counts.index[valid_day_index])
    test_mask = days > counts.index[valid_day_index]

    evaluation_model = clone(model)
    evaluation_model.fit(X.loc[train_mask], y.loc[train_mask])
    validation = _evaluate(evaluation_model, X.loc[valid_mask], y.loc[valid_mask])
    test = _evaluate(evaluation_model, X.loc[test_mask], y.loc[test_mask])
    metrics = {
        **test,
        "sample_count": len(X),
        "train_count": len(X),
        "evaluation_train_count": int(train_mask.sum()),
        "validation_count": int(valid_mask.sum()),
        "test_count": int(test_mask.sum()),
        "split_strategy": "utc_date_grouped_chronological_70_15_15_then_full_refit",
        "metrics_scope": "held_out_evaluation_model_before_full_refit",
        "deployment_fit_sample_count": len(X),
        "deployment_fit_from_date_utc": dates.min().isoformat(),
        "deployment_fit_to_date_utc": dates.max().isoformat(),
        "evaluation_train_to_date_utc": dates.loc[train_mask].max().isoformat(),
        "evaluation_validation_to_date_utc": dates.loc[valid_mask].max().isoformat(),
        "validation": validation,
        "test": test,
    }
    metrics["baselines"] = {}
    for name, columns in (baselines or {}).items():
        estimator = clone(model).fit(X.loc[train_mask, columns], y.loc[train_mask])
        metrics["baselines"][name] = _evaluate(estimator, X.loc[test_mask, columns], y.loc[test_mask])
    deployment_model = clone(model)
    deployment_model.fit(X, y)
    return deployment_model, metrics
