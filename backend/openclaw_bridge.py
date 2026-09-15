"""
OpenClaw Bridge — API endpoints for OpenClaw to push approved content to SocialFlow
This is the connector between OpenClaw's content pipeline and SocialFlow's posting engine.

Endpoints:
  POST /api/openclaw/publish    — Push approved content to a platform
  POST /api/openclaw/batch      — Push multiple items at once
  GET  /api/openclaw/status      — Check platform connection status
  GET  /api/openclaw/history     — Recent publish history
"""

import json
import sqlite3
from datetime import datetime
from typing import Optional, List
from pathlib import Path

from fastapi import Depends, APIRouter, HTTPException

from org_context import require_org
from pydantic import BaseModel

# Đợt 3 / E3 — cổng xác thực ở mức ROUTER: mọi route trong tệp này đòi một
# JWT `floraos_sso` hợp lệ. Đây mới là lớp CHẶN; lớp LỌC theo tổ chức nằm ở
# từng route (E4), và FastAPI nhớ kết quả phụ thuộc trong một request nên gọi
# hai lần không tốn thêm gì.
router = APIRouter(
    prefix="/api/openclaw",
    tags=["OpenClaw Bridge"],
    dependencies=[Depends(require_org)],
)


class OpenClawPublishRequest(BaseModel):
    """Content from OpenClaw approval pipeline"""
    platform: str           # linkedin, x, facebook, instagram, discord, reddit, medium, substack, heygen, beehiiv, mailerlite, brevo
    content: str            # The post/article text
    content_type: str       # product-update, ai-news, social-post, newsletter, video-brief, etc.
    title: Optional[str] = None         # For Medium, Substack, Reddit
    subtitle: Optional[str] = None      # For Substack
    subreddit: Optional[str] = None     # For Reddit
    image_paths: Optional[List[str]] = None
    approval_level: Optional[str] = None  # L1, L2, L3 — for audit trail
    source_file: Optional[str] = None     # Original file path in OpenClaw
    dry_run: bool = False                 # If true, don't actually post


class BatchPublishRequest(BaseModel):
    items: List[OpenClawPublishRequest]


# History storage
BRIDGE_LOG_PATH = Path("openclaw_bridge_log.json")


def log_bridge_event(event: dict):
    """Append to bridge log file"""
    events = []
    if BRIDGE_LOG_PATH.exists():
        try:
            events = json.loads(BRIDGE_LOG_PATH.read_text())
        except Exception:
            events = []
    events.append(event)
    # Keep last 500 events
    events = events[-500:]
    BRIDGE_LOG_PATH.write_text(json.dumps(events, indent=2))


@router.post("/publish")
async def openclaw_publish(request: OpenClawPublishRequest, organization_id: str = Depends(require_org)):
    """
    Receive approved content from OpenClaw and publish to the specified platform.
    This is the main bridge endpoint.
    """
    from automation_extended import get_automation_extended

    timestamp = datetime.now().isoformat()
    platform = request.platform.lower()

    # Validate platform
    supported = ["linkedin", "x", "twitter", "facebook", "instagram", "discord",
                 "reddit", "medium", "substack", "heygen", "beehiiv", "mailerlite", "brevo"]
    if platform not in supported:
        raise HTTPException(status_code=400,
                          detail=f"Unsupported platform: {platform}. Supported: {supported}")

    # Validate content
    if not request.content or len(request.content.strip()) < 5:
        raise HTTPException(status_code=400, detail="Content is too short or empty")

    # Dry run — just validate and return
    if request.dry_run:
        log_bridge_event({
            "organization_id": organization_id,
            "timestamp": timestamp,
            "platform": platform,
            "content_type": request.content_type,
            "action": "dry_run",
            "status": "validated",
            "source_file": request.source_file
        })
        return {
            "success": True,
            "action": "dry_run",
            "platform": platform,
            "message": f"Validated for {platform}. Would publish in live mode.",
            "content_preview": request.content[:200]
        }

    # Đợt 5 (rà soát bảo mật): trước đây Discord/Substack đọc MỘT tệp cấu hình
    # dùng chung cho mọi tổ chức (discord_webhook.json / substack_config.json)
    # — tổ chức B gọi endpoint này thì nội dung của B bị đăng lên Discord/
    # Substack của tổ chức A. Discord nay đọc đúng webhook của TỔ CHỨC NÀY từ
    # `accounts`, cùng cách agents/publisher.py._publish_discord() đã làm cho
    # đường lịch đăng tự động — cùng một nguồn sự thật, không phải hai đường
    # đọc khác nhau cho cùng một dữ liệu.
    kwargs = {}
    if platform == "discord":
        from duong_dan_db import duong_dan_db
        conn = sqlite3.connect(duong_dan_db())
        conn.row_factory = sqlite3.Row
        account = conn.execute(
            "SELECT * FROM accounts WHERE platform = 'discord' AND organization_id = ?",
            (organization_id,),
        ).fetchone()
        conn.close()
        if not account:
            raise HTTPException(status_code=400,
                              detail="Discord webhook not configured for this organization. "
                                     "Add a Discord account first.")
        from main import decrypt
        kwargs["webhook_url"] = decrypt(account["password_encrypted"])

    # Substack's publication_url has no per-organization storage today (the
    # old shared substack_config.json was exactly the bug this sweep found —
    # every organization's post used whichever URL was in that one file).
    # Rather than reintroduce shared state, this is left unset; the automation
    # falls back to https://substack.com/home. Per-org publication_url needs a
    # real storage decision (e.g. a column on `accounts`) before it comes back.

    try:
        # D1/A5: `organization_id` chọn đúng tệp phiên Playwright của tổ chức
        # này. Thiếu nó thì hai tổ chức dùng chung một phiên đăng nhập — tức
        # đăng bài của tổ chức này lên tài khoản của tổ chức kia.
        auto = get_automation_extended(platform, headless=True,
                                       organization_id=organization_id, **kwargs)

        # Build post kwargs based on platform — only pass params each platform accepts
        post_kwargs = {"content": request.content}
        if request.image_paths:
            post_kwargs["image_paths"] = request.image_paths

        # title only for platforms that accept it
        title_platforms = ("medium", "substack", "reddit", "beehiiv", "mailerlite", "brevo")
        if request.title and platform in title_platforms:
            post_kwargs["title"] = request.title
        if request.subtitle and platform == "substack":
            post_kwargs["subtitle"] = request.subtitle
        if request.subreddit and platform == "reddit":
            post_kwargs["subreddit"] = request.subreddit

        # For email platforms, add subject
        if platform in ("beehiiv", "mailerlite", "brevo") and request.title:
            post_kwargs["subject"] = request.title

        result = await auto.post(**post_kwargs)

        if hasattr(auto, 'close'):
            await auto.close()

        # Log the event
        log_bridge_event({
            "organization_id": organization_id,
            "timestamp": timestamp,
            "platform": platform,
            "content_type": request.content_type,
            "approval_level": request.approval_level,
            "action": "publish",
            "status": "success" if result.get("success") else "failed",
            "message": result.get("message", ""),
            "source_file": request.source_file
        })

        return {
            "success": result.get("success", False),
            "platform": platform,
            "message": result.get("message", ""),
            "timestamp": timestamp,
            "source_file": request.source_file
        }

    except Exception as e:
        log_bridge_event({
            "organization_id": organization_id,
            "timestamp": timestamp,
            "platform": platform,
            "content_type": request.content_type,
            "action": "publish",
            "status": "error",
            "message": str(e),
            "source_file": request.source_file
        })
        return {
            "success": False,
            "platform": platform,
            "message": f"Error: {str(e)}",
            "timestamp": timestamp
        }


