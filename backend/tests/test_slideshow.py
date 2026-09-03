"""Slideshow provider — the ffmpeg command it builds, and a real render.

Run:  cd backend && python3 tests/test_slideshow.py

The command-shape assertions run anywhere. The render is skipped with a
notice when ffmpeg is absent rather than failing the suite.
"""

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from video_providers.base import JobStatus, ProviderError  # noqa: E402
from video_providers.slideshow import FRAME, SlideshowProvider  # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


p = SlideshowProvider()

# ── free is 0.0, never "unknown" ──────────────────────────────────────────
check("free provider costs 0.0", p.estimated_cost(15), 0.0)
check("  and is not reported as unknown", p.estimated_cost(15) is None, False)

# ── the command ───────────────────────────────────────────────────────────
imgs = ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"]
cmd = p._build_command(imgs, Path("/tmp/out.mp4"), FRAME["9:16"], 12, None, "fade")
joined = " ".join(cmd)

check("one -loop input per image", cmd.count("-loop"), 3)  # exact argv tokens
check("targets 1080x1920", "scale=1080:1920" in joined, True)
check("letterboxes rather than crops",
      "force_original_aspect_ratio=decrease" in joined, True)
check("two crossfades for three images", joined.count("xfade"), 2)
check("ends on the [out] label", "[out]" in cmd, True)
check("h264 + yuv420p for social players",
      "libx264" in joined and "yuv420p" in joined, True)
check("faststart so it plays while downloading", "+faststart" in joined, True)
check("no audio mapping without audio", "-c:a" in cmd, False)
check("  three inputs, no fourth", cmd.count("-i"), 3)

# crossfades overlap, so slide length must exceed total/n to hit the target
per = float(cmd[cmd.index("-t") + 1])
n = 3
overlap = 0.5 * (n - 1)
check("slide length compensates for overlap",
      round(per * n - overlap, 1), 12.0)

one = p._build_command(["/tmp/a.png"], Path("/tmp/o.mp4"), FRAME["9:16"], 5, None, "fade")
check("single image needs no crossfade", " ".join(one).count("xfade"), 0)

with_audio = p._build_command(imgs, Path("/tmp/o.mp4"), FRAME["9:16"], 12,
                              "/tmp/music.m4a", "fade")
ja = " ".join(with_audio)
check("audio input added", with_audio.count("-i"), 4)  # argv tokens, not substrings
check("  audio stream mapped", "-map 3:a" in ja, True)
check("  encoded as aac", "aac" in ja, True)
check("  -shortest so a long track does not extend the video", "-shortest" in ja, True)

for ar, size in FRAME.items():
    c = " ".join(p._build_command(imgs, Path("/tmp/o.mp4"), size, 9, None, "fade"))
    check(f"{ar} scales to {size[0]}x{size[1]}", f"scale={size[0]}:{size[1]}" in c, True)

# ── refusals ──────────────────────────────────────────────────────────────
try:
    run(p.submit("x", image_paths=[]))
    check("no images refused", "accepted", "refused")
except ProviderError as e:
    check("no images refused", "carousel_asset_id" in e.reason, True)
    check("  as a 400", e.status_code, 400)

try:
    run(p.submit("x", image_paths=["/tmp/definitely-not-here-9z.png"]))
    check("missing file refused", "accepted", "refused")
except ProviderError as e:
    check("missing file refused", "not found" in e.reason, True)

# ── a real render ─────────────────────────────────────────────────────────
if not shutil.which("ffmpeg"):
    SKIP.append("render (ffmpeg not installed)")
else:
    try:
        from PIL import Image
    except ImportError:
        SKIP.append("render (Pillow not installed)")
        Image = None

    if Image:
        tmp = Path(tempfile.mkdtemp(prefix="slideshow-"))
        paths = []
        for i, colour in enumerate([(200, 60, 90), (40, 150, 180), (240, 180, 60)]):
            f = tmp / f"s{i}.png"
            Image.new("RGB", (1024, 1024), colour).save(f)
            paths.append(str(f))

        job = run(p.submit("test", duration=9, aspect_ratio="9:16", image_paths=paths))
        check("render succeeds", job.status, JobStatus.SUCCEEDED)
        check("  reports slide count", job.raw["slides"], 3)

        out = Path(job.video_url)
        check("  file produced", out.is_file(), True)

        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(out)],
            capture_output=True, text=True).stdout)
        v = [s for s in probe["streams"] if s["codec_type"] == "video"][0]
        duration = float(probe["format"]["duration"])
        check("  vertical 1080x1920", (v["width"], v["height"]), (1080, 1920))
        check("  h264", v["codec_name"], "h264")
        check(f"  duration near 9s (got {duration:.1f})", 8.0 <= duration <= 10.0, True)

        polled = run(p.poll(job.provider_job_id))
        check("poll finds the finished render", polled.status, JobStatus.SUCCEEDED)
        data = run(p.download(job))
        check("download returns the bytes", len(data), out.stat().st_size)
        check("  it is really an mp4", data[4:8], b"ftyp")

        out.unlink(missing_ok=True)
        gone = run(p.poll(job.provider_job_id))
        check("poll after cleanup -> failed, not a crash", gone.status, JobStatus.FAILED)

        shutil.rmtree(tmp, ignore_errors=True)

print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
for s in SKIP:
    print(f"SKIP  {s}")
print(f"{len(PASS)} passed, {len(FAIL)} failed, {len(SKIP)} skipped")
print("=" * 62)
raise SystemExit(1 if FAIL else 0)
