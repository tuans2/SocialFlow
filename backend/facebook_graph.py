"""
Facebook Page publishing through the official Graph API.

Why this exists: driving facebook.com with Playwright means fighting the UI —
login checkpoints, a composer that re-renders, hashtag popups covering buttons,
two elements sharing aria-label="Post". None of that is a bug in SocialFlow; it
is the cost of automating a user interface. The Graph API publishes a caption
and its image in ONE request, so the two can never end up as separate posts,
there is no session to expire and no selector to break.

Drop-in for FacebookAutomation: same `post(content, image_paths)` contract and
the same no-op browser lifecycle methods the publisher calls.

Configuration (.env):
    FB_PAGE_ID=1234567890
    FB_PAGE_ACCESS_TOKEN=EAAG...        # page token, never logged
    FB_GRAPH_VERSION=v21.0              # optional

Use facebook_graph_setup.py to obtain the token.
"""

import mimetypes
import os
from pathlib import Path
from typing import Dict, List, Optional

import httpx

try:
    from media_utils import resolve_media_path
except ImportError:  # pragma: no cover - only when imported outside backend/
    def resolve_media_path(path):
        p = Path(str(path))
        return str(p.resolve()) if p.is_file() else None


GRAPH_HOST = "https://graph.facebook.com"


class FacebookGraphAutomation:
    """Publish to a Facebook Page via the Graph API."""

    PLATFORM = "facebook"

    def __init__(self, page_id: str = None, access_token: str = None,
                 graph_version: str = None, headless: bool = True):
        # An explicitly passed value wins, even an empty one — only `None` falls
        # back to the environment, so a caller can construct an unconfigured
        # adapter on purpose.
        self.page_id = (
            os.getenv("FB_PAGE_ID", "") if page_id is None else page_id
        ).strip()
        self.access_token = (
            os.getenv("FB_PAGE_ACCESS_TOKEN", "") if access_token is None else access_token
        ).strip()
        self.graph_version = (
            os.getenv("FB_GRAPH_VERSION", "v21.0") if graph_version is None else graph_version
        ).strip() or "v21.0"
        self.headless = headless  # accepted for signature compatibility, unused

    # ---- configuration -----------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self.page_id and self.access_token)

    def _endpoint(self, path: str) -> str:
        return f"{GRAPH_HOST}/{self.graph_version}/{path}"

    # ---- browser lifecycle no-ops (the publisher calls these) --------------

    async def init_browser(self):
        return None

    async def close(self):
        return None

    async def save_session(self, platform: str = None):
        return None

    async def load_session(self, platform: str = None) -> bool:
        """There is no browser session — a configured token is the session."""
        return self.is_configured()

    # ---- account checks ----------------------------------------------------

    async def check_login(self) -> bool:
        if not self.is_configured():
            return False
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    self._endpoint(self.page_id),
                    params={"fields": "id,name", "access_token": self.access_token},
                )
            return response.status_code == 200 and "error" not in response.json()
        except Exception:
            return False

    async def login(self, username: str = None, password: str = None) -> Dict:
        """No interactive login exists for the Graph API — verify the token."""
        if not self.is_configured():
            return {
                "success": False,
                "message": "Facebook Page not configured. Add FB_PAGE_ID and "
                           "FB_PAGE_ACCESS_TOKEN to .env (see facebook_graph_setup.py)."
            }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    self._endpoint(self.page_id),
                    params={"fields": "id,name,fan_count", "access_token": self.access_token},
                )
            payload = response.json()
        except Exception as e:
            return {"success": False, "message": f"Could not reach the Graph API: {e}"}

        if response.status_code != 200 or "error" in payload:
            return {"success": False, "message": self._error_message(payload, response)}

        return {
            "success": True,
            "message": f"Connected to Page \"{payload.get('name')}\" (id {payload.get('id')})",
            "page": payload,
        }

    # ---- publishing --------------------------------------------------------

    async def post(self, content: str, image_paths: List[str] = None) -> Dict:
        """Publish a post. Caption and image go up in a single request."""
        if not self.is_configured():
            return {
                "success": False,
                "message": "Facebook Page not configured (FB_PAGE_ID / "
                           "FB_PAGE_ACCESS_TOKEN missing from .env)."
            }

        resolved: List[str] = []
        for item in (image_paths or []):
            if not item:
                continue
            path = resolve_media_path(item)
            if not path:
                return {"success": False, "message": f"Image file not found: {item}"}
            resolved.append(path)

        try:
            if not resolved:
                return await self._post_text(content)
            if len(resolved) == 1:
                return await self._post_single_photo(content, resolved[0])
            return await self._post_multi_photo(content, resolved)
        except httpx.TimeoutException:
            return {"success": False, "message": "Facebook Graph API timed out."}
        except httpx.HTTPError as e:
            return {"success": False, "message": f"Facebook Graph API request failed: {e}"}

    async def _post_text(self, content: str) -> Dict:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self._endpoint(f"{self.page_id}/feed"),
                data={"message": content, "access_token": self.access_token},
            )
        return self._result(response, "Posted to Facebook Page")

    async def _post_single_photo(self, content: str, path: str) -> Dict:
        """One request carries the caption AND the photo — they cannot split."""
        name = Path(path).name
        mime = mimetypes.guess_type(name)[0] or "image/png"
        async with httpx.AsyncClient(timeout=180.0) as client:
            with open(path, "rb") as handle:
                response = await client.post(
                    self._endpoint(f"{self.page_id}/photos"),
                    data={
                        "message": content,
                        "published": "true",
                        "access_token": self.access_token,
                    },
                    files={"source": (name, handle, mime)},
                )
        return self._result(response, "Posted to Facebook Page with image")

    async def _post_multi_photo(self, content: str, paths: List[str]) -> Dict:
        """Upload each photo unpublished, then publish one post holding them all."""
        media_ids: List[str] = []
        async with httpx.AsyncClient(timeout=180.0) as client:
            for path in paths:
                name = Path(path).name
                mime = mimetypes.guess_type(name)[0] or "image/png"
                with open(path, "rb") as handle:
                    response = await client.post(
                        self._endpoint(f"{self.page_id}/photos"),
                        data={"published": "false", "access_token": self.access_token},
                        files={"source": (name, handle, mime)},
                    )
                payload = self._json(response)
                if response.status_code != 200 or "error" in payload:
                    return {"success": False,
                            "message": self._error_message(payload, response)}
                media_ids.append(payload.get("id"))

            data = {"message": content, "access_token": self.access_token}
            for index, media_id in enumerate(media_ids):
                data[f"attached_media[{index}]"] = f'{{"media_fbid":"{media_id}"}}'
            response = await client.post(self._endpoint(f"{self.page_id}/feed"), data=data)

        return self._result(response, f"Posted to Facebook Page with {len(media_ids)} images")

    # ---- responses ---------------------------------------------------------

    @staticmethod
    def _json(response) -> Dict:
        try:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _error_message(payload: Dict, response) -> str:
        error = payload.get("error") or {}
        message = error.get("message") or (response.text or "")[:300]
        code = error.get("code")
        subcode = error.get("error_subcode")
        detail = f" (code {code}" + (f"/{subcode}" if subcode else "") + ")" if code else ""
        return f"Facebook Graph API error{detail}: {message}"

    def _result(self, response, success_message: str) -> Dict:
        payload = self._json(response)
        if response.status_code != 200 or "error" in payload:
            return {"success": False, "message": self._error_message(payload, response)}

        post_id = payload.get("post_id") or payload.get("id")
        return {
            "success": True,
            "message": success_message,
            "post_id": post_id,
            "url": f"https://www.facebook.com/{post_id}" if post_id else None,
        }
