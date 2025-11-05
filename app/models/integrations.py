# app/models/integrations.py
from typing import Optional, Dict, Any
from pydantic import BaseModel, validator

class IntegrationIn(BaseModel):
    profile_id: str
    destination_name: Optional[str] = None
    destination_type: Optional[str] = None
    ip_address: str
    port: int
    auth_token: Optional[str] = None

    @validator("auth_token", always=True)
    def require_token_for_splunk(cls, v: Optional[str], values: Dict[str, Any]) -> Optional[str]:
        name = (values.get("destination_name") or "")
        if name.strip().lower() == "splunk" and not v:
            raise ValueError("auth_token is required for Splunk integrations")
        return v

class IntegrationUpdate(IntegrationIn):
    pass
