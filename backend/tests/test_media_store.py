"""Media ingest — type sniffing, size caps, dedup, storage layout, inventory.

Run:  cd backend && python3 tests/test_media_store.py

Dependency-free (no pytest). Redirects the store at a temp directory and a
copy of the database, so the real uploads/ and socialflow.db are never touched.
"""

import hashlib
import io
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

SCRATCH = Path(tempfile.mkdtemp(prefix="mediastore-"))

import asset_inventory as inv  # noqa: E402

if (BACKEND / "socialflow.db").exists():
    shutil.copy(BACKEND / "socialflow.db", SCRATCH / "socialflow.db")
inv.DATABASE_PATH = str(SCRATCH / "socialflow.db")
if not (SCRATCH / "socialflow.db").exists():
    inv.init_asset_tables()
    sys.path.insert(0, str(BACKEND))
    from migrations import run_migrations
    run_migrations(inv.DATABASE_PATH)

import media_store as ms  # noqa: E402

ms.BACKEND_DIR = SCRATCH
ms.UPLOADS_DIR = SCRATCH / "uploads"
ms.THUMBS_DIR = ms.UPLOADS_DIR / "_thumbs"
ms.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


def png_bytes(size=(1080, 1350), colour=(30, 60, 120)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


# ── type is read from the bytes, never from the name ──────────────────────
check("sniff png", ms.sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)[2], "image")
check("sniff jpeg", ms.sniff(b"\xff\xd8\xff\xe0" + b"\x00" * 8)[2], "image")
check("sniff gif", ms.sniff(b"GIF89a" + b"\x00" * 8)[2], "image")
check("sniff webp vs wav (RIFF collision)",
      (ms.sniff(b"RIFF\x00\x00\x00\x00WEBP")[2], ms.sniff(b"RIFF\x00\x00\x00\x00WAVE")[2]),
      ("image", "audio"))
check("sniff mp4 by ftyp brand", ms.sniff(b"\x00\x00\x00\x20ftypisom")[2], "video")
check("sniff m4a is audio not video", ms.sniff(b"\x00\x00\x00\x20ftypM4A ")[2], "audio")
check("sniff mov", ms.sniff(b"\x00\x00\x00\x20ftypqt  ")[1], ".mov")
check("sniff mp3", ms.sniff(b"ID3\x04\x00\x00\x00\x00\x00\x00")[2], "audio")
check("sniff unknown -> None", ms.sniff(b"#!/bin/sh\nrm -rf /"), None)

# ── happy path ────────────────────────────────────────────────────────────
data = png_bytes()
r1 = ms.store_bytes(data, declared_name="cover photo.png", title="Cover")
check("png stored as image", r1["asset_type"], "image")
check("  sha256 matches content", r1["sha256"], hashlib.sha256(data).hexdigest())
check("  dimensions probed", (r1["width"], r1["height"]), (1080, 1350))
check("  laid out under yyyy/mm/image", "/image/" in r1["path"].replace("\\", "/"), True)
check("  filename is the hash", Path(r1["path"]).stem, r1["sha256"])
check("  thumbnail produced", bool(r1["thumb_url"]), True)
check("  thumbnail file exists", (SCRATCH / "uploads" / "_thumbs" / f"{r1['sha256']}.jpg").is_file(), True)
check("  url is servable form", r1["url"].startswith("/uploads/"), True)

# ── deduplication ─────────────────────────────────────────────────────────
r2 = ms.store_bytes(data, declared_name="a completely different name.png")
check("identical bytes -> deduped", r2["deduped"], True)
check("  same asset id", r2["asset_id"], r1["asset_id"])
check("  only one file on disk", len(list((SCRATCH / "uploads").rglob("*.png"))), 1)
check("  only one inventory row", len(inv.list_assets(limit=100, origin="uploaded")), 1)

r3 = ms.store_bytes(png_bytes(colour=(200, 40, 90)), declared_name="other.png")
check("different bytes -> new asset", r3["deduped"], False)
check("  distinct asset id", r3["asset_id"] != r1["asset_id"], True)

