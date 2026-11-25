import os
import pickle
from sqlalchemy.orm import Session
from app.db.models import MatchLiveSnapshot, TrendModel
from app.db.repositories import TrendModelRepository
from app.services.bwf_candidate import FEATURE_COLUMNS, MODEL_TYPE, POLICY_RULE, paired_probabilities
from app.services.feature_engineering import FeatureExtractor
from loguru import logger
from typing import Dict, Any, Optional

class TrendPredictor:
    def __init__(self):
        self.model_repo = TrendModelRepository()
        self._cached_models: Dict[str, Any] = {}
        self.extractor = FeatureExtractor()

    def _load_model(self, db: Session, target_label: str, model_record=None) -> Optional[Any]:
        """Loads and caches the latest pickled model from model records."""
        model_rec = model_record or self.model_repo.get_latest_model(db, target_label)
        if not model_rec:
            return None

        candidate = model_rec.model_type == MODEL_TYPE
        expected = FEATURE_COLUMNS if candidate else self.extractor.feature_columns
        if model_rec.feature_columns_json.get("columns") != expected:
            return None

        # Check cache
        cache_key = f"{model_rec.id}_{model_rec.model_path}"
        if cache_key in self._cached_models:
            return self._cached_models[cache_key]

        if not os.path.exists(model_rec.model_path):
            logger.warning(f"Model file not found at path: {model_rec.model_path}")
            return None

        try:
            with open(model_rec.model_path, "rb") as f:
                model = pickle.load(f)
                if candidate:
                    manifest = model["manifest"]
                    if (manifest["scoring_target"] != 21 or manifest["feature_columns"] != expected
                            or manifest["rule"] != POLICY_RULE):
                        return None
                    model = model["estimator"]
                    if list(model.feature_names_in_) != expected or list(model.classes_) != [0, 1]:
                        return None
            if len(self._cached_models) >= 2:
                del self._cached_models[next(iter(self._cached_models))]
            self._cached_models[cache_key] = model
            logger.info(f"Loaded model '{model_rec.model_name}' successfully.")
            return model
        except Exception as e:
            logger.error(f"Failed to deserialize model pickle: {e}")
            return None

    def predict_continuation(self, db: Session, snapshot: MatchLiveSnapshot) -> float:
        """
        Predicts:
        1. match_win_probability: probability the player will win the match.
        """
        feature_cols = self.extractor.feature_columns
        features_dict = self.extractor.extract_features(snapshot)
        record = self.model_repo.get_latest_model(db, "label_win_match")
        model_win = (self._load_model(db, "label_win_match", model_record=record)
                     if record and record.model_type != MODEL_TYPE else None)

        # 2. Match win outcome prediction
        if model_win:
            try:
                import pandas as pd
                df = pd.DataFrame([features_dict])[feature_cols]
                prob_win = float(model_win.predict_proba(df)[0][1])
            except Exception as e:
                logger.error(f"Inference failed on model_win: {e}")
                prob_win = snapshot.match_control_score
        else:
            # Rules fallback: if leading games/sets and high control
            prob_win = 0.5 + (snapshot.match_control_score - 0.5) * 0.6 + (snapshot.player_sets - snapshot.opponent_sets) * 0.15
            prob_win = max(0.02, min(0.98, prob_win))

        return prob_win

    def predict_batch(self, db: Session, snapshots, model_record=None):
        """Missing/broken models fail closed; one inference call per fetched batch."""
        model = self._load_model(db, "label_win_match", model_record)
        if model is None:
            return None
        try:
            import pandas as pd
            candidate = model_record is not None and model_record.model_type == MODEL_TYPE
            if candidate:
                pairs = zip(snapshots[::2], snapshots[1::2])
                if len(snapshots) % 2 or any(
                    a.match_id != b.match_id or a.snapshot_point_index != b.snapshot_point_index
                    or (a.player_id, a.opponent_id) != (b.opponent_id, b.player_id)
                    for a, b in pairs
                ):
                    return None
                frame = pd.DataFrame([{key: s.raw_snapshot_json["features"][key] for key in FEATURE_COLUMNS}
                                      for s in snapshots], columns=FEATURE_COLUMNS)
                return paired_probabilities(model.predict_proba(frame)[:, 1]).tolist()
            model.n_jobs = 1
            columns = list(getattr(model, "feature_names_in_", self.extractor.feature_columns))
            frame = pd.DataFrame([self.extractor.extract_features(s) for s in snapshots])[columns]
            return [float(p) for p in model.predict_proba(frame)[:, list(model.classes_).index(1)]]
        except Exception as exc:
            logger.error(f"Formal S inference unavailable: {type(exc).__name__}")
            return None
