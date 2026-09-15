import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Request

from org_context import require_org, sso_token_tho
from m07.domain.models import (
    ApprovePostRequest,
    ContentGenerationRequest,
    ContentGenerationResponse,
)
from m07.use_cases.generate_content import execute_generate_content

DB_PATH = Path(__file__).resolve().parent.parent / "socialflow.db"

router = APIRouter(
    prefix="/api/m07",
    tags=["M07 Content Engine"],
    dependencies=[Depends(require_org)],
)


class BatchScheduleRequest(BaseModel):
    post_ids: List[int]
    scheduled_time: Optional[str] = None


@router.get("/meta")
async def api_get_meta(organization_id: str = Depends(require_org)):
    """Trả về danh sách kênh và các góc tiếp cận (angles) được hỗ trợ."""
    return {
        "supported_channels": [
            {"key": "facebook", "label": "Facebook Fanpage", "icon": "facebook"},
            {"key": "instagram", "label": "Instagram", "icon": "instagram"},
            {"key": "tiktok", "label": "TikTok Video Script", "icon": "video"},
            {"key": "zalo", "label": "Zalo OA", "icon": "message-circle"},
        ],
        "supported_angles": [
            {"key": "emotional", "label": "Cảm xúc & Tình yêu", "description": "Lãng mạn, thơ mộng, gắn kết người nhận"},
            {"key": "technical", "label": "Kỹ thuật & Tay nghề", "description": "Tôn vinh độ tươi, phối màu và tay nghề Florist"},
            {"key": "promotional", "label": "Ưu đãi & Chốt đơn", "description": "Quà tặng thiệp/banner, freeship và ưu đãi"},
        ],
        "organization_id": organization_id,
    }


@router.post("/generate", response_model=ContentGenerationResponse)
async def api_generate_content(
    request: Request,
    body: ContentGenerationRequest,
    organization_id: str = Depends(require_org),
    sso_token: Optional[str] = Depends(sso_token_tho),
):
    """Sinh nội dung tiếp thị hoa tươi đa kênh."""
    try:
        result = await execute_generate_content(
            request=body,
            organization_id=organization_id,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi sinh nội dung M07: {str(e)}")


@router.post("/posts/{post_id}/approve")
async def api_approve_post(
    post_id: str,
    body: Optional[ApprovePostRequest] = None,
    organization_id: str = Depends(require_org),
):
    """Duyệt bài viết và chuyển trạng thái sang scheduled (đã lên lịch) để xuất bản."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        new_content = None
        if body and (body.title or body.body):
            title = body.title or ""
            content_body = body.body or ""
            cta = body.cta or ""
            tags = " ".join(body.hashtags or [])
            parts = [p for p in [title, content_body, cta, tags] if p]
            new_content = (chr(10) + chr(10)).join(parts).strip()

        # Thử tìm bài viết theo id
        cursor = conn.execute(
            "SELECT * FROM posts WHERE id = ? AND organization_id = ?",
            (post_id, organization_id),
        )
        row = cursor.fetchone()

        now_str = datetime.utcnow().isoformat()

        if row:
            # Cập nhật bài viết hiện có
            if new_content:
                conn.execute(
                    """
                    UPDATE posts 
                    SET status = 'scheduled', content = ?, scheduled_time = ?, published_at = NULL
                    WHERE id = ? AND organization_id = ?
                    """,
                    (new_content, now_str, post_id, organization_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE posts 
                    SET status = 'scheduled', scheduled_time = ?, published_at = NULL
                    WHERE id = ? AND organization_id = ?
                    """,
                    (now_str, post_id, organization_id),
                )
            conn.commit()
            return {"status": "SUCCESS", "post_id": post_id, "approval_state": "APPROVED"}
        else:
            # Nếu không tìm thấy bằng id (ví dụ client gửi uuid tạm), tự động tạo bản ghi mới
            platform = (body.channel if body and body.channel else "facebook").lower()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO posts (
                    organization_id, platform, content_type, content, status, scheduled_time, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    platform,
                    "post",
                    new_content or (body.body if body else "Bản đăng đã duyệt"),
                    "scheduled",
                    now_str,
                    now_str,
                ),
            )
            conn.commit()
            new_id = str(cur.lastrowid)
            return {"status": "SUCCESS", "post_id": new_id, "approval_state": "APPROVED"}
    finally:
        conn.close()


@router.post("/posts/batch-schedule")
async def api_batch_schedule(
    body: BatchScheduleRequest,
    organization_id: str = Depends(require_org),
):
    """Xác nhận lên lịch hàng loạt cho danh sách bài viết."""
    if not body.post_ids:
        return {"status": "SUCCESS", "updated_count": 0}

    conn = sqlite3.connect(str(DB_PATH))
    try:
        now_str = body.scheduled_time or datetime.utcnow().isoformat()
        placeholders = ", ".join("?" for _ in body.post_ids)
        params = ["scheduled", now_str, organization_id] + body.post_ids
        cur = conn.cursor()
        cur.execute(
            f"""
            UPDATE posts 
            SET status = ?, scheduled_time = ?
            WHERE organization_id = ? AND id IN ({placeholders})
            """,
            params,
        )
        conn.commit()
        return {"status": "SUCCESS", "updated_count": cur.rowcount}
    finally:
        conn.close()


