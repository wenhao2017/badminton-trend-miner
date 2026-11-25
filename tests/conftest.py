import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
import app.db.models # Ensure tables are registered

# Disable alerts during test execution to prevent real Telegram/email notifications
from app.config import settings
settings.ENABLE_TELEGRAM_ALERT = False
settings.ENABLE_EMAIL_ALERT = False


@pytest.fixture(scope="function")
def db_session():
    """In-memory SQLite session for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
