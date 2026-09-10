"""
Media Library API — one way in, one way to browse, one way to reuse.

Everything SocialFlow generates or the user uploads becomes an asset row, so a
carousel from last week can be pulled back into a new post instead of being
regenerated. Backed by the assets table that already existed in
asset_inventory.py but had nothing writing to it.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, APIRouter, File, HTTPException, Query, UploadFile

from org_context import require_org
from pydantic import BaseModel

from asset_inventory import (
    archive_asset,
    count_assets,
    get_asset,
    list_assets,
    update_asset,
)
from media_store import BACKEND_DIR, MediaRejected, public_url, store_upload
from media_utils import resolve_media_path, validate_publish_media

# Đợt 3 / E3 — cổng xác thực ở mức ROUTER: mọi route trong tệp này đòi một
# JWT `floraos_sso` hợp lệ. Đây mới là lớp CHẶN; lớp LỌC theo tổ chức nằm ở
# từng route (E4), và FastAPI nhớ kết quả phụ thuộc trong một request nên gọi
# hai lần không tốn thêm gì.
router = APIRouter(
    prefix="/api/assets",
    tags=["Media Library"],
    dependencies=[Depends(require_org)],
)

MAX_FILES_PER_REQUEST = 20


DATABASE_PATH = str(BACKEND_DIR / "socialflow.db")

# What an asset becomes when it is turned into a post. A video is a reel, a
# picture is a post, a carousel is a carousel — chosen here so the caller does
# not have to know, and so the publish gate sees the right content type.
CONTENT_TYPE_FOR = {
    "video": "reel",
    "image": "post",
    "carousel": "carousel",
}


class AssetToPost(BaseModel):
    platform: str
    content: str = ""
    content_type: Optional[str] = None      # override the mapping above
    scheduled_time: Optional[datetime] = None


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
                        tags: str = "",
                        organization_id: str = Depends(require_org)):
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
                organization_id=organization_id,
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
    organization_id: str = Depends(require_org),
):
    """Browse the media library."""
    filters = dict(
        asset_type=asset_type, origin=origin, source=source, campaign=campaign,
        topic=topic, tag=tag, q=q, include_archived=include_archived,
    )
    rows = list_assets(limit=limit, offset=offset, organization_id=organization_id, **filters)
    return {
        "assets": [_decorate(r) for r in rows],
        "total": count_assets(organization_id=organization_id, **filters),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{asset_id}")
async def read_asset(asset_id: str, organization_id: str = Depends(require_org)):
    row = get_asset(asset_id, organization_id=organization_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    return _decorate(row)


@router.patch("/{asset_id}")
async def edit_asset(asset_id: str, payload: AssetUpdate, organization_id: str = Depends(require_org)):
    if not get_asset(asset_id, organization_id=organization_id):
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    row = update_asset(
        asset_id, organization_id=organization_id, **payload.dict(exclude_none=True)
    )
    return _decorate(row)


@router.delete("/{asset_id}")
async def remove_asset(asset_id: str, organization_id: str = Depends(require_org)):
    """Soft delete. The row and the file both survive, so any post already
    referencing this asset still publishes; it just stops appearing in the
    library."""
    if not get_asset(asset_id, organization_id=organization_id):
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    archive_asset(asset_id, organization_id=organization_id)
    return {"success": True, "asset_id": asset_id, "state": "archived"}


def _carousel_slide_paths(carousel_asset_id: str, *, organization_id: str) -> List[str]:
    """Slides of a carousel, in order, as stored paths.

    Reads the ordered id list the carousel keeps, which survives
    deduplication, and falls back to the parent_asset_id back-pointer for
    carousels made before that list existed.
    """
    carousel = get_asset(carousel_asset_id, organization_id=organization_id)
    if carousel:
        try:
            meta = json.loads(carousel.get("metadata") or "{}")
        except (TypeError, ValueError):
            meta = {}
        ids = meta.get("slide_asset_ids") or []
        if ids:
            paths = []
            for i in ids:
                a = get_asset(i, organization_id=organization_id)
                if a and a.get("file_path"):
                    paths.append(a["file_path"])
            if paths:
                return paths

    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT file_path FROM assets WHERE parent_asset_id = ? "
            "AND organization_id = ? AND asset_type = 'image' ORDER BY id",
            (carousel_asset_id, organization_id),
        ).fetchall()
    finally:
        conn.close()
    return [r["file_path"] for r in rows if r["file_path"]]


@router.post("/{asset_id}/to-post")
async def asset_to_post(asset_id: str, payload: AssetToPost, organization_id: str = Depends(require_org)):
    """Turn an asset into a draft post.

    This is the step that was missing: video generated in the app landed in the
    library and stopped there, because nothing connected an asset to the posts
    table. Generate -> look -> lose.

    The media is validated against the same publish gate the publisher uses, so
    a draft that cannot be published is refused here rather than at 11pm when
    the scheduler tries it.
    """
    asset = get_asset(asset_id, organization_id=organization_id)
    if not asset:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")

    content_type = payload.content_type or CONTENT_TYPE_FOR.get(
        asset.get("asset_type"), "post"
    )

    if asset.get("asset_type") == "carousel":
        media = _carousel_slide_paths(asset_id, organization_id=organization_id)
        if not media:
            raise HTTPException(
                status_code=400,
                detail=f"Carousel {asset_id} has no slides to post",
            )
    else:
        if not asset.get("file_path"):
            raise HTTPException(
                status_code=400,
                detail=f"Asset {asset_id} has no file to post",
            )
        media = [asset["file_path"]]

    missing = [m for m in media if not resolve_media_path(m)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"File missing on disk: {', '.join(missing[:3])}",
        )

    ok, gate_error, _ = validate_publish_media(payload.platform, content_type, media)
    if not ok:
        raise HTTPException(status_code=400, detail=gate_error)

    status = "scheduled" if payload.scheduled_time else "draft"
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        cur = conn.execute(
            """INSERT INTO posts (platform, content_type, content, media_paths,
                                  scheduled_time, status, organization_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.platform,
                content_type,
                payload.content or asset.get("title") or "",
                json.dumps(media),
                payload.scheduled_time.isoformat() if payload.scheduled_time else None,
                status,
                organization_id,
            ),
        )
        post_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "post_id": post_id,
        "status": status,
        "platform": payload.platform,
        "content_type": content_type,
        "media_count": len(media),
        "asset_id": asset_id,
    }
