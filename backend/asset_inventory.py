"""
SocialFlow Asset Inventory — Content & Media Tracking System

Tracks all generated assets (videos, images, text) across the pipeline.
Think of this as SocialFlow's content library / DAM (Digital Asset Manager).

Every generated piece — whether from HeyGen, AI text, carousel, or manual upload —
gets registered here with metadata for reuse, routing, and auditing.
"""

import json
import os
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
DATABASE_PATH = str(BACKEND_DIR / "socialflow.db")


def init_asset_tables():
    """Create asset inventory tables if they don't exist"""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()

    # Core asset inventory
    c.execute('''CREATE TABLE IF NOT EXISTS assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id TEXT UNIQUE NOT NULL,
        asset_type TEXT NOT NULL,
        source TEXT NOT NULL,
        title TEXT,
        description TEXT,
        file_path TEXT,
        file_url TEXT,
        file_size INTEGER,
        duration_seconds INTEGER,
        aspect_ratio TEXT,
        campaign TEXT,
        topic TEXT,
        tags TEXT,
        metadata TEXT,
        state TEXT DEFAULT 'available',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    # Content queue — platform-specific distribution items
    c.execute('''CREATE TABLE IF NOT EXISTS content_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        queue_id TEXT UNIQUE NOT NULL,
        asset_id TEXT,
        platform TEXT NOT NULL,
        content_type TEXT NOT NULL,
        caption TEXT,
        hashtags TEXT,
        copy_text TEXT,
        media_paths TEXT,
        scheduled_time DATETIME,
        state TEXT DEFAULT 'pending',
        campaign TEXT,
        approval_level TEXT,
        source_job_id TEXT,
        error TEXT,
        published_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
    )''')

    # Campaign grouping
    c.execute('''CREATE TABLE IF NOT EXISTS campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        start_date DATE,
        end_date DATE,
        state TEXT DEFAULT 'active',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    conn.commit()
    conn.close()


# ─── ASSET CRUD ───────────────────────────────────────────────

def register_asset(
    asset_type: str,
    source: str,
    title: str = "",
    description: str = "",
    file_path: str = None,
    file_url: str = None,
    file_size: int = None,
    duration_seconds: int = None,
    aspect_ratio: str = None,
    campaign: str = "",
    topic: str = "",
    tags: List[str] = None,
    metadata: dict = None,
    sha256: str = None,
    origin: str = "generated",
    provider: str = None,
    cost_usd: float = None,
    thumb_path: str = None,
    parent_asset_id: str = None
) -> dict:
    """Register a new asset in the inventory.

    sha256/origin/provider/cost_usd/thumb_path/parent_asset_id are added by
    migrations.py; they are written defensively so this still works against a
    database that has not been migrated yet.
    """
    asset_id = f"asset-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"

    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()

    cols = {
        "asset_id": asset_id,
        "asset_type": asset_type,
        "source": source,
        "title": title,
        "description": description,
        "file_path": file_path,
        "file_url": file_url,
        "file_size": file_size,
        "duration_seconds": duration_seconds,
        "aspect_ratio": aspect_ratio,
        "campaign": campaign,
        "topic": topic,
        "tags": json.dumps(tags or []),
        "metadata": json.dumps(metadata or {}),
    }

    # Only write the newer columns if this database actually has them.
    present = {r[1] for r in c.execute("PRAGMA table_info(assets)")}
    for key, value in (
        ("sha256", sha256),
        ("origin", origin),
        ("provider", provider),
        ("cost_usd", cost_usd),
        ("thumb_path", thumb_path),
        ("parent_asset_id", parent_asset_id),
    ):
        if key in present:
            cols[key] = value

    names = ", ".join(cols)
    holes = ", ".join("?" for _ in cols)
    c.execute(f"INSERT INTO assets ({names}) VALUES ({holes})", tuple(cols.values()))

    conn.commit()
    conn.close()

    return {
        "asset_id": asset_id,
        "asset_type": asset_type,
        "source": source,
        "title": title,
        "origin": origin,
        "sha256": sha256,
    }


