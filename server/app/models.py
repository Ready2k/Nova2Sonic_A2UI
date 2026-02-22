from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class WebSocketMessage(BaseModel):
    type: str
    ts: str = Field(default_factory=now_iso)
    sessionId: str
    payload: Optional[Dict[str, Any]] = None

class ActionPayload(BaseModel):
    actionId: str
    data: Optional[Dict[str, Any]] = None
