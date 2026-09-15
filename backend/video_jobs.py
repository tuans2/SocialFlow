"""
Video job bookkeeping.

A provider hands back its own opaque task id. Storing that alone is what limits
the app to a single video API forever — when the frontend later asks "is task
abc123 done?", nothing knows who to ask. This table remembers the pairing, plus
enough context to show a useful status without calling the provider again.
"""

import json
import sqlite3
import uuid
from pathlib import Path
from typing import List, Optional
from duong_dan_db import duong_dan_db

BACKEND_DIR = Path(__file__).resolve().parent
DATABASE_PATH = duong_dan_db()


def _conn():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    job = dict(row)
    try:
        job["params"] = json.loads(job.get("params") or "{}")
    except (TypeError, ValueError):
        job["params"] = {}
    return job


def create(provider: str, provider_job_id: str, *, prompt: str = "",
           kind: str = "video", params: dict = None,
           cost_usd: float = None, organization_id: str) -> dict:
    """Record a submitted job and return it. job_id is ours, not the provider's."""
    job_id = f"vid-{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO video_jobs
               (job_id, provider, provider_job_id, status, kind, prompt, params,
                cost_usd, organization_id)
               VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?)""",
            (job_id, provider, provider_job_id, kind, prompt,
             json.dumps(params or {}), cost_usd, organization_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get(job_id, organization_id=organization_id)


def get(job_id: str, *, organization_id: str) -> Optional[dict]:
    conn = _conn()
    try:
        return _row(conn.execute(
            "SELECT * FROM video_jobs WHERE job_id = ? AND organization_id = ?",
            (job_id, organization_id)).fetchone())
    finally:
        conn.close()


def get_by_provider_job(provider_job_id: str, *, organization_id: str) -> Optional[dict]:
    """Look up by the provider's own id.

    Kept so a task id issued before this table existed, or pasted in by hand,
    still resolves instead of 404ing.
    """
    conn = _conn()
    try:
        return _row(conn.execute(
            "SELECT * FROM video_jobs WHERE provider_job_id = ? AND organization_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (provider_job_id, organization_id)).fetchone())
    finally:
        conn.close()


def update(job_id: str, *, organization_id: str, **fields) -> Optional[dict]:
    allowed = {"status", "asset_id", "video_url", "error", "cost_usd", "params"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        sets.append(f"{key} = ?")
        params.append(json.dumps(value) if key == "params" else value)
    if not sets:
        return get(job_id, organization_id=organization_id)
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(job_id)
    conn = _conn()
    try:
        params.append(organization_id)
        conn.execute(
            f"UPDATE video_jobs SET {', '.join(sets)} "
            "WHERE job_id = ? AND organization_id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()
    return get(job_id, organization_id=organization_id)


def recent(limit: int = 25, status: str = None, *, organization_id: str) -> List[dict]:
    conn = _conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM video_jobs WHERE organization_id = ? AND status = ? "
                "ORDER BY id DESC LIMIT ?",
                (organization_id, status, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM video_jobs WHERE organization_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (organization_id, limit)).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def spend_summary(*, organization_id: str) -> dict:
    """What video generation actually cost, per provider.

    Deliberately summed here and not from assets.cost_usd: when a generation
    returns bytes already held, the media store reuses the existing asset and
    that asset keeps the FIRST run's cost. Every submission is billed, so the
    job table is the only place with one row per charge.
    """
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT provider,
                      COUNT(*) AS jobs,
                      SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded,
                      ROUND(COALESCE(SUM(cost_usd), 0), 4) AS estimated_usd
               FROM video_jobs WHERE organization_id = ? GROUP BY provider""",
            (organization_id,),
        ).fetchall()
        by_provider = [dict(r) for r in rows]
        return {
            "by_provider": by_provider,
            "total_estimated_usd": round(sum(r["estimated_usd"] or 0 for r in by_provider), 4),
            "note": "Estimated from the configured per-second rate; not a billing statement.",
        }
    finally:
        conn.close()
