"""
Google Veo via the Gemini API.

Chosen as the first provider because SocialFlow already holds a GEMINI_API_KEY:
the same key that drives text generation drives video, with no extra account
and no JWT dance. Auth is a single header.

Shapes below follow the published Gemini API reference:
  POST /v1beta/models/{model}:predictLongRunning   -> {"name": "<operation>"}
  GET  /v1beta/{operation}                         -> {"done": bool, ...}
  video URI at response.generateVideoResponse.generatedSamples[0].video.uri

One consequence worth knowing: Google keeps a generated video for about two
days and then deletes it. Anything we want to keep has to be downloaded, which
is why download() exists and why the route stores the bytes locally instead of
handing the frontend a provider URL — the mistake the older Kling path made.
"""

import os
from typing import Optional

import httpx

from .base import JobStatus, ProviderError, VideoJob, VideoProvider

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class VeoProvider(VideoProvider):
    name = "veo"
    label = "Google Veo 3.1"
    supports_image_to_video = True
    # The reference lists 16:9 and 9:16 only. Asking for anything else is a
    # 400, so the provider snaps to a supported value rather than failing.
    aspect_ratios = ("9:16", "16:9")
    durations = (4, 6, 8)
    default_duration = 8

    def __init__(self, api_key: str = None, model: str = None,
                 cost_per_second: float = None, resolution: str = None):
        # Everything is read lazily from the environment unless explicitly
        # passed in. The Settings page can rewrite .env at runtime, and a
        # provider that cached its key at import time would keep reporting
        # "not configured" until the process restarted.
        self._api_key = api_key
        self._model = model
        self._resolution = resolution
        self._cost_per_second = cost_per_second

    @property
    def api_key(self) -> str:
        return self._api_key if self._api_key is not None else os.getenv("GEMINI_API_KEY", "")

    # Default to the fast tier, not the standard one. Veo ships three tiers
    # and the standard model costs several times more per clip; for the
    # vertical social clips this app makes, fast is the sensible default and
    # nobody should pay the premium by simply not setting a variable.
    DEFAULT_MODEL = "veo-3.1-fast-generate-preview"

    @property
    def model(self) -> str:
        return self._model or os.getenv("VEO_MODEL") or self.DEFAULT_MODEL

    @property
    def resolution(self) -> str:
        return self._resolution or os.getenv("VEO_RESOLUTION", "720p")

    @property
    def cost_per_second(self):
        raw = self._cost_per_second
        if raw is None:
            raw = os.getenv("VEO_COST_PER_SECOND", "")
        try:
            return float(raw) if raw not in ("", None) else None
        except (TypeError, ValueError):
            return None

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def _headers(self) -> dict:
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def _require_key(self):
        if not self.api_key:
            raise ProviderError(
                "Google Veo needs GEMINI_API_KEY in .env "
                "(the same key used for text generation).",
                400,
            )

    async def submit(self, prompt: str, *, duration: int = None,
                     aspect_ratio: str = "9:16", image_bytes: bytes = None,
                     image_mime: str = "image/png", negative_prompt: str = "",
                     resolution: str = None, **options) -> VideoJob:
        self._require_key()
        if not (prompt or "").strip():
            raise ProviderError("A prompt is required.", 400)

        duration = self.normalise_duration(duration)
        aspect = self.normalise_aspect(aspect_ratio)
        res = resolution or self.resolution
        # 1080p and 4k are only produced at the maximum duration.
        if res in ("1080p", "4k"):
            duration = 8

        instance = {"prompt": prompt}
        if image_bytes:
            import base64
            instance["image"] = {
                "inlineData": {
                    "mimeType": image_mime,
                    "data": base64.b64encode(image_bytes).decode(),
                }
            }

        parameters = {
            "aspectRatio": aspect,
            # A NUMBER, not a string. The published reference shows "4"/"6"/"8"
            # quoted; the live API rejects that with
            #   "The value type for `durationSeconds` needs to be a number."
            # Verified against the real endpoint on 2026-09-03.
            "durationSeconds": int(duration),
            "resolution": res,
        }
        if negative_prompt:
            parameters["negativePrompt"] = negative_prompt

        payload = {"instances": [instance], "parameters": parameters}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BASE_URL}/models/{self.model}:predictLongRunning",
                headers=self._headers, json=payload, timeout=60.0,
            )

        if resp.status_code != 200:
            raise ProviderError(self._explain(resp), resp.status_code)

        data = resp.json()
        operation = data.get("name")
        if not operation:
            raise ProviderError(f"Veo did not return an operation name: {data}")

        return VideoJob(
            provider_job_id=operation,
            provider=self.name,
            status=JobStatus.QUEUED,
            raw={"submitted": data, "duration": duration, "aspect_ratio": aspect,
                 "resolution": res, "model": self.model},
        )

    async def poll(self, provider_job_id: str) -> VideoJob:
        self._require_key()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/{provider_job_id}",
                headers={"x-goog-api-key": self.api_key}, timeout=30.0,
            )

        if resp.status_code != 200:
            raise ProviderError(self._explain(resp), resp.status_code)

        data = resp.json()
        job = VideoJob(provider_job_id=provider_job_id, provider=self.name, raw=data)

        if not data.get("done"):
            job.status = JobStatus.RUNNING
            return job

        if "error" in data:
            job.status = JobStatus.FAILED
            job.error = data["error"].get("message") or str(data["error"])
            return job

        uri = self._video_uri(data)
        if uri:
            job.status = JobStatus.SUCCEEDED
            job.video_url = uri
        else:
            job.status = JobStatus.FAILED
            # A finished operation with no sample is usually a safety block.
            job.error = self._blocked_reason(data) or (
                f"Veo finished but returned no video: {str(data)[:300]}"
            )
        return job

    async def download(self, job: VideoJob) -> bytes:
        self._require_key()
        if not job.video_url:
            raise ProviderError("Nothing to download — the job has no video URL.")
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(
                job.video_url, headers={"x-goog-api-key": self.api_key}, timeout=300.0
            )
        if resp.status_code != 200:
            raise ProviderError(
                f"Could not download the finished video (HTTP {resp.status_code}). "
                "Veo keeps generated files for about two days.",
                resp.status_code,
            )
        return resp.content

    # ── response helpers ──────────────────────────────────────────────────

    @staticmethod
    def _video_uri(data: dict) -> Optional[str]:
        samples = (
            data.get("response", {})
            .get("generateVideoResponse", {})
            .get("generatedSamples", [])
        )
        if samples:
            uri = (samples[0].get("video") or {}).get("uri")
            if uri:
                return uri
        # Tolerate the flatter shape some responses use.
        for key in ("generatedVideos", "videos"):
            alt = data.get("response", {}).get(key) or []
            if alt:
                uri = (alt[0].get("video") or alt[0]).get("uri")
                if uri:
                    return uri
        return None

    @staticmethod
    def _blocked_reason(data: dict) -> Optional[str]:
        resp = data.get("response", {}).get("generateVideoResponse", {})
        reason = resp.get("raiMediaFilteredReasons")
        if reason:
            text = reason[0] if isinstance(reason, list) and reason else reason
            return f"Veo declined this prompt: {text}"
        return None

    @staticmethod
    def _explain(resp) -> str:
        try:
            err = resp.json().get("error", {})
            message = err.get("message") or resp.text[:300]
        except Exception:
            message = resp.text[:300]
        if resp.status_code in (401, 403):
            return (f"Google rejected the API key ({resp.status_code}). Check that "
                    f"GEMINI_API_KEY is valid and that the Veo model is enabled for it. {message}")
        if resp.status_code == 429:
            return f"Veo rate limit or quota reached. {message}"
        return f"Veo error {resp.status_code}: {message}"
