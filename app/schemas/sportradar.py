from pydantic import BaseModel
from typing import Dict, Any, List, Optional

class SportradarBaseSchema(BaseModel):
    pass

class SportradarResponse(BaseModel):
    status_code: int
    data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
