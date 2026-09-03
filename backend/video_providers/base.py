"""
What every video provider must look like from the outside.

Video APIs differ wildly in auth and payload shape but share one lifecycle:
submit work, poll until it is done, fetch the bytes. Pinning that down here
means adding a provider is writing three methods, not rewiring the app — and
main.py never learns which providers exist.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    TERMINAL = {SUCCEEDED, FAILED}


@dataclass
class VideoJob:
    """One generation request, as the app sees it."""

    provider_job_id: str
    provider: str
    status: str = JobStatus.QUEUED
    video_url: Optional[str] = None      # provider-side URL, often short-lived
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status in JobStatus.TERMINAL

    @property
    def ok(self) -> bool:
        return self.status == JobStatus.SUCCEEDED


class ProviderError(Exception):
    """A provider could not do what was asked. `.reason` is user-safe."""

    def __init__(self, reason: str, status_code: int = 502):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class VideoProvider(ABC):
    """Implement these four and register the class; nothing else changes."""

    name: str = "unnamed"
    label: str = "Unnamed provider"
    # What this provider can actually do — the API reports it rather than the
    # caller guessing, so the UI can offer only valid choices.
    supports_image_to_video: bool = False
    aspect_ratios: tuple = ("16:9", "9:16")
    durations: tuple = (5,)
    default_duration: int = 5

    @abstractmethod
    def is_configured(self) -> bool:
        """True when the credentials this provider needs are present."""

    @abstractmethod
    async def submit(self, prompt: str, *, duration: int = None,
                     aspect_ratio: str = "9:16", image_bytes: bytes = None,
                     image_mime: str = "image/png", **options) -> VideoJob:
        """Start a generation. Returns a job in a non-terminal state."""

    @abstractmethod
    async def poll(self, provider_job_id: str) -> VideoJob:
        """Current state of a submitted job."""

    @abstractmethod
    async def download(self, job: VideoJob) -> bytes:
        """The finished video's bytes. Called once, then stored locally."""

    # ── shared helpers ────────────────────────────────────────────────────

    def normalise_duration(self, duration) -> int:
        """Snap a requested duration to the nearest one this provider allows.

        Ties go to the LONGER option, deliberately. Providers offer a coarse
        set (Veo: 4, 6, 8) and the app's own default is 5, which sits exactly
        between two of them. Rounding down would quietly truncate a script
        written for five seconds; rounding up keeps the content, and the
        caller is told the real duration and its cost before anything runs.
        """
        if not duration:
            return self.default_duration
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            return self.default_duration
        if duration in self.durations:
            return duration
        # sort by distance, then prefer the longer of an equidistant pair
        return sorted(self.durations, key=lambda d: (abs(d - duration), -d))[0]

    def normalise_aspect(self, aspect_ratio: str) -> str:
        return aspect_ratio if aspect_ratio in self.aspect_ratios else self.aspect_ratios[0]

    def estimated_cost(self, duration: int) -> Optional[float]:
        """USD for a clip of this length, when the rate is configured."""
        rate = getattr(self, "cost_per_second", None)
        return round(rate * duration, 4) if rate else None

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "configured": self.is_configured(),
            "supports_image_to_video": self.supports_image_to_video,
            "aspect_ratios": list(self.aspect_ratios),
            "durations": list(self.durations),
            "default_duration": self.default_duration,
            "cost_per_second": getattr(self, "cost_per_second", None),
        }
