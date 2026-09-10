# Đợt 3 — trạng thái thực thi

Kế hoạch đầy đủ: `floraos-core/docs/kien-truc/DOT_3_NOI_SOCIALFLOW.md`.
Bảng kiểm kê route: `docs/KIEM_KE_ROUTE_DOT_3.md`.

## Đã xong (mã + test chạy được không cần máy chủ)

| # | Việc | Ghi chú |
|---|---|---|
| E1 | Xác minh JWT `floraos_sso` | `sso_auth.py`, thư viện chuẩn, không thêm PyJWT. 32 ca |
| E2 | Kiểm kê 75 route | `scripts/kiem_ke_route.py` sinh bảng; 66 nhóm a · 7 trung tính · 2 quản trị |
| E3 | Cổng xác thực | `org_context.require_org`; router phụ gác ở mức router, `main.py` gác từng route |
| E4 | Lọc theo tổ chức | `posts` ở tầng route; `asset_inventory`/`media_store`/`video_jobs` ở tầng dữ liệu — 48 chỗ gọi |
| E5 | `video_jobs` | Cột + index + 6 hàm; bảng này lọt khỏi A4 |
| E6 | Facebook Page theo tổ chức | `facebook_page_store.py`; `.env` chỉ dùng cho tổ chức nêu ở `FB_PAGE_OWNER_ORG_ID` |
| E7 | Bỏ tổ chức mặc định | 13 tệp, 30 `COALESCE`; thay bằng `to_chuc.bat_buoc()` |
| F1 | Ánh xạ brand | `brand_dong_bo.py`, 32 ca |
| F2 | Client gọi core | `floraos_core.py`, hai đường xác thực |
| F3 | Đồng bộ brand khi đọc | TTL 5 phút; core sập thì dùng bản đã lưu, KHÔNG chặn |
| F4 | Khoá cột core làm chủ | `PUT /api/brand/config` trả 409 cho các cột ấy |
| F5 | `forbidden_styles` là ràng buộc | Vào cuối prompt của Creator |
| G1 | Token máy-gọi-máy theo tổ chức | `core_token_store.py`, mã hoá Fernet |
| G2 | Báo chi phí về core | `usage_bao_cao.py`; ảnh + video; không bao giờ ném |
| G3 | Script đối chiếu | `scripts/doi_chieu_usage.py` |
| H1 | Script gán tổ chức | `scripts/gan_to_chuc.py`, mặc định xem trước, idempotent |

## Chưa xong — cần máy thật

| # | Việc | Vì sao chưa |
|---|---|---|
| E8 | Test hai tổ chức đầu-cuối | Cần máy chủ chạy thật; sandbox không có `fastapi`/`httpx` |
| H1 | **CHẠY** script gán tổ chức | Cần UUID thật của AVI GIFT tại thời điểm cắt |
| H2 | Đổi tên tệp phiên Playwright | Sau H1; nếu không, ba tài khoản phải đăng nhập lại |
| H3 | Siết CORS | Cần biết origin thật (chờ quyết định domain, Nhóm C) |
| H4 | Nghiệm thu đầu-cuối | Sau H1–H3 |

## Trạng thái trung gian — đọc trước khi chạy

Sau E7, **SocialFlow chưa dùng được** cho tới khi chạy H1: dữ liệu hiện tại
mang `organization_id` NULL, còn mọi truy vấn nay lọc chặt theo UUID thật. Danh
sách sẽ rỗng. Đây là có chủ đích, không phải hỏng.

Thứ tự chạy trên máy thật:

```
cd backend && source ../venv/bin/activate
python3 -c "from migrations import run_migrations; print(run_migrations())"
cd .. && python3 scripts/gan_to_chuc.py --org-id=<uuid AVI GIFT>
python3 scripts/gan_to_chuc.py --org-id=<uuid AVI GIFT> --that-su
```

Rồi cấp token máy-gọi-máy bên core và lưu lại:

```
python3 -c "import sys; sys.path.insert(0,'backend'); \
  from core_token_store import luu_token; \
  luu_token('<uuid AVI GIFT>', '<token tu POST /api/v1/integration-tokens>')"
```

Và thêm vào `backend/.env`:

```
SSO_SESSION_SECRET=<trùng với floraos-core>
FLORAOS_CORE_BASE_URL=http://localhost:3100/api/v1
FB_PAGE_OWNER_ORG_ID=<uuid AVI GIFT>
```

## Nợ ghi lại trong đợt này

- `brand_config` hàng mầm mang danh mục sản phẩm của thương hiệu gốc
  (InBharat AI). Sau H1 nó thành brand kit của AVI GIFT — cần rà lại nội dung.
- 45 bài đăng và chỉ số lịch sử của thương hiệu gốc sẽ tính vào số liệu của
  AVI GIFT (quyết định Đ3-3, đã chấp nhận).
- `logo_asset_id` của core chưa ánh xạ sang `logo_path`: cần URL ký sẵn, cùng
  đường với Master Image, thuộc M04b — ngoài phạm vi Đợt 3.
- `/api/config` (nhóm c) đọc/ghi biến môi trường máy chủ, chưa có cổng quản
  trị riêng.
- Mã tính năng `social.image` / `social.video` chưa đăng ký trong bảng giá của
  core (`src/modules/usage/domain/pricing.ts`) — rơi vào giá mặc định.
