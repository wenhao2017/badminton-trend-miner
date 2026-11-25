import hashlib

def hash_api_key(api_key: str) -> str:
    """
    Returns a secure SHA-256 hash of the API key.
    If the key is short (like dummy keys in tests), returns it as-is or hashed.
    """
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

def mask_key(api_key: str) -> str:
    """
    Returns the first 8 characters of an API key and truncates the rest for logs.
    """
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:8]}***"
