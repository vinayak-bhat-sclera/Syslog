# app/models/profiles.py
from typing import List, Optional, Annotated
from pydantic import BaseModel, Field, StringConstraints


class ProfileBase(BaseModel):
    name: Optional[str] = Field(None, description="Profile name")
    priorities: Optional[List[int]] = Field(None, description="List of priority codes")
    facilities: Optional[List[int]] = Field(None, description="List of facility codes")
    keywords: Optional[List[str]] = Field(None, description="List of keywords for matching")
    devices: Optional[List[str]] = Field(None, description="List of device UUIDs associated with this profile")


class ProfileIn(ProfileBase):
    type: Annotated[
        str,
        StringConstraints(pattern="^(internal|external)$")
    ] = Field(..., description="Profile type")
    network: str = Field(..., description="Network name or identifier")


class ProfileUpdate(ProfileBase):
    """
    For updating existing profiles:
    - `type` and `network` cannot be updated.
    - If `devices` is omitted, device mappings remain unchanged.
    """
    pass
