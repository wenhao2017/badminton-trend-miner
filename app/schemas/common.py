from pydantic import BaseModel
from typing import Optional, Any

class MessageResponse(BaseModel):
    message: str

class APIQuotaInfo(BaseModel):
    api_key_hash: str
    quota_limit: int
    quota_used: int
    quota_remaining: int
    cooldown_until: Optional[str] = None
    last_request_at: Optional[str] = None
