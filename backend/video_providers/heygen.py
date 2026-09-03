"""
HeyGen avatar videos over the REST API.

Replaces the Playwright path in heygen_adapter.py, which drove the HeyGen web
editor by guessing at selectors and — worse — marked a job COMPLETED even when
it could not find the Generate button, so a job that rendered nothing reported
success. An API cannot do that: either a video_id comes back or it does not.

Shapes below follow the published reference:
  POST https://api.heygen.com/v2/video/generate      -> {"data": {"video_id": ...}}
  GET  https://api.heygen.com/v1/video_status.get    -> {"data": {"status", "video_url", ...}}
  POST https://api.heygen.com/v3/assets              -> {"data": {"asset_id", "url", ...}}
Auth is the X-Api-Key header throughout.

Two things to know before trusting this file:

1. NOT verified against the live API. There is no HEYGEN_API_KEY on this
   install — .env holds an email and password, which are the web login, not
   API credentials. Everything here is written from the documentation, and
   the last time that was the only evidence the docs turned out to be wrong
   about a field type. Run check_heygen.py before relying on it.

2. The asset-upload endpoint is genuinely ambiguous in the sources: some
   describe POST /v3/assets with multipart, others upload.heygen.com/v1/asset
   with a raw body. This implements the former and reports the response it
   actually got when it fails, so the first real run says which is right.
"""

import os
from typing import Optional

import httpx

from .base import JobStatus, ProviderError, VideoJob, VideoProvider

BASE_URL = "https://api.heygen.com"

DIMENSION = {
    "9:16": {"width": 720, "height": 1280},
    "16:9": {"width": 1280, "height": 720},
    "1:1": {"width": 1080, "height": 1080},
}


