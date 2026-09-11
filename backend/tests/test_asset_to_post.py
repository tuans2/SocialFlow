"""Assets becoming posts, and the carousel slide list that survives dedup.

Run:  cd backend && python3 tests/test_asset_to_post.py

Redirects every module's DATABASE_PATH and the media store at a temp copy, so
the real database and uploads/ are untouched.
"""

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

PASS, FAIL, SKIP = [], [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


try:
    from PIL import Image
except ImportError:
    print("SKIP: Pillow not installed")
    raise SystemExit(0)

SCRATCH = Path(tempfile.mkdtemp(prefix="topost-"))
DB = SCRATCH / "socialflow.db"

import asset_inventory as inv  # noqa: E402

inv.DATABASE_PATH = str(DB)
inv.init_asset_tables()

from migrations import run_migrations  # noqa: E402

run_migrations(str(DB))
sqlite3.connect(DB).executescript(
    "CREATE TABLE IF NOT EXISTS posts ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, content_type TEXT,"
    " content TEXT, media_paths TEXT, scheduled_time DATETIME, status TEXT,"
    " created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
)

import media_store as ms  # noqa: E402

ms.BACKEND_DIR = SCRATCH
ms.UPLOADS_DIR = SCRATCH / "uploads"
ms.THUMBS_DIR = ms.UPLOADS_DIR / "_thumbs"
ms.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

import media_utils as mu  # noqa: E402

mu.BACKEND_DIR = SCRATCH
mu.UPLOADS_DIR = SCRATCH / "uploads"

import assets_routes as ar  # noqa: E402

# Đợt 3 / E4: kho asset nay theo tổ chức. Bộ test dùng một mã cố định — nó chỉ
# cần một giá trị NHẤT QUÁN để ghi vào rồi đọc lại, không cần tổ chức thật.
TO_CHUC = "to-chuc-thu-nghiem"

ar.DATABASE_PATH = str(DB)


def png(colour):
    import io
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), colour).save(buf, "PNG")
    return buf.getvalue()


def make_carousel(colours, topic):
    """Mimic /api/generate-carousel: a parent asset plus generated slides."""
    parent = inv.register_asset(asset_type="carousel", source="carousel-maker",
                                title=topic, origin="generated", organization_id=TO_CHUC)
    slides = []
    for c in colours:
        slides.append(ms.store_bytes(png(c), declared_name="slide.png",
                                     origin="generated", provider="test",
                                     parent_asset_id=parent["asset_id"], organization_id=TO_CHUC))
    inv.update_asset(parent["asset_id"],
                     metadata={"slide_asset_ids": [s["asset_id"] for s in slides]}, organization_id=TO_CHUC)
    return parent["asset_id"], slides


# ── the dedup case ────────────────────────────────────────────────────────
# Two carousels whose images come out byte-identical. The media store reuses
# the existing assets, whose parent_asset_id still points at the FIRST
# carousel — so the second one has no children at all. Before the carousel
# kept its own ordered list, its slides silently vanished while the API still
# reported three of them.
colours = [(10, 20, 30), (40, 50, 60), (70, 80, 90)]
c1, s1 = make_carousel(colours, "first")
c2, s2 = make_carousel(colours, "second")

check("two distinct carousels", c1 != c2, True)
check("slides deduped to 3 assets, not 6",
      len({s["asset_id"] for s in s1 + s2}), 3)

children = sqlite3.connect(DB).execute(
    "SELECT count(*) FROM assets WHERE parent_asset_id = ?", (c2,)).fetchone()[0]
check("second carousel has no parent-linked children", children, 0)

check("...but it still knows its 3 slides", len(ar._carousel_slide_paths(c2, organization_id=TO_CHUC)), 3)
check("first carousel too", len(ar._carousel_slide_paths(c1, organization_id=TO_CHUC)), 3)
check("both resolve to the same files",
      ar._carousel_slide_paths(c1, organization_id=TO_CHUC), ar._carousel_slide_paths(c2, organization_id=TO_CHUC))

# a carousel from before the list existed still works via the back-pointer
legacy = inv.register_asset(asset_type="carousel", source="old", title="legacy", organization_id=TO_CHUC)
ms.store_bytes(png((1, 2, 3)), declared_name="l1.png", origin="generated",
               parent_asset_id=legacy["asset_id"], organization_id=TO_CHUC)
ms.store_bytes(png((4, 5, 6)), declared_name="l2.png", origin="generated",
               parent_asset_id=legacy["asset_id"], organization_id=TO_CHUC)
check("legacy carousel falls back to parent_asset_id",
      len(ar._carousel_slide_paths(legacy["asset_id"], organization_id=TO_CHUC)), 2)

# Đợt 3 / E4: danh sách slide cũng phải dừng ở ranh giới tổ chức. Không có
# phép thử này thì một carousel vẫn trả đủ ảnh cho người của tổ chức khác.
check("tổ chức khác KHÔNG lấy được slide của carousel này",
      ar._carousel_slide_paths(c1, organization_id="to-chuc-khac"), [])
check("  kể cả carousel kiểu cũ",
      ar._carousel_slide_paths(legacy["asset_id"], organization_id="to-chuc-khac"), [])

# ── metadata merge must not clobber ───────────────────────────────────────
inv.update_asset(c1, metadata={"caption": "hello"}, organization_id=TO_CHUC)
meta = json.loads(inv.get_asset(c1, organization_id=TO_CHUC)["metadata"])
check("metadata merge keeps slide_asset_ids", len(meta.get("slide_asset_ids", [])), 3)
check("  and adds the new key", meta.get("caption"), "hello")

# ── the gate a saved post has to pass ─────────────────────────────────────
video = ms.store_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 900,
                       declared_name="reel.mp4", origin="generated", provider="slideshow", organization_id=TO_CHUC)
check("video stored", video["asset_type"], "video")

ok, err, media = mu.validate_publish_media("instagram", "reel", [video["path"]])
check("reel with its video passes", ok, True)
ok, err, _ = mu.validate_publish_media("instagram", "reel", None)
check("reel without video is blocked", ok, False)

slide_paths = ar._carousel_slide_paths(c2, organization_id=TO_CHUC)
ok, err, media = mu.validate_publish_media("instagram", "carousel", slide_paths)
check("carousel with 3 slides passes", ok, True)
check("  all three resolve", len(media), 3)

# ── content type mapping ──────────────────────────────────────────────────
check("video -> reel", ar.CONTENT_TYPE_FOR["video"], "reel")
check("image -> post", ar.CONTENT_TYPE_FOR["image"], "post")
check("carousel -> carousel", ar.CONTENT_TYPE_FOR["carousel"], "carousel")

print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 62)
shutil.rmtree(SCRATCH, ignore_errors=True)
raise SystemExit(1 if FAIL else 0)
