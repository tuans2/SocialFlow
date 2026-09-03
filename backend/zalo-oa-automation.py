"""
SocialFlow — Zalo Official Account (OA) Automation
Uses Zalo OA API (OAuth 2.0) — no browser needed.

Requires:
  - ZALO_APP_ID: App ID from Zalo Developers
  - ZALO_SECRET_KEY: Secret Key from Zalo Developers
  - ZALO_REFRESH_TOKEN: Refresh Token (valid 3 months, obtain via OAuth flow)

Endpoints used:
  - OAuth:   POST https://oauth.zaloapp.com/v4/oa/access_token
  - OA Info: GET  https://openapi.zalo.me/v2.0/oa/getoa
  - Upload:  POST https://openapi.zalo.me/v2.0/oa/upload/image
  - Article: POST https://openapi.zalo.me/v2.0/article/create
  - Broadcast: POST https://openapi.zalo.me/v2.0/oa/message
"""

import httpx
import time
import os
from pathlib import Path
from typing import Optional, List, Dict


# ── Constants ────────────────────────────────────────────────────────────────

ZALO_OAUTH_URL = "https://oauth.zaloapp.com/v4/oa/access_token"
ZALO_API_BASE = "https://openapi.zalo.me/v2.0"

# Access Token lifespan: 25 hours (Zalo spec)
ACCESS_TOKEN_TTL_SECONDS = 25 * 60 * 60


