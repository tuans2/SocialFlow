"""
Which providers exist, and which one to use.

main.py asks the registry for a provider by name and gets an object with three
methods. Adding a provider is one import and one line here.
"""

import os
from typing import Dict, List, Optional

from .base import ProviderError, VideoProvider
from .heygen import HeyGenProvider
from .slideshow import SlideshowProvider
from .veo import VeoProvider

_REGISTRY: Dict[str, VideoProvider] = {}


def register(provider: VideoProvider) -> VideoProvider:
    _REGISTRY[provider.name] = provider
    return provider


def _bootstrap():
    if _REGISTRY:
        return
    register(VeoProvider())
    # Needs no account, key or billing — just ffmpeg. Registered
    # unconditionally so it is always an option when a paid provider is
    # unavailable; is_configured() reports whether ffmpeg is actually there.
    register(SlideshowProvider())
    # Avatar videos. Registered unconditionally so the UI can say what it
    # needs; is_configured() reports whether a key is actually present.
    register(HeyGenProvider())
    # Kling is registered only when a key exists: the integration in main.py
    # predates this abstraction, was never verified against the live API, and
    # should not be offered as a working option just because the file is there.
    if os.getenv("KLING_API_KEY"):
        try:
            from .kling import KlingProvider
            register(KlingProvider())
        except ImportError:
            pass


def get(name: str = None) -> VideoProvider:
    """Resolve a provider by name, falling back to the configured default."""
    _bootstrap()
    wanted = (name or os.getenv("DEFAULT_VIDEO_PROVIDER", "veo")).strip().lower()

    provider = _REGISTRY.get(wanted)
    if provider is None:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise ProviderError(
            f"Unknown video provider '{wanted}'. Available: {known}.", 400
        )
    if not provider.is_configured():
        raise ProviderError(
            f"{provider.label} is not configured. "
            f"{_configuration_hint(provider.name)}",
            400,
        )
    return provider


def get_optional(name: str) -> Optional[VideoProvider]:
    """Resolve without raising — for status pages and polling old jobs."""
    _bootstrap()
    return _REGISTRY.get((name or "").strip().lower())


def available() -> List[dict]:
    """Every registered provider and whether it can actually run."""
    _bootstrap()
    return [p.describe() for p in _REGISTRY.values()]


def default_name() -> str:
    return (os.getenv("DEFAULT_VIDEO_PROVIDER", "veo") or "veo").strip().lower()


def _configuration_hint(name: str) -> str:
    return {
        "veo": "Add GEMINI_API_KEY to .env — the same key text generation uses.",
        "kling": "Add KLING_API_KEY to .env.",
        "slideshow": "Install ffmpeg — on macOS: brew install ffmpeg",
        "heygen": "Add HEYGEN_API_KEY to .env (from HeyGen's API settings, "
                  "not the web login already stored there).",
    }.get(name, "Check the provider's credentials in .env.")
