import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.services.historical_importer import HistoricalImporter


def main():
    parser = argparse.ArgumentParser(description="Import completed badminton women's singles matches.")
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        result = HistoricalImporter().import_date_range(db, args.start_date, args.end_date, force=args.force)
        print(json.dumps(result, indent=2))
        return int(bool(result["failed_imports"]))


if __name__ == "__main__":
    raise SystemExit(main())

