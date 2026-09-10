"""
Planner Agent — Content Strategy

Responsibilities:
  - Read unprocessed signals from Scout
  - Decide: what to post, which platform, what format, what time
  - Apply rate-limits and platform best practices
  - Avoid duplicates and topic repetition
  - Create content plans in SQLite

Runs: After Scout completes
"""

import json
import os
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional

from to_chuc import bat_buoc

DB_PATH = Path(__file__).parent.parent / "socialflow.db"

# Cung hang so voi migrations.py/main.py/orchestrator.py.

# Platform content format mapping
PLATFORM_FORMATS = {
    "linkedin": {"max_chars": 3000, "supports_image": True, "supports_carousel": True},
    "x": {"max_chars": 280, "supports_image": True, "supports_carousel": False},
    "discord": {"max_chars": 2000, "supports_image": True, "supports_carousel": False},
    "instagram": {"max_chars": 2200, "supports_image": True, "supports_carousel": True},
    "reddit": {"max_chars": 40000, "supports_image": False, "supports_carousel": False},
    "facebook": {"max_chars": 63206, "supports_image": True, "supports_carousel": True},
}

# Default rate-limits (overridden by brand_config if set)
DEFAULT_LIMITS = {
    "linkedin": {"daily_cap": 2, "windows": [11, 23]},
    "x": {"daily_cap": 5, "windows": None},
    "discord": {"daily_cap": 10, "windows": None},
    "instagram": {"daily_cap": 3, "windows": None},
    "reddit": {"daily_cap": 1, "windows": None},
    "facebook": {"daily_cap": 3, "windows": None},
}


