"""Thông tin Facebook Page THEO TỪNG TỔ CHỨC — Đợt 3 / E6.

Trước lượt này, `FacebookGraphAutomation` đọc MỘT cặp Page ID/token từ biến
môi trường của máy chủ (`FB_PAGE_ID`/`FB_PAGE_ACCESS_TOKEN`). Mọi tổ chức dùng
chung máy chủ đều đăng qua ĐÚNG một Page đó. Đây không phải rò dữ liệu đọc —
nó là ĐĂNG CÔNG KHAI NHẦM THƯƠNG HIỆU, thứ tệ nhất trong danh sách của bản rà
soát, và `automation_extended.py` đã ghi rõ nó là khoảng trống chưa đóng của
A5.

Nay mỗi tổ chức giữ Page riêng trong bảng `accounts` (cùng nơi đã giữ mật khẩu
nền tảng, mã hoá bằng cùng khoá Fernet).

Về đường rơi về biến môi trường: nó KHÔNG im lặng. Cặp trong `.env` chỉ dùng
được cho ĐÚNG MỘT tổ chức, nêu tên tường minh ở `FB_PAGE_OWNER_ORG_ID`. Không
đặt biến đó thì không tổ chức nào thừa hưởng cặp ấy. Lý do: một đường rơi về
mặc định "cho tiện" chính là hình dạng của lỗi mà tệp này sinh ra để sửa.
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional, Tuple
from duong_dan_db import duong_dan_db

DATABASE_PATH = duong_dan_db()


def _co_cot(conn, bang: str, cot: str) -> bool:
    return cot in {r[1] for r in conn.execute(f"PRAGMA table_info({bang})")}


def doc_page_facebook(organization_id: str) -> Optional[Tuple[str, str]]:
    """`(page_id, access_token)` của tổ chức, hoặc `None`.

    `None` nghĩa là tổ chức này chưa cấu hình Page — bên gọi phải rơi về
    Playwright hoặc báo lỗi, KHÔNG được mượn Page của tổ chức khác.
    """
    if not organization_id:
        return None

    conn = sqlite3.connect(DATABASE_PATH)
    try:
        if not _co_cot(conn, "accounts", "page_id"):
            hang = None
        else:
            conn.row_factory = sqlite3.Row
            hang = conn.execute(
                "SELECT page_id, page_token_encrypted FROM accounts "
                "WHERE platform = 'facebook' AND organization_id = ?",
                (organization_id,),
            ).fetchone()
    finally:
        conn.close()

    if hang and hang["page_id"] and hang["page_token_encrypted"]:
        from main import decrypt  # nạp muộn: tránh vòng import lúc khởi động
        return hang["page_id"], decrypt(hang["page_token_encrypted"])

    # Đường rơi về biến môi trường — chỉ cho tổ chức được nêu đích danh.
    chu_so_huu = (os.getenv("FB_PAGE_OWNER_ORG_ID") or "").strip()
    if chu_so_huu and chu_so_huu == organization_id:
        page_id = (os.getenv("FB_PAGE_ID") or "").strip()
        token = (os.getenv("FB_PAGE_ACCESS_TOKEN") or "").strip()
        if page_id and token:
            return page_id, token

    return None
