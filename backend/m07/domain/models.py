from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field


class ContentGenerationRequest(BaseModel):
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    flower_details: Optional[str] = None
    colors: Optional[str] = None
    price: Optional[float] = None
    occasion: Optional[str] = None
    channels: List[str] = Field(default_factory=lambda: ["facebook", "instagram", "tiktok", "zalo"])
    angle: str = Field(default="emotional", description="emotional | technical | promotional")
    custom_instructions: Optional[str] = None
    image_url: Optional[str] = None
    provider: Optional[str] = Field(default="ollama", description="ollama | openai | gemini | anthropic")
    model: Optional[str] = None

    model_config = {"protected_namespaces": ()}


class ChannelPostContent(BaseModel):
    post_id: Optional[str] = None
    channel: str
    title: str = ""
    body: str = ""
    hashtags: List[str] = Field(default_factory=list)
    cta: str = ""
    seo_description: Optional[str] = None
    script: Optional[Dict[str, Any]] = None  # Dành cho TikTok kịch bản quay/hook
    media_url: Optional[str] = None
    status: str = "DRAFT"

    model_config = {"protected_namespaces": ()}


class ContentGenerationResponse(BaseModel):
    result_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str
    product_id: Optional[str] = None
    angle: str = "emotional"
    channels: Dict[str, ChannelPostContent] = Field(default_factory=dict)
    provider: str = "ollama"
    model_name: str = "qwen2.5:7b"
    created_at: str

    model_config = {"protected_namespaces": ()}


class ApprovePostRequest(BaseModel):
    post_id: Optional[str] = None
    channel: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    hashtags: Optional[List[str]] = None
    cta: Optional[str] = None

    model_config = {"protected_namespaces": ()}
