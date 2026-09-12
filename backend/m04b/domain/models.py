from typing import Optional
from uuid import uuid4, UUID

from pydantic import BaseModel, Field


class BackgroundRemovalRequest(BaseModel):
    product_id: str = Field(..., min_length=1)


class MasterImageRef(BaseModel):
    asset_id: str
    storage_key: str
    url: str
    mime_type: str
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: Optional[str] = None


class BackgroundRemovalResult(BaseModel):
    asset_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_asset_id: str = ""
    product_id: str = ""
    storage_key: str = ""
    mime_type: str = "image/png"
    width: int = 0
    height: int = 0
    provider: str = "m04b.background_removal"
    model_version: str = "aic-11-v1"
    parameters: dict = Field(default_factory=dict)

    model_config = {"protected_namespaces": ()}
