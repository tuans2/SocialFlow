# Phương án: Multi-Provider Video + Media Hub
SocialFlow · 2026-09-03 · trả lời 4 yêu cầu của anh

---

## 0. Ba phát hiện làm thay đổi phương án

Trước khi vào chi tiết, 3 điều em tìm ra khi khảo sát — chúng làm khối lượng công việc **giảm đáng kể** so với dự tính ban đầu:

**① Anh đã có sẵn key để chạy video, không cần mở tài khoản mới.**
`.env` đang có `GEMINI_API_KEY` (53 ký tự). Google Veo 3.1 chạy **trực tiếp bằng chính key Gemini đó**, qua REST:
```
POST https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-preview:predictLongRunning
Header: x-goog-api-key: $GEMINI_API_KEY
```
Không JWT, không AccessKey/SecretKey, không đăng ký thêm. Nghĩa là **Video/Reel Maker có thể sống lại trong hôm nay** mà không tốn một đồng setup nào.

**② Kho dữ liệu anh muốn — đã build xong 70%, chỉ là chưa ai cắm dây vào.**
`backend/asset_inventory.py` đã có sẵn bảng `assets` (17 cột: type, source, path, size, duration, aspect_ratio, campaign, topic, tags, metadata, state...) + `content_queue` + `campaigns`. `init_asset_tables()` đã được gọi ở `main.py:812`. Bảng **tồn tại thật trong DB** — và có **0 dòng**.

Lý do: chỉ `heygen_routes.py` và `visual_content_routes.py` gọi `register_asset()`, mà **cả hai đều không được UI đụng tới**. Toàn bộ Carousel / Create Content / 4 endpoint upload đều ghi thẳng vào `uploads/` rồi quên luôn.

→ Đây không phải việc "xây kho dữ liệu". Đây là việc **nối dây vào cái kho đã xây sẵn**. Rẻ hơn rất nhiều.

**③ HeyGen có API chính thức trả tiền theo dùng, tối thiểu $5 — và anh đã có tài khoản.**
`.env` có `HEYGEN_EMAIL` + `HEYGEN_PASSWORD`. Hiện `heygen_adapter.py` đang **lái browser bằng Playwright** để bấm nút trong editor HeyGen — cực kỳ dễ vỡ, và đúng như audit hôm qua: nó `update_job_state(COMPLETED)` cả khi không tìm thấy nút Generate. HeyGen có REST API pay-as-you-go từ $5, không cần subscription.

→ Bỏ browser automation cho HeyGen, đổi sang API. Xoá được ~250 dòng code mong manh nhất trong repo.

---

## 1. Branch strategy

### ⚠️ Vấn đề phải xử lý trước
Working tree đang **bẩn** và có tài sản chưa được bảo vệ:

```
Branch hiện tại: Them_Kenh_Facebook   (main ở phía sau)

 M .env.example, backend/agents/publisher.py, backend/automation.py,
   backend/automation_extended.py, backend/main.py, frontend/index.html, start.sh
?? backend/media_utils.py          ← toàn bộ quality gate hôm qua, CHƯA COMMIT
?? backend/facebook_graph.py       ← chưa commit
?? backend/manual_login.py         ← chưa commit
?? backend/tiktok-automation.py, zalo-oa-automation.py
?? 12 file .png debug (~5 MB rác)
```

`media_utils.py` là untracked — nếu có sự cố `git clean` thì mất trắng. **Commit trước, branch sau.**

### Phương án đề xuất

```bash
# B1. Dọn rác debug trước khi commit
cat >> .gitignore <<'EOF'
backend/debug_fb_*.png
backend/facebook_*.png
backend/*.instagram_backup
EOF

# B2. Chốt lại công việc đang dở trên branch hiện tại
git add .gitignore backend/media_utils.py backend/facebook_graph.py \
        backend/facebook_graph_setup.py backend/manual_login.py \
        backend/main.py backend/agents/publisher.py frontend/index.html .env.example
git commit -m "feat: image quality gate + media path resolver + FB Graph publishing"

# B3. Branch mới cho toàn bộ việc sắp làm
git checkout -b feat/media-hub
```

