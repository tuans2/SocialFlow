"""Token máy-gọi-máy của floraos-core, MỘT cái mỗi tổ chức — Đợt 3 / G1.

Vì sao SocialFlow cần thứ này còn LocalBudd thì không: lịch tự động của repo
này (`run_scheduled_cycle`, bốn job APScheduler) chạy khi KHÔNG có người dùng
nào đứng sau, nên không có JWT `floraos_sso` nào để chuyển tiếp. Đó là khác
biệt thật về bản chất, không phải một lựa chọn tiện tay.

Token lưu mã hoá bằng đúng khoá Fernet đang dùng cho mật khẩu nền tảng
(`.secret_key`), trong một bảng riêng thay vì nhồi vào `accounts` — `accounts`
là tài khoản MẠNG XÃ HỘI của tổ chức, còn đây là chứng thư gọi core; trộn hai
thứ vào một bảng làm cả hai khó suy luận.

Cấp token bên core: `POST /api/v1/integration-tokens` với `client=SOCIALFLOW`,
gác bằng năng lực `F9`.
"""

import sqlite3
from pathlib import Path
from typing import Optional

DATABASE_PATH = str(Path(__file__).resolve().parent / "socialflow.db")

DDL = """
CREATE TABLE IF NOT EXISTS core_integration_tokens (
    organization_id TEXT PRIMARY KEY,
    token_encrypted TEXT NOT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""


def dung_bang(conn: Optional[sqlite3.Connection] = None) -> None:
    tu_mo = conn is None
    conn = conn or sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(DDL)
        conn.commit()
    finally:
        if tu_mo:
            conn.close()


def luu_token(organization_id: str, token: str) -> None:
    """Ghi đè token của tổ chức. Xoay token bên core rồi gọi lại hàm này."""
    if not organization_id or not token:
        raise ValueError("organization_id và token đều bắt buộc")
    from main import encrypt  # nạp muộn: tránh vòng import lúc khởi động

    conn = sqlite3.connect(DATABASE_PATH)
    try:
        dung_bang(conn)
        conn.execute(
            "INSERT INTO core_integration_tokens (organization_id, token_encrypted) "
            "VALUES (?, ?) "
            "ON CONFLICT(organization_id) DO UPDATE SET "
            "  token_encrypted = excluded.token_encrypted, "
            "  updated_at = CURRENT_TIMESTAMP",
            (organization_id, encrypt(token)),
        )
        conn.commit()
    finally:
        conn.close()


def doc_token(organization_id: str) -> Optional[str]:
    """Token thô của tổ chức, hoặc `None`.

    `None` KHÔNG phải lỗi ở mọi nơi: lời gọi thay mặt người dùng đi bằng JWT và
    không cần token này. Chỉ đường chạy nền mới bắt buộc có.
    """
    if not organization_id:
        return None
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        dung_bang(conn)
        hang = conn.execute(
            "SELECT token_encrypted FROM core_integration_tokens WHERE organization_id = ?",
            (organization_id,),
        ).fetchone()
    finally:
        conn.close()
    if not hang:
        return None
    from main import decrypt

    return decrypt(hang[0])


def xoa_token(organization_id: str) -> bool:
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        dung_bang(conn)
        cur = conn.execute(
            "DELETE FROM core_integration_tokens WHERE organization_id = ?",
            (organization_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
