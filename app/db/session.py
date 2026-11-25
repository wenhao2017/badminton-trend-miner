from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import settings

url = make_url(settings.DATABASE_URL)
if url.get_backend_name() != "sqlite":
    raise ValueError("DATABASE_URL must point to a SQLite database.")
if url.database and url.database != ":memory:":
    Path(url.database).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})


@event.listens_for(engine, "connect")
def configure_sqlite(connection, _):
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


SessionLocal = sessionmaker(autoflush=False, bind=engine)


def get_db():
    with SessionLocal() as db:
        yield db

