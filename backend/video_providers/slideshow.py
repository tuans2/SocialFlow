"""
Slideshow — build a vertical reel locally from images already in the library.

The only provider here that needs no account, no key and no billing. It exists
because a "5 tips for…" reel is a sequence of captioned frames, and rendering
that from the carousel slides the app already generated is both free and, for
that format, closer to what people actually post than an AI-generated clip.

It also keeps the Reel Maker usable while a paid provider is unavailable —
which is exactly the situation this was written in.

Rendering is a single ffmpeg pass: each image is fitted to the target frame,
crossfaded into the next, and muxed with an optional audio track. Deliberately
NOT using the zoompan filter for a Ken Burns effect — measured at over two
minutes for a nine-second clip, versus about three seconds without it.
"""

import asyncio
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional

from .base import JobStatus, ProviderError, VideoJob, VideoProvider

BACKEND_DIR = Path(__file__).resolve().parent.parent
RENDER_DIR = BACKEND_DIR / ".render"

FRAME = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}

FPS = 30
TRANSITION = 0.5          # seconds of crossfade between slides
MIN_SLIDE_SECONDS = 1.0
RENDER_TIMEOUT = 300
TEMP_TTL_SECONDS = 3600


class SlideshowProvider(VideoProvider):
    name = "slideshow"
    label = "Slideshow (local ffmpeg)"
    supports_image_to_video = True
    aspect_ratios = ("9:16", "16:9", "1:1")
    durations = tuple(range(3, 121))
    default_duration = 15
    cost_per_second = 0.0        # local render: no API charge

    def is_configured(self) -> bool:
        return shutil.which("ffmpeg") is not None

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def submit(self, prompt: str, *, duration: int = None,
                     aspect_ratio: str = "9:16", image_bytes: bytes = None,
                     image_mime: str = "image/png", image_paths: List[str] = None,
                     audio_path: str = None, transition: str = "fade",
                     **options) -> VideoJob:
        if not self.is_configured():
            raise ProviderError(
                "ffmpeg is not installed. On macOS: brew install ffmpeg", 400
            )

        images = [p for p in (image_paths or []) if p]
        if not images:
            raise ProviderError(
                "A slideshow needs images. Pass image_paths, or carousel_asset_id "
                "to use the slides of a carousel already in the library.",
                400,
            )

        missing = [p for p in images if not Path(p).is_file()]
        if missing:
            raise ProviderError(
                f"{len(missing)} image file(s) not found: {', '.join(missing[:3])}", 400
            )

        total = self.normalise_duration(duration)
        frame = FRAME.get(self.normalise_aspect(aspect_ratio), FRAME["9:16"])

        RENDER_DIR.mkdir(exist_ok=True)
        self._sweep_old_renders()

        job_id = uuid.uuid4().hex
        out = RENDER_DIR / f"{job_id}.mp4"

        args = self._build_command(images, out, frame, total, audio_path, transition)

        # ffmpeg is blocking; keep it off the event loop.
        result = await asyncio.to_thread(
            subprocess.run, args, capture_output=True, text=True, timeout=RENDER_TIMEOUT
        )

        if result.returncode != 0 or not out.is_file():
            tail = (result.stderr or "").strip().splitlines()[-4:]
            raise ProviderError("ffmpeg failed: " + " | ".join(tail or ["no output"]))

        return VideoJob(
            provider_job_id=job_id,
            provider=self.name,
            status=JobStatus.SUCCEEDED,
            video_url=str(out),
            raw={"duration": total, "aspect_ratio": self.normalise_aspect(aspect_ratio),
                 "slides": len(images), "has_audio": bool(audio_path),
                 "model": "ffmpeg"},
        )

    async def poll(self, provider_job_id: str) -> VideoJob:
        """Rendering finished inside submit(); this reports the file's fate.

        Reading the filesystem rather than an in-memory table means a job
        survives the --reload restarts this app runs with in development.
        """
        out = RENDER_DIR / f"{provider_job_id}.mp4"
        if out.is_file():
            return VideoJob(provider_job_id=provider_job_id, provider=self.name,
                            status=JobStatus.SUCCEEDED, video_url=str(out))
        return VideoJob(
            provider_job_id=provider_job_id, provider=self.name,
            status=JobStatus.FAILED,
            error="Rendered file is gone — it may have been cleaned up already.",
        )

    async def download(self, job: VideoJob) -> bytes:
        path = Path(job.video_url or "")
        if not path.is_file():
            raise ProviderError("Rendered file not found.")
        return path.read_bytes()

    # ── the ffmpeg command ────────────────────────────────────────────────

    def _build_command(self, images: List[str], out: Path, frame, total: int,
                       audio_path: Optional[str], transition: str) -> list:
        w, h = frame
        n = len(images)

        # With crossfades the clip is shorter than the sum of its slides: each
        # transition overlaps two of them. Solve for the slide length that
        # lands on the requested total.
        overlap = TRANSITION * (n - 1)
        per = max(MIN_SLIDE_SECONDS + TRANSITION, (total + overlap) / n)

        args = ["ffmpeg", "-y", "-loglevel", "error"]
        for img in images:
            args += ["-loop", "1", "-t", f"{per:.3f}", "-i", str(img)]
        if audio_path:
            args += ["-i", str(audio_path)]

        chains = []
        for i in range(n):
            chains.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},"
                f"format=yuv420p[v{i}]"
            )

        if n == 1:
            final = "[v0]"
        else:
            prev = "[v0]"
            for i in range(1, n):
                offset = per * i - TRANSITION * i
                label = "[out]" if i == n - 1 else f"[x{i}]"
                chains.append(
                    f"{prev}[v{i}]xfade=transition={transition}:"
                    f"duration={TRANSITION}:offset={offset:.3f}{label}"
                )
                prev = label
            final = "[out]"

        args += ["-filter_complex", ";".join(chains), "-map", final]
        if audio_path:
            args += ["-map", f"{n}:a", "-c:a", "aac", "-shortest"]
        args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                 "-r", str(FPS), "-movflags", "+faststart", str(out)]
        return args

    @staticmethod
    def _sweep_old_renders():
        """Temp renders are copied into the library; the originals are litter."""
        cutoff = time.time() - TEMP_TTL_SECONDS
        try:
            for f in RENDER_DIR.glob("*.mp4"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except OSError:
            pass
