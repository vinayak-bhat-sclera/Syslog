# app/models/profiles.py
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

class ProfileBase(BaseModel):
    name: Optional[str] = Field(None, description="Profile name")
    priorities: Optional[List[int]] = Field(None, description="List of priority codes")
    facilities: Optional[List[int]] = Field(None, description="List of facility codes")
    keywords: Optional[List[str]] = Field(None, description="List of keywords for matching")
    device_ids: Optional[List[str]] = Field(None, description="Device IDs associated with this profile")

    # ---------------------------------------------------------
    # Normalize device_ids → strip whitespace
    # ---------------------------------------------------------
    @field_validator("device_ids", mode="before")
    def normalize_device_ids(cls, v):
        if not v:
            return v
        if isinstance(v, list):
            return [item.strip() for item in v if isinstance(item, str)]
        return v

    # ---------------------------------------------------------
    # Normalize keywords → lowercase + strip
    # ---------------------------------------------------------
    @field_validator("keywords", mode="before")
    def normalize_keywords(cls, v):
        if not v:
            return v
        if isinstance(v, list):
            return [item.lower().strip() for item in v if isinstance(item, str)]
        return v


class ProfileIn(ProfileBase):
    type: str = Field(..., pattern="^(internal|external)$", description="Profile type (internal|external)")


class ProfileUpdate(ProfileBase):

    # Validate non-empty name (if provided)
    @field_validator("name")
    def validate_name(cls, v):
        if v is None:
            return v
        if not v.strip():
            raise ValueError("Profile name cannot be empty or whitespace")
        return v.strip()