class HeyGenProvider(VideoProvider):
    name = "heygen"
    label = "HeyGen avatar video"
    supports_image_to_video = True          # a photo becomes the speaker
    aspect_ratios = ("9:16", "16:9", "1:1")
    # Length follows the script, not a parameter — these are the plausible
    # range for social clips and exist so the UI has something to offer.
    durations = tuple(range(5, 121))
    default_duration = 30

    def __init__(self, api_key: str = None, cost_per_second: float = None):
        self._api_key = api_key
        self._cost_per_second = cost_per_second

    @property
    def api_key(self) -> str:
        return self._api_key if self._api_key is not None else os.getenv("HEYGEN_API_KEY", "")

    @property
    def cost_per_second(self):
        raw = self._cost_per_second
        if raw is None:
            raw = os.getenv("HEYGEN_COST_PER_SECOND", "")
        try:
            return float(raw) if raw not in ("", None) else None
        except (TypeError, ValueError):
            return None

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def _headers(self) -> dict:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    def _require_key(self):
        if not self.api_key:
            raise ProviderError(
                "HeyGen needs HEYGEN_API_KEY in .env. The HEYGEN_EMAIL and "
                "HEYGEN_PASSWORD already there are the web login, not an API "
                "credential — the key comes from HeyGen's API settings page.",
                400,
            )

    # ── discovery ─────────────────────────────────────────────────────────

    async def list_avatars(self) -> list:
        """Avatars this account can use. Also the cheapest proof the key works."""
        self._require_key()
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{BASE_URL}/v2/avatars",
                                    headers={"X-Api-Key": self.api_key}, timeout=30.0)
        if resp.status_code != 200:
            raise ProviderError(self._explain(resp), resp.status_code)
        data = resp.json().get("data") or {}
        return data.get("avatars") or data.get("talking_photos") or []

    async def list_voices(self) -> list:
        self._require_key()
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{BASE_URL}/v2/voices",
                                    headers={"X-Api-Key": self.api_key}, timeout=30.0)
        if resp.status_code != 200:
            raise ProviderError(self._explain(resp), resp.status_code)
        return (resp.json().get("data") or {}).get("voices") or []

    async def upload_asset(self, data: bytes, mime: str = "image/png",
                           filename: str = "upload.png") -> dict:
        """Put a photo or audio file on HeyGen and get an id to reference it."""
        self._require_key()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BASE_URL}/v3/assets",
                headers={"X-Api-Key": self.api_key},
                files={"file": (filename, data, mime)},
                timeout=120.0,
            )
        if resp.status_code != 200:
            raise ProviderError(
                f"HeyGen asset upload failed ({resp.status_code}): {resp.text[:300]}",
                resp.status_code,
            )
        return resp.json().get("data") or {}

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def submit(self, prompt: str, *, duration: int = None,
                     aspect_ratio: str = "9:16", image_bytes: bytes = None,
                     image_mime: str = "image/png",
                     avatar_id: str = None, talking_photo_id: str = None,
                     voice_id: str = None, audio_asset_id: str = None,
                     title: str = "", **options) -> VideoJob:
        self._require_key()

        if not (prompt or "").strip() and not audio_asset_id:
            raise ProviderError("A script (or an audio asset) is required.", 400)

        # A photo supplied inline is uploaded first and becomes the speaker.
        if image_bytes and not (avatar_id or talking_photo_id):
            asset = await self.upload_asset(image_bytes, image_mime, "avatar.png")
            talking_photo_id = (asset.get("talking_photo_id") or asset.get("asset_id")
                                or asset.get("id"))
            if not talking_photo_id:
                raise ProviderError(
                    f"HeyGen accepted the photo but returned no usable id: {asset}"
                )

        if not (avatar_id or talking_photo_id):
            raise ProviderError(
                "Choose an avatar: pass avatar_id, talking_photo_id, or upload a "
                "photo. GET /api/heygen-avatars lists what this account has.",
                400,
            )

        character = ({"type": "talking_photo", "talking_photo_id": talking_photo_id}
                     if talking_photo_id
                     else {"type": "avatar", "avatar_id": avatar_id, "avatar_style": "normal"})

        if audio_asset_id:
            voice = {"type": "audio", "audio_asset_id": audio_asset_id}
        else:
            voice = {"type": "text", "input_text": prompt,
                     "voice_id": voice_id or os.getenv("HEYGEN_VOICE_ID", "")}
            if not voice["voice_id"]:
                raise ProviderError(
                    "A voice_id is required for a spoken script. Set HEYGEN_VOICE_ID "
                    "in .env or pass voice_id — GET /api/heygen-voices lists them.",
                    400,
                )

        payload = {
            "title": title or (prompt or "SocialFlow avatar")[:60],
            "video_inputs": [{"character": character, "voice": voice}],
            "dimension": DIMENSION.get(self.normalise_aspect(aspect_ratio),
                                       DIMENSION["9:16"]),
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BASE_URL}/v2/video/generate",
                                     headers=self._headers, json=payload, timeout=60.0)

        if resp.status_code != 200:
            raise ProviderError(self._explain(resp), resp.status_code)

        body = resp.json()
        video_id = (body.get("data") or {}).get("video_id")
        if not video_id:
            raise ProviderError(f"HeyGen returned no video_id: {str(body)[:300]}")

        return VideoJob(
            provider_job_id=video_id,
            provider=self.name,
            status=JobStatus.QUEUED,
            raw={"submitted": body, "aspect_ratio": self.normalise_aspect(aspect_ratio),
                 "duration": self.normalise_duration(duration),
                 "model": "heygen-v2",
                 "character": character.get("type")},
        )

    async def poll(self, provider_job_id: str) -> VideoJob:
        self._require_key()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/v1/video_status.get",
                params={"video_id": provider_job_id},
                headers={"X-Api-Key": self.api_key}, timeout=30.0,
            )
        if resp.status_code != 200:
            raise ProviderError(self._explain(resp), resp.status_code)

        data = (resp.json().get("data") or {})
        job = VideoJob(provider_job_id=provider_job_id, provider=self.name, raw=data)
        status = (data.get("status") or "").lower()

        if status in ("completed", "success", "done"):
            url = data.get("video_url")
            if url:
                job.status = JobStatus.SUCCEEDED
                job.video_url = url
            else:
                job.status = JobStatus.FAILED
                job.error = "HeyGen reported completion without a video URL."
        elif status in ("failed", "error"):
            job.status = JobStatus.FAILED
            err = data.get("error")
            job.error = (err.get("message") if isinstance(err, dict) else err) or \
                        "HeyGen reported the job as failed."
        else:
            # pending / processing / waiting — and anything unrecognised, which
            # must NOT be treated as done. That mistake is why the browser
            # adapter it replaces reported success on empty jobs.
            job.status = JobStatus.RUNNING
        return job

    async def download(self, job: VideoJob) -> bytes:
        if not job.video_url:
            raise ProviderError("Nothing to download — the job has no video URL.")
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(job.video_url, timeout=300.0)
        if resp.status_code != 200:
            raise ProviderError(
                f"Could not download the finished video (HTTP {resp.status_code}). "
                "HeyGen video links expire after about seven days.",
                resp.status_code,
            )
        return resp.content

    # ── errors ────────────────────────────────────────────────────────────

    @staticmethod
    def _explain(resp) -> str:
        try:
            body = resp.json()
            err = body.get("error") or body.get("message") or body
            message = err.get("message") if isinstance(err, dict) else str(err)
        except Exception:
            message = resp.text[:300]
        if resp.status_code in (401, 403):
            return (f"HeyGen rejected the API key ({resp.status_code}). It comes from "
                    f"HeyGen's API settings, not the web login. {message}")
        if resp.status_code == 429:
            return f"HeyGen rate limit or credit balance reached. {message}"
        return f"HeyGen error {resp.status_code}: {message}"
