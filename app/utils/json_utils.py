import json
from datetime import datetime
from typing import Any

class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

def dumps(obj: Any) -> str:
    return json.dumps(obj, cls=CustomJSONEncoder)

def loads(s: str) -> Any:
    return json.loads(s)
