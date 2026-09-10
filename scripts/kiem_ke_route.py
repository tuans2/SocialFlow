#!/usr/bin/env python3
"""Sinh lại bảng kiểm kê route — Đợt 3 / E2.

    cd SocialFlow && python3 scripts/kiem_ke_route.py

Ghi đè `docs/KIEM_KE_ROUTE_DOT_3.md`. Chạy lại sau khi thêm/bớt route để bảng
không nói dối. Ô đã tích `[x]` sẽ MẤT khi sinh lại — đọc kỹ diff trước khi
commit, hoặc tích lại theo bản cũ trong git.

Vì sao cần một bảng sinh bằng mã: nhóm E phải chạm 66 route theo tổ chức nằm
rải trong bảy tệp. Đếm tay một lần đã ra 61, đếm bằng mã ra 75 tổng — chênh vì
`analytics_store.py` cũng khai route, và một route dùng đường dẫn rỗng
(`@router.get("")`) mà biểu thức chính quy đầu tiên bỏ sót. Bảng gõ tay sẽ sai
theo đúng kiểu đó.
"""

import re
from pathlib import Path

GOC = Path(__file__).resolve().parent.parent
BACKEND = GOC / "backend"

TEP = [
    "main.py", "analytics_store.py", "heygen_routes.py", "assets_routes.py",
    "visual_content_routes.py", "openclaw_bridge.py", "brand_kit.py",
]

# Đường dẫn rỗng là hợp lệ (`@router.get("")` = chính gốc của prefix), nên
# `[^"\']*` chứ không phải `+`.
DECORATOR = re.compile(r'@(?:app|router)\.(get|post|put|delete|patch)\(\s*["\']([^"\']*)["\']')

TRUNG_TINH = {
    "/", "/api/health", "/api/video-providers", "/api/avatar-catalog",
    "/api/heygen/platform-rules", "/api/heygen/platform-rules/{platform}",
    "/api/visual/types",
}
QUAN_TRI = {"/api/config"}


def quet():
    ket_qua = []
    for ten in TEP:
        src = (BACKEND / ten).read_text(encoding="utf-8")
        dong = src.splitlines()

        prefix = ""
        khai_bao = re.search(r"APIRouter\(([^)]*)\)", src, re.S)
        if khai_bao:
            p = re.search(r'prefix\s*=\s*["\']([^"\']*)["\']', khai_bao.group(1))
            if p:
                prefix = p.group(1)

        for i, line in enumerate(dong):
            m = DECORATOR.search(line)
            if not m:
                continue
            duong_dan = prefix + m.group(2) or "/"
            than = "\n".join(dong[i:i + 22])
            nhom = "b" if duong_dan in TRUNG_TINH else "c" if duong_dan in QUAN_TRI else "a"
            ket_qua.append({
                "tep": ten,
                "method": m.group(1).upper(),
                "duong_dan": duong_dan,
                "nhac_org": "organization_id" in than,
                "nhom": nhom,
            })
    return ket_qua


def dung_tai_lieu(routes):
    dem = {"a": 0, "b": 0, "c": 0}
    for r in routes:
        dem[r["nhom"]] += 1

    d = []
    w = d.append
    w("# Kiểm kê route — Đợt 3 / E2")
    w("")
    w("**Sinh bằng mã, không gõ tay** — `scripts/kiem_ke_route.py`. Phân nhóm là")
    w("quyết định của con người, nằm trong chính script đó.")
    w("")
    w("Sửa route nào thì tích ô route đó. Bảng này là thứ E8 đối chiếu từng dòng —")
    w('"đã sửa xong" mà không có bảng thì không nhận.')
    w("")
    w("## Bốn nhóm")
    w("")
    w("| Nhóm | Nghĩa | Số route |")
    w("|---|---|---|")
    w(f"| **a** | Theo tổ chức — `organization_id` lấy từ token đã xác minh | **{dem['a']}** |")
    w(f"| **b** | Trung tính — không đọc/ghi dữ liệu của tổ chức nào | {dem['b']} |")
    w(f"| **c** | Quản trị máy chủ — đọc/ghi biến môi trường, không phải dữ liệu tổ chức | {dem['c']} |")
    w("| **d** | Chạy nền — điểm vào của APScheduler, không phải route HTTP | 0 |")
    w(f"| | **Tổng** | **{len(routes)}** |")
    w("")
    w('Cột "nhắc org" chỉ nói hàm có nhắc tới `organization_id` trong 22 dòng đầu —')
    w("KHÔNG nói nó lọc đúng. Nhiều route nhắc tới nó vì NHẬN từ query của bên gọi,")
    w("thứ mà E7 phải bỏ.")
    w("")

    w("## Nhóm a — theo tổ chức")
    w("")
    w("| ✓ | Tệp | Method | Đường dẫn | Nhắc org |")
    w("|---|---|---|---|---|")
    for r in routes:
        if r["nhom"] == "a":
            w(f'| [ ] | `{r["tep"]}` | {r["method"]} | `{r["duong_dan"]}` | '
              f'{"có" if r["nhac_org"] else "—"} |')
    w("")

    for nhom, ten in (("b", "Nhóm b — trung tính"), ("c", "Nhóm c — quản trị máy chủ")):
        w(f"## {ten}")
        w("")
        w("| Tệp | Method | Đường dẫn |")
        w("|---|---|---|")
        for r in routes:
            if r["nhom"] == nhom:
                w(f'| `{r["tep"]}` | {r["method"]} | `{r["duong_dan"]}` |')
        w("")

    w("## Ghi chú phân nhóm")
    w("")
    w("- `/api/config` (nhóm c) đọc/ghi biến môi trường của máy chủ — khoá API, cấu")
    w("  hình provider. Không phải dữ liệu tổ chức, nên không gác bằng tổ chức; nó")
    w("  cần một cổng quản trị riêng — ghi vào nợ kỹ thuật, không xử lý ở Đợt 3.")
    w("- `platform-rules`, `video-providers`, `avatar-catalog`, `visual/types` trả")
    w("  năng lực của nhà cung cấp, giống nhau với mọi tổ chức.")
    w("- `/api/health` và `/` phải mở, nếu không thì không giám sát được và không")
    w("  tải được giao diện để đăng nhập.")
    w("- `analytics_store.py` cũng khai 5 route — lọt khi chỉ đếm `main.py` và các")
    w("  tệp có chữ `routes` trong tên.")
    return "\n".join(d) + "\n"


if __name__ == "__main__":
    routes = quet()
    dich = GOC / "docs" / "KIEM_KE_ROUTE_DOT_3.md"
    dich.parent.mkdir(exist_ok=True)
    dich.write_text(dung_tai_lieu(routes), encoding="utf-8")
    print(f"{len(routes)} route → {dich.relative_to(GOC)}")
