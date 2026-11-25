import uuid

def generate_uuid() -> str:
    return str(uuid.uuid4())

def parse_external_id(raw_id: str) -> str:
    """Extract id or keep it clean."""
    if not raw_id:
        return ""
    # Sportradar IDs look like: sr:competitor:12345 or sr:match:67890
    return raw_id.strip()
