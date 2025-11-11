# app/models/integrations.py
from typing import Optional, Dict, Any
from pydantic import BaseModel, validator, Field


class IntegrationIn(BaseModel):
    profile_id: str = Field(..., description="Associated profile ID")
    destination_name: Optional[str] = Field(None, description="Integration destination name (e.g. Splunk, ELK)")
    ip_address: str = Field(..., description="Destination IP address or hostname")
    port: int = Field(..., description="Destination port number")
    auth_token: Optional[str] = Field(None, description="Optional auth token (required for Splunk)")

    @validator("auth_token", always=True)
    def require_token_for_splunk(cls, v: Optional[str], values: Dict[str, Any]) -> Optional[str]:
        name = (values.get("destination_name") or "").lower()
        if name == "splunk" and not v:
            raise ValueError("auth_token is required for Splunk integrations")
        return v


class IntegrationUpdate(IntegrationIn):
    pass
