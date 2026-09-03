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
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media_utils import validate_publish_media  # noqa: E402

DB_PATH = Path(__file__).parent.parent / "socialflow.db"


def get_approved_posts(platform: str = None, limit: int = 10):
    """Get approved posts ready for publishing."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if platform:
        rows = conn.execute(
            "SELECT * FROM posts WHERE status = 'approved' AND platform = ? ORDER BY created_at ASC LIMIT ?",
            (platform, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM posts WHERE status = 'approved' ORDER BY created_at ASC LIMIT ?",
            (limit,)
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
        post: Post dict from DB
        automation_getter: Function that returns the platform automation instance
    """
    platform = post["platform"]
    content = post["content"]
    post_id = post["id"]

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
        automation = automation_getter(platform)
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
    """Publish to Discord via webhook."""
    import subprocess
    from pathlib import Path

    # Read webhook from DB accounts table
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    account = conn.execute(
        "SELECT * FROM accounts WHERE platform = 'discord'"
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


async def run(automation_getter=None):
    """Main publisher run — publish all approved posts.

    Args:
        automation_getter: Function(platform) -> automation instance
    """
    print(f"[Publisher] Starting at {datetime.now().strftime('%H:%M:%S')}")

    posts = get_approved_posts(limit=10)
    print(f"[Publisher] {len(posts)} approved posts to publish")

    posted = 0
    failed = 0

    for post in posts:
        success = await publish_post(post, automation_getter)
        if success:
            posted += 1
        else:
            failed += 1

    print(f"[Publisher] Done — posted: {posted}, failed: {failed}")
    return {"posted": posted, "failed": failed}
