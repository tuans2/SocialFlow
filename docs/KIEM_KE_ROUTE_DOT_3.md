# Kiểm kê route — Đợt 3 / E2

**Sinh bằng mã, không gõ tay** — `scripts/kiem_ke_route.py`. Phân nhóm là
quyết định của con người, nằm trong chính script đó.

Sửa route nào thì tích ô route đó. Bảng này là thứ E8 đối chiếu từng dòng —
"đã sửa xong" mà không có bảng thì không nhận.

## Bốn nhóm

| Nhóm | Nghĩa | Số route |
|---|---|---|
| **a** | Theo tổ chức — `organization_id` lấy từ token đã xác minh | **66** |
| **b** | Trung tính — không đọc/ghi dữ liệu của tổ chức nào | 7 |
| **c** | Quản trị máy chủ — đọc/ghi biến môi trường, không phải dữ liệu tổ chức | 2 |
| **d** | Chạy nền — điểm vào của APScheduler, không phải route HTTP | 0 |
| | **Tổng** | **75** |

Cột "nhắc org" chỉ nói hàm có nhắc tới `organization_id` trong 22 dòng đầu —
KHÔNG nói nó lọc đúng. Nhiều route nhắc tới nó vì NHẬN từ query của bên gọi,
thứ mà E7 phải bỏ.

## Nhóm a — theo tổ chức

| ✓ | Tệp | Method | Đường dẫn | Nhắc org |
|---|---|---|---|---|
| [ ] | `main.py` | POST | `/api/pipeline/run` | có |
| [ ] | `main.py` | GET | `/api/pipeline/status` | có |
| [ ] | `main.py` | GET | `/api/pipeline/queue` | — |
| [ ] | `main.py` | POST | `/api/pipeline/approve/{post_id}` | — |
| [ ] | `main.py` | POST | `/api/pipeline/reject/{post_id}` | — |
| [ ] | `main.py` | POST | `/api/pipeline/publish/{post_id}` | — |
| [ ] | `main.py` | GET | `/api/pipeline/signals` | có |
| [ ] | `main.py` | GET | `/api/pipeline/analytics` | có |
| [ ] | `main.py` | GET | `/api/accounts` | có |
| [ ] | `main.py` | POST | `/api/accounts` | có |
| [ ] | `main.py` | POST | `/api/accounts/{platform}/login` | có |
| [ ] | `main.py` | POST | `/api/accounts/{platform}/check` | có |
| [ ] | `main.py` | DELETE | `/api/accounts/{platform}` | có |
| [ ] | `main.py` | POST | `/api/generate` | — |
| [ ] | `main.py` | POST | `/api/generate-carousel` | — |
| [ ] | `main.py` | POST | `/api/generate-video` | — |
| [ ] | `main.py` | GET | `/api/video-status/{task_id}` | — |
| [ ] | `main.py` | GET | `/api/video-jobs` | — |
| [ ] | `main.py` | POST | `/api/generate-reel` | — |
| [ ] | `main.py` | POST | `/api/generate-avatar` | — |
| [ ] | `main.py` | POST | `/api/generate-avatar-with-script` | — |
| [ ] | `main.py` | POST | `/api/upload-avatar-photo` | — |
| [ ] | `main.py` | POST | `/api/upload-audio` | — |
| [ ] | `main.py` | GET | `/api/posts` | — |
| [ ] | `main.py` | POST | `/api/posts` | — |
| [ ] | `main.py` | PUT | `/api/posts/{post_id}` | — |
| [ ] | `main.py` | DELETE | `/api/posts/{post_id}` | — |
| [ ] | `main.py` | POST | `/api/posts/{post_id}/publish` | — |
| [ ] | `main.py` | POST | `/api/upload` | — |
| [ ] | `main.py` | POST | `/api/accounts/{platform}/login-extended` | có |
| [ ] | `main.py` | POST | `/api/posts/{post_id}/publish-extended` | — |
| [ ] | `analytics_store.py` | POST | `/api/analytics/metrics` | có |
| [ ] | `analytics_store.py` | GET | `/api/analytics/platform/{platform}` | có |
| [ ] | `analytics_store.py` | GET | `/api/analytics/top-content-types` | có |
| [ ] | `analytics_store.py` | GET | `/api/analytics/pillars` | có |
| [ ] | `analytics_store.py` | GET | `/api/analytics/weekly-summary` | có |
| [ ] | `heygen_routes.py` | POST | `/api/heygen/jobs` | — |
| [ ] | `heygen_routes.py` | GET | `/api/heygen/jobs` | — |
| [ ] | `heygen_routes.py` | GET | `/api/heygen/jobs/{job_id}` | — |
| [ ] | `heygen_routes.py` | POST | `/api/heygen/jobs/{job_id}/generate` | — |
| [ ] | `heygen_routes.py` | POST | `/api/heygen/jobs/{job_id}/register-output` | — |
| [ ] | `heygen_routes.py` | POST | `/api/heygen/jobs/{job_id}/distribute` | — |
| [ ] | `heygen_routes.py` | GET | `/api/heygen/assets` | — |
| [ ] | `heygen_routes.py` | GET | `/api/heygen/assets/{asset_id}` | — |
| [ ] | `heygen_routes.py` | GET | `/api/heygen/queue` | — |
| [ ] | `heygen_routes.py` | POST | `/api/heygen/queue/{queue_id}/approve` | — |
| [ ] | `heygen_routes.py` | POST | `/api/heygen/queue/{queue_id}/publish` | — |
| [ ] | `assets_routes.py` | POST | `/api/assets/upload` | — |
| [ ] | `assets_routes.py` | GET | `/api/assets` | — |
| [ ] | `assets_routes.py` | GET | `/api/assets/{asset_id}` | — |
| [ ] | `assets_routes.py` | PATCH | `/api/assets/{asset_id}` | — |
| [ ] | `assets_routes.py` | DELETE | `/api/assets/{asset_id}` | — |
| [ ] | `assets_routes.py` | POST | `/api/assets/{asset_id}/to-post` | — |
| [ ] | `visual_content_routes.py` | POST | `/api/visual/generate` | — |
| [ ] | `visual_content_routes.py` | POST | `/api/visual/auto-generate` | — |
| [ ] | `visual_content_routes.py` | GET | `/api/visual/briefs` | — |
| [ ] | `visual_content_routes.py` | GET | `/api/visual/stats` | — |
| [ ] | `openclaw_bridge.py` | POST | `/api/openclaw/publish` | — |
| [ ] | `openclaw_bridge.py` | POST | `/api/openclaw/batch` | — |
| [ ] | `openclaw_bridge.py` | GET | `/api/openclaw/status` | — |
| [ ] | `openclaw_bridge.py` | GET | `/api/openclaw/history` | — |
| [ ] | `brand_kit.py` | GET | `/api/brand/config` | có |
| [ ] | `brand_kit.py` | PUT | `/api/brand/config` | có |
| [ ] | `brand_kit.py` | POST | `/api/brand/logo` | có |
| [ ] | `brand_kit.py` | GET | `/api/brand/colors` | có |
| [ ] | `brand_kit.py` | GET | `/api/brand/image-prompt-context` | có |

