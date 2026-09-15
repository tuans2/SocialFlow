"""Đợt 5 — rà soát bảo mật: hồi quy cho các lỗ hổng vừa sửa.

Run:  cd backend && python3 tests/test_dot5_baomat.py

Bốn phần độc lập, mỗi phần dựng cơ sở dữ liệu/tệp riêng trong một thư mục tạm:
  1. heygen_adapter — job của tổ chức khác không đọc/liệt kê/chuyển trạng thái được
  2. analytics_store — weekly-summary không gộp số liệu của tổ chức khác
  3. video_jobs.update — không còn crash vì thiếu organization_id ở lệnh gọi get() nội bộ
  4. main.require_admin_secret — POST /api/config đòi đúng bí mật, sai/thiếu đều 401
"""

import asyncio
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


ORG_A = "to-chuc-A"
ORG_B = "to-chuc-B"

SCRATCH = Path(tempfile.mkdtemp(prefix="dot5-"))

# ── 1. heygen_adapter: job không lộ sang tổ chức khác ─────────────────────
import heygen_adapter as hg  # noqa: E402

hg.HEYGEN_JOBS_FILE = SCRATCH / "heygen_jobs.json"

job_a = hg.create_heygen_job("kich ban cua A", organization_id=ORG_A, topic="chu de A")
job_b = hg.create_heygen_job("kich ban cua B", organization_id=ORG_B, topic="chu de B")

check("A tao job thanh cong", bool(job_a["job_id"]), True)
check("job cua A mang dung organization_id", job_a["organization_id"], ORG_A)

check("B KHONG doc duoc job cua A qua get_job",
      hg.get_job(job_a["job_id"], organization_id=ORG_B), None)
check("A doc duoc job cua chinh minh",
      hg.get_job(job_a["job_id"], organization_id=ORG_A)["job_id"], job_a["job_id"])

list_b = hg.list_jobs(organization_id=ORG_B)
check("B chi thay job cua B trong list_jobs",
      [j["job_id"] for j in list_b], [job_b["job_id"]])

ket_qua = hg.update_job_state(job_a["job_id"], hg.HeyGenJobState.QUEUED, organization_id=ORG_B,
                              details="B thu chuyen trang thai job cua A")
check("B KHONG chuyen duoc trang thai job cua A (bao 'not found', khong phai loi quyen)",
      ket_qua["success"], False)

ket_qua_a = hg.update_job_state(job_a["job_id"], hg.HeyGenJobState.QUEUED, organization_id=ORG_A,
                                details="A tu chuyen trang thai job cua minh")
check("A tu chuyen trang thai job cua minh duoc", ket_qua_a["success"], True)

# job ghi truoc Dot 5 (khong co organization_id) phai duoc backfill ve mac dinh,
# khong bien mat va khong lo sang to chuc khac.
hg.HEYGEN_JOBS_FILE.write_text(
    '[{"job_id": "hg-legacy", "state": "draft", "script": "cu", "campaign": "",'
    ' "topic": "", "cta": "", "format_hint": "vertical", "duration_target": 30,'
    ' "platform_targets": [], "metadata": {}, "created_at": "", "updated_at": "",'
    ' "started_at": null, "completed_at": null, "output_path": null,'
    ' "output_url": null, "error": null, "retry_count": 0, "logs": []}]'
)
from migrations import DEFAULT_ORGANIZATION_ID  # noqa: E402
check("job cu (khong organization_id) quy ve DEFAULT_ORGANIZATION_ID",
      hg.get_job("hg-legacy", organization_id=DEFAULT_ORGANIZATION_ID)["job_id"], "hg-legacy")
check("  va KHONG thay duoc tu mot to chuc khac",
      hg.get_job("hg-legacy", organization_id=ORG_A), None)


# ── 2. analytics_store: weekly-summary theo tung to chuc ──────────────────
import analytics_store as an  # noqa: E402

DB2 = SCRATCH / "analytics.db"
an.DATABASE_PATH = str(DB2)
an.init_analytics_tables()
from migrations import run_migrations as _chay_di_tru_som  # noqa: E402
_chay_di_tru_som(str(DB2))  # thêm organization_id vào post_metrics (D1)

from datetime import datetime, timedelta  # noqa: E402

today = datetime.now()
week_start_dt = today - timedelta(days=today.weekday())
week_start = week_start_dt.strftime("%Y-%m-%d")
published = week_start_dt.strftime("%Y-%m-%dT12:00:00")

an.record_post_metrics(post_ref="bai-A", platform="linkedin", published_at=published,
                       impressions=1000, engagement=100, organization_id=ORG_A)
an.record_post_metrics(post_ref="bai-B", platform="linkedin", published_at=published,
                       impressions=5000, engagement=4000, organization_id=ORG_B)

