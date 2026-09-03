"""Video provider abstraction + the Veo implementation.

Run:  cd backend && python3 tests/test_video_providers.py

No network: httpx is replaced with a scripted fake, so the request shapes are
asserted against the published Gemini API contract rather than a live call.
"""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from video_providers.base import JobStatus, ProviderError, VideoProvider  # noqa: E402
import video_providers.veo as veomod  # noqa: E402
from video_providers.veo import VeoProvider  # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


OP = "models/veo-3.1-generate-preview/operations/op-1"
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 512


class Resp:
    def __init__(self, code=200, payload=None, content=b""):
        self.status_code = code
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeHttp:
    """Scripted client. `post_result` / `get_results` drive the responses."""

    post_result = Resp(200, {"name": OP})
    get_results = []
    calls = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, timeout=None):
        FakeHttp.calls.append(("POST", url, headers, json))
        return FakeHttp.post_result

    async def get(self, url, headers=None, timeout=None):
        FakeHttp.calls.append(("GET", url, headers, None))
        return FakeHttp.get_results.pop(0) if FakeHttp.get_results else Resp(200, {})


veomod.httpx.AsyncClient = lambda *a, **k: FakeHttp()


def reset(post=None, gets=None):
    FakeHttp.calls = []
    FakeHttp.post_result = post or Resp(200, {"name": OP})
    FakeHttp.get_results = list(gets or [])


# ── the base class contract ───────────────────────────────────────────────
p = VeoProvider(api_key="k")
check("5 snaps up to 6, not down to 4", p.normalise_duration(5), 6)
check("  7 also ties upward to 8", p.normalise_duration(7), 8)
check("  9 snaps down to 8 (nearest wins over longer)", p.normalise_duration(9), 8)
check("  3 snaps up to 4", p.normalise_duration(3), 4)
check("  8 is exact", p.normalise_duration(8), 8)
check("  None -> default", p.normalise_duration(None), 8)
check("  garbage -> default", p.normalise_duration("abc"), 8)
check("aspect passes through when valid", p.normalise_aspect("16:9"), "16:9")
check("  invalid aspect falls back, does not 400 later",
      p.normalise_aspect("1:1"), "9:16")
check("cost is None without a rate", VeoProvider(api_key="k").estimated_cost(8), None)
check("cost computed with a rate",
      VeoProvider(api_key="k", cost_per_second=0.15).estimated_cost(8), 1.2)

# ── configuration is read lazily, so Settings edits take effect ───────────
os.environ.pop("GEMINI_API_KEY", None)
lazy = VeoProvider()
check("unconfigured without a key", lazy.is_configured(), False)
os.environ["GEMINI_API_KEY"] = "set-at-runtime"
check("picks the key up without a restart", lazy.is_configured(), True)

try:
    run(VeoProvider(api_key="").submit("x"))
    check("missing key refused", "submitted", "refused")
except ProviderError as e:
    check("missing key refused", "GEMINI_API_KEY" in e.reason, True)
    check("  as a 400, not a 502", e.status_code, 400)

try:
    run(p.submit("   "))
    check("empty prompt refused", "submitted", "refused")
except ProviderError as e:
    check("empty prompt refused", e.status_code, 400)

# ── submit: request shape matches the documented contract ────────────────
reset()
job = run(p.submit("a cat surfing", duration=8, aspect_ratio="9:16"))
method, url, headers, body = FakeHttp.calls[0]
check("posts to predictLongRunning", url.endswith(":predictLongRunning"), True)
check("  header auth, not a query param", headers["x-goog-api-key"], "k")
check("  prompt sits in instances[0]", body["instances"][0]["prompt"], "a cat surfing")
check("  aspectRatio in parameters", body["parameters"]["aspectRatio"], "9:16")
check("  durationSeconds is a string", body["parameters"]["durationSeconds"], "8")
check("  job starts non-terminal", job.done, False)
check("  operation name captured", job.provider_job_id, OP)

reset()
run(p.submit("x", negative_prompt="blurry, watermark"))
check("negativePrompt forwarded when given",
      FakeHttp.calls[0][3]["parameters"]["negativePrompt"], "blurry, watermark")

reset()
run(p.submit("x", duration=4, resolution="1080p"))
check("1080p forces the max duration",
      FakeHttp.calls[0][3]["parameters"]["durationSeconds"], "8")

# ── image-to-video is inline base64, per the reference ───────────────────
reset()
run(p.submit("x", image_bytes=b"\x89PNG\r\n\x1a\nfake", image_mime="image/png"))
inst = FakeHttp.calls[0][3]["instances"][0]
check("image sent as inlineData", "inlineData" in inst["image"], True)
check("  mime carried", inst["image"]["inlineData"]["mimeType"], "image/png")
check("  base64 round-trips",
      base64.b64decode(inst["image"]["inlineData"]["data"]), b"\x89PNG\r\n\x1a\nfake")