def get_asset_by_sha256(sha256: str) -> Optional[dict]:
    """Find an already-stored asset by content hash (deduplication)."""
    if not sha256:
        return None
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if "sha256" not in {r[1] for r in conn.execute("PRAGMA table_info(assets)")}:
            return None
        row = conn.execute(
            "SELECT * FROM assets WHERE sha256 = ? AND state != 'deleted' "
            "ORDER BY id LIMIT 1",
            (sha256,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def archive_asset(asset_id: str) -> bool:
    """Soft delete — the row and the file both stay, the asset stops listing."""
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        cur = conn.execute(
            "UPDATE assets SET state = 'archived', updated_at = CURRENT_TIMESTAMP "
            "WHERE asset_id = ?",
            (asset_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_asset(asset_id: str, **fields) -> Optional[dict]:
    """Update editable metadata (title, description, campaign, topic, tags, state)."""
    allowed = {"title", "description", "campaign", "topic", "tags", "state"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        sets.append(f"{key} = ?")
        params.append(json.dumps(value) if key == "tags" else value)
    if not sets:
        return get_asset(asset_id)

    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(asset_id)
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(f"UPDATE assets SET {', '.join(sets)} WHERE asset_id = ?", params)
        conn.commit()
    finally:
        conn.close()
    return get_asset(asset_id)


def get_asset(asset_id: str) -> Optional[dict]:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def list_assets(
    asset_type: str = None,
    source: str = None,
    campaign: str = None,
    limit: int = 50,
    origin: str = None,
    topic: str = None,
    tag: str = None,
    q: str = None,
    offset: int = 0,
    include_archived: bool = False
) -> List[dict]:
    """List assets, newest first.

    Archived assets are hidden unless include_archived is set — the media
    library should not show what the user removed, but the row and the file
    both survive so any post already referencing it still resolves.
    """
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    present = {r[1] for r in c.execute("PRAGMA table_info(assets)")}

    query = "SELECT * FROM assets WHERE 1=1"
    params = []

    if not include_archived:
        query += " AND COALESCE(state, 'available') != 'archived'"
    if asset_type:
        query += " AND asset_type = ?"
        params.append(asset_type)
    if source:
        query += " AND source = ?"
        params.append(source)
    if campaign:
        query += " AND campaign = ?"
        params.append(campaign)
    if topic:
        query += " AND topic = ?"
        params.append(topic)
    if origin and "origin" in present:
        query += " AND origin = ?"
        params.append(origin)
    if tag:
        # tags is a JSON array; match the quoted token so "ai" does not hit "email"
        query += " AND tags LIKE ?"
        params.append(f'%"{tag}"%')
    if q:
        query += " AND (title LIKE ? OR description LIKE ? OR topic LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])

    query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])

    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def count_assets(**filters) -> int:
    """How many assets match — same filters as list_assets, ignoring paging."""
    filters.pop("limit", None)
    filters.pop("offset", None)
    return len(list_assets(limit=100000, **filters))


# ─── CONTENT QUEUE CRUD ──────────────────────────────────────

def queue_content(
    platform: str,
    content_type: str,
    caption: str = "",
    hashtags: str = "",
    copy_text: str = "",
    media_paths: List[str] = None,
    asset_id: str = None,
    scheduled_time: str = None,
    campaign: str = "",
    approval_level: str = None,
    source_job_id: str = None
) -> dict:
    """Add content to the distribution queue"""
    queue_id = f"q-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{platform[:3]}-{os.urandom(2).hex()}"

    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()

    c.execute('''INSERT INTO content_queue
        (queue_id, asset_id, platform, content_type, caption, hashtags, copy_text,
         media_paths, scheduled_time, campaign, approval_level, source_job_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            queue_id, asset_id, platform, content_type, caption, hashtags, copy_text,
            json.dumps(media_paths or []),
            scheduled_time, campaign, approval_level, source_job_id
        )
    )

    conn.commit()
    conn.close()

    return {"queue_id": queue_id, "platform": platform, "state": "pending"}


def list_queue(
    platform: str = None,
    state: str = None,
    campaign: str = None,
    limit: int = 50
) -> List[dict]:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    query = "SELECT * FROM content_queue WHERE 1=1"
    params = []

    if platform:
        query += " AND platform = ?"
        params.append(platform)
    if state:
        query += " AND state = ?"
        params.append(state)
    if campaign:
        query += " AND campaign = ?"
        params.append(campaign)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def update_queue_state(queue_id: str, new_state: str, error: str = None) -> bool:
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()

    updates = ["state = ?", "updated_at = ?"]
    params = [new_state, datetime.now().isoformat()]

    if error:
        updates.append("error = ?")
        params.append(error)

    if new_state == "published":
        updates.append("published_at = ?")
        params.append(datetime.now().isoformat())

    params.append(queue_id)
    c.execute(f"UPDATE content_queue SET {', '.join(updates)} WHERE queue_id = ?", params)

    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected > 0


# Initialize tables on import
init_asset_tables()
