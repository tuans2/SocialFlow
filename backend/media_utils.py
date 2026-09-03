"""
Media path helpers + publish-time image quality gate.

Single source of truth for two things:
  1. Turning whatever is stored in `posts.media_paths` (browser URL like
     "/uploads/foo.png", relative "uploads/foo.png", or an absolute filesystem
     path) into a real filesystem path Playwright can hand to an <input type=file>.
  2. Enforcing the platform media policy BEFORE any browser automation starts:
     single posts need an image, carousels need at least two real slides.

No new image engine, no new storage, no new routes — this only reads what the
existing pipeline already produces in backend/uploads/.
"""

import json
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

# Resolved from this file's location — never hardcode a user's absolute path.
BACKEND_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BACKEND_DIR / "uploads"

# Platform image policy (section 4 of the spec).
#   REQUIRED : facebook, instagram
#   OPTIONAL : linkedin, twitter/x, zalo, tiktok, ...
IMAGE_REQUIRED_PLATFORMS = {"facebook", "instagram"}

# Three DIFFERENT questions, deliberately kept apart:
#
#   AUTOGEN_IMAGE_CONTENT_TYPES -> "should /api/generate auto-create one post
#       image for this?"  Only a single "post" qualifies. carousel has its own
#       slide pipeline, reel has the video pipeline, thread stays text-only.
#
#   PLATFORM_MEDIA_CONTENT_TYPES -> content whose media requirement depends on
#       WHERE it goes. A plain post needs a picture on Facebook and Instagram
#       and nowhere else.
#
#   INHERENT_MEDIA_CONTENT_TYPES -> content that IS its media, wherever it
#       goes. A reel is a video and a carousel is a set of images by
#       definition; publishing either without the files posts a bare caption
#       to a surface built for the media. Platform does not enter into it.
AUTOGEN_IMAGE_CONTENT_TYPES = {"post"}
PLATFORM_MEDIA_CONTENT_TYPES = {"post"}
INHERENT_MEDIA_CONTENT_TYPES = {"carousel", "reel"}

MEDIA_REQUIRED_CONTENT_TYPES = PLATFORM_MEDIA_CONTENT_TYPES | INHERENT_MEDIA_CONTENT_TYPES

# Backwards-compatible alias (older imports referred to this name).
IMAGE_REQUIRED_CONTENT_TYPES = AUTOGEN_IMAGE_CONTENT_TYPES

# A carousel with one slide is not a carousel. Minimum media count per
# content type; anything absent here needs at least one file — a reel is one
# video, so it takes the default.
MIN_MEDIA_COUNT = {"carousel": 2}

PLATFORM_LABEL = {"facebook": "Facebook", "instagram": "Instagram"}


def parse_media_paths(raw: Any) -> List[str]:
    """Normalize a DB value / request value into a list of path strings."""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(p) for p in raw if p]
    if isinstance(raw, (str, bytes)):
        text = raw.decode() if isinstance(raw, bytes) else raw
        text = text.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                loaded = json.loads(text)
                if isinstance(loaded, list):
                    return [str(p) for p in loaded if p]
            except (ValueError, TypeError):
                return []
            return []
        return [text]
    return []


def resolve_media_path(path: Any) -> Optional[str]:
    """Resolve one stored media reference to an EXISTING absolute file path.

    Accepts:
        "/uploads/foo.png"            (browser URL served by app.mount("/uploads"))
        "uploads/foo.png"             (relative to backend/)
        "/Users/.../backend/uploads/foo.png"  (absolute filesystem path)
        "foo.png"                     (bare filename inside uploads/)

    Returns the absolute path as a string, or None if no such file exists.
    """
    if not path:
        return None

    raw = str(path).strip()
    if not raw:
        return None

    candidates: List[Path] = []

    if raw.startswith("/uploads/"):
        candidates.append(UPLOADS_DIR / raw[len("/uploads/"):])
    elif raw.startswith("uploads/"):
        candidates.append(BACKEND_DIR / raw)
    else:
        p = Path(raw)
        if p.is_absolute():
            candidates.append(p)
        else:
            candidates.append(BACKEND_DIR / p)
            candidates.append(Path.cwd() / p)

    # Last-resort fallback: same filename inside uploads/ (keeps rows portable
    # if the repository was moved to another machine).
    candidates.append(UPLOADS_DIR / Path(raw).name)

    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate.resolve())
        except OSError:
            continue
    return None


