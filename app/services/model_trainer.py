from __future__ import annotations

import os
import pickle
from collections import defaultdict
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.models import Match, TrendTrainingSample, TrendModel
from app.db.repositories import TrendModelRepository, TrendTrainingSampleRepository
from app.config import settings
from app.services.feature_engineering import FeatureExtractor
from loguru import logger
from typing import List, Dict, Any, Tuple, Optional

class ModelTrainer:
    def __init__(self):
        self.sample_repo = TrendTrainingSampleRepository()
        self.model_repo = TrendModelRepository()
        self.extractor = FeatureExtractor()
        
        self.models_dir = settings.MODEL_DIRECTORY
        os.makedirs(self.models_dir, exist_ok=True)

    def _prepare_data(self, samples: List[TrendTrainingSample], target_label: str) -> Tuple[pd.DataFrame, pd.Series]:
        """Converts database objects into pandas DataFrame (features) and Series (target)."""
        import pandas as pd

        data_list = []
        for s in samples:
            feat = s.feature_json.copy()
            # Add label
            feat["target"] = getattr(s, target_label)
            data_list.append(feat)

        df = pd.DataFrame(data_list)
        if df.empty:
            return pd.DataFrame(), pd.Series()

        feature_cols = self.extractor.feature_columns

        # Handle potential missing columns in raw JSON features
        for col in feature_cols:
            if col not in df.columns:
                df[col] = 0.0

        X = df[feature_cols].fillna(0.0)
        y = df["target"].astype(int)
        return X, y

    def train_model(self, db: Session, target_label: str = "label_win_match", *, scoring_target=None) -> Optional[TrendModel]:
        """
        Trains a RandomForest model for a given target label and stores it.
        Production training requires real labeled snapshots and refuses synthetic fallback data.
        """
        import sklearn
        from sklearn.ensemble import RandomForestClassifier
        from app.services.model_training import evaluate_and_refit

        if target_label != "label_win_match":
            raise ValueError("The badminton model supports label_win_match.")
        if scoring_target is None:
            scoring_target = db.scalar(select(Match.scoring_target).where(
                Match.timeline_complete.is_(True)).order_by(Match.match_date.desc()).limit(1)) or 21
        if scoring_target not in (15, 21):
            raise ValueError("Scoring target must be 15 or 21.")
        limit = settings.MODEL_MAX_TRAINING_SAMPLES
        samples = self.sample_repo.get_samples_for_training(db, limit=limit, scoring_target=scoring_target)
        logger.info(f"Retrieved {len(samples)} training samples for target '{target_label}'")

        X, y = self._prepare_data(samples, target_label)
        
        if len(samples) < settings.MODEL_MIN_TRAINING_SAMPLES:
            raise ValueError(
                f"Training samples ({len(samples)}) below minimum requirement "
                f"({settings.MODEL_MIN_TRAINING_SAMPLES}). Import more real historical point data, "
                "run replay/backtest sample generation, then retry."
            )

        if y.nunique() < 2:
            raise ValueError(f"Target '{target_label}' has only one class. Import broader historical data before training.")

        clf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, n_jobs=1)
        score = ["scoring_target", "game_no", "games_lead", "game_score_fraction",
                 "point_margin_fraction", "is_deciding_game"]
        clf, metrics = evaluate_and_refit(
            clf, X, y, [sample.match_date for sample in samples],
            baselines={"score_only": score,
                       "score_and_prior": score + ["prior_match_win_rate", "opponent_prior_match_win_rate",
                                                   "prior_rally_win_rate"]})
        metrics["scoring_target"] = scoring_target
        metrics["sklearn_version"] = sklearn.__version__
        metrics["deployment_fit_match_count"] = len({sample.match_id for sample in samples})
        metrics["selection_sample_limit"] = limit
        by_year = defaultdict(list)
        for sample in samples:
            by_year[sample.match_date.year].append(sample)
        metrics["deployment_fit_by_season"] = {
            str(year): {
                "matches": len({sample.match_id for sample in season}),
                "samples": len(season),
                "first_date_utc": min(sample.match_date for sample in season).date().isoformat(),
                "last_date_utc": max(sample.match_date for sample in season).date().isoformat(),
            }
            for year, season in sorted(by_year.items())
        }
        logger.info(f"Model evaluated and refitted on all {len(samples)} selected samples: {metrics}")

        # Save model pickle
        model_filename = f"rf_badminton_{scoring_target}_{target_label}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.pkl"
        model_path = os.path.join(self.models_dir, model_filename)
        with open(model_path, "wb") as f:
            pickle.dump(clf, f)

        # Log into database
        feature_cols_json = {"columns": list(X.columns)}
        
        model_record = TrendModel(
            scoring_target=scoring_target,
            model_name=f"Badminton_RF_{scoring_target}_{target_label}",
            model_type="RandomForest",
            target_label=target_label,
            feature_columns_json=feature_cols_json,
            metrics_json=metrics,
            model_path=model_path,
            trained_from_date=datetime.fromisoformat(metrics["deployment_fit_from_date_utc"]),
            trained_to_date=max(sample.last_rally_at for sample in samples)
        )
        try:
            self.model_repo.create(db, model_record)
        except Exception:
            os.unlink(model_path)
            raise
        return model_record
