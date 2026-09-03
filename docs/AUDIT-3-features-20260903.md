# Audit 3 chức năng Dashboard — Carousel / Video-Reel / Avatar
Ngày: 2026-09-03 · Repo: `~/SocialFlow`

## Tóm tắt

| # | Chức năng | UI | Backend | Chạy được thật? |
|---|---|---|---|---|
| 1 | 🎠 Carousel Maker | ✅ đầy đủ | ✅ `/api/generate-carousel` | **PASS (generate)** / **FAIL (publish IG)** |
| 2 | 🎬 Video/Reel Maker | ⚠️ thiếu Save | ⚠️ `/api/generate-reel`, `/api/generate-video` | **FAIL — không có `KLING_API_KEY`** |
| 3 | 🧑‍💼 Avatar Videos | ⚠️ thiếu Save | ⚠️ `/api/generate-avatar` | **FAIL — không có `KLING_API_KEY`** |

Cả 3 tab đều đã được render (`frontend/index.html:2367-2369`), không có tab chết.

---

## 1. Carousel Maker — hoạt động, nhưng chết ở khâu publish

### Đường đi
`CarouselMaker` (index.html:1178) → `POST /api/generate-carousel` (main.py:1162)
→ `generate_content_ai()` sinh JSON slides → loop `generate_image()` từng slide
→ file vào `backend/uploads/carousel_<ts>_slide_N.png` → Save Draft gọi `POST /api/posts`.

### Test đã chạy
Mock `generate_content_ai` + `generate_image` trên bản copy cô lập của backend (DB + uploads riêng, DB thật không đụng vào — `max(id)` vẫn = 47):

| Test | Kết quả |
|---|---|
| `/api/generate-carousel` 3 slides | PASS — `success: true`, đủ 3 slide |
| Slide schema | PASS — `slide_number, headline, body, image_path, image_url` |
| Đặt tên + ghi file uploads | PASS — `carousel_<ts>_slide_N.png` |
| Save Draft → `/api/posts` | PASS — `media_paths` lưu đúng JSON array |
| `resolve_media_path()` với `"uploads/..."` | PASS |

### Bằng chứng chạy thật của anh hôm nay
`backend/uploads/` có 5 file `carousel_20260903_161053_slide_1..5.png` (1.1–1.8 MB/file) → **generation thật với `gpt-image-1.5` đã chạy được**, bug `quality` enum hôm qua đã hết.

### ❌ Nhưng publish thì fail
```
post 46  instagram carousel  failed
  Timeout 10000ms — waiting for locator("input[type=\"file\"]")
post 47  instagram carousel  failed
  Target page, context or browser has been closed
```
`InstagramAutomation.post()` (automation.py:332) không tìm được nút "New post" / `input[type=file]`. Đây là **selector Instagram lỗi thời hoặc session hết hạn** (`sessions/instagram_session.json` từ 02/09), không phải lỗi path — path resolve OK.

### ❌ Lỗ hổng gate ảnh
```
post 45  facebook  carousel  published  media_paths = NULL
```
`media_utils.IMAGE_REQUIRED_CONTENT_TYPES = {"post"}` → carousel **không** bị gate. Kết quả: một carousel Facebook đã publish thành công **mà không có ảnh nào**. Carousel mà không ảnh thì vô nghĩa — nên đưa `carousel` vào danh sách bắt buộc ảnh.

### ⚠️ Vấn đề nhỏ
- Nút "💾 Save Draft" và "🚀 Save & Post" gọi **cùng một hàm** `saveCarousel` (index.html:1310-1311) — "Save & Post" không post gì cả.
- Nếu 1 slide fail, response vẫn `success: true`, slide đó chỉ có `error` → Save Draft âm thầm lưu carousel thiếu slide.
- UI ghi "DALL-E 3 (~$0.04/slide)" nhưng thực tế đang dùng `gpt-image-1.5` quality `high` → giá thật cao hơn nhiều.
- Không có bước overlay `headline`/`body` lên ảnh — prompt yêu cầu "Do NOT include any text in the image", nên slide xuất ra là **ảnh nền trơn không chữ**. Text chỉ hiện trong preview HTML, không nằm trong file PNG đem đăng.

---

## 2 & 3. Video/Reel Maker + Avatar Videos — chưa chạy được, cùng một nguyên nhân

### Nguyên nhân gốc: thiếu key
`.env` hiện có: `AI_PROVIDER, OPENAI_API_KEY, HEYGEN_EMAIL, HEYGEN_PASSWORD, HEADLESS, OLLAMA_*, IMAGE_MODEL, GEMINI_API_KEY`.
**Không có `KLING_API_KEY`** — và `.env.example` cũng không liệt kê biến này.

