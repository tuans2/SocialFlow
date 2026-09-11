"""Hai tổ chức, một máy chủ — Đợt 3 / E8.

Run:  cd backend && python3 tests/test_hai_to_chuc.py

Bộ test DUY NHẤT của Đợt 3 gọi route thật qua HTTP (FastAPI TestClient). Bảy
bộ còn lại kiểm bằng cây cú pháp hoặc hàm thuần — chúng bắt được lỗi cấu trúc
nhưng không bắt được lỗi hành vi: một route trả 200 cho tổ chức sai vẫn qua
được mọi phép thử tĩnh.

Dựng cơ sở dữ liệu tạm riêng, không đụng `socialflow.db` thật.
"""

import base64
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

SECRET = "khoa-thu-nghiem-du-dai-32-ky-tu-tro-len"
os.environ["SSO_SESSION_SECRET"] = SECRET
os.environ.setdefault("FLORAOS_CORE_BASE_URL", "http://localhost:3100/api/v1")

SCRATCH = Path(tempfile.mkdtemp(prefix="hai-to-chuc-"))
DB = SCRATCH / "socialflow.db"

# PHẢI đặt TRƯỚC mọi import của backend: cuối main.py có init_asset_tables()
# và run_migrations() chạy ngay lúc nạp mô-đun, nên gán lại hằng số sau khi
# import là đã muộn — cơ sở dữ liệu thật đã bị chạm.
os.environ["SOCIALFLOW_DATABASE_PATH"] = str(DB)

import asset_inventory  # noqa: E402
import brand_kit  # noqa: E402
import core_token_store  # noqa: E402
import facebook_page_store  # noqa: E402
import main  # noqa: E402
import migrations  # noqa: E402
import video_jobs  # noqa: E402

for mod in (main, asset_inventory, video_jobs, core_token_store, facebook_page_store):
    assert getattr(mod, "DATABASE_PATH", str(DB)) == str(DB), f"{mod.__name__} trỏ sai DB"
assert brand_kit.DB_PATH == DB, "brand_kit trỏ sai DB"

from fastapi.testclient import TestClient  # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def ky(org, sub="nguoi-dung"):
    now = int(time.time())
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64(json.dumps({"sub": sub, "org": org, "email": f"{sub}@vi-du.test",
                           "iat": now, "exp": now + 900}, separators=(",", ":")).encode())
    sig = hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"


ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"

# Đúng thứ tự lifespan() của main.py: tạo bảng TRƯỚC, vá cột SAU. Đảo lại thì
# run_migrations() bỏ qua bảng chưa có ("table missing") và các cột của Đợt 3
# không bao giờ được thêm.
main.init_db()
brand_kit.init_brand_tables()
asset_inventory.init_asset_tables()
migrations.run_migrations(str(DB))

conn = sqlite3.connect(str(DB))
conn.execute(
    "INSERT INTO posts (platform, content_type, content, status, organization_id) "
    "VALUES ('linkedin', 'post', 'bai cua A', 'draft', ?)", (ORG_A,))
id_a = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
conn.execute(
    "INSERT INTO posts (platform, content_type, content, status, organization_id) "
    "VALUES ('linkedin', 'post', 'bai cua B', 'draft', ?)", (ORG_B,))
id_b = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
conn.commit()
conn.close()

client = TestClient(main.app)
ck_a = {"floraos_sso": ky(ORG_A)}
ck_b = {"floraos_sso": ky(ORG_B)}

# ── 1. Không có chứng thư ───────────────────────────────────────────────────
check("GET /api/posts không cookie → 401", client.get("/api/posts").status_code, 401)
check("GET /api/pipeline/queue không cookie → 401",
      client.get("/api/pipeline/queue").status_code, 401)
check("GET /api/brand/config không cookie → 401",
      client.get("/api/brand/config").status_code, 401)
check("GET /api/assets không cookie → 401", client.get("/api/assets").status_code, 401)
check("POST /api/pipeline/approve không cookie → 401",
      client.post(f"/api/pipeline/approve/{id_a}").status_code, 401)

# ── 2. Route trung tính vẫn mở ──────────────────────────────────────────────
check("GET /api/health mở", client.get("/api/health").status_code, 200)

# ── 3. JWT hỏng ─────────────────────────────────────────────────────────────
check("JWT sai chữ ký → 401",
      client.get("/api/posts", cookies={"floraos_sso": ky(ORG_A) + "x"}).status_code, 401)
check("JWT không có org → 401",
      client.get("/api/posts", cookies={"floraos_sso": ky(None)}).status_code, 401)

# ── 4. Mỗi tổ chức chỉ thấy bài của mình ────────────────────────────────────
ra_a = client.get("/api/posts", cookies=ck_a)
ra_b = client.get("/api/posts", cookies=ck_b)
check("A đọc được", ra_a.status_code, 200)
check("A chỉ thấy bài của A",
      [p["content"] for p in ra_a.json()["posts"]], ["bai cua A"])
check("B chỉ thấy bài của B",
      [p["content"] for p in ra_b.json()["posts"]], ["bai cua B"])

# ── 5. Thao tác theo ID không vượt được ranh giới ──────────────────────────
check("B KHÔNG duyệt được bài của A → 404",
      client.post(f"/api/pipeline/approve/{id_a}", cookies=ck_b).status_code, 404)
