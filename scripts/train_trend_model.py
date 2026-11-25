import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.services.model_trainer import ModelTrainer


def main():
    parser = argparse.ArgumentParser(description="Train a badminton match-winner model.")
    parser.add_argument("--scoring-target", type=int, choices=(15, 21))
    args = parser.parse_args()
    with SessionLocal() as db:
        model = ModelTrainer().train_model(db, scoring_target=args.scoring_target)
        print(json.dumps({"model_id": model.id, "metrics": model.metrics_json}, indent=2))


if __name__ == "__main__":
    main()

