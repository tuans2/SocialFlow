"""
Media Library API — one way in, one way to browse, one way to reuse.

Everything SocialFlow generates or the user uploads becomes an asset row, so a
carousel from last week can be pulled back into a new post instead of being
regenerated. Backed by the assets table that already existed in
asset_inventory.py but had nothing writing to it.
"""

from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from asset_inventory import (
    archive_asset,
    count_assets,
    get_asset,
    list_assets,
    update_asset,
)
from media_store import MediaRejected, public_url, store_upload

router = APIRouter(prefix="/api/assets", tags=["Media Library"])

MAX_FILES_PER_REQUEST = 20


class AssetUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    campaign: Optional[str] = None
    topic: Optional[str] = None
    tags: Optional[List[str]] = None


def _decorate(row: dict) -> dict:
    """Add the URLs the frontend needs without changing what is stored."""
    if not row:
        return row
    row = dict(row)
    row["url"] = row.get("file_url") or public_url(row.get("file_path"))
    row["thumb_url"] = public_url(row.get("thumb_path")) if row.get("thumb_path") else None
    return row


@router.post("/upload")
async def upload_assets(files: List[UploadFile] = File(...),
                        campaign: str = "",
                        topic: str = "",
                        tags: str = ""):
    """Upload one or more files into the media library.

    Each file is type-sniffed from its bytes, size-capped, hashed and deduped.
    A file already held is not written twice — its existing asset comes back
    with deduped: true.

    Partial success is normal and reported per file rather than failing the
    whole request: one bad file in a batch of twenty should not lose the
    other nineteen.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files in one request (max {MAX_FILES_PER_REQUEST})",
        )

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    stored, rejected = [], []

    for upload in files:
        name = upload.filename or "(unnamed)"
        try:
            result = await store_upload(
                upload,
                origin="uploaded",
                provider="user",
                campaign=campaign,
                topic=topic,
                tags=tag_list,
            )
            result["filename"] = name
            stored.append(result)
        except MediaRejected as e:
            rejected.append({"filename": name, "reason": e.reason})
        except Exception as e:
            rejected.append({"filename": name, "reason": f"Unexpected error: {e}"})
        finally:
            try:
                await upload.close()
            except Exception:
                pass

    return {
        "success": bool(stored),
        "stored": stored,
        "rejected": rejected,
        "deduped_count": sum(1 for s in stored if s.get("deduped")),
    }


@router.get("")
async def browse_assets(
    asset_type: Optional[str] = Query(None, description="image | video | audio | document"),
    origin: Optional[str] = Query(None, description="generated | uploaded"),
    source: Optional[str] = None,
    campaign: Optional[str] = None,
    topic: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = Query(None, description="search title/description/topic"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_archived: bool = False,
):
    """Browse the media library."""
    filters = dict(
        asset_type=asset_type, origin=origin, source=source, campaign=campaign,
        topic=topic, tag=tag, q=q, include_archived=include_archived,
    )
    rows = list_assets(limit=limit, offset=offset, **filters)
    return {
        "assets": [_decorate(r) for r in rows],
        "total": count_assets(**filters),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{asset_id}")
async def read_asset(asset_id: str):
    row = get_asset(asset_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    return _decorate(row)


@router.patch("/{asset_id}")
async def edit_asset(asset_id: str, payload: AssetUpdate):
    if not get_asset(asset_id):
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    row = update_asset(asset_id, **payload.dict(exclude_none=True))
    return _decorate(row)


@router.delete("/{asset_id}")
async def remove_asset(asset_id: str):
    """Soft delete. The row and the file both survive, so any post already
    referencing this asset still publishes; it just stops appearing in the
    library."""
    if not get_asset(asset_id):
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    archive_asset(asset_id)
    return {"success": True, "asset_id": asset_id, "state": "archived"}