def resolve_media_paths(paths: Any) -> Tuple[List[str], List[str]]:
    """Resolve many references.

    Returns (existing_absolute_paths, missing_original_references).
    """
    existing: List[str] = []
    missing: List[str] = []
    for item in parse_media_paths(paths):
        resolved = resolve_media_path(item)
        if resolved:
            existing.append(resolved)
        else:
            missing.append(item)
    return existing, missing


def to_public_url(path: Any) -> Optional[str]:
    """Filesystem path -> browser URL served by the existing /uploads mount.

    Preserves any sub-directory under uploads/. Files are now stored as
    uploads/YYYY/MM/<category>/<sha>.<ext>, so flattening to the bare filename
    (as this used to do) would produce a URL that 404s.
    """
    if not path:
        return None
    raw = str(path).replace("\\", "/")
    if raw.startswith("/uploads/"):
        return raw

    try:
        rel = Path(raw).resolve().relative_to(UPLOADS_DIR)
        return f"/uploads/{rel.as_posix()}"
    except (ValueError, OSError):
        pass

    marker = "uploads/"
    idx = raw.rfind(marker)
    if idx != -1:
        return f"/uploads/{raw[idx + len(marker):]}"

    return f"/uploads/{Path(raw).name}"


def requires_image(platform: str, content_type: str) -> bool:
    """Should the generator auto-create a post image for this combination?

    Used by /api/generate. NOT the publish gate — see requires_media().
    """
    return (
        (platform or "").lower() in IMAGE_REQUIRED_PLATFORMS
        and (content_type or "").lower() in AUTOGEN_IMAGE_CONTENT_TYPES
    )


def requires_media(platform: str, content_type: str) -> bool:
    """Should publishing be blocked when the media is missing?

    Wider than requires_image() in two ways: a carousel is gated even though
    nothing auto-generates its slides, and reels/carousels are gated on EVERY
    platform rather than only the two that require pictures on plain posts —
    a reel without a video is broken on TikTok exactly as it is on Instagram.
    """
    kind = (content_type or "").lower()
    if kind in INHERENT_MEDIA_CONTENT_TYPES:
        return True
    return (
        (platform or "").lower() in IMAGE_REQUIRED_PLATFORMS
        and kind in PLATFORM_MEDIA_CONTENT_TYPES
    )


def minimum_media_count(content_type: str) -> int:
    return MIN_MEDIA_COUNT.get((content_type or "").lower(), 1)


def validate_publish_media(
    platform: str,
    content_type: str,
    media_paths: Any,
) -> Tuple[bool, Optional[str], List[str]]:
    """Publish quality gate.

    Returns (ok, error_message, resolved_absolute_paths).

    Facebook/Instagram single posts are BLOCKED when there is no media entry at
    all, or when none of the referenced files actually exist on disk. Every
    other platform / content type passes through unchanged, with its media
    simply resolved to usable absolute paths.
    """
    existing, missing = resolve_media_paths(media_paths)

    if not requires_media(platform, content_type):
        return True, None, existing

    label = PLATFORM_LABEL.get((platform or "").lower(), (platform or "").title())
    kind = (content_type or "").lower() or "post"
    minimum = minimum_media_count(kind)
    if minimum > 1:
        need = f"at least {minimum} slides"
    elif kind == "reel":
        need = "a video"
    else:
        need = "at least one image"

    if not parse_media_paths(media_paths):
        return False, f"{label} {kind}s require {need}.", []

    if not existing:
        return (
            False,
            f"{label} {kind}s require {need}. "
            f"Media file not found: {', '.join(missing) or 'unknown path'}",
            [],
        )

    if len(existing) < minimum:
        detail = f"only {len(existing)} usable file(s) found"
        if missing:
            detail += f"; missing: {', '.join(missing)}"
        return False, f"{label} {kind}s require {need} — {detail}.", []

    return True, None, existing
