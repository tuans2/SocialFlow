#!/usr/bin/env python3
"""Đối chiếu chi phí SocialFlow đã tiêu với usage đã báo về core — Đợt 3 / G3.

    python3 scripts/doi_chieu_usage.py --org-id=<uuid> [--tu=2026-09-01]

Cùng tinh thần `npm run doi-chieu` của P8 bên core: không tin là đã nối đúng
cho tới khi hai con số khớp.

Script này in ra PHÍA SOCIALFLOW. Phía core phải đọc bằng
`GET /api/v1/usage/summary` bằng phiên người dùng — core cố ý KHÔNG phơi
endpoint đọc usage cho token tích hợp (đặc tả 08 mục 4 chỉ liệt kê chiều GHI),
nên script không tự lấy được và cũng không nên tự lấy.

Vì sao `video_jobs` chứ không phải `assets` cho phần video: khi một lượt sinh
trả về đúng byte đã có, kho tệp tái dùng asset cũ và asset ấy giữ chi phí của
LƯỢT ĐẦU. Mọi lượt gửi đều bị tính tiền, nên bảng job mới là chỗ có đúng một
dòng cho mỗi lần bị tính.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "backend" / "socialflow.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True, help="organization_id (UUID) bên core")
    parser.add_argument("--tu", default=None, help="mốc bắt đầu, ISO (mặc định: từ đầu)")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    dieu_kien_anh = "organization_id = ? AND origin = 'generated' AND cost_usd IS NOT NULL"
    tham_so_anh = [args.org_id]
    dieu_kien_video = "organization_id = ? AND cost_usd IS NOT NULL"
    tham_so_video = [args.org_id]
    if args.tu:
        dieu_kien_anh += " AND created_at >= ?"
        tham_so_anh.append(args.tu)
        dieu_kien_video += " AND created_at >= ?"
        tham_so_video.append(args.tu)

    anh = conn.execute(
        f"SELECT COUNT(*) n, ROUND(COALESCE(SUM(cost_usd),0), 4) usd "
        f"FROM assets WHERE {dieu_kien_anh}", tham_so_anh).fetchone()
    video = conn.execute(
        f"SELECT COUNT(*) n, ROUND(COALESCE(SUM(cost_usd),0), 4) usd "
        f"FROM video_jobs WHERE {dieu_kien_video}", tham_so_video).fetchone()

    mo_coi = conn.execute(
        "SELECT (SELECT COUNT(*) FROM assets WHERE organization_id IS NULL) a, "
        "       (SELECT COUNT(*) FROM video_jobs WHERE organization_id IS NULL) v"
    ).fetchone()
    conn.close()

    print("=" * 66)
    print(f"Tổ chức: {args.org_id}" + (f"   từ: {args.tu}" if args.tu else ""))
    print("-" * 66)
    print(f"  Ảnh sinh ra      : {anh['n']:>5} lượt   ${anh['usd']}")
    print(f"  Video sinh ra    : {video['n']:>5} lượt   ${video['usd']}")
    print(f"  TỔNG             : {anh['n']+video['n']:>5} lượt   "
          f"${round(anh['usd'] + video['usd'], 4)}")
    print("-" * 66)
    print("Đối chiếu với core:  GET /api/v1/usage/summary (bằng phiên người dùng)")
    if mo_coi["a"] or mo_coi["v"]:
        print()
        print(f"CẢNH BÁO: còn {mo_coi['a']} asset và {mo_coi['v']} video_job mang")
        print("organization_id NULL — dữ liệu trước Đợt 3, chưa chạy scripts/gan_to_chuc.py.")
        print("Chúng KHÔNG nằm trong các con số trên.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