@router.post("/batch")
async def openclaw_batch_publish(request: BatchPublishRequest, organization_id: str = Depends(require_org)):
    """Publish multiple items from OpenClaw in sequence"""
    results = []
    for item in request.items:
        # Gọi thẳng như một hàm Python, KHÔNG qua FastAPI — nên phải truyền
        # `organization_id` tường minh. Bỏ trống thì tham số nhận đúng đối
        # tượng `Depends(...)` làm giá trị mặc định, và lỗi sẽ im lặng đi tiếp
        # thay vì nổ ra.
        result = await openclaw_publish(item, organization_id=organization_id)
        results.append(result)
    return {
        "total": len(results),
        "succeeded": sum(1 for r in results if r.get("success")),
        "failed": sum(1 for r in results if not r.get("success")),
        "results": results
    }


@router.get("/status")
async def openclaw_platform_status(organization_id: str = Depends(require_org)):
    """Check which platforms have saved sessions / are ready to post (this organization only)"""
    from automation_extended import get_automation_extended
    from to_chuc import bat_buoc

    org_id = bat_buoc(organization_id)
    sessions_dir = Path("sessions")
    status = {}

    platforms = ["linkedin", "instagram", "x", "facebook", "reddit",
                 "medium", "substack", "heygen", "beehiiv", "mailerlite", "brevo"]

    for p in platforms:
        session_file = sessions_dir / f"{org_id}_{p}_session.json"
        has_session = session_file.exists()

        status[p] = {
            "has_session": has_session,
            "session_age": None,
            "ready": has_session
        }

        if has_session:
            try:
                session_data = json.loads(session_file.read_text())
                saved_at = session_data.get("saved_at", "")
                status[p]["session_age"] = saved_at
            except Exception:
                pass

    # Discord check — same per-organization source as /publish (accounts
    # table), not the old shared discord_webhook.json.
    from duong_dan_db import duong_dan_db
    conn = sqlite3.connect(duong_dan_db())
    has_discord = conn.execute(
        "SELECT 1 FROM accounts WHERE platform = 'discord' AND organization_id = ?",
        (org_id,),
    ).fetchone() is not None
    conn.close()
    status["discord"] = {
        "has_session": has_discord,
        "ready": has_discord,
        "type": "webhook"
    }

    return {"platforms": status}


@router.get("/history")
async def openclaw_publish_history(limit: int = 50, organization_id: str = Depends(require_org)):
    """Get recent publish history for this organization only.

    Đợt 5 (rà soát bảo mật): trước đây route này trả TOÀN BỘ log dùng chung,
    không lọc theo tổ chức nào — bất kỳ tổ chức nào gọi cũng thấy lịch sử
    đăng bài của mọi tổ chức khác. Sự kiện ghi trước khi có sửa này không
    mang organization_id — loại hẳn khỏi kết quả thay vì đoán chúng thuộc
    tổ chức nào, để không lộ nhầm cho một tổ chức không liên quan.
    """
    events = []
    if BRIDGE_LOG_PATH.exists():
        try:
            events = json.loads(BRIDGE_LOG_PATH.read_text())
        except Exception:
            events = []

    events = [e for e in events if e.get("organization_id") == organization_id]

    # Return most recent first
    return {"events": list(reversed(events[-limit:]))}
