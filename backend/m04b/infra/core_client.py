import json
import uuid
from typing import Any, Dict, Optional

import httpx

from org_context import sso_token_tho


class CoreClientAdapter:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    @classmethod
    def default(cls) -> "CoreClientAdapter":
        import os
        return cls(
            base_url=os.getenv("FLORAOS_CORE_BASE_URL", "http://localhost:3100/api/v1"),
            timeout_seconds=float(os.getenv("FLORAOS_CORE_TIMEOUT_SECONDS", "10")),
        )

    def _headers(self, sso_token: Optional[str]) -> Dict[str, str]:
        if sso_token:
            return {"X-FloraOS-SSO": sso_token}
        return {}

    def get_master_image(
        self, product_id: str, sso_token: Optional[str]
    ) -> Dict[str, Any]:
        url = f"{self._base_url}/integration/products/{product_id}/master-image"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url, headers=self._headers(sso_token))
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Core trả {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    def download_image(self, url: str) -> bytes:
        with httpx.Client(timeout=self._timeout * 6) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Không tải được ảnh từ core: {resp.status_code}"
            )
        return resp.content

    def register_asset(
        self,
        organization_id: str,
        sso_token: Optional[str],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        url = f"{self._base_url}/integration/assets"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                url,
                headers={
                    **self._headers(sso_token),
                    "Content-Type": "application/json",
                    "X-FloraOS-Org-Id": organization_id,
                },
                json=asset,
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Core trả {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    def list_assets(
        self, product_id: str, sso_token: Optional[str]
    ) -> Dict[str, Any]:
        url = f"{self._base_url}/integration/products/{product_id}/assets"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url, headers=self._headers(sso_token))
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Core trả {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()