@router.post("/posts/{post_id}/publish")
async def api_publish_post(
    post_id: int,
    organization_id: str = Depends(require_org),
):
    """Xuất bản ngay lập tức một bài viết qua SocialFlow Publisher thật."""
    from main import publish_scheduled_post
    
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, platform, status, media_paths FROM posts WHERE id = ? AND organization_id = ?",
            (post_id, organization_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài viết hoặc bài viết không thuộc tổ chức này.")
        
        platform = row[1]
        media_paths = row[3]

        # Xử lý media thực tế của sản phẩm (chính xác 100%, không dùng ảnh mẫu cố định)
        import os, json, httpx
        valid_local_paths = []
        if media_paths:
            try:
                parsed = json.loads(media_paths) if isinstance(media_paths, str) else media_paths
                if isinstance(parsed, list):
                    for p in parsed:
                        if not isinstance(p, str):
                            continue
                        if os.path.exists(p):
                            valid_local_paths.append(p)
                        elif p.startswith("http://") or p.startswith("https://") or p.startswith("/"):
                            try:
                                url = p if p.startswith("http") else f"http://localhost:3100{p}"
                                ext = os.path.splitext(p.split("?")[0])[1] or ".jpg"
                                out_name = f"product_media_{post_id}{ext}"
                                out_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads", out_name))
                                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                                with httpx.Client(timeout=15.0) as client:
                                    resp = client.get(url)
                                    if resp.status_code == 200:
                                        with open(out_path, "wb") as f_img:
                                            f_img.write(resp.content)
                                        valid_local_paths.append(out_path)
                            except Exception as ex_dl:
                                print(f"[M07] Lỗi tải ảnh sản phẩm {p}: {ex_dl}", flush=True)
            except Exception:
                pass

        if valid_local_paths:
            cur.execute(
                "UPDATE posts SET media_paths = ?, content_type = 'image' WHERE id = ?",
                (json.dumps(valid_local_paths), post_id),
            )
            conn.commit()
        else:
            # Bài viết thuần chữ nếu không có ảnh sản phẩm — tuyệt đối không lấy bừa ảnh khác
            cur.execute(
                "UPDATE posts SET media_paths = NULL, content_type = 'post' WHERE id = ?",
                (post_id,),
            )
            conn.commit()
    finally:
        conn.close()

    # Thực thi trình duyệt xuất bản thật sự qua Playwright
    await publish_scheduled_post(post_id)

    # Đọc lại kết quả thực tế sau khi chạy Playwright
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute("SELECT status, error_message, post_id, published_at FROM posts WHERE id = ?", (post_id,))
        final_row = cur.fetchone()
        status, err_msg, pub_post_id, pub_at = final_row if final_row else ('failed', 'Unknown error', None, None)
        
        if status == 'published' or status == 'posted':
            return {
                "status": "SUCCESS",
                "post_id": post_id,
                "published_at": pub_at,
                "platform_post_id": pub_post_id,
                "message": f"Đã xuất bản thành công bài viết #{post_id} lên {platform}!"
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=err_msg or f"Không thể xuất bản lên {platform}. Vui lòng kiểm tra lại phiên đăng nhập."
            )
    finally:
        conn.close()



@router.post("/posts/{post_id}/retry")
async def api_retry_post(
    post_id: int,
    organization_id: str = Depends(require_org),
):
    """Thử lại bài viết bị lỗi."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        now_str = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE posts 
            SET status = 'scheduled', error_message = NULL, scheduled_time = ?
            WHERE id = ? AND organization_id = ?
            """,
            (now_str, post_id, organization_id),
        )
        conn.commit()
        return {"status": "SUCCESS", "post_id": post_id}
    finally:
        conn.close()


@router.get("/posts")
async def api_list_posts(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    organization_id: str = Depends(require_org),
):
    """Liệt kê danh sách bài viết thuộc tổ chức, chuẩn hóa thông tin hiển thị."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM posts WHERE organization_id = ?"
        params = [organization_id]
        if platform:
            query += " AND LOWER(platform) = ?"
            params.append(platform.lower())
        if status:
            query += " AND LOWER(status) = ?"
            params.append(status.lower())
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            raw_content = d.get("content") or ""
            # Trích xuất dòng tiêu đề đầu tiên
            first_line = ""
            for line in raw_content.split(chr(10)):
                clean = line.strip().lstrip("#* 	")
                if clean and not clean.lower().startswith("tiêu đề:"):
                    first_line = clean
                    break
                elif clean and clean.lower().startswith("tiêu đề:"):
                    first_line = clean[8:].strip().lstrip("#* 	")
                    break

            platform_name = (d.get("platform") or "social").capitalize()
            if platform_name.lower() == "zalo":
                platform_name = "Zalo OA"

            d["title"] = first_line[:120] if first_line else f"Nội dung {platform_name}"
            d["channel_label"] = platform_name
            result.append(d)
        return result
    finally:
        conn.close()
