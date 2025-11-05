# app/models/incidents.py
# currently not used in request bodies but placeholder for future typed responses
from pydantic import BaseModel
from typing import Optional

class Incident(BaseModel):
    id: str
    device_id: str
    profile_id: Optional[str]
    priority_code: Optional[int]
    facility_code: Optional[int]
    message: Optional[str]
    timestamp: Optional[str]
