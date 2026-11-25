import json
import pickle
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier

from app.config import settings
from app.db.models import Match, Player, TrendTrainingSample
from app.db.repositories import TrendTrainingSampleRepository
from app.services.model_trainer import ModelTrainer
from app.services.model_training import evaluate_and_refit


def test_evaluation_precedes_full_refit_and_keeps_dates_separate(monkeypatch):
    events = []
    original_fit = RandomForestClassifier.fit
    original_predict = RandomForestClassifier.predict

    def record_fit(self, X, y, *args, **kwargs):
        events.append(("fit", set(X.index)))
        return original_fit(self, X, y, *args, **kwargs)

    def record_predict(self, X):
        assert not events[0][1].intersection(X.index)
        events.append(("evaluate", set(X.index)))
        return original_predict(self, X)

    monkeypatch.setattr(RandomForestClassifier, "fit", record_fit)
    monkeypatch.setattr(RandomForestClassifier, "predict", record_predict)
    X = pd.DataFrame({"value": range(60)})
    y = pd.Series([i % 2 for i in range(60)])
    dates = [datetime(2026, 1, 1) + timedelta(days=(59 - i) // 2) for i in range(60)]
    model, metrics = evaluate_and_refit(RandomForestClassifier(n_estimators=3, random_state=42), X, y, dates)

    assert [event[0] for event in events] == ["fit", "evaluate", "evaluate", "fit"]
    assert events[-1][1] == set(range(60))
    partitions = [events[i][1] for i in (0, 1, 2)]
    date_partitions = [{dates[i].date() for i in indices} for indices in partitions]
    assert not date_partitions[0] & date_partitions[1]
    assert not date_partitions[0] & date_partitions[2]
    assert not date_partitions[1] & date_partitions[2]
    assert model._n_samples == metrics["train_count"] == metrics["deployment_fit_sample_count"] == 60
    assert metrics["evaluation_train_count"] + metrics["validation_count"] + metrics["test_count"] == 60
    assert metrics["metrics_scope"] == "held_out_evaluation_model_before_full_refit"
    assert metrics["deployment_fit_to_date_utc"] == "2026-01-30T00:00:00"


def test_single_class_evaluation_prefix_does_not_use_holdout_labels():
    X = pd.DataFrame({"value": range(42)})
    dates = [datetime(2026, 1, 1)] * 40 + [datetime(2026, 1, 2), datetime(2026, 1, 3)]
    y = pd.Series([0] * 40 + [1, 1])
    model, metrics = evaluate_and_refit(RandomForestClassifier(n_estimators=3), X, y, dates)
    assert metrics["evaluation_train_count"] == 40
    assert metrics["validation"]["accuracy"] == metrics["test"]["accuracy"] == 0
    assert model._n_samples == 42


@pytest.mark.parametrize("dates", [[None] * 6, [datetime(2026, 1, 1)] * 6])
def test_invalid_or_insufficient_dates_refuse_evaluation(dates):
    with pytest.raises(ValueError, match="match date"):
        evaluate_and_refit(RandomForestClassifier(n_estimators=3), pd.DataFrame({"value": range(6)}),
                           pd.Series([0, 1] * 3), dates)


def seed_samples(db):
    db.add_all([Player(id=i, external_player_id=str(i), provider="sportradar", name=str(i)) for i in (1, 2)])
    for day in range(30):
        db.add(Match(id=day + 1, external_match_id=str(day), provider="sportradar", tournament="test",
                     tournament_id="test", match_date=datetime(2026, 1, 1) + timedelta(days=day),
                     surface="indoor", status="completed", winner_player_id=1, loser_player_id=2,
                     timeline_complete=True, last_rally_at=datetime(2026, 1, 1) + timedelta(days=day)))
        for player in (1, 2):
            db.add(TrendTrainingSample(
                match_id=day + 1, player_id=player, opponent_id=3-player, snapshot_point_index=12,
                surface="hard", set_no=1, game_no=1, feature_json={"player_games": day % 4},
                label_win_match=False, label_trend_continue_3_games=player == 1,
                created_at=datetime(2026, 11, 1) - timedelta(days=day),
            ))
    db.commit()


def test_repository_uses_match_dates_final_winners_and_all_supported_labels(db_session):
    seed_samples(db_session)
    repo = TrendTrainingSampleRepository()
    samples = repo.get_samples_for_training(db_session, limit=10)
    assert len(samples) == 10
    assert samples[0].match_date == datetime(2026, 1, 26)
    assert samples[-1].match_date == datetime(2026, 1, 30)
    assert [sample.label_win_match for sample in samples] == [True, False] * 5
    assert [sample.label_trend_continue_3_games for sample in samples] == [True, False] * 5
    db_session.get(Match, 30).status = "live"
    db_session.get(Match, 29).winner_player_id = None
    db_session.get(Match, 28).provider = "mock"
    db_session.commit()
    assert len(repo.get_samples_for_training(db_session)) == 54


def test_saved_model_and_registry_describe_full_fit_not_evaluation(db_session, tmp_path, monkeypatch):
    seed_samples(db_session)
    monkeypatch.setattr(settings, "MODEL_MIN_TRAINING_SAMPLES", 10)
    monkeypatch.setattr(settings, "MODEL_MAX_TRAINING_SAMPLES", 60)
    trainer = ModelTrainer()
    trainer.models_dir = str(tmp_path)
    record = trainer.train_model(db_session)
    with open(record.model_path, "rb") as fh:
        model = pickle.load(fh)
    assert model._n_samples == 60
    assert record.metrics_json["train_count"] == 60
    assert record.metrics_json["evaluation_train_count"] < 60
    assert record.trained_from_date == datetime(2026, 1, 1)
    assert record.trained_to_date == datetime(2026, 1, 30)
    assert set(record.metrics_json["baselines"]) == {"score_only", "score_and_prior"}


