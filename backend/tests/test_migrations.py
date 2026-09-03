"""Schema migrations must be idempotent and must not touch existing data.

Run:  cd backend && python3 tests/test_migrations.py

Builds a database in the pre-migration shape (what init_asset_tables creates)
and seeds it, so the test is meaningful whether or not the developer's own
database has already been migrated.
"""

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from migrations import COLUMNS, run_migrations  # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


scratch = Path(tempfile.mkdtemp(prefix="mig-"))
db = scratch / "socialflow.db"

# Pre-migration shape: exactly what init_asset_tables() creates.
import asset_inventory as inv  # noqa: E402
inv.DATABASE_PATH = str(db)
inv.init_asset_tables()

seeded = sqlite3.connect(db)
seeded.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, content TEXT)")
seeded.executemany("INSERT INTO posts (content) VALUES (?)",
                   [("one",), ("two",), ("three",)])
# a row written before the new columns existed
seeded.execute(
    "INSERT INTO assets (asset_id, asset_type, source, title) VALUES (?,?,?,?)",
    ("asset-legacy-1", "image", "carousel", "made before the migration"))
seeded.commit()

pre_cols = {r[1] for r in seeded.execute("PRAGMA table_info(assets)")}
check("starts without the new columns",
      any(c in pre_cols for _, c, _ in COLUMNS), False)
seeded.close()

first = run_migrations(str(db))
check("adds every declared column", len(first["added_columns"]), len(COLUMNS))
check("nothing skipped", first["skipped"], [])

check("second run adds no columns", run_migrations(str(db))["added_columns"], [])
check("third run adds no columns", run_migrations(str(db))["added_columns"], [])

conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
cols = {r[1] for r in conn.execute("PRAGMA table_info(assets)")}
for table, column, _ in COLUMNS:
    check(f"{table}.{column} present", column in cols, True)

idx = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_assets%'")}
check("indexes created", len(idx) >= 5, True)

check("existing posts survive", conn.execute("SELECT count(*) FROM posts").fetchone()[0], 3)
legacy = dict(conn.execute(
    "SELECT * FROM assets WHERE asset_id='asset-legacy-1'").fetchone())
check("pre-migration row survives", legacy["title"], "made before the migration")
check("  new column defaults to 'generated'", legacy["origin"], "generated")
check("  nullable new column is NULL", legacy["sha256"], None)

# register_asset must work against both shapes.
inv.DATABASE_PATH = str(db)
made = inv.register_asset(asset_type="image", source="test", sha256="deadbeef",
                          origin="uploaded", provider="user")
check("register_asset writes new columns",
      inv.get_asset(made["asset_id"])["provider"], "user")
check("  and they are queryable",
      inv.get_asset_by_sha256("deadbeef")["asset_id"], made["asset_id"])

# A database missing the assets table must not raise, just report the skip.
bare = scratch / "bare.db"
sqlite3.connect(bare).execute("CREATE TABLE unrelated (id INTEGER)")
bare_result = run_migrations(str(bare))
check("missing table is skipped, not fatal", len(bare_result["skipped"]), len(COLUMNS))
check("  nothing added to a bare db", bare_result["added_columns"], [])

conn.close()
print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 62)
shutil.rmtree(scratch, ignore_errors=True)
raise SystemExit(1 if FAIL else 0)
