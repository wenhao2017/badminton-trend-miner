"""Create a consistent live SQLite backup with the models it references."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory

STATE = Path("/var/lib/badminton-trend-miner")
BACKUP_NAME = re.compile(r"backup-\d{8}T\d{12}Z")


def backup(database: Path, model_directory: Path, output: Path, retain: int) -> Path:
    database = database.resolve(strict=True)
    model_directory = model_directory.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = output / f"backup-{timestamp}"

    with TemporaryDirectory(prefix=".backup-", dir=output) as temporary:
        bundle = Path(temporary) / "bundle"
        bundle.mkdir(mode=0o700)
        target_db = bundle / database.name
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=15)) as source:
            with closing(sqlite3.connect(target_db)) as target:
                source.backup(target, pages=256, sleep=0.1)
                target.execute("PRAGMA journal_mode=DELETE")
                if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise RuntimeError("SQLite backup failed its integrity check")
                paths = [row[0] for row in target.execute("SELECT DISTINCT model_path FROM trend_models")]

        for saved_path in paths:
            source_model = Path(saved_path)
            if not source_model.is_absolute():
                source_model = model_directory / source_model.name
            source_model = source_model.resolve(strict=True)
            relative = source_model.relative_to(model_directory)
            target_model = bundle / "models" / relative
            target_model.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_model, target_model)

        bundle.rename(destination)

    managed = sorted(item for item in output.iterdir()
                     if BACKUP_NAME.fullmatch(item.name) and item.is_dir() and not item.is_symlink())
    for old in managed[:-retain]:
        shutil.rmtree(old)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=STATE / "data/badminton.sqlite3")
    parser.add_argument("--models", type=Path, default=STATE / "models")
    parser.add_argument("--output", type=Path, default=Path("/var/backups/badminton-trend-miner"))
    parser.add_argument("--retain", type=int, default=14)
    args = parser.parse_args()
    if args.retain < 1:
        parser.error("--retain must be positive")
    print(backup(args.database, args.models, args.output, args.retain))


if __name__ == "__main__":
    main()
