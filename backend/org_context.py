"""Cổng danh tính của SocialFlow — Đợt 3 / E3.

Một chỗ duy nhất trả lời câu "lời gọi này thuộc tổ chức nào", và câu trả lời
chỉ đến từ một JWT do `floraos-core` ký. KHÔNG bao giờ từ query, body hay
header do bên gọi tự khai — đúng luật đặc tả 08 mục 3 của core: engine ngoài
gửi `organization_id` lên thì giá trị đó bị BỎ QUA, không phải bị trả lỗi, vì
trả lỗi cho biết trường ấy có tồn tại và có tác dụng.

Trước Đợt 3, `organization_id` ở repo này là giá trị bên gọi tự khai với số
lần xác minh bằng không, mặc định rơi về chuỗi `"default"`
(`SocialFlow/TECHNICAL_DEBT.md` mục 1). Tệp này là thứ thay nó.
"""

from typing import Optional

from fastapi import HTTPException, Request

from sso_auth import SsoSecretMissing, read_sso_token, verify_sso_token


def _giai_to_chuc(request: Request) -> Optional[str]:
    token = read_sso_token(request.headers, request.cookies)
    if not token:
        return None
    try:
        claims = verify_sso_token(token)
    except SsoSecretMissing:
        # Cấu hình thiếu là lỗi vận hành, không phải lỗi người dùng — 500 để
        # nó hiện ra ở giám sát thay vì lặng lẽ thành 401 và bị hiểu nhầm là
        # người dùng gõ sai mật khẩu.
        raise HTTPException(status_code=500, detail="SSO_SESSION_SECRET chưa cấu hình")
    if claims is None or not claims.org:
        return None
    return claims.org


async def require_org(request: Request) -> str:
    """Phụ thuộc cho MỌI route thuộc tổ chức (nhóm a của
    `docs/KIEM_KE_ROUTE_DOT_3.md`).

    Trả `organization_id` đã xác minh, hoặc 401. Không có đường rơi về mặc
    định: một route không biết mình thuộc tổ chức nào thì không được chạy.

    `claims.org` rỗng cũng là 401 — phiên bên core chưa chọn tổ chức nào, nên
    không có gì để đọc. Trả cùng một câu trả lời với token sai, không phân
    biệt lý do ra ngoài (cùng quy ước `verify_sso_token`).
    """
    org = _giai_to_chuc(request)
    if not org:
        raise HTTPException(
            status_code=401,
            detail="Cần đăng nhập qua FloraOS (thiếu hoặc sai cookie floraos_sso).",
        )
    return org


async def optional_org(request: Request) -> Optional[str]:
    """Cho route trung tính muốn ghi nhật ký theo tổ chức khi có, nhưng vẫn
    phục vụ được khi không có. KHÔNG dùng cho route đọc/ghi dữ liệu — ở đó
    `None` nghĩa là không lọc, tức là lộ dữ liệu của mọi tổ chức."""
    return _giai_to_chuc(request)
