import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from m04b.adapters.image_processor import remove_background
from m04b.domain.models import (
    BackgroundRemovalRequest,
    BackgroundRemovalResult,
    MasterImageRef,
)
from m04b.infra.core_client import CoreClientAdapter


SUPPORTED_MIME = {"image/png", "image/jpeg", "image/webp"}
UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"


def _store_locally(organization_id: str, product_id: str, asset_id: str,
                   image_bytes: bytes) -> str:
    storage_key = f"org/{organization_id}/{product_id}/{asset_id}.png"
    dest = UPLOADS_DIR / storage_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(image_bytes)
    return storage_key


def request_background_removal(
    organization_id: str,
    request: BackgroundRemovalRequest,
    sso_token: Optional[str] = None,
) -> BackgroundRemovalResult:
    core = CoreClientAdapter.default()

    master_data = core.get_master_image(request.product_id, sso_token)
    master = MasterImageRef(
        asset_id=master_data["master"]["id"],
        storage_key=master_data["master"]["storage_key"],
        url=master_data["master"]["url"],
        mime_type=master_data["master"]["mime_type"],
        width=master_data["master"].get("width"),
        height=master_data["master"].get("height"),
        aspect_ratio=master_data["master"].get("aspect_ratio"),
    )

    if master.mime_type not in SUPPORTED_MIME:
        raise ValueError(
            f"Không hỗ trợ định dạng {master.mime_type}, chỉ hỗ trợ PNG/JPEG/WebP"
        )

    image_bytes = core.download_image(master.url)
    processed_bytes, width, height = remove_background(image_bytes)

    asset_id = str(uuid.uuid4())
    storage_key = _store_locally(
        organization_id, request.product_id, asset_id, processed_bytes,
    )

    now = datetime.utcnow()
    parameters = {
        "aic_capability": "AIC-11",
        "aic_name": "background_removal",
        "parent_asset_id": master.asset_id,
        "applied_at": now.isoformat(),
    }

    asset = {
        "asset_id": asset_id,
        "product_id": request.product_id,
        "parent_asset_id": master.asset_id,
        "kind": "MARKETING",
        "storage_key": storage_key,
        "mime_type": "image/png",
        "width": width,
        "height": height,
        "aspect_ratio": "1:1" if width == height else f"{width}:{height}",
        "file_size": len(processed_bytes),
        "provider": "m04b.background_removal",
        "model": "local_cv",
        "model_version": "aic-11-v1",
        "parameters": parameters,
        "output_sha256": _sha256(processed_bytes),
        "generated_flags": {
            "background_removed": True,
            "original_master": master.asset_id,
        },
        "cost_usd": 0,
    }

    result = core.register_asset(organization_id, sso_token, asset)

    return BackgroundRemovalResult(
        asset_id=result.get("asset_id", asset_id),
        parent_asset_id=master.asset_id,
        product_id=request.product_id,
        storage_key=storage_key,
        mime_type="image/png",
        width=width,
        height=height,
    )


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def request_background_removal_from_bytes(
    organization_id: str,
    product_id: str,
    image_bytes: bytes,
) -> BackgroundRemovalResult:
    processed_bytes, width, height = remove_background(image_bytes)
    asset_id = str(uuid.uuid4())
    storage_key = f"org/{organization_id}/{product_id}/{asset_id}.png"
    return BackgroundRemovalResult(
        asset_id=asset_id,
        parent_asset_id="",
        product_id=product_id,
        storage_key=storage_key,
        mime_type="image/png",
        width=width,
        height=height,
    )