**Tên branch:** `feat/media-hub` — gom cả 3 việc (multi-provider, asset library, upload) vì chúng **dùng chung một bảng `assets`**, tách nhỏ sẽ phải merge chéo liên tục.

Nếu anh muốn tách nhỏ hơn, tách theo thứ tự phụ thuộc, không tách song song:
```
feat/media-hub-core      → assets schema + storage + upload API   (nền)
  └ feat/video-providers → provider abstraction + Veo + HeyGen    (dựa trên core)
     └ feat/media-ui     → Library tab + AssetPicker              (dựa trên cả hai)
```

**Quy tắc từ đây:** `main` chỉ nhận merge, không commit thẳng. Mỗi phase = 1 commit có thể revert độc lập.

---

## 2. Có nên bổ sung nền tảng ngoài Kling không? — **Có. Và không phức tạp, nếu làm đúng thứ tự.**

### Vì sao "có"
Kling hiện tại trong repo là **code viết theo phỏng đoán, chưa chạy lần nào** (audit hôm qua): sai auth (Bearer tĩnh thay vì JWT), sai base URL, route `/videos/avatar` không tồn tại. Sửa cho đúng Kling tốn công **ngang với** viết mới một provider khác — mà provider khác lại dùng key anh đã có sẵn.

Ngoài ra, phụ thuộc 1 nhà cung cấp video là rủi ro thật: Sora 2 của OpenAI đã đóng cửa tháng 3/2026. Nhà cung cấp video biến mất nhanh hơn nhà cung cấp text nhiều.

### Vì sao "không phức tạp"
**Chìa khoá là làm lớp abstraction TRƯỚC, rồi provider chỉ còn là plugin.** Cả 5 nhà cung cấp video đều cùng một hình dạng:

```
submit(prompt, opts) → job_id      # gửi việc, nhận id
poll(job_id)         → status      # hỏi xong chưa
fetch(job_id)        → video_url   # lấy file
```

Nên `backend/video_providers/base.py` chỉ cần định nghĩa 3 method đó, mỗi provider là **~80-120 dòng**. Thêm provider thứ 4, thứ 5 sau này là việc của một buổi chiều.

```
backend/video_providers/
├── base.py         VideoProvider ABC + JobResult dataclass   (~70 LOC)
├── registry.py     đăng ký + chọn provider + fallback chain  (~60 LOC)
├── veo.py          Google Veo 3.1                            (~110 LOC)
├── heygen.py       HeyGen REST API (avatar)                  (~120 LOC)
├── fal.py          fal.ai (tuỳ chọn — mở ra 450+ model)      (~100 LOC)
└── kling.py        Kling, viết lại đúng JWT (tuỳ chọn)       (~140 LOC)
```

`main.py` sau đó **không biết provider nào tồn tại**, chỉ gọi:
```python
provider = registry.get(request.provider or CONFIG["DEFAULT_VIDEO_PROVIDER"])
job = await provider.submit(prompt, duration=8, aspect_ratio="9:16")
```

### Nên thêm những gì — xếp theo tỷ lệ giá trị/công sức

| Ưu tiên | Provider | Dùng cho | Key | Công sức | Ghi chú |
|---|---|---|---|---|---|
| **P0** | **Google Veo 3.1** | text→video, image→video, **có audio gốc** | ✅ **đã có `GEMINI_API_KEY`** | ~4h | Chạy được ngay hôm nay. Có cả bản `fast` rẻ hơn. |
| **P0** | **HeyGen REST API** | Avatar Videos | ⚠️ cần đổi email/pass → API key ($5 PAYG) | ~5h | Thay browser automation. Avatar III ~$1/phút. |
| **P1** | **fal.ai** | mở khoá Kling, Seedance, Wan, LTX… qua 1 key | ❌ cần đăng ký mới | ~4h | 1 key = 450+ model. ~$0.04–0.20/giây. Đây là cách rẻ nhất để "có nhiều lựa chọn". |
| **P2** | Kling trực tiếp | nếu muốn giá gốc rẻ hơn qua aggregator | ❌ AccessKey+SecretKey | ~6h | Chỉ làm nếu volume đủ lớn để bù công JWT. |
| **P2** | **ffmpeg slideshow** | reel "0 đồng" từ ảnh carousel + TTS | ✅ không cần key | ~6h | Không phải AI video, nhưng đăng được ngay và miễn phí. Rất hợp cho reel dạng list/tips. |

