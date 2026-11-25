"""Verify a trusted offline model bundle and register it for 21-point inference."""

import argparse
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import pickle

from sqlalchemy import select, text

from app.config import settings
from app.db.models import TrendModel
from app.db.session import SessionLocal
from app.services.bwf_candidate import FEATURE_COLUMNS, MODEL_TYPE, POLICY, POLICY_RULE, utc_naive


def register(path, expected_hash):
    import numpy
    import pandas
    import sklearn

    path = path.resolve(strict=True)
    path.relative_to(Path(settings.MODEL_DIRECTORY).resolve(strict=True))
    payload = path.read_bytes()
    if sha256(payload).hexdigest() != expected_hash:
        raise ValueError("Candidate model SHA-256 mismatch")
    bundle = pickle.loads(payload)
    manifest, model = bundle["manifest"], bundle["estimator"]
    if (manifest["scoring_target"] != 21 or manifest["label"] != "label_win_match"
            or manifest["rule"] != POLICY_RULE or manifest["feature_columns"] != FEATURE_COLUMNS
            or list(model.feature_names_in_) != FEATURE_COLUMNS or list(model.classes_) != [0, 1]):
        raise ValueError("Model is incompatible with the 21-point policy")
    versions = {"sklearn": sklearn.__version__, "numpy": numpy.__version__, "pandas": pandas.__version__}
    if any(manifest[name] != version for name, version in versions.items()):
        raise ValueError(f"Candidate model dependency versions differ from {versions}")

    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        existing = db.scalar(select(TrendModel).where(TrendModel.model_path == str(path)))
        if existing is not None:
            if existing.model_type != MODEL_TYPE or existing.metrics_json.get("model_sha256") != expected_hash:
                raise ValueError("Model path is already registered with different metadata")
            return existing.id
        record = TrendModel(scoring_target=21, model_name=f"{POLICY}-{expected_hash[:12]}",
                            model_type=MODEL_TYPE, target_label="label_win_match",
                            feature_columns_json={"columns": FEATURE_COLUMNS},
                            metrics_json={**manifest, "model_sha256": expected_hash, "policy": POLICY},
                            model_path=str(path),
                            trained_from_date=datetime.fromisoformat(manifest["fit"]["first_scheduled_date_utc"]),
                            trained_to_date=utc_naive(manifest["fit"]["last_rally_utc"]))
        db.add(record)
        db.flush()
        record_id = record.id
        db.commit()
        return record_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    print(f"Registered 21-point model ID {register(args.model, args.sha256)}")
