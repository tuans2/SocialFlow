"""Xác minh JWT phiên liên-app `floraos_sso` do floraos-core ký.

Đợt 3 / E1. Bản dịch từng dòng của
`floraos-core/src/modules/sso/infra/sso-jwt.ts` sang Python — cố ý giữ cùng
cấu trúc để hai bên đối chiếu bằng mắt được khi một bên đổi.

KHÔNG dùng PyJWT. Ba lý do, theo thứ tự quan trọng:

1. Toàn bộ việc cần làm là HMAC-SHA256 trên `header.payload` — `hmac` và
   `hashlib` của thư viện chuẩn làm đủ, và `hmac.compare_digest` đã cho so
   sánh chống đo thời gian.
2. `requirements.txt` của repo này ghim phiên bản chặt và chạy trên Python
   3.9; thêm một phụ thuộc cho 25 dòng mã là thêm một thứ phải xoay và phải
   kiểm tra bảo mật.
3. Bên ký (core) cũng viết tay bằng `node:crypto`. Hai bản viết tay đối chiếu
   được với nhau; một bản viết tay đối chiếu với một thư viện thì không.

SocialFlow chỉ XÁC MINH — không bao giờ tự ký token này. Chỉ core mới ký.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

# Tên cookie core đặt (`floraos-core/src/core/http/cookies.ts`). Host-only,
# không đặt `Domain`, nên trên localhost trình duyệt tự gửi nó theo TÊN MÁY,
# không phân biệt cổng — ba app ba cổng vẫn nhận được.
SSO_COOKIE = "floraos_sso"

# Header dùng khi gọi server-to-server, nơi không có cookie nào tự đi kèm.
# Phải khớp `SSO_HEADER` ở `floraos-core/src/modules/integration/domain/
# integration-credential.ts`.
SSO_HEADER = "x-floraos-sso"


@dataclass(frozen=True)
class SsoClaims:
    """Nội dung token — CHỈ danh tính, không mang quyền.

    Cố tình không có mã năng lực: SocialFlow có mô hình quyền riêng của nó, và
    mã năng lực của core không có ý nghĩa gì để nó tự diễn giải
    (`UNIFIED_SHELL.md` §3).
    """

    sub: str
    org: Optional[str]
    email: Optional[str]
    iat: int
    exp: int


class SsoSecretMissing(RuntimeError):
    """`SSO_SESSION_SECRET` chưa cấu hình — hỏng to và hỏng sớm."""


def _secret() -> bytes:
    """Đọc muộn, không đọc lúc import, để test tiêm được giá trị riêng."""
    raw = os.getenv("SSO_SESSION_SECRET", "")
    if not raw:
        raise SsoSecretMissing(
            "Thiếu SSO_SESSION_SECRET. Giá trị PHẢI TRÙNG với SSO_SESSION_SECRET "
            "của floraos-core — đây là khoá đối xứng, không phải cặp khoá."
        )
    return raw.encode("utf-8")


def _b64url_decode(part: str) -> bytes:
    """base64url không đệm, đúng cách Node `Buffer.toString('base64url')` sinh ra."""
    padding = "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part + padding)


def _sign(header_and_payload: str) -> str:
    digest = hmac.new(_secret(), header_and_payload.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_sso_token(token: str, now: Optional[float] = None) -> Optional[SsoClaims]:
    """`None` cho MỌI hình thức không hợp lệ — chữ ký sai, hình dạng sai, JSON
    hỏng, hay hết hạn. Không phân biệt lý do ra ngoài, cùng quy ước
    `verifySsoToken` của core: nói rõ vì sao một token bị từ chối là nói cho
    người tấn công biết họ đang sai ở chỗ nào.

    KHÔNG kiểm nội dung phần header. Chữ ký được tính trên đúng chuỗi
    `header.payload` NHẬN ĐƯỢC bằng thuật toán ta tự chọn (HMAC-SHA256), nên
    một token khai `alg: none` hay `alg: RS256` vẫn phải qua đúng phép thử ấy
    — lỗ hổng "alg confusion" không tồn tại ở đây. Cùng lựa chọn của
    `LocalBudd/src/modules/sso/infra/sso-jwt.ts`.
    """
    if not isinstance(token, str):
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None
    header, payload, signature = parts
    if not header or not payload or not signature:
        return None

    try:
        expected = _sign(f"{header}.{payload}")
    except SsoSecretMissing:
        raise

    if not hmac.compare_digest(signature, expected):
        return None

    try:
        body = json.loads(_b64url_decode(payload).decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(body, dict):
        return None

    sub = body.get("sub")
    exp = body.get("exp")
    if not isinstance(sub, str) or not isinstance(exp, int) or isinstance(exp, bool):
        return None

    moment = time.time() if now is None else now
    if exp <= int(moment):
        return None

    org = body.get("org")
    email = body.get("email")
    iat = body.get("iat")
    return SsoClaims(
        sub=sub,
        org=org if isinstance(org, str) else None,
        email=email if isinstance(email, str) else None,
        iat=iat if isinstance(iat, int) and not isinstance(iat, bool) else 0,
        exp=exp,
    )


def read_sso_token(headers, cookies) -> Optional[str]:
    """Lấy token thô từ một request. Header thắng cookie: lời gọi
    server-to-server nêu tường minh danh tính nó mang, còn cookie là thứ trình
    duyệt tự gửi kèm — cái tường minh thắng cái ngầm định.

    Nhận `headers`/`cookies` dạng ánh xạ bất kỳ (Starlette `Request.headers`
    và `Request.cookies` đều hợp), nên tệp này không import FastAPI và test
    được mà không cần dựng máy chủ.
    """
    if headers is not None:
        value = headers.get(SSO_HEADER) or headers.get(SSO_HEADER.title())
        if value and value.strip():
            return value.strip()
    if cookies is not None:
        value = cookies.get(SSO_COOKIE)
        if value and value.strip():
            return value.strip()
    return None
