import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.services.backtest_engine import BacktestEngine


def main():
    parser = argparse.ArgumentParser(description="Replay badminton rallies using prior-trained models.")
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", required=True)
    parser.add_argument("--name", default="badminton-replay")
    args = parser.parse_args()
    with SessionLocal() as db:
        run = BacktestEngine().run_backtest(db, args.name, args.start_date, args.end_date)
        print(json.dumps(run.metrics_json, indent=2))


if __name__ == "__main__":
    main()