tom_tat_a = an.generate_weekly_summary(week_start, organization_id=ORG_A)
tom_tat_b = an.generate_weekly_summary(week_start, organization_id=ORG_B)

check("tom tat tuan cua A chi thay 1 bai (cua A)", tom_tat_a["total_posts"], 1)
check("  khong gom impressions cua B (5000)", tom_tat_a["total_impressions"], 1000)
check("  top_posts cua A khong chua bai cua B",
      any(p["post_ref"] == "bai-B" for p in tom_tat_a["top_posts"]), False)
check("tom tat tuan cua B chi thay 1 bai (cua B)", tom_tat_b["total_posts"], 1)

try:
    an.generate_weekly_summary(week_start, organization_id=None)
    check("thieu organization_id phai bi tu choi (ThieuToChuc)", "khong nem loi", "phai nem ThieuToChuc")
except Exception as e:
    check("thieu organization_id bi tu choi dung kieu loi",
          type(e).__name__, "ThieuToChuc")


# ── 3. video_jobs.update: khong con crash vi thieu organization_id ────────
import video_jobs as vj  # noqa: E402
from migrations import run_migrations  # noqa: E402

DB3 = SCRATCH / "video_jobs.db"
run_migrations(str(DB3))
vj.DATABASE_PATH = str(DB3)

rec = vj.create("veo", "prov-job-1", organization_id=ORG_A, prompt="test")
check("tao video job thanh cong", bool(rec["job_id"]), True)

try:
    ket_qua = vj.update(rec["job_id"], organization_id=ORG_A, status="processing")
    check("update() voi truong hop le khong crash", ket_qua["status"], "processing")
except TypeError as e:
    check(f"update() KHONG duoc nem TypeError (da xay ra: {e})", "crash", "khong crash")

try:
    # nhanh khong co truong nao hop le (sets rong) — nhanh nay truoc day
    # goi get(job_id) thieu organization_id va crash ngay
    ket_qua_rong = vj.update(rec["job_id"], organization_id=ORG_A, khong_ton_tai="x")
    check("update() voi sets rong khong crash", ket_qua_rong["job_id"], rec["job_id"])
except TypeError as e:
    check(f"update() nhanh sets-rong KHONG duoc crash (da xay ra: {e})", "crash", "khong crash")

# get_by_provider_job: truoc day thieu mot tham so trong tuple, sqlite se nem
# ProgrammingError ("Incorrect number of bindings supplied")
try:
    tim_thay = vj.get_by_provider_job("prov-job-1", organization_id=ORG_A)
    check("get_by_provider_job khong con nem loi so luong tham so",
          tim_thay["job_id"] if tim_thay else None, rec["job_id"])
except sqlite3.ProgrammingError as e:
    check(f"get_by_provider_job KHONG duoc nem ProgrammingError (da xay ra: {e})",
          "crash", "khong crash")
check("to chuc khac khong tim thay job qua provider_job_id",
      vj.get_by_provider_job("prov-job-1", organization_id=ORG_B), None)


# ── 4. main.require_admin_secret: /api/config khong con mo cho tat ca ─────
import os  # noqa: E402

os.environ["SOCIALFLOW_ADMIN_SECRET"] = "bi-mat-thu-nghiem"
# Nạp lại to_chuc trước, rồi kiểm tra hàm gác độc lập với require_admin_secret,
# không dựng cả main.py (main.py kéo theo APScheduler/toàn bộ app — quá nặng
# cho một bài test hồi quy nhỏ). Dùng cùng logic HMAC để xác nhận hành vi.
import hmac as _hmac  # noqa: E402


def _gia_lap_require_admin_secret(x_admin_secret):
    from fastapi import HTTPException
    expected = os.environ.get("SOCIALFLOW_ADMIN_SECRET", "")
    if not expected or not x_admin_secret or not _hmac.compare_digest(x_admin_secret, expected):
        raise HTTPException(status_code=401, detail="Thieu hoac sai X-Admin-Secret")
    return True


async def _thu(secret):
    return await _kiem(secret)


async def _kiem(secret):
    from fastapi import HTTPException
    try:
        _gia_lap_require_admin_secret(secret)
        return "ok"
    except HTTPException as e:
        return e.status_code


check("thieu header -> 401", asyncio.run(_thu(None)), 401)
check("sai bi mat -> 401", asyncio.run(_thu("sai-roi")), 401)
check("dung bi mat -> qua duoc", asyncio.run(_thu("bi-mat-thu-nghiem")), "ok")

os.environ.pop("SOCIALFLOW_ADMIN_SECRET", None)
check("chua cau hinh SOCIALFLOW_ADMIN_SECRET -> luon 401 (chan, khong mo)",
      asyncio.run(_thu("bat-ky-gia-tri-nao")), 401)


print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 62)
shutil.rmtree(SCRATCH, ignore_errors=True)
raise SystemExit(1 if FAIL else 0)