def init_plans_table():
    """Create content_plans table."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('''CREATE TABLE IF NOT EXISTS content_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER,
        platform TEXT NOT NULL,
        content_type TEXT NOT NULL,
        priority INTEGER DEFAULT 5,
        scheduled_hour INTEGER,
        status TEXT DEFAULT 'planned',
        brief TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (signal_id) REFERENCES signals(id)
    )''')
    conn.commit()
    conn.close()


def get_todays_plan_count(platform: str, organization_id: Optional[str] = None) -> int:
    """How many plans this organization already created for this platform today."""
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(str(DB_PATH))
    today = date.today().isoformat()
    count = conn.execute(
        "SELECT COUNT(*) FROM content_plans WHERE platform = ? AND DATE(created_at) = ? "
        "AND organization_id = ?",
        (platform, today, org_id)
    ).fetchone()[0]
    conn.close()
    return count


def get_todays_post_count(platform: str, organization_id: Optional[str] = None) -> int:
    """How many posts this organization already sent for this platform today."""
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(str(DB_PATH))
    today = date.today().isoformat()
    count = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE platform = ? AND status = 'posted' AND DATE(published_at) = ? "
        "AND organization_id = ?",
        (platform, today, org_id)
    ).fetchone()[0]
    conn.close()
    return count


def create_plan(signal_id: int, platform: str, content_type: str,
                priority: int = 5, scheduled_hour: int = None, brief: str = "",
                organization_id: Optional[str] = None):
    """Create a content plan entry, tagged to the organization that requested it."""
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO content_plans (signal_id, platform, content_type, priority, scheduled_hour, brief, organization_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (signal_id, platform, content_type, priority, scheduled_hour, brief, org_id)
    )
    conn.commit()
    conn.close()


def plan_signal(signal: Dict, organization_id: Optional[str] = None) -> List[Dict]:
    """Decide what to do with a signal — which platforms and formats, for ONE organization.

    `signal` itself is shared (see migrations.py) but every cap check and
    every plan created here is scoped to `organization_id` — two
    organizations independently deciding what to do with the same shared
    signal must not see or affect each other's daily caps.

    Returns list of plan dicts created.
    """
    org_id = bat_buoc(organization_id)
    plans = []
    title = signal.get("title", "")
    signal_type = signal.get("signal_type", "news")
    score = signal.get("score", 0)
    relevance = signal.get("relevance_score", 0)

    # High-relevance news → LinkedIn + X + Discord
    if signal_type == "news" and (relevance > 0.3 or score > 100):
        for platform, hour in [("discord", None), ("linkedin", 11), ("x", None)]:
            limits = DEFAULT_LIMITS.get(platform, {})
            cap = limits.get("daily_cap", 5)
            current = (get_todays_plan_count(platform, org_id)
                       + get_todays_post_count(platform, org_id))
            if current < cap:
                create_plan(
                    signal_id=signal["id"],
                    platform=platform,
                    content_type="ai-news",
                    priority=min(10, score // 50 + int(relevance * 5)),
                    scheduled_hour=hour,
                    brief=f"News post about: {title}",
                    organization_id=org_id,
                )
                plans.append({"platform": platform, "type": "ai-news"})

    # Medium-relevance news → Discord only
    elif signal_type == "news" and relevance > 0.1:
        current = (get_todays_plan_count("discord", org_id)
                   + get_todays_post_count("discord", org_id))
        if current < DEFAULT_LIMITS["discord"]["daily_cap"]:
            create_plan(
                signal_id=signal["id"],
                platform="discord",
                content_type="ai-news",
                priority=3,
                brief=f"Quick news drop: {title}",
                organization_id=org_id,
            )
            plans.append({"platform": "discord", "type": "ai-news"})

    # Repo signals → Discord + LinkedIn (3x/week) + Reddit
    elif signal_type in ("repo", "release"):
        for platform in ["discord", "linkedin", "reddit"]:
            limits = DEFAULT_LIMITS.get(platform, {})
            cap = limits.get("daily_cap", 5)
            current = (get_todays_plan_count(platform, org_id)
                       + get_todays_post_count(platform, org_id))
            if current < cap:
                create_plan(
                    signal_id=signal["id"],
                    platform=platform,
                    content_type="repo-promo",
                    priority=7,
                    brief=f"Promote repo: {title}",
                    organization_id=org_id,
                )
                plans.append({"platform": platform, "type": "repo-promo"})

    return plans


async def run(organization_id: Optional[str] = None):
    """Main planner run — process this organization's unprocessed signals."""
    from agents.scout import get_unprocessed_signals, mark_processed

    org_id = bat_buoc(organization_id)
    init_plans_table()
    print(f"[Planner] Starting at {datetime.now().strftime('%H:%M:%S')} for org '{org_id}'")

    signals = get_unprocessed_signals(organization_id=org_id, limit=15)
    print(f"[Planner] Processing {len(signals)} unprocessed signals")

    total_plans = 0
    processed_ids = []

    for signal in signals:
        plans = plan_signal(signal, organization_id=org_id)
        total_plans += len(plans)
        processed_ids.append(signal["id"])
        if plans:
            platforms = ", ".join(p["platform"] for p in plans)
            print(f"[Planner] Signal '{signal['title'][:50]}' → {platforms}")

    if processed_ids:
        mark_processed(processed_ids, organization_id=org_id)

    print(f"[Planner] Done — {total_plans} plans created from {len(signals)} signals")
    return total_plans


def get_pending_plans(organization_id: Optional[str] = None, limit: int = 20) -> List[Dict]:
    """Get this organization's planned content that hasn't been created yet."""
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('''
        SELECT cp.*, s.title as signal_title, s.url as signal_url, s.summary as signal_summary,
               s.source as signal_source
        FROM content_plans cp
        JOIN signals s ON cp.signal_id = s.id
        WHERE cp.status = 'planned' AND COALESCE(cp.organization_id, ?) = ?
        ORDER BY cp.priority DESC, cp.created_at ASC
        LIMIT ?
    ''', (org_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_plan_completed(plan_id: int):
    """Mark a plan as completed (content created).

    plan_id already comes from an organization-scoped read (get_pending_plans),
    so this update does not need its own organization filter — it can only
    ever touch a row the caller already knows belongs to their organization.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE content_plans SET status = 'completed' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()
