"""Client gọi floraos-core — Đợt 3 / F2.

Hai đường xác thực, chọn theo việc lời gọi có người dùng đứng sau hay không —
đúng hai đường core phơi ra (`floraos-core/docs/dac-ta/08-integration-
specification.md` mục 3):

    X-FloraOS-SSO   thay mặt một người đang đăng nhập. Tổ chức nằm trong JWT
                    do core ký, nên luôn đúng tổ chức của chính người ấy.
    Authorization   lời gọi NỀN (lịch tự động) — không có người dùng nào, nên
      Bearer        dùng token máy-gọi-máy của tổ chức (`core_token_store`).

`httpx` đồng bộ, không phải bất đồng bộ: `get_brand_config()` của `creator.py`
là hàm đồng bộ và nằm sâu trong luồng agent. Bọc cả chuỗi đó thành async chỉ
để gọi một request ngắn là thay đổi lớn hơn nhiều so với thứ nó mua lại.
"""

import os
from typing import Any, Dict, Optional

import httpx

THOI_GIAN_CHO_GIAY = float(os.getenv("FLORAOS_CORE_TIMEOUT_SECONDS", "10"))


class CoreKhongGoiDuoc(RuntimeError):
    """Không hỏi được core: mạng, core sập, hoặc chưa có chứng thư nào."""


class CoreTuChoi(RuntimeError):
    """Core TRẢ LỜI RÕ là không — hết hạn mức, hết quyền, không tìm thấy.

    Tách khỏi `CoreKhongGoiDuoc` vì hai thứ này đòi hai cách xử lý khác nhau:
    "core nói không" là một câu trả lời, "không hỏi được core" thì không.
    """

    def __init__(self, status_code: int, body: str):
        super().__init__(f"floraos-core {status_code}: {body[:300]}")
        self.status_code = status_code


def _goc_api() -> str:
    return (os.getenv("FLORAOS_CORE_BASE_URL") or "http://localhost:3100/api/v1").rstrip("/")


class CoreClient:
    """Một thể hiện = một danh tính. Không có client dùng chung toàn tiến trình,
    vì một client dùng chung là một danh tính dùng chung."""

    def __init__(self, *, sso_token: Optional[str] = None, bearer: Optional[str] = None):
        if not sso_token and not bearer:
            raise CoreKhongGoiDuoc(
                "Không có chứng thư nào để gọi core: cần JWT floraos_sso (lời gọi "
                "thay mặt người dùng) hoặc token máy-gọi-máy của tổ chức (lời gọi nền)."
            )
        self._sso_token = sso_token
        self._bearer = bearer

    def _headers(self) -> Dict[str, str]:
        if self._sso_token:
            return {"X-FloraOS-SSO": self._sso_token}
        return {"Authorization": f"Bearer {self._bearer}"}

    def _goi(self, method: str, duong_dan: str, **kwargs) -> Any:
        url = f"{_goc_api()}{duong_dan}"
        try:
            with httpx.Client(timeout=THOI_GIAN_CHO_GIAY) as client:
                dap = client.request(method, url, headers=self._headers(), **kwargs)
        except httpx.HTTPError as e:
            raise CoreKhongGoiDuoc(f"Không gọi được {url}: {e}") from e

        if dap.status_code >= 400:
            raise CoreTuChoi(dap.status_code, dap.text)
        if not dap.content:
            return None
        try:
            return dap.json()
        except ValueError as e:
            raise CoreKhongGoiDuoc(f"Đáp ứng của core không phải JSON: {e}") from e

    # ── Đọc ─────────────────────────────────────────────────────────────────

    def lay_brand_profile(self) -> Optional[Dict[str, Any]]:
        """`GET /integration/brand-profile`. `None` khi tổ chức chưa có hồ sơ —
        core trả `null`, không phải 404."""
        return self._goi("GET", "/integration/brand-profile")

    # ── Ghi ─────────────────────────────────────────────────────────────────

    def ghi_usage(self, *, feature: str, quantity: int = 1,
                  cost_usd: Optional[float] = None,
                  status: str = "COMPLETED",
                  job_id: Optional[str] = None) -> Dict[str, Any]:
        """`POST /integration/usage` — ghi mức dùng phát sinh ở engine ngoài.

        Ghi nhận SAU khi việc đã xảy ra, không phải cổng chặn. Khác hẳn
        `POST /integration/jobs` mà LocalBudd dùng làm cổng hạn mức: ở đây thứ
        đã tốn tiền là một lượt gọi nhà cung cấp đã hoàn tất, chặn nó lúc này
        không lấy lại được gì.
        """
        than = {"feature": feature, "quantity": quantity, "status": status}
        if cost_usd is not None:
            than["cost_usd"] = cost_usd
        if job_id:
            than["job_id"] = job_id
        return self._goi("POST", "/integration/usage", json=than)


def client_thay_mat_nguoi_dung(sso_token: str) -> CoreClient:
    return CoreClient(sso_token=sso_token)


def client_chay_nen(organization_id: str) -> CoreClient:
    """Client cho lịch tự động. Ném `CoreKhongGoiDuoc` khi tổ chức chưa được
    cấp token — nói thẳng thay vì lặng lẽ không báo usage."""
    from core_token_store import doc_token

    token = doc_token(organization_id)
    if not token:
        raise CoreKhongGoiDuoc(
            f"Tổ chức {organization_id} chưa có token máy-gọi-máy của core. "
            "Cấp bằng POST /api/v1/integration-tokens (client=SOCIALFLOW) trên "
            "floraos-core rồi lưu bằng core_token_store.luu_token()."
        )
    return CoreClient(bearer=token)
