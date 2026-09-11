"""Đường dẫn cơ sở dữ liệu — một chỗ duy nhất.

Trước đây mỗi mô-đun tự nối đường dẫn cứng `backend/socialflow.db`. Hệ quả:
chỉ cần `import main` là đã chạm vào cơ sở dữ liệu THẬT, vì cuối main.py có
`init_asset_tables()` và `run_migrations()` chạy ngay lúc nạp mô-đun. Bộ test
không thể tránh điều đó bằng cách gán lại hằng số sau khi import — lúc đó đã
muộn.

Nay mọi mô-đun lấy đường dẫn qua đây, nên chỉ cần đặt biến môi trường
SOCIALFLOW_DATABASE_PATH TRƯỚC khi import là cả tiến trình trỏ sang cơ sở dữ
liệu khác.
"""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent


def duong_dan_db() -> str:
    """Đường dẫn tệp SQLite mà cả tiến trình này dùng."""
    return os.getenv("SOCIALFLOW_DATABASE_PATH") or str(BACKEND_DIR / "socialflow.db")
