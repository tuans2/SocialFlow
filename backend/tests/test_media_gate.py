"""Publish media gate — carousel policy + regression guard on existing posts.

Run:  cd backend && python3 tests/test_media_gate.py

Deliberately dependency-free (no pytest) so it runs anywhere the app runs.
Fixtures are written to a temp dir; the real uploads/ and DB are never touched.
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mediagate-"))
(_TMP / "uploads").mkdir()
for _n in ("slide_1.png", "slide_2.png", "slide_3.png", "one.png"):
    (_TMP / "uploads" / _n).write_bytes(b"\x89PNG fake")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import media_utils as m

# Point the resolver at the fixture dir instead of the real uploads/.
m.BACKEND_DIR = _TMP
m.UPLOADS_DIR = _TMP / "uploads"


PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


V = m.validate_publish_media

# The bug we are fixing: post 45 — facebook carousel, media_paths NULL
ok, err, _ = V("facebook", "carousel", None)
check("fb carousel, no media -> BLOCKED", ok, False)
check("  message mentions slides", "slides" in (err or ""), True)

ok, _, _ = V("instagram", "carousel", None)
check("ig carousel, no media -> BLOCKED", ok, False)

ok, _, _ = V("facebook", "carousel", "[]")
check("fb carousel, empty list -> BLOCKED", ok, False)

# Carousel needs >= 2 real slides
ok, err, _ = V("facebook", "carousel", ["uploads/one.png"])
check("fb carousel, 1 slide -> BLOCKED", ok, False)
check("  message says at least 2", "at least 2" in (err or ""), True)

ok, err, media = V("facebook", "carousel",
                   ["uploads/slide_1.png", "uploads/slide_2.png"])
check("fb carousel, 2 slides -> ALLOWED", ok, True)
check("  resolves both to abs paths", len(media) == 2 and all(p.startswith("/") for p in media), True)

ok, _, media = V("instagram", "carousel",
                 '["uploads/slide_1.png","uploads/slide_2.png","uploads/slide_3.png"]')
check("ig carousel, 3 slides as JSON -> ALLOWED", ok, True)
check("  resolves all three", len(media), 3)

# Ghost files: rows point at slides that no longer exist
ok, err, _ = V("facebook", "carousel",
               ["uploads/slide_1.png", "uploads/gone.png"])
check("fb carousel, 1 real + 1 missing -> BLOCKED", ok, False)
check("  message names the missing file", "gone.png" in (err or ""), True)

# No regression: single-post policy unchanged
ok, _, _ = V("facebook", "post", None)
check("fb post, no image -> BLOCKED (unchanged)", ok, False)
ok, _, _ = V("facebook", "post", ["uploads/one.png"])
check("fb post, 1 image -> ALLOWED (unchanged)", ok, True)
ok, _, _ = V("instagram", "post", ["uploads/one.png"])
check("ig post, 1 image -> ALLOWED (unchanged)", ok, True)

# Ungated combinations still pass through.
# carousel (on non-FB/IG platforms) and reel used to be asserted here as
# allowed. They were moved out deliberately when the policy changed from
# "which platform is this" to "what is this content" — a carousel is images
# and a reel is video wherever they are posted. Plain posts stay
# platform-dependent, which is what this block still guards.
for plat in ["linkedin", "twitter", "x", "zalo", "tiktok"]:
    ok, _, _ = V(plat, "post", None)
    check(f"{plat} post, no image -> ALLOWED (unchanged)", ok, True)

for ct in ["thread", "ai-news", "repo-promo"]:
    ok, _, _ = V("facebook", ct, None)
    check(f"fb {ct}, no media -> ALLOWED (unchanged)", ok, True)

# /api/generate must NOT start auto-generating images for carousels
check("requires_image(fb, post) still True", m.requires_image("facebook", "post"), True)
check("requires_image(fb, carousel) still False", m.requires_image("facebook", "carousel"), False)
check("requires_image(fb, reel) still False", m.requires_image("facebook", "reel"), False)
check("requires_media(fb, carousel) now True", m.requires_media("facebook", "carousel"), True)
check("requires_media(linkedin, carousel) True — carousels are images anywhere",
      m.requires_media("linkedin", "carousel"), True)
check("requires_media(linkedin, post) False — plain posts stay platform-dependent",
      m.requires_media("linkedin", "post"), False)

# Backwards-compat alias kept
check("IMAGE_REQUIRED_CONTENT_TYPES alias intact", m.IMAGE_REQUIRED_CONTENT_TYPES, {"post"})

# Case-insensitivity
ok, _, _ = V("Facebook", "Carousel", None)
check("case-insensitive platform/type -> BLOCKED", ok, False)

# ── reels are video by nature, on every platform ──────────────────────────
# A plain post needs a picture only where the platform demands one. A reel
# needs its video everywhere: publishing one without the file posts a bare
# caption to a surface built for video, on TikTok exactly as on Instagram.
for plat in ["instagram", "facebook", "tiktok", "linkedin", "x", "youtube"]:
    ok, err, _ = V(plat, "reel", None)
    check(f"{plat} reel, no video -> BLOCKED", ok, False)
    check(f"  {plat} message says video", "video" in (err or "").lower(), True)

    ok, _, _ = V(plat, "carousel", None)
    check(f"{plat} carousel, no media -> BLOCKED", ok, False)

ok, _, media = V("tiktok", "reel", ["uploads/one.png"])
check("reel with one real file -> ALLOWED", ok, True)
check("  resolves it", len(media), 1)

# a reel needs one file, not two — the carousel minimum must not leak
ok, _, _ = V("instagram", "reel", ["uploads/one.png"])
check("reel does not inherit the 2-slide carousel minimum", ok, True)

ok, err, _ = V("instagram", "reel", ["uploads/gone.mp4"])
check("reel pointing at a missing file -> BLOCKED", ok, False)
check("  names the file", "gone.mp4" in (err or ""), True)

# ── plain posts stay platform-dependent ───────────────────────────────────
for plat in ["tiktok", "linkedin", "x", "zalo"]:
    ok, _, _ = V(plat, "post", None)
    check(f"{plat} post, no image -> still ALLOWED", ok, True)

check("requires_media(tiktok, reel) True", m.requires_media("tiktok", "reel"), True)
check("requires_media(tiktok, post) False", m.requires_media("tiktok", "post"), False)
check("requires_image(fb, reel) still False — no auto image for reels",
      m.requires_image("facebook", "reel"), False)

print("=" * 60)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 60)
raise SystemExit(1 if FAIL else 0)
