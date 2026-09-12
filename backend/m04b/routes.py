from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from org_context import require_org, sso_token_tho
from m04b.domain.models import BackgroundRemovalRequest
from m04b.use_cases.background_removal import (
    request_background_removal,
)

UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"

router = APIRouter(
    prefix="/api/m04b",
    tags=["M04b Marketing Creative"],
    dependencies=[Depends(require_org)],
)


@router.post("/background-removal")
async def api_background_removal(
    request: Request,
    body: BackgroundRemovalRequest,
    organization_id: str = Depends(require_org),
    sso_token: Optional[str] = Depends(sso_token_tho),
):
    try:
        result = request_background_removal(
            organization_id=organization_id,
            request=body,
            sso_token=sso_token,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "asset_id": result.asset_id,
        "product_id": result.product_id,
        "parent_asset_id": result.parent_asset_id,
        "storage_key": result.storage_key,
        "mime_type": result.mime_type,
        "width": result.width,
        "height": result.height,
        "status": "PENDING",
        "created_at": datetime.utcnow().isoformat(),
    }


@router.get("/assets/{asset_id}/download")
async def api_download_asset(
    asset_id: str,
    organization_id: str = Depends(require_org),
):
    matches = list(UPLOADS_DIR.glob(f"org/{organization_id}/*/{asset_id}.png"))
    if not matches:
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(matches[0]), media_type="image/png")