### Khuyến nghị
**Làm P0 (Veo + HeyGen) trước, chốt lớp abstraction, rồi mới quyết P1.**
Lý do: sau khi có abstraction + 2 provider chạy thật, anh sẽ biết chính xác mình cần thêm gì. Thêm fal.ai lúc đó chỉ tốn ~4h. Còn nếu làm cả 5 ngay từ đầu thì đó là 5 lần đoán mò như Kling.

> ⚠️ Giá cả trên là con số tham khảo tại thời điểm 09/2026 từ blog so sánh, **cần kiểm tra lại trang pricing chính thức trước khi cam kết ngân sách**. Riêng cách gọi Veo bằng `GEMINI_API_KEY` thì em đã đọc trực tiếp từ doc của Google.

---

## 3. Kho dữ liệu tái sử dụng — **nối dây vào cái đã có, đừng xây mới**

### Hiện trạng
```
assets           : 0 dòng   ← bảng đã tồn tại, schema đủ dùng
content_queue    : 0 dòng
campaigns        : 0 dòng
backend/uploads/ : 17 file phẳng, không index, không metadata, không ai biết file nào của post nào
```
Mọi thứ sinh ra đều rơi vào `uploads/` rồi mất dấu. Ảnh carousel 1.5 MB × 5 hôm nay — lần sau muốn dùng lại phải mở Finder tìm bằng mắt.

### Việc cần làm

**3.1. Bổ sung 6 cột vào `assets` (không phá dữ liệu cũ)**

| Cột mới | Kiểu | Dùng để |
|---|---|---|
| `sha256` | TEXT | **chống trùng** — upload/sinh lại cùng file thì trỏ về asset cũ, không tốn đĩa |
| `origin` | TEXT | `generated` \| `uploaded` — phân biệt AI sinh vs user tải lên |
| `provider` | TEXT | `gpt-image-1.5` \| `veo-3.1` \| `heygen` \| `user` |
| `cost_usd` | REAL | biết mỗi tháng đốt bao nhiêu tiền, theo từng provider |
| `thumb_path` | TEXT | thumbnail để Library load nhanh, không tải 1.5 MB/ảnh |
| `parent_asset_id` | TEXT | truy vết: reel này sinh từ ảnh nào, variant 9:16 này cắt từ đâu |

