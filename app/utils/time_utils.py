from datetime import datetime, timezone
import dateutil.parser

def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)

def parse_iso_datetime(dt_str: str) -> datetime:
    """Parses standard ISO 8601 strings into datetime objects."""
    if not dt_str:
        return datetime.now()
    try:
        dt = dateutil.parser.isoparse(dt_str)
        # Ensure naive datetime is converted or handled
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return datetime.now()
