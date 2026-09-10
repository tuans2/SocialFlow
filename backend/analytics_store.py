"""
SocialFlow Analytics Store — Post-Publish Performance Tracking

This is the foundation for the feedback loop.
Even before full platform API connections, this provides:
- schema for per-post metrics
- ingestion structure
- comparison queries
- weekly/monthly aggregation
- content type performance ranking

Data can be entered manually, via API, or automated later.
"""

import os

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from pathlib import Path

from to_chuc import bat_buoc

DATABASE_PATH = "socialflow.db"

# Same env var/default as main.py and migrations.py — D1 (đa tenant thật,
# 2026-09-10). post_metrics is the one table in this module with a real
# read/write path; every function below filters or tags by organization.


def init_analytics_tables():
    """Create analytics tables"""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()

    # Per-post performance metrics
    c.execute('''CREATE TABLE IF NOT EXISTS post_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_ref TEXT NOT NULL,
        platform TEXT NOT NULL,
        content_type TEXT,
        topic TEXT,
        campaign TEXT,
        pillar TEXT,
        published_at DATETIME,
        impressions INTEGER DEFAULT 0,
        reach INTEGER DEFAULT 0,
        engagement INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        comments INTEGER DEFAULT 0,
        shares INTEGER DEFAULT 0,
        saves INTEGER DEFAULT 0,
        clicks INTEGER DEFAULT 0,
        video_views INTEGER DEFAULT 0,
        engagement_rate REAL DEFAULT 0.0,
        notes TEXT,
        collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        source TEXT DEFAULT 'manual'
    )''')

    # Weekly aggregated metrics
    c.execute('''CREATE TABLE IF NOT EXISTS weekly_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start DATE NOT NULL,
        week_end DATE NOT NULL,
        platform TEXT NOT NULL,
        total_posts INTEGER DEFAULT 0,
        total_impressions INTEGER DEFAULT 0,
        total_engagement INTEGER DEFAULT 0,
        avg_engagement_rate REAL DEFAULT 0.0,
        top_post_ref TEXT,
        top_post_engagement INTEGER DEFAULT 0,
        notes TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    # Content performance by type/pillar (for feedback loop)
    c.execute('''CREATE TABLE IF NOT EXISTS content_insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT NOT NULL,
        insight_type TEXT NOT NULL,
        key_name TEXT NOT NULL,
        value REAL DEFAULT 0.0,
        sample_size INTEGER DEFAULT 0,
        details TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    conn.commit()
    conn.close()


# ─── RECORD METRICS ──────────────────────────────────────────

def record_post_metrics(
    post_ref: str,
    platform: str,
    content_type: str = "",
    topic: str = "",
    campaign: str = "",
    pillar: str = "",
    published_at: str = None,
    impressions: int = 0,
    reach: int = 0,
    engagement: int = 0,
    likes: int = 0,
    comments: int = 0,
    shares: int = 0,
    saves: int = 0,
    clicks: int = 0,
    video_views: int = 0,
    notes: str = "",
    source: str = "manual",
    organization_id: Optional[str] = None
) -> dict:
    """Record metrics for a single post"""
    # Calculate engagement rate
    eng_rate = 0.0
    if reach > 0:
        eng_rate = round((engagement / reach) * 100, 2)
    elif impressions > 0:
        eng_rate = round((engagement / impressions) * 100, 2)

    org_id = bat_buoc(organization_id)

    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO post_metrics
        (post_ref, platform, content_type, topic, campaign, pillar,
         published_at, impressions, reach, engagement, likes, comments,
         shares, saves, clicks, video_views, engagement_rate, notes, source,
         organization_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (post_ref, platform, content_type, topic, campaign, pillar,
         published_at or datetime.now().isoformat(),
         impressions, reach, engagement, likes, comments,
         shares, saves, clicks, video_views, eng_rate, notes, source, org_id)
    )
    conn.commit()
    conn.close()

    return {"post_ref": post_ref, "platform": platform, "engagement_rate": eng_rate, "organization_id": org_id}


# ─── QUERY METRICS ───────────────────────────────────────────

def get_platform_performance(platform: str, days: int = 30, organization_id: Optional[str] = None) -> List[dict]:
    """Get recent performance for a platform, scoped to one organization."""
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    c.execute("""SELECT * FROM post_metrics
                 WHERE platform = ? AND collected_at > ?
                   AND organization_id = ?
                 ORDER BY engagement_rate DESC""",
              (platform, since, org_id))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_top_content_types(days: int = 30, organization_id: Optional[str] = None) -> List[dict]:
    """Which content types perform best, scoped to one organization?"""
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    c.execute("""SELECT content_type, platform,
                        COUNT(*) as post_count,
                        AVG(engagement_rate) as avg_eng_rate,
                        SUM(engagement) as total_engagement
                 FROM post_metrics
                 WHERE collected_at > ? AND content_type != ''
                   AND organization_id = ?
                 GROUP BY content_type, platform
                 ORDER BY avg_eng_rate DESC""",
              (since, org_id))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_pillar_performance(days: int = 30, organization_id: Optional[str] = None) -> List[dict]:
    """Which content pillars perform best, scoped to one organization?"""
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    c.execute("""SELECT pillar,
                        COUNT(*) as post_count,
                        AVG(engagement_rate) as avg_eng_rate,
                        SUM(impressions) as total_impressions
                 FROM post_metrics
                 WHERE collected_at > ? AND pillar != ''
                   AND organization_id = ?
                 GROUP BY pillar
                 ORDER BY avg_eng_rate DESC""",
              (since, org_id))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def generate_weekly_summary(week_start: str = None) -> dict:
    """Generate weekly performance summary"""
    if not week_start:
        today = datetime.now()
        start = today - timedelta(days=today.weekday() + 7)
        week_start = start.strftime('%Y-%m-%d')

    week_end = (datetime.strptime(week_start, '%Y-%m-%d') + timedelta(days=6)).strftime('%Y-%m-%d')

    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""SELECT platform,
                        COUNT(*) as posts,
                        SUM(impressions) as impressions,
                        SUM(engagement) as engagement,
                        AVG(engagement_rate) as avg_rate
                 FROM post_metrics
                 WHERE published_at BETWEEN ? AND ?
                 GROUP BY platform""",
              (week_start, week_end + "T23:59:59"))

    platforms = {row["platform"]: dict(row) for row in c.fetchall()}

    c.execute("""SELECT post_ref, platform, engagement_rate, topic
                 FROM post_metrics
                 WHERE published_at BETWEEN ? AND ?
                 ORDER BY engagement_rate DESC LIMIT 3""",
              (week_start, week_end + "T23:59:59"))

    top_posts = [dict(r) for r in c.fetchall()]
    conn.close()

    return {
        "week_start": week_start,
        "week_end": week_end,
        "platforms": platforms,
        "top_posts": top_posts,
        "total_posts": sum(p.get("posts", 0) for p in platforms.values()),
        "total_impressions": sum(p.get("impressions", 0) for p in platforms.values()),
        "total_engagement": sum(p.get("engagement", 0) for p in platforms.values())
    }


# ─── ANALYTICS API ROUTES ────────────────────────────────────

def register_analytics_routes(app):
    """Register analytics API endpoints on the FastAPI app"""
    from fastapi import APIRouter, Depends

    from org_context import require_org
    from pydantic import BaseModel
    from typing import Optional

    router = APIRouter(
        prefix="/api/analytics",
        tags=["Analytics"],
        dependencies=[Depends(require_org)],
    )

    class MetricsInput(BaseModel):
        post_ref: str
        platform: str
        content_type: str = ""
        topic: str = ""
        campaign: str = ""
        pillar: str = ""
        published_at: Optional[str] = None
        impressions: int = 0
        reach: int = 0
        engagement: int = 0
        likes: int = 0
        comments: int = 0
        shares: int = 0
        saves: int = 0
        clicks: int = 0
        video_views: int = 0
        notes: str = ""
        organization_id: Optional[str] = None

    @router.post("/metrics")
    async def add_metrics(m: MetricsInput):
        result = record_post_metrics(**m.dict())
        return {"success": True, "result": result}

    @router.get("/platform/{platform}")
    async def platform_perf(platform: str, days: int = 30, organization_id: str = Depends(require_org)):
        return {"platform": platform, "data": get_platform_performance(platform, days, organization_id)}

    @router.get("/top-content-types")
    async def top_types(days: int = 30, organization_id: str = Depends(require_org)):
        return {"data": get_top_content_types(days, organization_id)}

    @router.get("/pillars")
    async def pillar_perf(days: int = 30, organization_id: str = Depends(require_org)):
        return {"data": get_pillar_performance(days, organization_id)}

    @router.get("/weekly-summary")
    async def weekly(week_start: str = None, organization_id: str = Depends(require_org)):
        return generate_weekly_summary(week_start, organization_id)

    app.include_router(router)


# Initialize on import
init_analytics_tables()