Repo **chưa có migration system** (PRD gap #10). Đề xuất thêm `backend/migrations.py` tối giản — đọc `PRAGMA table_info`, thiếu cột nào thì `ALTER TABLE ADD COLUMN` cột đó, chạy lúc startup. ~40 dòng, idempotent, không cần Alembic.

**3.2. Cấu trúc thư mục có kỷ luật**
```
backend/uploads/
├── 2026/09/image/  ab3f9c...png      ← đặt tên theo sha256, không bao giờ trùng
├── 2026/09/video/  7d21e4...mp4
├── 2026/09/audio/  91c0af...mp3
└── _thumbs/        ab3f9c...jpg      ← 320px, sinh bằng Pillow (đã có trong requirements)
```
File cũ trong `uploads/` **giữ nguyên tại chỗ** — `media_utils.resolve_media_path()` đã có sẵn fallback tìm theo tên file, nên 45 post cũ vẫn publish được bình thường. Không migrate, không rủi ro.

**3.3. Nối dây vào mọi chỗ đang sinh file**

| File | Chỗ cần thêm `register_asset()` |
|---|---|
| `main.py` | `generate_image()` — mọi ảnh sinh ra |
| `main.py:1162` | `/api/generate-carousel` — từng slide + link `parent_asset_id` về nhau |
| `main.py:1257,1305` | video/reel sau khi provider trả file |
| `main.py:1446,1472,1631` | 3 endpoint upload |
| `brand_kit.py:146` | upload logo |
| `agents/creator.py` | ảnh do pipeline tự động sinh |

**3.4. API + UI**
```
GET    /api/assets              ?type=&origin=&campaign=&tag=&q=&limit=  (đã có list_assets, cần route)
GET    /api/assets/{id}
PATCH  /api/assets/{id}          đổi title/tags/campaign
DELETE /api/assets/{id}          soft delete (state='archived'), không xoá file
POST   /api/assets/{id}/reuse    tạo post mới từ asset có sẵn ← đây chính là "dùng nhiều lần"
```
Frontend: thêm tab **📚 Media Library** vào `Sidebar.navItems` (index.html:720), grid thumbnail + filter + search.

**Quan trọng nhất:** viết component `<AssetPicker />` dùng lại được, gắn vào **cả 4 chỗ** — CreateContent, CarouselMaker, VideoMaker, AvatarMaker. Đây mới là thứ biến kho dữ liệu từ "chỗ chứa" thành "chỗ dùng lại".

---

## 4. Upload của user — **Có, và đây là mắt xích đang thiếu để 3 tab kia sống được**

### Hiện trạng: 4 endpoint upload rời rạc, không cái nào lưu vết

| Endpoint | Vấn đề |
|---|---|
| `/api/upload` (main.py:1631) | không validate gì cả — **upload file gì cũng nhận** |
| `/api/upload-avatar-photo` (1446) | chỉ check `content_type` do client khai — client nói dối được |
| `/api/upload-audio` (1472) | comment ghi "be more permissive" → **không check gì** |
| `/api/brand/logo` (brand_kit.py:146) | riêng lẻ |

Cả 4 đều: không giới hạn dung lượng, không sniff nội dung thật, không hash, không `register_asset()`, không có endpoint nào liệt kê lại. Upload xong là mất dấu.

Và quan trọng: **không có đường nào để user upload video của chính mình** — nên dù không có Kling, anh vẫn không đăng được reel tự quay.

### Phương án: một endpoint hợp nhất

```
POST /api/assets/upload        multipart, nhận NHIỀU file một lần
```

Luồng xử lý:
```
nhận file
  → chặn theo dung lượng (ảnh 20MB / audio 50MB / video 500MB), stream chunk, không nạp hết vào RAM
  → sniff magic bytes để biết loại THẬT (không tin content_type client gửi)
  → tính sha256 khi stream
  → nếu sha256 đã có trong assets → trả asset cũ, KHÔNG ghi file lần hai
  → lưu vào uploads/{yyyy}/{mm}/{type}/{sha}.{ext}
  → bóc metadata: ảnh → kích thước (Pillow); video/audio → duration + aspect (ffprobe)
  → sinh thumbnail
  → register_asset(origin='uploaded', ...)
  → trả về {asset_id, url, thumb_url, dedup: true/false}
```

4 endpoint cũ **giữ nguyên đường dẫn**, viết lại thành wrapper mỏng gọi vào hàm chung → frontend cũ không vỡ.

**Bảo mật (đang thiếu hoàn toàn):**
- whitelist đuôi + magic bytes, tên file sinh từ hash → chặn path traversal và file thực thi
- ảnh: `Image.open().verify()` để loại file giả dạng
- `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff` trên mount `/uploads`
- giới hạn tổng dung lượng thư mục, cảnh báo khi vượt ngưỡng

**UI:** thêm drag-and-drop vào Media Library + nút "📁 Từ thư viện" ngay cạnh mỗi ô upload hiện có.

**⚠️ Phụ thuộc cần kiểm tra:** metadata video/audio cần `ffprobe` (bộ ffmpeg). Em chưa xác nhận được máy anh có chưa — VM sandbox của em không phải máy anh. Nếu chưa có: `brew install ffmpeg`. Nếu không muốn cài, phần duration/aspect để trống, mọi thứ còn lại vẫn chạy.

---

## 5. Lộ trình đề xuất

| Phase | Nội dung | Ước tính | Kết quả nhìn thấy được |
|---|---|---|---|
| **0** | Commit việc dở + `.gitignore` + tạo `feat/media-hub` | 15 phút | Không mất `media_utils.py` |
| **0.5** | **Vá 2 lỗi P0 từ audit**: gate ảnh cho carousel, selector Instagram | 2h | Hết cảnh publish carousel không ảnh |
| **1** | `migrations.py` + 6 cột mới + cấu trúc thư mục + `POST /api/assets/upload` | 1 ngày | Upload được video/audio của mình, có chống trùng |
| **2** | Nối `register_asset()` vào mọi chỗ sinh file + `GET /api/assets` | 0.5 ngày | Bảng `assets` bắt đầu có dữ liệu thật |
| **3** | Tab **Media Library** + `<AssetPicker />` gắn vào 4 tab | 1 ngày | **Dùng lại được nội dung cũ** — đúng yêu cầu #3 |
| **4** | `video_providers/` abstraction + **Veo 3.1** | 1 ngày | **Video/Reel Maker sống lại**, dùng key sẵn có |
| **5** | **HeyGen REST API** thay browser automation | 1 ngày | **Avatar Videos sống lại**, xoá ~250 dòng mong manh |
| **6** | Nút Save Draft + tải video về `uploads/` cho VideoMaker/AvatarMaker | 0.5 ngày | Hết ngõ cụt "generate → xem → mất" |
| **7** | *(tuỳ chọn)* fal.ai + ffmpeg slideshow | 1.5 ngày | Nhiều lựa chọn provider + reel 0 đồng |

**Tổng P0→P6: khoảng 6 ngày công.** Sau Phase 4 là anh đã có video chạy thật.

Thứ tự này cố ý: **kho dữ liệu (1-3) làm trước provider (4-5)**, vì video sinh ra mà không có chỗ lưu thì lại rơi vào đúng cái bẫy hiện tại — generate xong rồi mất.

---

## 6. Rủi ro & giới hạn đã biết

| Rủi ro | Mức | Xử lý |
|---|---|---|
| Veo 3.1 là `-preview`, Google có thể đổi tên model/API | Trung bình | Đưa model id vào `.env`, không hardcode |
| Chưa xác minh được giá Veo/HeyGen từ trang chính thức | Trung bình | Chạy 1 clip test, đo `cost_usd` thật trước khi mở rộng |
| `ffprobe` có thể chưa có trên máy | Thấp | Degrade gracefully, chỉ mất metadata |
| `uploads/` phình to (video nặng hơn ảnh ~50×) | Trung bình | sha256 dedup + soft delete + cảnh báo ngưỡng; sau này chuyển S3/R2 nếu cần |
| `main.py` đã 62 KB, thêm nữa càng khó sửa (PRD gap #1) | Cao | Phase 1 tách `assets_routes.py` riêng, không nhồi thêm vào `main.py` |
| Repo không có test nào (PRD gap #2) | Cao | Mỗi phase kèm test cho phần đó; bắt đầu từ `media_utils` + upload vì đó là chỗ dễ vỡ nhất |
| Instagram/Facebook đổi selector bất cứ lúc nào | Cao | Đã thấy hôm nay (post 46/47). Dài hạn nên chuyển sang Graph API — `facebook_graph.py` anh đã bắt đầu rồi |

---

## 7. Câu hỏi cần anh chốt

1. **Ngân sách provider**: chỉ dùng Veo bằng key sẵn có (0đ setup), hay nạp thêm $5 HeyGen + fal.ai để có đủ lựa chọn?
2. **Branch**: gộp `feat/media-hub` một nhánh, hay tách 3 nhánh nối tiếp?
3. **Bắt đầu từ đâu**: Phase 0.5 (vá lỗi đang chảy máu) hay nhảy thẳng Phase 1?

---

## Phụ lục — nguồn tham khảo
- Google Veo trong Gemini API: https://ai.google.dev/gemini-api/docs/veo
- Video generation, Gemini API: https://ai.google.dev/gemini-api/docs/video
- So sánh FAL / Replicate / OpenAI: https://www.teamday.ai/blog/ai-image-video-api-providers-comparison-2026
- HeyGen API pricing: https://www.g2.com/articles/heygen-api-pricing

*Chưa file nào bị sửa. Đây là đề xuất để anh duyệt.*
