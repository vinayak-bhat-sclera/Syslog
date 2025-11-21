# app/models/profile_devices.py
from typing import List
from pydantic import BaseModel, Field
from typing import Optional


class ProfileDevicesAssign(BaseModel):
    profile_id: str = Field(..., description="Syslog profile ID to assign devices to")
    device_uuids: List[str] = Field(..., description="List of device UUIDs to associate with the profile")

class ProfileDeviceDeleteRequest(BaseModel):
    device_ids: Optional[List[str]] = Field(
        default=[],
        description="List of device IDs to delete from the profile"
    )
    search: Optional[str] = Field(
        default="",
        description="Search keyword for SpringBoot lookup"
    )
    select: Optional[str] = Field(
        default="",
        description='Must be "all" when search is used; otherwise ignored'
    )