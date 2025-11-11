# app/models/profiles.py
from typing import List, Optional
from pydantic import BaseModel, Field


class ProfileBase(BaseModel):
    name: Optional[str] = Field(None, description="Profile name")
    priorities: Optional[List[int]] = Field(None, description="List of priority codes")
    facilities: Optional[List[int]] = Field(None, description="List of facility codes")
    keywords: Optional[List[str]] = Field(None, description="List of keywords for matching")
    device_ids: Optional[List[str]] = Field(None, description="Device IDs associated with this profile")


class ProfileIn(ProfileBase):
    type: str = Field(..., pattern="^(internal|external)$", description="Profile type (internal|external)")


class ProfileUpdate(ProfileBase):
    pass
