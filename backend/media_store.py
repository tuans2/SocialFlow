"""
Media ingest — one way in for every file SocialFlow stores.

Replaces four ad-hoc upload handlers that each wrote straight into uploads/
with no validation, no size limit, no hash and no inventory row. Everything
that arrives here is:

    sniffed  -> the real type is read from the file's magic bytes, never from
                the Content-Type the client claims
    capped   -> per-category size limit, enforced while streaming so an
                oversized file is never fully buffered in memory
    hashed   -> sha256 computed during the same stream
    deduped  -> an identical file already held is reused, not written twice
    stored   -> uploads/YYYY/MM/<category>/<sha>.<ext>, name derived from the
                hash so a client-supplied filename can never steer the path
    probed   -> dimensions / duration where cheaply available
    thumbed  -> small preview so the library never loads full-size originals
    recorded -> a row in the assets inventory

Files written before this module keep working: they stay where they are and
media_utils.resolve_media_path() still finds them.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from asset_inventory import get_asset_by_sha256, register_asset

BACKEND_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BACKEND_DIR / "uploads"
THUMBS_DIR = UPLOADS_DIR / "_thumbs"

CHUNK = 1024 * 1024  # 1 MiB
THUMB_MAX_EDGE = 320

# Magic-byte signatures. (offset, signature, mime, extension, category)
# Deliberately a whitelist: anything not matched here is refused.
SIGNATURES = [
    (0, b"\x89PNG\r\n\x1a\n", "image/png", ".png", "image"),
    (0, b"\xff\xd8\xff", "image/jpeg", ".jpg", "image"),
    (0, b"GIF87a", "image/gif", ".gif", "image"),
    (0, b"GIF89a", "image/gif", ".gif", "image"),
    (0, b"BM", "image/bmp", ".bmp", "image"),
    (0, b"II\x2a\x00", "image/tiff", ".tiff", "image"),
    (0, b"MM\x00\x2a", "image/tiff", ".tiff", "image"),
    (0, b"ID3", "audio/mpeg", ".mp3", "audio"),
    (0, b"\xff\xfb", "audio/mpeg", ".mp3", "audio"),
    (0, b"\xff\xf3", "audio/mpeg", ".mp3", "audio"),
    (0, b"\xff\xf2", "audio/mpeg", ".mp3", "audio"),
    (0, b"OggS", "audio/ogg", ".ogg", "audio"),
    (0, b"fLaC", "audio/flac", ".flac", "audio"),
    (0, b"\x1a\x45\xdf\xa3", "video/webm", ".webm", "video"),
    (0, b"%PDF-", "application/pdf", ".pdf", "document"),
]

# Caps per category. Video is generous; everything else is not.
MAX_BYTES = {
    "image": 20 * 1024 * 1024,
    "audio": 50 * 1024 * 1024,
    "video": 500 * 1024 * 1024,
    "document": 20 * 1024 * 1024,
}

ASSET_TYPE_BY_CATEGORY = {
    "image": "image",
    "audio": "audio",
    "video": "video",
    "document": "document",
}


class MediaRejected(Exception):
    """The file cannot be stored. `.reason` is safe to show a user."""

    def __init__(self, reason: str, status_code: int = 400):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


# ── type detection ────────────────────────────────────────────────────────

def sniff(head: bytes) -> Optional[Tuple[str, str, str]]:
    """(mime, extension, category) read from the bytes themselves, or None."""
    for offset, sig, mime, ext, category in SIGNATURES:
        if head[offset:offset + len(sig)] == sig:
            return mime, ext, category

    # RIFF containers: WAV and WEBP share a prefix and differ at byte 8.
    if head[:4] == b"RIFF" and len(head) >= 12:
        if head[8:12] == b"WAVE":
            return "audio/wav", ".wav", "audio"
        if head[8:12] == b"WEBP":
            return "image/webp", ".webp", "image"

    # ISO base media (MP4/MOV/M4A): "ftyp" at byte 4, brand at byte 8.
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4", ".m4a", "audio"
        if brand == b"qt  ":
            return "video/quicktime", ".mov", "video"
        return "video/mp4", ".mp4", "video"

    return None


# ── probing ───────────────────────────────────────────────────────────────

def _probe_image(path: Path) -> dict:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()          # refuses a file that only looks like an image
        with Image.open(path) as im:
            w, h = im.size
            return {"width": w, "height": h, "aspect_ratio": _ratio(w, h)}
    except Exception as e:
        raise MediaRejected(f"File claims to be an image but cannot be decoded: {e}")


def _ffprobe(path: Path) -> dict:
    """Duration and dimensions via ffprobe. Absent ffprobe is not an error."""
    if shutil.which("ffprobe") is None:
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return {}
        data = json.loads(out.stdout or "{}")
        info = {}
        duration = (data.get("format") or {}).get("duration")
        if duration:
            info["duration_seconds"] = int(float(duration))
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                w, h = stream.get("width"), stream.get("height")
                if w and h:
                    info.update({"width": w, "height": h, "aspect_ratio": _ratio(w, h)})
                break
        return info
    except Exception:
        return {}


def _ratio(w: int, h: int) -> str:
    from math import gcd
    if not w or not h:
        return ""
    g = gcd(w, h)
    return f"{w // g}:{h // g}"


def _make_thumb(path: Path, sha: str, category: str) -> Optional[str]:
    """Return a repo-relative thumbnail path, or None if one is not possible."""
    if category != "image":
        return None
    try:
        from PIL import Image
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        out = THUMBS_DIR / f"{sha}.jpg"
        if out.exists():
            return _rel(out)
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE))
            im.save(out, "JPEG", quality=82, optimize=True)
        return _rel(out)
    except Exception:
        return None


def _rel(path: Path) -> str:
    """Store paths relative to backend/ so the repo stays portable."""
    try:
        return str(path.resolve().relative_to(BACKEND_DIR))
    except ValueError:
        return str(path)


def public_url(path) -> Optional[str]:
    """Filesystem path -> the URL the existing /uploads mount serves it at."""
    if not path:
        return None
    p = Path(str(path))
    try:
        rel = p.resolve().relative_to(UPLOADS_DIR)
    except (ValueError, OSError):
        raw = str(path).replace("\\", "/")
        marker = "uploads/"
        rel = raw[raw.index(marker) + len(marker):] if marker in raw else Path(raw).name
    return f"/uploads/{rel}"


# ── the ingest itself ─────────────────────────────────────────────────────

class _Spool:
    """A file being streamed to disk: hash, size and sniffed type as we go."""

    def __init__(self):
        fd, name = tempfile.mkstemp(prefix="ingest-", dir=str(BACKEND_DIR))
        self.path = Path(name)
        self._fh = os.fdopen(fd, "wb")
        self._digest = hashlib.sha256()
        self.total = 0
        self.head = b""
        self.detected = None
        self._cap = max(MAX_BYTES.values())

    def write(self, chunk: bytes):
        if not chunk:
            return
        if len(self.head) < 32:
            self.head += chunk[:32 - len(self.head)]
            if len(self.head) >= 12 and self.detected is None:
                self.detected = sniff(self.head)
                if self.detected:
                    self._cap = MAX_BYTES.get(self.detected[2], self._cap)
        self.total += len(chunk)
        if self.total > self._cap:
            limit_mb = self._cap // (1024 * 1024)
            kind = self.detected[2] if self.detected else "file"
            raise MediaRejected(
                f"{kind.capitalize()} exceeds the {limit_mb} MB limit.", 413
            )
        self._digest.update(chunk)
        self._fh.write(chunk)

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass

    def discard(self):
        self.close()
        try:
            self.path.unlink()
        except OSError:
            pass

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


def _finalize(spool: "_Spool", *, declared_name="", origin="uploaded",
              provider="user", title="", description="", campaign="", topic="",
              tags=None, cost_usd=None, parent_asset_id=None,
              extra_metadata=None) -> dict:
    """Validate, place, probe and record a fully-spooled file."""
    spool.close()

    if spool.total == 0:
        raise MediaRejected("Empty file.")

    detected = spool.detected or sniff(spool.head)
    if detected is None:
        raise MediaRejected(
            "Unsupported or unrecognised file type "
            "(accepted: PNG, JPEG, GIF, WebP, BMP, TIFF, MP4, MOV, WebM, "
            "MP3, WAV, OGG, FLAC, M4A, PDF)."
        )

    mime, ext, category = detected
    sha = spool.sha256

    existing = get_asset_by_sha256(sha)
    if existing and _resolve_existing(existing.get("file_path")):
        return {
            "asset_id": existing["asset_id"],
            "asset_type": existing.get("asset_type"),
            "sha256": sha,
            "path": existing.get("file_path"),
            "url": existing.get("file_url") or public_url(existing.get("file_path")),
            "thumb_url": public_url(existing.get("thumb_path")),
            "bytes": existing.get("file_size") or spool.total,
            "mime": mime,
            "deduped": True,
        }

    now = datetime.now()
    dest_dir = UPLOADS_DIR / now.strftime("%Y") / now.strftime("%m") / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    # The name comes from the content hash, never from the client, so a
    # filename like "../../etc/passwd" cannot steer where this lands.
    dest = dest_dir / f"{sha}{ext}"

    if dest.exists():
        spool.discard()
    else:
        shutil.move(str(spool.path), str(dest))
    try:
        os.chmod(dest, 0o644)
    except OSError:
        pass

    meta = {"mime": mime, "original_filename": declared_name or None}
    try:
        if category == "image":
            meta.update(_probe_image(dest))
        elif category in ("video", "audio"):
            meta.update(_ffprobe(dest))
    except MediaRejected:
        # A file that lied about being an image should not be kept.
        if not _shared_with_other_assets(dest):
            try:
                dest.unlink()
            except OSError:
                pass
        raise
    if extra_metadata:
        meta.update(extra_metadata)

    thumb = _make_thumb(dest, sha, category)

    asset = register_asset(
        asset_type=ASSET_TYPE_BY_CATEGORY.get(category, category),
        source=provider or origin,
        title=title or (declared_name or dest.name),
        description=description,
        file_path=_rel(dest),
        file_url=public_url(dest),
        file_size=spool.total,
        duration_seconds=meta.get("duration_seconds"),
        aspect_ratio=meta.get("aspect_ratio"),
        campaign=campaign,
        topic=topic,
        tags=tags or [],
        metadata=meta,
        sha256=sha,
        origin=origin,
        provider=provider,
        cost_usd=cost_usd,
        thumb_path=thumb,
        parent_asset_id=parent_asset_id,
    )

    return {
        "asset_id": asset["asset_id"],
        "asset_type": asset["asset_type"],
        "sha256": sha,
        "path": _rel(dest),
        "url": public_url(dest),
        "thumb_url": public_url(thumb) if thumb else None,
        "bytes": spool.total,
        "mime": mime,
        "width": meta.get("width"),
        "height": meta.get("height"),
        "duration_seconds": meta.get("duration_seconds"),
        "deduped": False,
    }


def _shared_with_other_assets(dest: Path) -> bool:
    """Is this file already referenced by a registered asset?"""
    try:
        from asset_inventory import list_assets
        rel = _rel(dest)
        return any(a.get("file_path") == rel for a in list_assets(limit=100000,
                                                                 include_archived=True))
    except Exception:
        return False


def store_stream(reader, **kwargs) -> dict:
    """Consume an iterator of byte chunks and store it as an asset."""
    spool = _Spool()
    try:
        for chunk in reader:
            spool.write(chunk)
        return _finalize(spool, **kwargs)
    except Exception:
        spool.discard()
        raise


async def store_upload(upload, **kwargs) -> dict:
    """Store a Starlette/FastAPI UploadFile without buffering it in memory."""
    kwargs.setdefault("declared_name", getattr(upload, "filename", "") or "")
    spool = _Spool()
    try:
        while True:
            chunk = await upload.read(CHUNK)
            if not chunk:
                break
            spool.write(chunk)
        return _finalize(spool, **kwargs)
    except Exception:
        spool.discard()
        raise


def _resolve_existing(stored_path) -> Optional[Path]:
    """Does the file a dedup candidate points at still exist?"""
    if not stored_path:
        return None
    p = Path(str(stored_path))
    for candidate in (p, BACKEND_DIR / p):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def store_bytes(data: bytes, **kwargs) -> dict:
    """Convenience wrapper for content already in memory."""
    return store_stream(iter([data]), **kwargs)


def store_path(path, *, move: bool = False, **kwargs) -> dict:
    """Ingest a file already on disk (a generated image, a downloaded video)."""
    src = Path(path)
    if not src.is_file():
        raise MediaRejected(f"No such file: {path}")
    kwargs.setdefault("declared_name", src.name)

    def reader():
        with open(src, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    return
                yield chunk

    result = store_stream(reader(), **kwargs)
    if move and not result.get("deduped"):
        try:
            src.unlink()
        except OSError:
            pass
    return result
