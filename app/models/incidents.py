# app/models/incidents.py
from typing import Optional
from pydantic import BaseModel


class Incident(BaseModel):
    id: str
    device_id: str
    profile_id: Optional[str]
    priority_code: Optional[int]
    facility_code: Optional[int]
    message: Optional[str]
    timestamp: Optional[str]
