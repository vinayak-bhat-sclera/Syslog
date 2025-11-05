# app/models/profile_devices.py
from typing import List
from pydantic import BaseModel, Field


class ProfileDevicesAssign(BaseModel):
    profile_id: str = Field(..., description="Syslog profile ID to assign devices to")
    devices: List[str] = Field(..., description="List of device UUIDs to associate with the profile")
