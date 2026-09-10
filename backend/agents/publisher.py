"""
Publisher Agent — Distribution

Responsibilities:
  - Take approved posts → publish via Playwright or API
  - Handle: login, session management, image upload
  - Retry on failure (max 2 attempts)
  - Record: post URL, timestamp
  - Enforce posting windows

Runs: At posting windows (11 AM, 11 PM) or on-demand
"""

import json
import os
import time
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media_utils import validate_publish_media  # noqa: E402

from to_chuc import bat_buoc

DB_PATH = Path(__file__).parent.parent / "socialflow.db"

# Cung hang so voi migrations.py/main.py/orchestrator.py.


def get_approved_posts(platform: str = None, limit: int = 10, organization_id: Optional[str] = None):
    """Get this organization's approved posts ready for publishing."""
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if platform:
        rows = conn.execute(
            "SELECT * FROM posts WHERE status = 'approved' AND platform = ? "
            "AND organization_id = ? ORDER BY created_at ASC LIMIT ?",
            (platform, org_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM posts WHERE status = 'approved' AND organization_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (org_id, limit)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_posted(post_id: int, post_url: str = ""):
    """Mark a post as successfully published."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE posts SET status = 'posted', post_id = ?, published_at = ? WHERE id = ?",
        (post_url, datetime.now().isoformat(), post_id)
    )
    conn.commit()
    conn.close()


def mark_failed(post_id: int, error: str):
    """Mark a post as failed."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE posts SET status = 'failed', error_message = ? WHERE id = ?",
        (error[:500], post_id)
    )
    conn.commit()
    conn.close()


async def publish_post(post: Dict, automation_getter) -> bool:
    """Publish a single post using the appropriate automation.

    Args:
        post: Post dict from DB — carries its own organization_id (D1, A5),
              which is passed to automation_getter so login sessions
              (automation.py) resolve to the RIGHT organization's browser
              session, not whichever organization logged in most recently.
        automation_getter: Function(platform, organization_id) that returns
              the platform automation instance.
    """
    platform = post["platform"]
    content = post["content"]
    post_id = post["id"]
    organization_id = bat_buoc(post.get("organization_id"))

    # ---- PUBLISH QUALITY GATE (before any browser is started) ----
    gate_ok, gate_error, media = validate_publish_media(
        platform, post.get("content_type"), post.get("media_paths")
    )
    if not gate_ok:
        print(f"[Publisher] BLOCKED #{post_id}: {gate_error}")
        mark_failed(post_id, gate_error)
        return False

    print(f"[Publisher] Publishing #{post_id} to {platform} ({len(content)} chars)")

    try:
        automation = automation_getter(platform, organization_id)
        if automation is None:
            # Discord uses webhook — handle separately
            if platform == "discord":
                return await _publish_discord(post)
            mark_failed(post_id, f"No automation for platform: {platform}")
            return False

        # Initialize browser
        await automation.init_browser()

        # Try to load existing session
        session_loaded = await automation.load_session(platform)
        if not session_loaded:
            mark_failed(post_id, f"No {platform} session. Login required via dashboard.")
            await automation.close()
            return False

        # Post content — automations take image_paths: List[str]
        result = await automation.post(content, media or None)
        if isinstance(result, dict):
            success = bool(result.get("success"))
            failure_message = result.get("message", "")
        else:
            success = bool(result)
            failure_message = ""

        if success:
            await automation.save_session(platform)
            mark_posted(post_id)
            print(f"[Publisher] POSTED #{post_id} to {platform}")
        else:
            mark_failed(post_id, failure_message or f"Playwright posting failed for {platform}")
            print(f"[Publisher] FAILED #{post_id} to {platform}")

        await automation.close()
        return success

    except Exception as e:
        mark_failed(post_id, str(e)[:500])
        print(f"[Publisher] ERROR #{post_id}: {e}")
        return False


async def _publish_discord(post: Dict) -> bool:
    """Publish to Discord via webhook, using THIS post's organization's webhook.

    Before D1 A5 this looked up `accounts WHERE platform = 'discord'` with no
    organization filter at all — any organization's post would publish
    through whichever organization's Discord webhook happened to be in that
    row (A1 finding 2).
    """
    import subprocess
    from pathlib import Path

    org_id = bat_buoc(post.get("organization_id"))

    # Read webhook from DB accounts table, scoped to this post's organization.
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    account = conn.execute(
        "SELECT * FROM accounts WHERE platform = 'discord' AND organization_id = ?",
        (org_id,)
    ).fetchone()
    conn.close()

    if not account:
        mark_failed(post["id"], "No Discord webhook configured in accounts")
        return False

    # The 'password_encrypted' field stores the webhook URL for Discord
    try:
        from main import decrypt
        webhook_url = decrypt(account["password_encrypted"])
    except Exception as e:
        mark_failed(post["id"], f"Cannot decrypt Discord webhook: {e}")
        return False

    if not webhook_url.startswith("https://discord.com/api/webhooks/"):
        mark_failed(post["id"], "Invalid Discord webhook URL")
        return False

    payload = json.dumps({
        "content": post["content"],
        "username": "InBharat Bot"
    })

    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-X", "POST", webhook_url,
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=15
        )
        if result.stdout.strip() in ("200", "204"):
            mark_posted(post["id"])
            print(f"[Publisher] POSTED #{post['id']} to Discord via webhook")
            return True
        else:
            mark_failed(post["id"], f"Discord webhook returned {result.stdout.strip()}")
            return False
    except Exception as e:
        mark_failed(post["id"], str(e))
        return False


async def run(automation_getter=None, organization_id: Optional[str] = None):
    """Main publisher run — publish this organization's approved posts.

    Args:
        automation_getter: Function(platform, organization_id) -> automation instance
        organization_id: Which organization's approved posts to publish.
    """
    org_id = bat_buoc(organization_id)
    print(f"[Publisher] Starting at {datetime.now().strftime('%H:%M:%S')} for org '{org_id}'")

    posts = get_approved_posts(limit=10, organization_id=org_id)
    print(f"[Publisher] {len(posts)} approved posts to publish")

    posted = 0
    failed = 0
    # Đo thời gian thật mỗi nền tảng (A2b, Unified Shell §7) — số đo này
    # quyết định giới hạn song song ở A2, không phải một cron chung xử lý
    # tuần tự từng tổ chức, và không phải đoán.
    platform_seconds: Dict[str, float] = {}

    for post in posts:
        platform = post.get("platform", "unknown")
        started = time.monotonic()
        success = await publish_post(post, automation_getter)
        elapsed = time.monotonic() - started
        platform_seconds[platform] = platform_seconds.get(platform, 0.0) + elapsed
        print(f"[Publisher] {platform} took {elapsed:.1f}s (post #{post.get('id')})")
        if success:
            posted += 1
        else:
            failed += 1

    print(f"[Publisher] Done — posted: {posted}, failed: {failed}")
    return {"posted": posted, "failed": failed, "platform_seconds": platform_seconds}