Test thật bằng TestClient:
```
KLING key set: False
POST /api/generate-video   → 200 {"success": false, "error": "400: Kling API key not configured..."}
POST /api/generate-avatar  → 200 {"success": false, "error": "400: Kling API key not configured"}
GET  /api/video-status/... → 200 {"success": false, "error": "400: Kling API key not configured"}
```

### Nguyên nhân thứ hai: integration Kling nhiều khả năng sai spec
Kể cả khi điền key, code hiện tại vẫn khó chạy:

| Trong code (`main.py:453-642`) | Thực tế Kling official API |
|---|---|
| `Authorization: Bearer {KLING_API_KEY}` | Kling dùng **JWT ký từ AccessKey + SecretKey**, hết hạn ~30 phút, không phải API key tĩnh |
| `https://api.klingai.com/v1` | Endpoint chính thức thường là `api-singapore.klingai.com` |
| `GET /videos/tasks/{task_id}` | Query task theo **loại**: `GET /v1/videos/text2video/{id}` |
| `POST /videos/avatar` | Không có route này — chỉ có `/v1/videos/lip-sync` |

→ Toàn bộ `class KlingAI` là code viết theo phỏng đoán, **chưa từng được gọi thật lần nào**. Cần viết lại theo doc chính thức nếu muốn dùng.

### Vấn đề chung của cả 2 tab: không có đường lưu kết quả
Grep toàn vùng `VideoMaker` (1330-1604) và `AvatarMaker` (1604-1955):
- **0 lần** gọi `api.post('/posts')` → video sinh ra **không bao giờ vào DB**
- prop `onCreated` được nhận nhưng **không bao giờ được gọi**
- không tải video về `uploads/`, chỉ `<video src={videoStatus.video_url}>` trỏ thẳng CDN Kling → link hết hạn là mất
- không có `content_type = 'reel'` nào được tạo từ UI

Nghĩa là dù key có hoạt động, pipeline vẫn **đứt** tại chỗ: generate → xem → mất. Không nối được vào Posts/Schedule/Publish.

### Riêng Avatar Videos: có 2 implementation, UI dùng nhầm cái chưa cấu hình
- `.env` có `HEYGEN_EMAIL` + `HEYGEN_PASSWORD`, `heygen_router` đã `include_router` (main.py:810), có đủ `/api/heygen/jobs`, `/generate`, `/distribute`, asset inventory…
- Nhưng **UI tab Avatar gọi `/api/generate-avatar` (Kling)**, hoàn toàn không đụng tới `/api/heygen/*`.
- HeyGen path cũng chưa sẵn sàng: `backend/sessions/` chỉ có facebook/instagram/linkedin — **không có `heygen_session.json`** → `create_video()` sẽ trả `"Not logged in to HeyGen"`.
- `heygen_adapter.create_video()` còn có bug logic: cả 3 nhánh (không tìm thấy nút Generate, không tìm thấy ô script) đều `update_job_state(..., COMPLETED)` → job báo **hoàn thành trong khi chưa render gì**.

---

## Đề xuất ưu tiên

**P0 — sửa cái đang chảy máu**
1. Thêm `carousel` vào `IMAGE_REQUIRED_CONTENT_TYPES` để chặn publish carousel không ảnh (post 45).
2. Fix selector Instagram + re-login lấy session mới (post 46/47).

**P1 — quyết định hướng đi cho video**
3. Chọn một: (a) làm đúng Kling JWT auth theo doc chính thức, hoặc (b) bỏ Kling, dồn Avatar về HeyGen (đã có credential) và Reel về ffmpeg/slideshow local.
4. Thêm nút Save Draft + tải video về `uploads/` cho VideoMaker và AvatarMaker — nếu không thì 2 tab này vĩnh viễn là ngõ cụt.

**P2 — dọn**
5. Sửa "Save & Post" của Carousel để thật sự publish (hoặc bỏ nút).
6. Sửa `heygen_adapter` không đánh dấu COMPLETED khi automation không tìm thấy element.
7. Overlay `headline`/`body` lên ảnh slide (Pillow) — hiện slide xuất ra không có chữ.
8. Cập nhật label giá + `.env.example` (thêm `KLING_API_KEY`).

---

## Files đã đọc (không sửa gì)
`backend/main.py`, `backend/media_utils.py`, `backend/automation.py`, `backend/heygen_routes.py`, `backend/heygen_adapter.py`, `frontend/index.html`, `.env`, `.env.example`, `backend/socialflow.db` (read-only).

**Không có file nào bị thay đổi.** Test chạy trên bản copy cô lập ở `/tmp` của VM, đã xoá sau khi xong.
