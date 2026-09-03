"""HeyGen provider — request shapes and, above all, status handling.

Run:  cd backend && python3 tests/test_heygen.py

No network: httpx is scripted. These assertions prove the code matches the
documented contract; they cannot prove the documentation is right. See
check_heygen.py for the part only a real key can answer.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from video_providers.base import JobStatus, ProviderError  # noqa: E402
import video_providers.heygen as hg  # noqa: E402
from video_providers.heygen import HeyGenProvider  # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class Resp:
    def __init__(self, code=200, payload=None, content=b""):
        self.status_code = code
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class Fake:
    post_results = []
    get_results = []
    calls = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, files=None, timeout=None):
        Fake.calls.append(("POST", url, headers, json, files))
        return Fake.post_results.pop(0) if Fake.post_results else Resp(200, {})

    async def get(self, url, headers=None, params=None, timeout=None):
        Fake.calls.append(("GET", url, headers, params, None))
        return Fake.get_results.pop(0) if Fake.get_results else Resp(200, {})


hg.httpx.AsyncClient = lambda *a, **k: Fake()


def reset(posts=None, gets=None):
    Fake.calls = []
    Fake.post_results = list(posts or [])
    Fake.get_results = list(gets or [])


p = HeyGenProvider(api_key="k")

# ── configuration ─────────────────────────────────────────────────────────
os.environ.pop("HEYGEN_API_KEY", None)
lazy = HeyGenProvider()
check("unconfigured without a key", lazy.is_configured(), False)
os.environ["HEYGEN_API_KEY"] = "runtime"
check("picks up a key without a restart", lazy.is_configured(), True)
os.environ.pop("HEYGEN_API_KEY", None)

try:
    run(HeyGenProvider(api_key="").submit("hi", voice_id="v"))
    check("missing key refused", "sent", "refused")
except ProviderError as e:
    check("missing key refused", "HEYGEN_API_KEY" in e.reason, True)
    check("  says the stored login is not an API key",
          "web login" in e.reason, True)

# ── submit: the documented v2 shape ──────────────────────────────────────
reset(posts=[Resp(200, {"data": {"video_id": "vid-1"}})])
job = run(p.submit("Hello there", avatar_id="Kristin_public_3", voice_id="voice-1",
                   aspect_ratio="9:16"))
method, url, headers, body, _ = Fake.calls[0]
check("posts to /v2/video/generate", url.endswith("/v2/video/generate"), True)
check("  auth is X-Api-Key", headers["X-Api-Key"], "k")
check("  video_inputs is a list", isinstance(body["video_inputs"], list), True)
character = body["video_inputs"][0]["character"]
check("  stock avatar -> type avatar", character["type"], "avatar")
check("  avatar_id carried", character["avatar_id"], "Kristin_public_3")
voice = body["video_inputs"][0]["voice"]
check("  spoken script -> type text", voice["type"], "text")
check("  script in input_text", voice["input_text"], "Hello there")
check("  voice_id carried", voice["voice_id"], "voice-1")
check("  9:16 is portrait", body["dimension"], {"width": 720, "height": 1280})
check("  job starts non-terminal", job.done, False)
check("  video_id becomes the job id", job.provider_job_id, "vid-1")

reset(posts=[Resp(200, {"data": {"video_id": "v2"}})])
run(p.submit("x", avatar_id="a", voice_id="v", aspect_ratio="16:9"))
check("16:9 is landscape", Fake.calls[0][3]["dimension"], {"width": 1280, "height": 720})

reset(posts=[Resp(200, {"data": {"video_id": "v3"}})])
run(p.submit("", avatar_id="a", audio_asset_id="aud-9"))
v = Fake.calls[0][3]["video_inputs"][0]["voice"]
check("audio asset -> type audio", v["type"], "audio")
check("  no input_text with audio", "input_text" in v, False)

# ── a photo is uploaded first, then used as the speaker ──────────────────
reset(posts=[Resp(200, {"data": {"asset_id": "asset-7"}}),
             Resp(200, {"data": {"video_id": "v4"}})])
run(p.submit("hi", image_bytes=b"\x89PNG\r\n\x1a\nfake", voice_id="v"))
up_url = Fake.calls[0][1]
check("photo uploaded first", up_url.endswith("/v3/assets"), True)
check("  sent as multipart", Fake.calls[0][4] is not None, True)
char = Fake.calls[1][3]["video_inputs"][0]["character"]
check("  becomes a talking_photo", char["type"], "talking_photo")
check("  with the returned id", char["talking_photo_id"], "asset-7")

# ── refusals are specific and actionable ─────────────────────────────────
for label, kwargs, expect in [
    ("no avatar and no photo", {"voice_id": "v"}, "avatar_id"),
    ("script with no voice", {"avatar_id": "a"}, "voice_id"),
]:
    reset()
    try:
        run(p.submit("some script", **kwargs))
        check(label + " refused", "sent", "refused")
    except ProviderError as e:
        check(label + " refused", expect in e.reason, True)
        check(f"  {label} is a 400", e.status_code, 400)

reset()
try:
    run(p.submit("   ", avatar_id="a", voice_id="v"))
    check("empty script refused", "sent", "refused")
except ProviderError as e:
    check("empty script refused", e.status_code, 400)

# ── polling: the part the browser adapter got wrong ──────────────────────
reset(gets=[Resp(200, {"data": {"status": "completed",
                                "video_url": "https://files.heygen.ai/v.mp4"}})])
j = run(p.poll("vid-1"))
check("completed -> succeeded", j.status, JobStatus.SUCCEEDED)
check("  url captured", j.video_url, "https://files.heygen.ai/v.mp4")
check("  queried by video_id", Fake.calls[0][3], {"video_id": "vid-1"})
check("  status path", Fake.calls[0][1].endswith("/v1/video_status.get"), True)

for state in ["pending", "processing", "waiting"]:
    reset(gets=[Resp(200, {"data": {"status": state}})])
    check(f"{state} -> running", run(p.poll("v")).status, JobStatus.RUNNING)

# An unrecognised status must NOT be read as done. Treating "not obviously
# failed" as success is exactly how the browser adapter reported finished
# videos that were never rendered.
reset(gets=[Resp(200, {"data": {"status": "some_new_state_heygen_added"}})])
check("unknown status -> running, never succeeded",
      run(p.poll("v")).status, JobStatus.RUNNING)

reset(gets=[Resp(200, {"data": {"status": "completed"}})])
j = run(p.poll("v"))
check("completed with no url -> failed, not succeeded", j.status, JobStatus.FAILED)
check("  and says so", "without a video URL" in (j.error or ""), True)

reset(gets=[Resp(200, {"data": {"status": "failed",
                                "error": {"message": "voice not found"}}})])
j = run(p.poll("v"))
check("failed -> failed", j.status, JobStatus.FAILED)
check("  message surfaced", j.error, "voice not found")

# ── error translation ─────────────────────────────────────────────────────
reset(posts=[Resp(401, {"error": {"message": "unauthorized"}})])
try:
    run(p.submit("x", avatar_id="a", voice_id="v"))
except ProviderError as e:
    check("401 explains where the key comes from", "API settings" in e.reason, True)

reset(posts=[Resp(429, {"error": {"message": "out of credits"}})])
try:
    run(p.submit("x", avatar_id="a", voice_id="v"))
except ProviderError as e:
    check("429 mentions credits", "credit" in e.reason.lower(), True)

reset(posts=[Resp(200, {"data": {}})])
try:
    run(p.submit("x", avatar_id="a", voice_id="v"))
    check("missing video_id raises", "no", "yes")
except ProviderError as e:
    check("missing video_id raises", "no video_id" in e.reason, True)

# ── download ──────────────────────────────────────────────────────────────
reset(gets=[Resp(200, {}, b"\x00\x00\x00\x20ftypisom")])
from video_providers.base import VideoJob  # noqa: E402
done = VideoJob(provider_job_id="v", provider="heygen",
                status=JobStatus.SUCCEEDED, video_url="https://files.heygen.ai/v.mp4")
check("download returns bytes", run(p.download(done))[4:8], b"ftyp")

reset(gets=[Resp(404, {})])
try:
    run(p.download(done))
except ProviderError as e:
    check("expired link explained", "seven days" in e.reason, True)

# ── discovery ─────────────────────────────────────────────────────────────
reset(gets=[Resp(200, {"data": {"avatars": [{"avatar_id": "a1", "avatar_name": "Ann"}]}})])
check("avatars listed", run(p.list_avatars())[0]["avatar_id"], "a1")
reset(gets=[Resp(200, {"data": {"voices": [{"voice_id": "v1", "name": "Nam"}]}})])
check("voices listed", run(p.list_voices())[0]["voice_id"], "v1")

print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 62)
raise SystemExit(1 if FAIL else 0)
