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

Call run_migrations() at startup, after the CREATE TABLE calls it targets
exist. In this app that is NOT a single point in time: assets/content_queue/
campaigns exist before the module-level call in main.py, but posts/accounts/
templates/signals/content_plans/brand_config are only created inside
lifespan() (main.py), which runs AFTER module import — so main.py calls
run_migrations() a second time at the end of lifespan(). Both calls are
safe: every migration here is idempotent.
"""

import os
import sqlite3
from pathlib import Path
from typing import List, Tuple

BACKEND_DIR = Path(__file__).resolve().parent
DATABASE_PATH = str(BACKEND_DIR / "socialflow.db")

# Same env var and default as main.py — keep the two in sync, this module
# normalizes NULL organization_id to this value when it rebuilds `accounts`.
DEFAULT_ORGANIZATION_ID = os.getenv("SOCIALFLOW_DEFAULT_ORGANIZATION_ID", "default")

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
    # Which organization owns this connected platform account (P7 — FloraOS
    # integration). NULL on rows created before organization scoping existed;
    # every lookup treats NULL as DEFAULT_ORGANIZATION_ID (see main.py).
    # NOTE: `accounts.platform` still carries a table-level UNIQUE constraint
    # from CREATE TABLE — this module only adds columns/indexes and never
    # rewrites an existing table, so one platform can still only ever belong
    # to one organization until `accounts` itself is rebuilt with
    # UNIQUE(organization_id, platform). See TECHNICAL_DEBT.md.
    ("accounts", "organization_id", "TEXT"),
    # D1 (đa tenant thật, 2026-09-10) — nhóm bảng an toàn: không nằm trong
    # luồng sáu agent (planner/scout/analyst/reviewer/creator/publisher), nên
    # thêm cột không cần sửa logic đọc/ghi kèm theo, trừ post_metrics (xem
    # analytics_store.py — bảng này CÓ đường ghi/đọc thật qua /api/analytics).
    # campaigns, templates, weekly_metrics, content_insights hiện không có
    # đường ghi/đọc nào trong mã (bảng tạo sẵn nhưng chưa dùng tới) — thêm cột
    # để sẵn sàng khi có, không phải vá một lỗ hổng đang hoạt động.
    ("campaigns", "organization_id", "TEXT"),
    ("templates", "organization_id", "TEXT"),
    ("post_metrics", "organization_id", "TEXT"),
    ("weekly_metrics", "organization_id", "TEXT"),
    ("content_insights", "organization_id", "TEXT"),
    # D1 (đa tenant thật, 2026-09-10) — Pha 2: bảng nằm TRONG luồng sáu agent
    # (planner/scout/analyst/reviewer/creator/publisher), phát hiện ở A1.
    # Cột này chỉ chuẩn bị chỗ chứa — chưa có agent nào đọc/ghi lọc theo
    # organization_id (đó là A5, còn mở). Tới khi A5 xong, mọi dòng hiện có
    # coi như thuộc DEFAULT_ORGANIZATION_ID qua COALESCE, giống accounts ở
    # Pha 1. posts: main.py init_db(). signals: agents/scout.py. content_plans:
    # agents/planner.py. content_queue, assets (kho asset nội bộ của
    # SocialFlow — khác Asset dùng chung ở floraos-core): asset_inventory.py.
    # brand_config: brand_kit.py — MỘT HÀNG MỖI TỔ CHỨC kể từ A5 (trước đó
    # là một hàng duy nhất dùng chung mọi tổ chức — xem TECHNICAL_DEBT.md).
    # KHÔNG có cột organization_id cho `signals`: tin tức Scout thu thập
    # (HackerNews/GitHub) là dữ liệu công khai dùng chung cho mọi tổ chức,
    # không phải sở hữu riêng của tổ chức nào — quyết định của anh Tony,
    # A5. Việc tổ chức nào đã dùng signal nào nằm ở bảng
    # signal_org_processed bên dưới (TABLES), không phải một cột ở đây —
    # tránh N tổ chức = N lần gọi lặp lại HackerNews/GitHub API (giới hạn
    # công khai của GitHub API là 60 lượt/giờ, dễ vượt nếu nhân bản theo
    # từng tổ chức khi chạy lô song song).
    ("posts", "organization_id", "TEXT"),
    ("content_plans", "organization_id", "TEXT"),
    ("content_queue", "organization_id", "TEXT"),
    ("assets", "organization_id", "TEXT"),
    ("brand_config", "organization_id", "TEXT"),
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
    # D1 (đa tenant thật, 2026-09-10) — A5. `signals` ở lại là MỘT bảng dùng
    # chung (tin tức công khai, không phải dữ liệu riêng của tổ chức nào —
    # xem comment ở COLUMNS phía trên). Bảng này ghi "tổ chức nào đã dùng
    # signal nào" — thay cho cột `signals.processed` cũ vốn chỉ có MỘT giá
    # trị dùng chung, không thể phản ánh việc nhiều tổ chức xử lý cùng một
    # signal độc lập với nhau. agents/scout.py đọc/ghi bảng này qua
    # get_unprocessed_signals(organization_id)/mark_processed(ids, organization_id).
    "signal_org_processed": """
        CREATE TABLE IF NOT EXISTS signal_org_processed (
            organization_id TEXT NOT NULL,
            signal_id INTEGER NOT NULL,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (organization_id, signal_id),
            FOREIGN KEY (signal_id) REFERENCES signals(id)
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
    ("idx_accounts_org", "accounts", "organization_id"),
    ("idx_campaigns_org", "campaigns", "organization_id"),
    ("idx_templates_org", "templates", "organization_id"),
    ("idx_post_metrics_org", "post_metrics", "organization_id"),
    ("idx_weekly_metrics_org", "weekly_metrics", "organization_id"),
    ("idx_content_insights_org", "content_insights", "organization_id"),
    ("idx_posts_org", "posts", "organization_id"),
    ("idx_content_plans_org", "content_plans", "organization_id"),
    ("idx_content_queue_org", "content_queue", "organization_id"),
    ("idx_assets_org", "assets", "organization_id"),
    ("idx_brand_config_org", "brand_config", "organization_id"),
]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _accounts_has_org_platform_unique(conn: sqlite3.Connection) -> bool:
    """True once `accounts` carries UNIQUE(organization_id, platform) instead
    of the old global UNIQUE(platform). Checked by substring on the table's
    own stored CREATE statement — safe because we control the exact DDL text
    below and never phrase it a second way."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='accounts'"
    ).fetchone()
    return bool(row and row[0] and "UNIQUE(organization_id, platform)" in row[0])


def _rebuild_accounts_unique_per_org(conn: sqlite3.Connection) -> bool:
    """D1 (đa tenant thật, 2026-09-10), TECHNICAL_DEBT.md #14-2.

    Drops the old global UNIQUE(platform) on `accounts` — the constraint that
    let only ONE organization system-wide ever hold credentials for a given
    platform — and replaces it with UNIQUE(organization_id, platform): one
    platform account per organization, any number of organizations.

    Safe to run on a populated table: the OLD UNIQUE(platform) constraint
    already guarantees no two existing rows share a platform value, so no
    row can ever collide under the new, narrower constraint. NULL
    organization_id is normalized to DEFAULT_ORGANIZATION_ID during the copy
    so the new constraint is well-defined (SQLite treats NULL as distinct in
    UNIQUE, which would silently defeat it for legacy rows otherwise).

    Requires `accounts.organization_id` to already exist — call this after
    the COLUMNS pass, not before. Idempotent: no-ops once already rebuilt.
    """
    if not _table_exists(conn, "accounts"):
        return False
    if _accounts_has_org_platform_unique(conn):
        return False

    conn.execute("ALTER TABLE accounts RENAME TO accounts_pre_org_unique")
    conn.execute('''CREATE TABLE accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        username TEXT NOT NULL,
        password_encrypted TEXT NOT NULL,
        is_logged_in INTEGER DEFAULT 0,
        last_login DATETIME,
        organization_id TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(organization_id, platform)
    )''')
    conn.execute(
        '''INSERT INTO accounts
               (id, platform, username, password_encrypted, is_logged_in,
                last_login, organization_id, created_at)
           SELECT id, platform, username, password_encrypted, is_logged_in,
                  last_login, COALESCE(organization_id, ?), created_at
           FROM accounts_pre_org_unique''',
        (DEFAULT_ORGANIZATION_ID,)
    )
    conn.execute("DROP TABLE accounts_pre_org_unique")
    return True


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

        accounts_rebuilt = _rebuild_accounts_unique_per_org(conn)

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
        "accounts_rebuilt": accounts_rebuilt,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_migrations(), indent=2))
