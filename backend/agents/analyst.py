"""
Analyst Agent — Performance + Learning

Responsibilities:
  - Track posts per platform, success/failure rates
  - Generate daily summary reports
  - Provide dashboard analytics data
  - Feed insights back to Planner

Runs: End of day (or on-demand via API)
"""

import os
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

DB_PATH = Path(__file__).parent.parent / "socialflow.db"

# Cung hang so voi migrations.py/main.py/orchestrator.py.
DEFAULT_ORGANIZATION_ID = os.getenv("SOCIALFLOW_DEFAULT_ORGANIZATION_ID", "default")


def get_daily_stats(target_date: str = None, organization_id: Optional[str] = None) -> Dict:
    """Get this organization's posting stats for a specific date."""
    if not target_date:
        target_date = date.today().isoformat()
    org_id = organization_id or DEFAULT_ORGANIZATION_ID

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Posts by platform and status
    rows = conn.execute("""
        SELECT platform, status, COUNT(*) as count
        FROM posts
        WHERE DATE(created_at) = ? AND COALESCE(organization_id, ?) = ?
        GROUP BY platform, status
    """, (target_date, DEFAULT_ORGANIZATION_ID, org_id)).fetchall()

    stats = {}
    for row in rows:
        p = row["platform"]
        if p not in stats:
            stats[p] = {"draft": 0, "approved": 0, "posted": 0, "failed": 0, "review": 0, "blocked": 0}
        stats[p][row["status"]] = row["count"]

    # Signals THIS ORGANIZATION acted on (signal_org_processed) — not a raw
    # count of `signals` fetched that day, since `signals` is now one shared
    # table across every organization (D1, A5) and a global count would not
    # mean much on a per-organization dashboard.
    signal_count = conn.execute(
        "SELECT COUNT(*) FROM signal_org_processed "
        "WHERE organization_id = ? AND DATE(processed_at) = ?",
        (org_id, target_date)
    ).fetchone()[0]

    # Plans created
    plan_count = conn.execute(
        "SELECT COUNT(*) FROM content_plans WHERE DATE(created_at) = ? AND COALESCE(organization_id, ?) = ?",
        (target_date, DEFAULT_ORGANIZATION_ID, org_id)
    ).fetchone()[0]

    conn.close()

    total_posted = sum(s.get("posted", 0) for s in stats.values())
    total_failed = sum(s.get("failed", 0) for s in stats.values())

    return {
        "date": target_date,
        "signals_fetched": signal_count,
        "plans_created": plan_count,
        "platforms": stats,
        "total_posted": total_posted,
        "total_failed": total_failed,
        "success_rate": round(total_posted / max(total_posted + total_failed, 1) * 100, 1),
    }


def get_weekly_stats(organization_id: Optional[str] = None) -> Dict:
    """Get this organization's stats for the past 7 days."""
    days = []
    for i in range(7):
        d = (date.today() - timedelta(days=i)).isoformat()
        days.append(get_daily_stats(d, organization_id=organization_id))

    total_posted = sum(d["total_posted"] for d in days)
    total_failed = sum(d["total_failed"] for d in days)

    return {
        "period": "7 days",
        "days": days,
        "total_posted": total_posted,
        "total_failed": total_failed,
        "avg_posts_per_day": round(total_posted / 7, 1),
        "success_rate": round(total_posted / max(total_posted + total_failed, 1) * 100, 1),
    }


def get_top_performing_posts(limit: int = 5, organization_id: Optional[str] = None) -> List[Dict]:
    """Get this organization's recent posted content (for dashboard)."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM posts
        WHERE status = 'posted' AND COALESCE(organization_id, ?) = ?
        ORDER BY published_at DESC
        LIMIT ?
    """, (DEFAULT_ORGANIZATION_ID, org_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


async def run(organization_id: Optional[str] = None):
    """Generate this organization's daily analytics report."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    print(f"[Analyst] Starting at {datetime.now().strftime('%H:%M:%S')} for org '{org_id}'")

    stats = get_daily_stats(organization_id=org_id)
    print(f"[Analyst] Today: {stats['total_posted']} posted, {stats['total_failed']} failed")
    print(f"[Analyst] Signals: {stats['signals_fetched']}, Plans: {stats['plans_created']}")
    for platform, counts in stats.get("platforms", {}).items():
        print(f"[Analyst]   {platform}: {counts}")

    weekly = get_weekly_stats(organization_id=org_id)
    print(f"[Analyst] Week: {weekly['total_posted']} posts, {weekly['avg_posts_per_day']}/day avg")

    return stats
