"""Xác minh JWT `floraos_sso` — Đợt 3 / E1.

Run:  cd backend && python3 tests/test_sso_auth.py

Ký token bằng đúng thuật toán core dùng (HMAC-SHA256 trên `header.payload`,
base64url không đệm) rồi kiểm bên xác minh đọc lại được — và kiểm mọi hình
thức hỏng đều bị từ chối.

Hình dạng claims dưới đây (`sub`/`org`/`email`/`iat`/`exp`, đúng thứ tự ấy)
đối chiếu với một token THẬT do core phát hành ngày 09/10 trong lượt xác minh
đầu-cuối. Token thật KHÔNG chép vào đây: nó mang user id, organization id và
địa chỉ thư thật, không thuộc về mã nguồn.
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

SECRET = "khoa-thu-du-dai-cho-bo-test-nay"
os.environ["SSO_SESSION_SECRET"] = SECRET

from sso_auth import (  # noqa: E402
    SSO_COOKIE,
    SSO_HEADER,
    SsoSecretMissing,
    read_sso_token,
    verify_sso_token,
)

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def ky(claims: dict, secret: str = SECRET, header: dict = None) -> str:
    """Bản sao cách core ký — cố ý không dùng lại mã của sso_auth, để test
    không tự chứng minh chính nó."""
    head = b64url(json.dumps(header or {"alg": "HS256", "typ": "JWT"},
                             separators=(",", ":")).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64url(sig)}"


def claims_hop_le(**doi):
    now = int(time.time())
    base = {
        "sub": "7b553ef2-a866-4adf-ae2d-047529b06880",
        "org": "bd9c9ff4-bd7a-46e0-83cc-f18f891558e0",
        "email": "ai@do.test",
        "iat": now,
        "exp": now + 900,
    }
    base.update(doi)
    return base


# ── Đường hợp lệ ────────────────────────────────────────────────────────────
tot = verify_sso_token(ky(claims_hop_le()))
check("token hợp lệ đọc được", tot is not None, True)
check("  sub đúng", tot.sub, "7b553ef2-a866-4adf-ae2d-047529b06880")
check("  org đúng", tot.org, "bd9c9ff4-bd7a-46e0-83cc-f18f891558e0")
check("  email đúng", tot.email, "ai@do.test")

check("org null đọc thành None, không phải chuỗi",
      verify_sso_token(ky(claims_hop_le(org=None))).org, None)
check("email null đọc thành None",
      verify_sso_token(ky(claims_hop_le(email=None))).email, None)

# ── Chữ ký ──────────────────────────────────────────────────────────────────
check("ký bằng khoá khác bị từ chối",
      verify_sso_token(ky(claims_hop_le(), secret="khoa-khac-hoan-toan-khac")), None)

goc = ky(claims_hop_le())
check("thêm một ký tự vào chữ ký bị từ chối", verify_sso_token(goc + "x"), None)

head, body, sig = goc.split(".")
gia = json.dumps(claims_hop_le(org="to-chuc-khac"), separators=(",", ":")).encode()
check("đổi payload giữ chữ ký cũ bị từ chối",
      verify_sso_token(f"{head}.{b64url(gia)}.{sig}"), None)

# Không kiểm nội dung header là CÓ CHỦ ĐÍCH: chữ ký vẫn tính bằng HMAC-SHA256
# ta tự chọn, nên token khai `alg: none` không đi qua được cửa nào cả.
check("token khai alg:none nhưng KHÔNG có chữ ký HMAC bị từ chối",
      verify_sso_token(f"{b64url(json.dumps({'alg':'none'}).encode())}.{body}."), None)

# ── Hạn ─────────────────────────────────────────────────────────────────────
now = int(time.time())
check("token hết hạn bị từ chối", verify_sso_token(ky(claims_hop_le(exp=now - 1))), None)
check("exp đúng bằng hiện tại bị từ chối (<=, không phải <)",
      verify_sso_token(ky(claims_hop_le(exp=now)), now=now), None)
check("còn một giây vẫn dùng được",
      verify_sso_token(ky(claims_hop_le(exp=now + 1)), now=now) is not None, True)

# ── Hình dạng hỏng ──────────────────────────────────────────────────────────
check("chuỗi rỗng", verify_sso_token(""), None)
check("hai phần", verify_sso_token("a.b"), None)
check("bốn phần", verify_sso_token("a.b.c.d"), None)
check("phần rỗng ở giữa", verify_sso_token("a..c"), None)
check("không phải chuỗi", verify_sso_token(None), None)
check("payload không phải base64", verify_sso_token(ky(claims_hop_le()).replace(body, "!!!")), None)

xau = b64url(b"khong-phai-json")
check("payload không phải JSON",
      verify_sso_token(f"{head}.{xau}.{hmac.new(SECRET.encode(), f'{head}.{xau}'.encode(), hashlib.sha256).hexdigest()}"),
      None)
check("payload là JSON nhưng không phải object",
      verify_sso_token(ky([1, 2, 3])), None)

check("thiếu sub", verify_sso_token(ky({"org": "x", "exp": now + 900})), None)
check("sub không phải chuỗi", verify_sso_token(ky(claims_hop_le(sub=123))), None)
check("thiếu exp", verify_sso_token(ky({"sub": "x"})), None)
check("exp là chuỗi", verify_sso_token(ky(claims_hop_le(exp=str(now + 900)))), None)
# Trong Python `True` là một `int`. Không loại tường minh thì `exp=True`
# thành exp=1 và token vĩnh viễn hết hạn — im lặng, không báo gì.
check("exp là bool bị từ chối, không đọc thành số",
      verify_sso_token(ky(claims_hop_le(exp=True))), None)

# ── Đọc token từ request ────────────────────────────────────────────────────
check("header thắng cookie",
      read_sso_token({SSO_HEADER: "tu-header"}, {SSO_COOKIE: "tu-cookie"}), "tu-header")
check("không có header thì lấy cookie",
      read_sso_token({}, {SSO_COOKIE: "tu-cookie"}), "tu-cookie")
check("header rỗng không che mất cookie",
      read_sso_token({SSO_HEADER: "   "}, {SSO_COOKIE: "tu-cookie"}), "tu-cookie")
check("không có gì trả None", read_sso_token({}, {}), None)
check("cả hai là None trả None", read_sso_token(None, None), None)

# ── Cấu hình thiếu ──────────────────────────────────────────────────────────
del os.environ["SSO_SESSION_SECRET"]
try:
    verify_sso_token(goc)
    check("thiếu SSO_SESSION_SECRET phải ném lỗi", "không ném", "ném SsoSecretMissing")
except SsoSecretMissing:
    check("thiếu SSO_SESSION_SECRET ném lỗi rõ ràng", True, True)
finally:
    os.environ["SSO_SESSION_SECRET"] = SECRET

print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 62)
raise SystemExit(1 if FAIL else 0)