## Nhóm b — trung tính

| Tệp | Method | Đường dẫn |
|---|---|---|
| `main.py` | GET | `/` |
| `main.py` | GET | `/api/health` |
| `main.py` | GET | `/api/video-providers` |
| `main.py` | GET | `/api/avatar-catalog` |
| `heygen_routes.py` | GET | `/api/heygen/platform-rules` |
| `heygen_routes.py` | GET | `/api/heygen/platform-rules/{platform}` |
| `visual_content_routes.py` | GET | `/api/visual/types` |

## Nhóm c — quản trị máy chủ

| Tệp | Method | Đường dẫn |
|---|---|---|
| `main.py` | GET | `/api/config` |
| `main.py` | POST | `/api/config` |

## Ghi chú phân nhóm

- `/api/config` (nhóm c) đọc/ghi biến môi trường của máy chủ — khoá API, cấu
  hình provider. Không phải dữ liệu tổ chức, nên không gác bằng tổ chức; nó
  cần một cổng quản trị riêng — ghi vào nợ kỹ thuật, không xử lý ở Đợt 3.
- `platform-rules`, `video-providers`, `avatar-catalog`, `visual/types` trả
  năng lực của nhà cung cấp, giống nhau với mọi tổ chức.
- `/api/health` và `/` phải mở, nếu không thì không giám sát được và không
  tải được giao diện để đăng nhập.
- `analytics_store.py` cũng khai 5 route — lọt khi chỉ đếm `main.py` và các
  tệp có chữ `routes` trong tên.
