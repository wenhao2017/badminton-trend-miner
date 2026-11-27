from datetime import datetime, timezone
import dateutil.parser

def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)

def parse_iso_datetime(dt_str: str) -> datetime:
    """Parses standard ISO 8601 strings into UTC naive datetime objects."""
    if not dt_str:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        dt = dateutil.parser.isoparse(dt_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)