# ── polling ───────────────────────────────────────────────────────────────
reset(gets=[Resp(200, {"name": OP, "done": False})])
check("not done -> running", run(p.poll(OP)).status, JobStatus.RUNNING)

done = {"name": OP, "done": True, "response": {"generateVideoResponse": {
    "generatedSamples": [{"video": {"uri": "https://example.test/v.mp4"}}]}}}
reset(gets=[Resp(200, done)])
j = run(p.poll(OP))
check("done -> succeeded", j.status, JobStatus.SUCCEEDED)
check("  video uri extracted", j.video_url, "https://example.test/v.mp4")

reset(gets=[Resp(200, {"name": OP, "done": True,
                       "error": {"message": "internal failure"}})])
j = run(p.poll(OP))
check("operation error -> failed", j.status, JobStatus.FAILED)
check("  message surfaced", j.error, "internal failure")

reset(gets=[Resp(200, {"name": OP, "done": True, "response": {
    "generateVideoResponse": {"raiMediaFilteredReasons": ["violates policy"]}}})])
j = run(p.poll(OP))
check("safety block -> failed", j.status, JobStatus.FAILED)
check("  says the prompt was declined", "declined" in (j.error or ""), True)

reset(gets=[Resp(200, {"name": OP, "done": True, "response": {}})])
check("done with no sample -> failed", run(p.poll(OP)).status, JobStatus.FAILED)

# ── error translation is human-readable ──────────────────────────────────
reset(post=Resp(403, {"error": {"message": "permission denied"}}))
try:
    run(p.submit("x"))
    check("403 raises", "no", "yes")
except ProviderError as e:
    check("403 explains the key", "rejected the API key" in e.reason, True)
    check("  keeps the status code", e.status_code, 403)

reset(post=Resp(429, {"error": {"message": "quota"}}))
try:
    run(p.submit("x"))
except ProviderError as e:
    check("429 mentions quota", "quota" in e.reason.lower(), True)

# ── download ──────────────────────────────────────────────────────────────
reset(gets=[Resp(200, {}, MP4)])
j.video_url = "https://example.test/v.mp4"
j.status = JobStatus.SUCCEEDED
check("download returns bytes", run(p.download(j)), MP4)
check("  authenticated download", FakeHttp.calls[0][2]["x-goog-api-key"], "k")

reset(gets=[Resp(404, {})])
try:
    run(p.download(j))
    check("expired file raises", "no", "yes")
except ProviderError as e:
    check("expired file explained", "two days" in e.reason, True)

j.video_url = None
try:
    run(p.download(j))
    check("no url raises", "no", "yes")
except ProviderError as e:
    check("no url raises", "Nothing to download" in e.reason, True)

# ── registry ──────────────────────────────────────────────────────────────
import video_providers as vp  # noqa: E402

names = [d["name"] for d in vp.available()]
check("veo is registered", "veo" in names, True)
check("kling absent without a key", "kling" in names, bool(os.getenv("KLING_API_KEY")))

try:
    vp.get("nope")
    check("unknown provider raises", "no", "yes")
except ProviderError as e:
    check("unknown provider lists the real ones", "Available:" in e.reason, True)
    check("  as a 400", e.status_code, 400)

os.environ.pop("GEMINI_API_KEY", None)
try:
    vp.get("veo")
    check("unconfigured provider raises", "no", "yes")
except ProviderError as e:
    check("unconfigured provider gives the fix", "GEMINI_API_KEY" in e.reason, True)
os.environ["GEMINI_API_KEY"] = "set-at-runtime"

check("get_optional never raises", vp.get_optional("nope"), None)

# a provider missing a method cannot be instantiated
try:
    class Broken(VideoProvider):
        name = "broken"
    Broken()
    check("incomplete provider rejected", "instantiated", "rejected")
except TypeError:
    check("incomplete provider rejected", True, True)

# ── the default tier is the cheap one, not the premium one ───────────────
os.environ.pop("VEO_MODEL", None)
check("defaults to the fast tier", VeoProvider(api_key="k").model,
      "veo-3.1-fast-generate-preview")
os.environ["VEO_MODEL"] = "veo-3.1-lite-generate-preview"
check("  .env overrides the default", VeoProvider(api_key="k").model,
      "veo-3.1-lite-generate-preview")
check("  an explicit argument beats .env",
      VeoProvider(api_key="k", model="veo-3.1-generate-preview").model,
      "veo-3.1-generate-preview")
os.environ.pop("VEO_MODEL", None)

print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 62)
raise SystemExit(1 if FAIL else 0)
