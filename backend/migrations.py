"""
Tiny forward-only schema migrations for SocialFlow.

The repo has no migration system: tables are created with CREATE TABLE IF NOT
EXISTS, which silently does nothing once a table exists — so a new column on an
existing table never appears. This module fills that gap without pulling in
Alembic.

Rules:
  - every migration is idempotent (safe to run on every startup)
  - forward-only: we add columns and indexes, never drop or rewrite them
  - SQLite ALTER TABLE ADD COLUMN is O(1) and does not rewrite the table,
    so this stays cheap even as the DB grows

Call run_migrations() once at startup, after the CREATE TABLE calls.
"""

import sqlite3
from pathlib import Path
from typing import List, Tuple

BACKEND_DIR = Path(__file__).resolve().parent
DATABASE_PATH = str(BACKEND_DIR / "socialflow.db")

# (table, column, SQL type + default) — appended only.
COLUMNS: List[Tuple[str, str, str]] = [
    # Content-addressed hash. Lets us recognise a file we already hold and skip
    # both the disk write and the duplicate row.
    ("assets", "sha256", "TEXT"),
    # 'generated' (AI made it) vs 'uploaded' (the user brought it).
    ("assets", "origin", "TEXT DEFAULT 'generated'"),
    # Which engine produced it: gpt-image-1.5, veo-3.1, heygen, user, ...
    ("assets", "provider", "TEXT"),
    # What this asset cost to produce, so spend is answerable per provider.
    ("assets", "cost_usd", "REAL"),
    # Small preview so the media library does not load full-size originals.
    ("assets", "thumb_path", "TEXT"),
    # Provenance: which asset this was derived from (slide -> carousel,
    # 9:16 variant -> master video).
    ("assets", "parent_asset_id", "TEXT"),
]

# Tables this module owns outright (asset_inventory.py owns the older ones).
TABLES = {
    # A generation request, so a poll knows which provider a task belongs to.
    # Without this the app can only ever talk to one video API: a bare task id
    # says nothing about who issued it.
    "video_jobs": """
        CREATE TABLE IF NOT EXISTS video_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE NOT NULL,
            provider TEXT NOT NULL,
            provider_job_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            kind TEXT DEFAULT 'video',
            prompt TEXT,
            params TEXT,
            asset_id TEXT,
            video_url TEXT,
            cost_usd REAL,
            error TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """,
}

INDEXES: List[Tuple[str, str, str]] = [
    ("idx_assets_sha256", "assets", "sha256"),
    ("idx_assets_type", "assets", "asset_type"),
    ("idx_assets_origin", "assets", "origin"),
    ("idx_assets_created", "assets", "created_at"),
    ("idx_assets_parent", "assets", "parent_asset_id"),
    ("idx_video_jobs_job", "video_jobs", "job_id"),
    ("idx_video_jobs_status", "video_jobs", "status"),
]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def run_migrations(database_path: str = None) -> dict:
    """Apply pending schema changes. Returns what was actually done."""
    path = database_path or DATABASE_PATH
    added_columns, added_indexes, skipped = [], [], []

    created_tables = []

    conn = sqlite3.connect(path)
    try:
        for table, ddl in TABLES.items():
            existed = _table_exists(conn, table)
            conn.execute(ddl)
            if not existed:
                created_tables.append(table)

        for table, column, decl in COLUMNS:
            if not _table_exists(conn, table):
                skipped.append(f"{table}.{column} (table missing)")
                continue
            if column in _columns(conn, table):
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            added_columns.append(f"{table}.{column}")

        for name, table, column in INDEXES:
            if not _table_exists(conn, table):
                continue
            if column not in _columns(conn, table):
                continue
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({column})")
            added_indexes.append(name)

        conn.commit()
    finally:
        conn.close()

    return {
        "created_tables": created_tables,
        "added_columns": added_columns,
        "added_indexes": added_indexes,
        "skipped": skipped,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_migrations(), indent=2))