class ZaloOAAutomation:
    """Zalo Official Account automation via REST API — no browser needed."""

    PLATFORM = "zalo"

    def __init__(
        self,
        app_id: str = "",
        secret_key: str = "",
        refresh_token: str = "",
    ):
        self.app_id = app_id or os.getenv("ZALO_APP_ID", "")
        self.secret_key = secret_key or os.getenv("ZALO_SECRET_KEY", "")
        self.refresh_token = refresh_token or os.getenv("ZALO_REFRESH_TOKEN", "")

        # Token cache
        self._access_token: str = ""
        self._token_expires_at: float = 0

    # ── Token Management ─────────────────────────────────────────────────

    def _is_configured(self) -> bool:
        """Check if all required credentials are present."""
        return bool(self.app_id and self.secret_key and self.refresh_token)

    async def _refresh_access_token(self) -> str:
        """Call Zalo OAuth endpoint to get a fresh Access Token.

        POST https://oauth.zaloapp.com/v4/oa/access_token
        Content-Type: application/x-www-form-urlencoded

        Returns the new access_token string.
        Raises RuntimeError on failure.
        """
        if not self._is_configured():
            raise RuntimeError(
                "Zalo OA not configured. "
                "Please set ZALO_APP_ID, ZALO_SECRET_KEY, and ZALO_REFRESH_TOKEN."
            )

        payload = {
            "refresh_token": self.refresh_token,
            "app_id": self.app_id,
            "grant_type": "refresh_token",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "secret_key": self.secret_key,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(ZALO_OAUTH_URL, data=payload, headers=headers)

        data = resp.json()

        if "access_token" not in data:
            error_msg = data.get("error_description", data.get("message", str(data)))
            raise RuntimeError(f"Zalo OAuth failed: {error_msg}")

        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + ACCESS_TOKEN_TTL_SECONDS

        # If Zalo returns a new refresh_token, update it
        if "refresh_token" in data and data["refresh_token"]:
            self.refresh_token = data["refresh_token"]

        return self._access_token

    async def _get_access_token(self) -> str:
        """Get a valid Access Token, refreshing if expired."""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        return await self._refresh_access_token()

    def _get_headers(self, access_token: str) -> Dict[str, str]:
        """Build request headers with the given access token."""
        return {
            "access_token": access_token,
            "Content-Type": "application/json",
        }

    # ── Login / Connection Check ─────────────────────────────────────────

    async def login(self, *args, **kwargs) -> dict:
        """Verify Zalo OA connection by calling the getoa endpoint.

        For Zalo OA, 'login' means verifying that the API credentials work.
        Username/password are NOT used — we rely on App ID + Secret + Refresh Token.
        """
        if not self._is_configured():
            return {
                "success": False,
                "message": (
                    "Zalo OA not configured. "
                    "Please enter your App ID, Secret Key, and Refresh Token in Settings."
                ),
            }

        try:
            token = await self._get_access_token()
            oa_info = await self._get_oa_info(token)

            oa_name = oa_info.get("data", {}).get("name", "Unknown OA")
            return {
                "success": True,
                "message": f"Connected to Zalo OA: {oa_name}",
            }
        except Exception as e:
            return {"success": False, "message": f"Zalo OA login failed: {str(e)}"}

    async def check_login(self) -> bool:
        """Check if the Zalo OA credentials are valid."""
        if not self._is_configured():
            return False
        try:
            token = await self._get_access_token()
            info = await self._get_oa_info(token)
            return info.get("error", -1) == 0
        except Exception:
            return False

    async def _get_oa_info(self, access_token: str) -> dict:
        """GET https://openapi.zalo.me/v2.0/oa/getoa — returns OA profile."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{ZALO_API_BASE}/oa/getoa",
                headers=self._get_headers(access_token),
            )
        return resp.json()

    # ── Upload Image ─────────────────────────────────────────────────────

    async def upload_image(self, image_path: str) -> Optional[str]:
        """Upload an image to Zalo and return its attachment_id.

        POST https://openapi.zalo.me/v2.0/oa/upload/image
        Content-Type: multipart/form-data

        Returns attachment_id on success, None on failure.
        """
        token = await self._get_access_token()

        file_path = Path(image_path)
        if not file_path.exists():
            print(f"[Zalo] Image not found: {image_path}")
            return None

        mime_type = "image/jpeg"
        suffix = file_path.suffix.lower()
        if suffix == ".png":
            mime_type = "image/png"
        elif suffix == ".gif":
            mime_type = "image/gif"

        async with httpx.AsyncClient(timeout=30.0) as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    f"{ZALO_API_BASE}/oa/upload/image",
                    headers={"access_token": token},
                    files={"file": (file_path.name, f, mime_type)},
                )

        data = resp.json()
        if data.get("error") == 0:
            return data.get("data", {}).get("attachment_id")
        else:
            print(f"[Zalo] Image upload failed: {data}")
            return None

    # ── Article Creation ─────────────────────────────────────────────────

    async def create_article(
        self,
        title: str,
        body: str,
        cover_image_path: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        """Create an article (bài viết) on the Zalo OA.

        POST https://openapi.zalo.me/v2.0/article/create

        Args:
            title: Article title
            body: Article body content (HTML supported)
            cover_image_path: Optional path to cover image
            description: Short description / summary

        Returns dict with article info including media_id.
        """
        token = await self._get_access_token()

        # Upload cover image if provided
        cover_attachment = None
        if cover_image_path:
            cover_attachment = await self.upload_image(cover_image_path)

        # Build article payload
        article_body = {
            "type": "normal",
            "title": title,
            "author": "SocialFlow",
            "cover": {},
            "description": description or title[:150],
            "body": [
                {
                    "type": "text",
                    "content": body,
                }
            ],
            "status": "show",
        }

        if cover_attachment:
            article_body["cover"] = {
                "cover_type": "photo",
                "photo_url": "",
                "status": "show",
            }
            # Attach cover via attachment_id
            article_body["cover"]["attachment_id"] = cover_attachment

        payload = {"body": article_body}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{ZALO_API_BASE}/article/create",
                headers=self._get_headers(token),
                json=payload,
            )

        data = resp.json()
        if data.get("error") == 0:
            return {
                "success": True,
                "message": "Article created on Zalo OA",
                "media_id": data.get("data", {}).get("token", ""),
                "data": data.get("data", {}),
            }
        else:
            return {
                "success": False,
                "message": f"Zalo article creation failed: {data.get('message', str(data))}",
            }

    # ── Broadcast ────────────────────────────────────────────────────────

    async def broadcast_article(self, media_id: str) -> dict:
        """Broadcast an article to all OA followers.

        POST https://openapi.zalo.me/v2.0/oa/message

        Args:
            media_id: The media_id/token returned from create_article()

        Note: Content will be reviewed by Zalo (~30 min) before delivery.
        """
        token = await self._get_access_token()

        payload = {
            "recipient": {"target": {}},
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "media",
                        "elements": [
                            {
                                "media_type": "article",
                                "attachment_id": media_id,
                            }
                        ],
                    },
                }
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{ZALO_API_BASE}/oa/message",
                headers=self._get_headers(token),
                json=payload,
            )

        data = resp.json()
        if data.get("error") == 0:
            return {
                "success": True,
                "message": (
                    "Article broadcast submitted to Zalo OA. "
                    "Content will be reviewed by Zalo (~30 min) before delivery."
                ),
            }
        else:
            return {
                "success": False,
                "message": f"Zalo broadcast failed: {data.get('message', str(data))}",
            }

    # ── Main Post Entry Point ────────────────────────────────────────────

    async def post(self, content: str, image_paths: List[str] = None) -> dict:
        """Main entry point — create an article and broadcast it.

        This follows the same interface as other platform automations.

        Args:
            content: The post content. First line is used as title.
            image_paths: Optional list of image paths (first one becomes cover).

        Returns:
            dict with success status and message.
        """
        if not self._is_configured():
            return {
                "success": False,
                "message": (
                    "Zalo OA not configured. "
                    "Please set ZALO_APP_ID, ZALO_SECRET_KEY, and ZALO_REFRESH_TOKEN in Settings."
                ),
            }

        try:
            # Parse title from content (first line or first 100 chars)
            lines = content.strip().split("\n")
            title = lines[0].strip()[:100] if lines else "Bài viết từ SocialFlow"
            body = content

            # Cover image (use the first image if available)
            cover = image_paths[0] if image_paths else None

            # Step 1: Create article
            article_result = await self.create_article(
                title=title,
                body=body,
                cover_image_path=cover,
            )

            if not article_result.get("success"):
                return article_result

            media_id = article_result.get("media_id", "")

            # Step 2: Broadcast article to followers
            if media_id:
                broadcast_result = await self.broadcast_article(media_id)
                return broadcast_result
            else:
                return {
                    "success": True,
                    "message": "Article created on Zalo OA (broadcast skipped — no media_id).",
                }

        except Exception as e:
            return {"success": False, "message": f"Zalo OA post failed: {str(e)}"}

    # ── Cleanup ──────────────────────────────────────────────────────────

    async def close(self):
        """No-op — API-based, no browser to close."""
        pass