check("B KHÔNG từ chối được bài của A → 404",
      client.post(f"/api/pipeline/reject/{id_a}", cookies=ck_b).status_code, 404)
check("B KHÔNG xoá được bài của A → 404",
      client.delete(f"/api/posts/{id_a}", cookies=ck_b).status_code, 404)
check("B KHÔNG sửa được bài của A → 404",
      client.put(f"/api/posts/{id_a}", json={"content": "cuop"}, cookies=ck_b).status_code, 404)
check("A duyệt được bài của chính A → 200",
      client.post(f"/api/pipeline/approve/{id_a}", cookies=ck_a).status_code, 200)

conn = sqlite3.connect(str(DB))
trang_thai = dict(
    conn.execute("SELECT id, status FROM posts WHERE id IN (?, ?)", (id_a, id_b)).fetchall()
)
conn.close()
check("bài của A thành approved", trang_thai[id_a], "approved")
check("bài của B KHÔNG bị đụng tới", trang_thai[id_b], "draft")

# ── 6. Bài tạo mới mang đúng tổ chức ────────────────────────────────────────
tao = client.post("/api/posts", cookies=ck_b,
                  json={"platform": "linkedin", "content_type": "post", "content": "moi cua B"})
check("B tạo bài → 200", tao.status_code, 200)
if tao.status_code == 200:
    conn = sqlite3.connect(str(DB))
    org_moi = conn.execute("SELECT organization_id FROM posts WHERE id = ?",
                           (tao.json()["id"],)).fetchone()[0]
    conn.close()
    check("bài mới mang tổ chức từ TOKEN, không từ thân yêu cầu", org_moi, ORG_B)

# ── 7. organization_id bên gọi tự khai bị BỎ QUA ───────────────────────────
cuop = client.post("/api/accounts", cookies=ck_b,
                   json={"platform": "tiktok", "username": "b", "password": "x" * 12,
                         "organization_id": ORG_A})
check("POST /api/accounts → 200", cuop.status_code, 200)
if cuop.status_code == 200:
    conn = sqlite3.connect(str(DB))
    org_tk = conn.execute(
        "SELECT organization_id FROM accounts WHERE platform='tiktok'").fetchone()[0]
    conn.close()
    check("organization_id tự khai bị bỏ qua, dùng tổ chức trong token", org_tk, ORG_B)

# ── 8. Brand kit: mỗi tổ chức một hàng, cột core làm chủ bị khoá ────────────
ba = client.get("/api/brand/config", cookies=ck_a)
bb = client.get("/api/brand/config", cookies=ck_b)
check("brand A đọc được", ba.status_code, 200)
check("brand B đọc được", bb.status_code, 200)
if ba.status_code == 200 and bb.status_code == 200:
    check("hai tổ chức hai hàng brand khác nhau", ba.json()["id"] != bb.json()["id"], True)
check("sửa cột core làm chủ bị từ chối → 409",
      client.put("/api/brand/config", cookies=ck_a,
                 json={"primary_color": "#123456"}).status_code, 409)
check("sửa cột phần riêng vẫn được → 200",
      client.put("/api/brand/config", cookies=ck_a,
                 json={"brand_name": "Tiem hoa A"}).status_code, 200)

# ── 9. Asset: khử trùng lặp theo tổ chức ───────────────────────────────────
a1 = asset_inventory.register_asset(asset_type="image", source="t", sha256="cungmotbam",
                                    organization_id=ORG_A)
check("A đăng ký asset", bool(a1["asset_id"]), True)
check("B KHÔNG thấy asset của A qua băm",
      asset_inventory.get_asset_by_sha256("cungmotbam", organization_id=ORG_B), None)
check("A thấy asset của chính mình qua băm",
      asset_inventory.get_asset_by_sha256("cungmotbam", organization_id=ORG_A)["asset_id"],
      a1["asset_id"])
check("B KHÔNG đọc được asset của A theo id",
      asset_inventory.get_asset(a1["asset_id"], organization_id=ORG_B), None)
check("B KHÔNG lưu trữ được asset của A",
      asset_inventory.archive_asset(a1["asset_id"], organization_id=ORG_B), False)

# ── 10. Facebook Page không mượn được của tổ chức khác ─────────────────────
os.environ["FB_PAGE_ID"] = "page-cua-A"
os.environ["FB_PAGE_ACCESS_TOKEN"] = "token-cua-A"
os.environ["FB_PAGE_OWNER_ORG_ID"] = ORG_A
check("A thừa hưởng Page trong .env",
      facebook_page_store.doc_page_facebook(ORG_A), ("page-cua-A", "token-cua-A"))
check("B KHÔNG thừa hưởng Page trong .env",
      facebook_page_store.doc_page_facebook(ORG_B), None)
os.environ.pop("FB_PAGE_OWNER_ORG_ID")
check("không ai thừa hưởng khi FB_PAGE_OWNER_ORG_ID để trống",
      facebook_page_store.doc_page_facebook(ORG_A), None)

print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 62)
shutil.rmtree(SCRATCH, ignore_errors=True)
raise SystemExit(1 if FAIL else 0)
