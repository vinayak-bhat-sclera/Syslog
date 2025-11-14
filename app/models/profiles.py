# app/models/profiles.py
from typing import List, Optional
from pydantic import BaseModel, Field, validator


class ProfileBase(BaseModel):
    name: Optional[str] = Field(None, description="Profile name")
    priorities: Optional[List[int]] = Field(None, description="List of priority codes")
    facilities: Optional[List[int]] = Field(None, description="List of facility codes")
    keywords: Optional[List[str]] = Field(None, description="List of keywords for matching")
    device_ids: Optional[List[str]] = Field(None, description="Device IDs associated with this profile")

    # Strip whitespace from each device_id
    @validator("device_ids", each_item=True)
    def normalize_device_ids(cls, v):
        return v.strip() if isinstance(v, str) else v

    # Normalize keywords: lowercase + strip
    @validator("keywords", each_item=True)
    def normalize_keywords(cls, v):
        return v.lower().strip() if isinstance(v, str) else v


class ProfileIn(ProfileBase):
    type: str = Field(..., pattern="^(internal|external)$", description="Profile type (internal|external)")


class ProfileUpdate(ProfileBase):
    pass