# ── refusals ──────────────────────────────────────────────────────────────
def refuses(name, payload, expect_in=None, status=None):
    try:
        ms.store_bytes(payload, declared_name=name)
        check(f"refuse {name}", "stored", "rejected")
    except ms.MediaRejected as e:
        if expect_in:
            check(f"refuse {name}", expect_in in e.reason, True)
        if status:
            check(f"  {name} status {status}", e.status_code, status)


refuses("script.png", b"#!/bin/sh\nrm -rf /\n", expect_in="Unsupported")
refuses("empty.png", b"", expect_in="Empty")
refuses("fake.png", b"\x89PNG\r\n\x1a\n" + b"garbage" * 40, expect_in="cannot be decoded")

_cap = ms.MAX_BYTES["image"]
ms.MAX_BYTES["image"] = 1024
refuses("huge.png", png_bytes() + b"\x00" * 8192, expect_in="exceeds", status=413)
ms.MAX_BYTES["image"] = _cap

# an undecodable image must not be left behind on disk
check("rejected file leaves no orphan",
      any("garbage" in p.name for p in (SCRATCH / "uploads").rglob("*")), False)

# ── a hostile filename cannot steer the path ──────────────────────────────
r4 = ms.store_bytes(png_bytes(colour=(1, 2, 3)), declared_name="../../../../etc/passwd")
stored = (SCRATCH / r4["path"]).resolve()
check("traversal filename ignored", str(stored).startswith(str((SCRATCH / "uploads").resolve())), True)
check("  original name kept only as metadata",
      inv.get_asset(r4["asset_id"])["metadata"].count("etc/passwd") >= 1, True)

# ── inventory columns are populated ───────────────────────────────────────
row = inv.get_asset(r1["asset_id"])
check("origin recorded", row["origin"], "uploaded")
check("provider recorded", row["provider"], "user")
check("sha256 recorded", row["sha256"], r1["sha256"])
check("size recorded", row["file_size"], len(data))
check("thumb path recorded", bool(row["thumb_path"]), True)
check("lookup by hash works", inv.get_asset_by_sha256(r1["sha256"])["asset_id"], r1["asset_id"])

# ── archive is soft: hidden from listing, row and file both survive ───────
inv.archive_asset(r3["asset_id"])
listed = [a["asset_id"] for a in inv.list_assets(limit=100)]
check("archived hidden", r3["asset_id"] in listed, False)
check("  row survives", inv.get_asset(r3["asset_id"])["state"], "archived")
check("  file survives", (SCRATCH / r3["path"]).is_file(), True)
check("  visible when asked", r3["asset_id"] in
      [a["asset_id"] for a in inv.list_assets(limit=100, include_archived=True)], True)

# ── editing metadata ──────────────────────────────────────────────────────
inv.update_asset(r1["asset_id"], title="Renamed", tags=["cover", "q3"], campaign="launch")
row = inv.get_asset(r1["asset_id"])
check("title updated", row["title"], "Renamed")
check("campaign updated", row["campaign"], "launch")
check("tag filter matches whole token", len(inv.list_assets(tag="cover", limit=50)), 1)
check("tag filter does not substring-match", len(inv.list_assets(tag="cov", limit=50)), 0)
check("text search matches title", len(inv.list_assets(q="Renam", limit=50)), 1)

# ── nothing pre-existing was disturbed ────────────────────────────────────
if (BACKEND / "socialflow.db").exists():
    live = sqlite3.connect(BACKEND / "socialflow.db").execute(
        "select count(*) from posts").fetchone()[0]
    copy = sqlite3.connect(inv.DATABASE_PATH).execute(
        "select count(*) from posts").fetchone()[0]
    check("posts untouched by ingest", copy, live)

print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 62)
shutil.rmtree(SCRATCH, ignore_errors=True)
raise SystemExit(1 if FAIL else 0)
