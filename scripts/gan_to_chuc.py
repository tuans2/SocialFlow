#!/usr/bin/env python3
"""Gán dữ liệu cũ cho một tổ chức thật — Đợt 3 / H1.

    python3 scripts/gan_to_chuc.py --org-id=<uuid>            # xem trước
    python3 scripts/gan_to_chuc.py --org-id=<uuid> --that-su  # ghi thật

Trước Đợt 3, `organization_id` hoặc là NULL, hoặc là chuỗi `"default"` do
`SOCIALFLOW_DEFAULT_ORGANIZATION_ID` sinh ra. E7 bỏ hẳn đường rơi về mặc định
và mọi truy vấn nay lọc chặt theo tổ chức — nên tới khi chạy script này, dữ
liệu cũ VÔ HÌNH với ứng dụng. Đó là trạng thái trung gian có chủ đích, không
phải hỏng.

Quyết định của anh Tony (AskUserQuestion 2026-09-10): gán toàn bộ cho AVI GIFT.
Hệ quả đã biết và chấp nhận: 45 bài đăng cùng chỉ số lịch sử của thương hiệu
gốc (InBharat AI) sẽ tính vào số liệu của AVI GIFT. Ai đọc analytics sau này
phải biết mốc đó.

MẶC ĐỊNH LÀ XEM TRƯỚC. Phải gõ `--that-su` mới ghi. Chạy nhiều lần vô hại:
lần hai không còn hàng nào NULL/"default" để đổi.

`--org-id` không có giá trị mặc định vì UUID của AVI GIFT ĐỔI mỗi lần dựng lại
dữ liệu trên máy phát triển. Gắn cứng một UUID vào script là cách chắc chắn
nhất để một ngày nào đó nó gán dữ liệu cho một tổ chức không còn tồn tại.
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "backend" / "socialflow.db"
CU = ("default",)  # giá trị cũ, ngoài NULL


def bang_co_cot_to_chuc(conn) -> list:
    ra = []
    for (ten,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        cot = {r[1] for r in conn.execute(f'PRAGMA table_info("{ten}")')}
        if "organization_id" in cot:
            ra.append(ten)
    return ra


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org-id", required=True,
                        help="organization_id (UUID) bên floraos-core")
    parser.add_argument("--that-su", action="store_true",
                        help="ghi thật; không có cờ này thì chỉ xem trước")
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-fA-F-]{16,}", args.org_id):
        print(f"--org-id trông không giống UUID: {args.org_id!r}", file=sys.stderr)
        print("Lấy đúng giá trị từ core (npm run nap:danh-muc in ra lúc bootstrap).",
              file=sys.stderr)
        return 1

    if not DB.exists():
        print(f"Không thấy cơ sở dữ liệu: {DB}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(DB))
    try:
        bang = bang_co_cot_to_chuc(conn)
        cho_trong = "?, " * len(CU)
        dieu_kien = f"organization_id IS NULL OR organization_id IN ({cho_trong.rstrip(', ')})"

        tong = 0
        print("=" * 66)
        print(f"{'Bảng':<26}{'Sẽ gán':>10}{'Đã đúng':>12}{'Khác':>10}")
        print("-" * 66)
        for ten in bang:
            can = conn.execute(
                f'SELECT COUNT(*) FROM "{ten}" WHERE {dieu_kien}', CU).fetchone()[0]
            dung = conn.execute(
                f'SELECT COUNT(*) FROM "{ten}" WHERE organization_id = ?',
                (args.org_id,)).fetchone()[0]
            khac = conn.execute(
                f'SELECT COUNT(*) FROM "{ten}" WHERE organization_id IS NOT NULL '
                f'AND organization_id != ? AND organization_id NOT IN ({cho_trong.rstrip(", ")})',
                (args.org_id,) + CU).fetchone()[0]
            tong += can
            if can or dung or khac:
                print(f"{ten:<26}{can:>10}{dung:>12}{khac:>10}")
        print("-" * 66)
        print(f"{'TỔNG SẼ GÁN':<26}{tong:>10}")
        print("=" * 66)

        if not args.that_su:
            print("\nXem trước — chưa ghi gì. Thêm --that-su để ghi thật.")
            return 0
        if tong == 0:
            print("\nKhông có hàng nào cần gán. Không ghi gì.")
            return 0

        for ten in bang:
            conn.execute(
                f'UPDATE "{ten}" SET organization_id = ? WHERE {dieu_kien}',
                (args.org_id,) + CU)
        conn.commit()
        print(f"\nĐã gán {tong} hàng cho {args.org_id}.")
        print("Bước tiếp theo: đổi tên tệp phiên Playwright theo organization_id mới")
        print("(H2) — nếu không, ba tài khoản đang đăng nhập sẽ phải đăng nhập lại.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
