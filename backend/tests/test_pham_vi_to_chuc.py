"""Ranh giới tổ chức ở tầng HTTP — Đợt 3 / E3, E4.

Run:  cd backend && python3 tests/test_pham_vi_to_chuc.py

Kiểm bằng cách đọc CÂY CÚ PHÁP, không cần cơ sở dữ liệu, không cần dựng máy
chủ, không cần venv của macOS. Đó là lý do bộ này tồn tại: 66 route theo tổ
chức nằm rải trong bảy tệp, và cách duy nhất để một trong số đó lặng lẽ tuột
ra khỏi hàng rào là không ai đếm lại.

Bốn phép thử:

1. Không hàm nào ĐỌC `organization_id` mà không có nó trong phạm vi. Bắt đúng
   lỗi đã xảy ra thật khi vá hàng loạt: một câu lệnh SQL trong worker nền được
   thêm `AND organization_id = ?` cùng lượt với các route, nhưng worker không
   có biến ấy — cú pháp vẫn đúng, `NameError` chỉ nổ lúc chạy.
2. Mọi route nhóm (a) của `docs/KIEM_KE_ROUTE_DOT_3.md` có `organization_id`
   trong tham số.
3. Không route nào còn NHẬN `organization_id` từ bên gọi (`Optional[str] = None`).
4. Sáu router phụ khai `dependencies=[Depends(require_org)]`.
"""

import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

TEP_ROUTE = [
    "main.py", "analytics_store.py", "heygen_routes.py", "assets_routes.py",
    "visual_content_routes.py", "openclaw_bridge.py", "brand_kit.py",
]
ROUTER_PHU = [
    "analytics_store.py", "heygen_routes.py", "assets_routes.py",
    "visual_content_routes.py", "openclaw_bridge.py", "brand_kit.py",
]
# Nhóm (b) và (c) — phân nhóm nằm ở `scripts/kiem_ke_route.py`, chép lại ở đây
# để bộ test không phải import một script sinh tài liệu.
MO = {
    "/", "/api/health", "/api/video-providers", "/api/avatar-catalog", "/api/config",
}

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


# ── 1. organization_id luôn nằm trong phạm vi khi được đọc ──────────────────
ngoai_pham_vi = []
for ten in TEP_ROUTE:
    cay = ast.parse((BACKEND / ten).read_text(encoding="utf-8"))
    for node in ast.walk(cay):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tham_so = {a.arg for a in node.args.args + node.args.kwonlyargs}
        gan = {
            sub.id for sub in ast.walk(node)
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)
        }
        doc = any(
            isinstance(sub, ast.Name) and sub.id == "organization_id"
            and isinstance(sub.ctx, ast.Load)
            for sub in ast.walk(node)
        )
        if doc and "organization_id" not in tham_so and "organization_id" not in gan:
            ngoai_pham_vi.append(f"{ten}:{node.lineno} {node.name}")
check("không hàm nào đọc organization_id ngoài phạm vi", ngoai_pham_vi, [])


# ── 2 & 3. Chữ ký của từng route ────────────────────────────────────────────
DEC = re.compile(r'@(?:app|router)\.(get|post|put|delete|patch)\(\s*["\']([^"\']*)["\']')
thieu_org, con_nhan_tu_caller = [], []

for ten in TEP_ROUTE:
    dong = (BACKEND / ten).read_text(encoding="utf-8").splitlines()
    src = "\n".join(dong)
    prefix = ""
    khai = re.search(r"APIRouter\(([^)]*)\)", src, re.S)
    if khai:
        pm = re.search(r'prefix\s*=\s*["\']([^"\']*)["\']', khai.group(1))
        if pm:
            prefix = pm.group(1)

    for i, line in enumerate(dong):
        m = DEC.search(line)
        if not m:
            continue
        duong_dan = prefix + m.group(2) or "/"
        j = i + 1
        while j < len(dong) and not re.match(r"\s*(async )?def ", dong[j]):
            j += 1
        sig = dong[j] if j < len(dong) else ""

        if "organization_id: Optional[str] = None" in sig:
            con_nhan_tu_caller.append(f"{ten} {duong_dan}")

        if duong_dan in MO:
            continue
        # Router phụ gác ở mức router; route trong đó chỉ cần tham số khi thân
        # hàm thật sự dùng tới tổ chức.
        if ten == "main.py" and "organization_id" not in sig:
            thieu_org.append(f"{ten} {m.group(1).upper()} {duong_dan}")

check("mọi route nhóm a của main.py có organization_id trong chữ ký", thieu_org, [])
check("không route nào còn nhận organization_id từ bên gọi", con_nhan_tu_caller, [])


# ── 4. Router phụ có cổng chặn ở mức router ─────────────────────────────────
thieu_cong = []
for ten in ROUTER_PHU:
    src = (BACKEND / ten).read_text(encoding="utf-8")
    if "dependencies=[Depends(require_org)]" not in src:
        thieu_cong.append(ten)
check("sáu router phụ đều gác bằng require_org", thieu_cong, [])


# ── 5. Mọi lời gọi tầng dữ liệu đều mang organization_id ────────────────────
# `asset_inventory` và `media_store` đòi `organization_id` là tham số chỉ-từ-khoá
# BẮT BUỘC, nên thiếu nó là `TypeError` lúc chạy. Phép thử này bắt TRƯỚC khi
# chạy: một nhánh hiếm (xử lý lỗi, đường quản trị) có thể không bao giờ được gọi
# trong lúc thử tay, và lỗi chỉ nổ ở môi trường thật.
CAN_ORG = {
    "register_asset", "get_asset", "get_asset_by_sha256", "archive_asset",
    "update_asset", "list_assets", "count_assets", "queue_content",
    "list_queue", "update_queue_state",
    "store_upload", "store_bytes", "store_path", "store_stream",
}
# Hai module ĐỊNH NGHĨA các hàm ấy được miễn: chúng gọi lẫn nhau bên trong, nơi
# `organization_id` đã nằm sẵn trong phạm vi.
MIEN = {"asset_inventory.py", "media_store.py"}

thieu_org = []
for tep in sorted(BACKEND.rglob("*.py")):
    if any(x in tep.parts for x in ("venv", "__pycache__")) or tep.name in MIEN:
        continue
    try:
        cay = ast.parse(tep.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    for node in ast.walk(cay):
        if not isinstance(node, ast.Call):
            continue
        ten = (
            node.func.id if isinstance(node.func, ast.Name)
            else node.func.attr if isinstance(node.func, ast.Attribute)
            else None
        )
        if ten in CAN_ORG and "organization_id" not in {k.arg for k in node.keywords}:
            thieu_org.append(f"{tep.relative_to(BACKEND)}:{node.lineno} {ten}()")
check("mọi lời gọi tầng dữ liệu đều truyền organization_id", thieu_org, [])


print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 62)
raise SystemExit(1 if FAIL else 0)
