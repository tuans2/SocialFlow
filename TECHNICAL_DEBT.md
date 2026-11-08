# TECHNICAL_DEBT.md — SocialFlow

## P7 (FloraOS Integration Layer) — `organization_id` on `accounts`

### What changed

`backend/migrations.py` adds `accounts.organization_id` (nullable `TEXT`) and an index on it, using the repo's existing forward-only column-migration mechanism. Every endpoint that reads or writes `accounts` (`GET/POST /api/accounts`, `POST /api/accounts/{platform}/login`, `POST /api/accounts/{platform}/check`, `DELETE /api/accounts/{platform}`, `POST /api/accounts/{platform}/login-extended`, and the standalone `manual_login.py` CLI) now accepts an optional `organization_id` and resolves a missing value to `DEFAULT_ORGANIZATION_ID` (env `SOCIALFLOW_DEFAULT_ORGANIZATION_ID`, default `"default"`), so existing single-operator installs behave exactly as before.

`add_account` also now refuses (HTTP 409) to overwrite a platform's credentials with a different `organization_id` than the one already stored — this doubles as a real bug fix: the previous `INSERT OR REPLACE` would silently clobber another operator's saved login for the same platform with no organization concept to even notice.

### What this deliberately does not do

1. **No caller-identity mechanism.** SocialFlow has no session, JWT, API key, or any other way to know who is calling it — `organization_id` here is a caller-supplied value with **zero verification** that the caller is entitled to it. This is schema and plumbing readiness, not a security boundary. A real boundary would need something like floraos-core's new machine-to-machine integration tokens (`YC-T8`, per-organization, signed, rotatable), resolving `organization_id` server-side the way every floraos-core rule requires — not yet built here.
2. **[RESOLVED 2026-09-10 — see "D1 (đa tenant thật) — Phase 1" below]** ~~`accounts.platform` is still globally UNIQUE.~~ SQLite `ALTER TABLE ADD COLUMN` cannot change table-level constraints, and `migrations.py`'s own rule is to add columns/indexes only, never rewrite an existing table. Consequence: only one organization can ever hold credentials for a given platform (e.g. one Facebook account) system-wide, until `accounts` is rebuilt with `UNIQUE(organization_id, platform)`. The 409 guard above makes this limitation loud instead of silent, but doesn't lift it.
3. **[RESOLVED 2026-09-10 — see "D1 (đa tenant thật) — Phase 2" below, A1–A6]** ~~The publish pipeline is not organization-scoped.~~ Every table SocialFlow owns (`posts`, `templates`, `content_plans`, `post_metrics`, `weekly_metrics`, `content_insights`, `campaigns`, plus new `content_queue`/`assets`/`brand_config`/`signal_org_processed`) now carries or resolves `organization_id`; `signals` itself stays intentionally shared (decision below) via the junction table. `agents/publisher.py` and `publish_scheduled_post()` in `main.py` look up accounts by `(organization_id, platform)`, not `platform` alone.
4. **[RESOLVED 2026-09-10 — D1 xong hoàn toàn, xem mục "D1 (đa tenant thật) — HOÀN TẤT" ở cuối tệp.]** ~~This does not resolve D1.~~ floraos-core's PRD (`claude/PRD-floraos-core.md`, §12 Sổ quyết định) decision **D1 — "`SocialFlow` lên đa tenant, hay ở lại làm worker đơn tenant"** chốt "đa tenant thật" và nay đã triển khai xong hết end-to-end: (a) xác thực caller-identity thật vẫn còn ở Nhóm B (SSO, `UNIFIED_SHELL.md`) — đây là mở rộng ranh giới trình bày, không phải điều kiện của D1; (b) `organization_id` đã có trên mọi bảng SocialFlow sở hữu; (c) `accounts` đã dựng lại bỏ ràng buộc unique toàn cục từ Pha 1.

### Verification performed

- `ast.parse` (syntax only) on `main.py`, `migrations.py`, `manual_login.py`.
- `run_migrations()` executed against a copy of the real `socialflow.db` — confirmed `accounts.organization_id` and `idx_accounts_org` are added cleanly and idempotently, with `skipped: []`.
- This sandbox still cannot boot the app itself (`backend/venv` is a macOS virtualenv unreachable from this Linux sandbox). The server was started for real, on the actual Mac (`python -m uvicorn main:app`, an alternate port since 8000 was already held by an unrelated local service), against the actual `backend/socialflow.db` — which already holds three live accounts (`linkedin`, `instagram`, `facebook`, all `organization_id = NULL`). End-to-end curl smoke test: creating an account for a new platform under a new organization succeeds; a second organization claiming the same platform is rejected with 409; `GET /api/accounts?organization_id=...` returns only that organization's accounts in both directions (no leak of the three real accounts into the test organization, no leak of the test account into the default organization); the test account was deleted afterward, leaving the three real accounts untouched.


## D1 (đa tenant thật) — Phase 1: safe table group, 2026-09-10

Product-owner decision (Cowork session, AskUserQuestion): D1 is resolved as **"SocialFlow lên đa tenant thật"** — not the "đơn tenant worker" option this file previously pointed toward as cheaper. Point 2 below (`accounts` rebuild) and part of point 3 (five of the eight remaining tables) are done in this pass. The rest of point 3 — `posts`, `signals`, `content_plans`, threaded through six agents — is deliberately **not** done here; see "What's still open."

### What changed

- **`accounts` rebuilt** with `UNIQUE(organization_id, platform)`, replacing the old global `UNIQUE(platform)` (resolves point 2). `migrations.py._rebuild_accounts_unique_per_org()` renames the old table, recreates it with the new constraint, copies every row with NULL `organization_id` normalized to `DEFAULT_ORGANIZATION_ID`, drops the old table. Safe by construction: the old constraint already guaranteed no two rows shared a platform, so no copy can violate the new one. `add_account`'s manual cross-organization 409 check is removed — it existed only as a workaround for the old constraint, not as a desired restriction. Two organizations can now each hold their own credentials for the same platform; the same organization re-adding a platform still replaces its own row via `INSERT OR REPLACE` keyed on the new composite constraint.
- **`organization_id` (nullable `TEXT`) added** to `campaigns`, `templates`, `post_metrics`, `weekly_metrics`, `content_insights` via the existing forward-only column mechanism, plus an index on each.
- **`post_metrics` is the only one of those five with a live read/write path** (`/api/analytics/*`) — threaded `organization_id` through `record_post_metrics()` and all four read functions (`get_platform_performance`, `get_top_content_types`, `get_pillar_performance`, `generate_weekly_summary`), same `COALESCE(organization_id, ?) = ?` pattern already established for `accounts`.
- `campaigns`/`templates`/`weekly_metrics`/`content_insights` have **no code path that reads or writes them at all**, confirmed by grep across the repo, not assumed. The column on these four is schema readiness for when a write path exists, not a fix to a live gap.

### What's still open

- **`posts`, `signals`, `content_plans`** — not touched in this pass. These sit inside a six-agent pipeline (`agents/planner.py`, `scout.py`, `analyst.py`, `reviewer.py`, `creator.py`, `publisher.py`) that currently has no organization concept anywhere in its call chain — these agents read and write across all rows with no per-organization separation. Adding a column here without re-threading the agent call chain would create a column nobody reads correctly — worse than not adding it, because it would look resolved without being resolved. Needs a call-flow map of the six agents before any schema change, as its own piece of work.
- **Real auth mechanism** — point 1 above still stands unchanged. `organization_id` is still caller-supplied with zero verification anywhere in this repo. The Unified Shell SSO design (`floraos-core/docs/kien-truc/UNIFIED_SHELL.md`) is the planned fix — a FastAPI middleware here verifying a JWT session cookie issued by `floraos-core`. Not built yet, and Unified Shell explicitly should not begin against SocialFlow until the agent-pipeline half of D1 above is done — SSO into a pipeline that still mixes organizations internally would just make the leak reachable from the outside instead of removing it.

### Verification performed

- `ast.parse` (syntax only) on `migrations.py`, `main.py`, `analytics_store.py`.
- `run_migrations()` executed against a COPY of the real `socialflow.db` (never the original): row counts match before/after on every touched table, the three real accounts (`linkedin`, `instagram`, `facebook`) survive with identical `id`/`platform`/`username` and `organization_id` normalized `NULL → "default"`, second run is idempotent (`accounts_rebuilt: false`, `added_columns: []`).
- Functional test against a disposable copy: two different organizations adding the same platform (`twitter`) now both persist as separate rows; the same organization re-adding a platform still replaces its own row (not a duplicate); `post_metrics` recorded under two different `organization_id` values only shows each organization its own rows via `get_platform_performance()`.
- Same sandbox limitation as the P7 pass: cannot boot `uvicorn` (`backend/venv` is a macOS virtualenv, unreachable from this Linux sandbox) — no live end-to-end curl test against a running server this time. Needs the same real-Mac verification pass P7 got before this is considered fully proven.


## D1 (đa tenant thật) — Phase 2: agent-pipeline flow map, 2026-09-10

Read-only investigation (A1 of the Unified Shell completion plan, `floraos-core/docs/kien-truc/UNIFIED_SHELL.md` §7). No code changed in this pass. Two findings here are bigger than the "add `organization_id` to three tables" framing the earlier entry used — they change what Phase 2 actually has to do.

### The pipeline, as it runs today

`agents/orchestrator.py` drives six agents in a fixed sequence: **Scout → Planner → Creator → Reviewer → Publisher → Analyst**. Nothing imports across agent files directly — the orchestrator is the only thing that calls `agent.run()`, either from `POST /api/pipeline/run`, a fixed `APScheduler` job registered at startup (`main.py`, six `scheduler.add_job` calls), or a manual dashboard button. **No caller anywhere passes an `organization_id` into any of this** — not the scheduler, not the manual endpoint, not any agent.

| Agent | Reads | Writes | Notes |
|---|---|---|---|
| Scout | `signals` (dedup check by `url`) | `signals` (insert) | Fetches external news/signals, no org concept |
| Planner | `content_plans`, `posts` (daily count checks), `signals` (join for brief) | `content_plans` (insert), `content_plans.status` (update) | Turns unprocessed signals into a content plan |
| Creator | `brand_config` (`get_brand_config()` — **`ORDER BY id DESC LIMIT 1`, no filter at all**) | `posts` (insert, `status='draft'`) | Generates the actual post content |
| Reviewer | `posts` (`status='draft'`) | `posts.status`, `posts.error_message` | Auto-approves/rejects drafts |
| Publisher | `posts` (`status='approved'`), `accounts` (`platform='discord'` only, for a webhook notification) | `posts.status`, `post_id`, `published_at`, `error_message` | Actual publish call goes through `automation_getter(platform)` — platform string only, no org |
| Analyst | `posts`, `signals`, `content_plans` (all unscoped aggregate counts) | (writes only via `analytics_store.record_post_metrics`, itself now org-aware per Phase 1 — but nothing calls it with a real `organization_id` yet) | Daily summary across everything, not per organization |

### Finding 1 — the orchestrator has no concept of "one run per organization"

`orchestrator.py` keeps a single module-level `PipelineStatus()` singleton (`status.running`, `status.current_agent`, `status.last_result`). `run_full_pipeline()` starts with `if status.running: skip`. This means **only one pipeline execution can be in flight at a time, for the entire process — across every organization.** If organization A's run is in progress, organization B's request to run the pipeline is silently skipped and handed back organization A's `last_result`. `GET /api/pipeline/status` would show organization B a different organization's `current_agent`/`last_result` while a run is active. Adding `organization_id` to the tables underneath does not fix this — the orchestration layer itself needs to become per-organization (either a status object keyed by `organization_id`, or a queue/lock scoped per organization) before "one pipeline run per org, safely concurrent" is actually true. This is separate work from, and larger than, threading a column through six files.

### Finding 2 — `brand_config` and the Discord `accounts` lookup are unscoped, not just unlabeled

- `creator.get_brand_config()` picks the single most-recently-saved `brand_config` row, full stop. Once more than one organization exists, every organization's generated post content would be branded with whichever organization saved their brand kit most recently — a real cross-organization content leak, not just a missing label on an otherwise-correct query. `brand_config` was not in either the original TECHNICAL_DEBT list or the Phase 1 "safe group" — it needs `organization_id` and a real caller-supplied filter, as part of Phase 2, not after it.
- `publisher._publish_discord()` queries `accounts WHERE platform = 'discord'` with no organization filter at all (the six other platforms go through `automation_getter`, which is itself unscoped — see the pipeline table above). Same category of gap as `brand_config`.

### What Phase 2 actually needs (updates the Phase 1 "What's still open" note)

1. Design how `organization_id` enters the pipeline at all — currently nothing upstream (scheduler, manual endpoint, dashboard button) has one to pass in. This likely means: the platform either runs one pipeline per organization on an org-by-org schedule (not one global cron), or the manual/API entry points require it explicitly and the automatic `APScheduler` jobs loop over known organizations.
2. Make `PipelineStatus` per-organization, not a module-level singleton.
3. Add `organization_id` to `posts`, `signals`, `content_plans`, **and `brand_config`** (not in the original three-table list).
4. Scope every read in the table above by `organization_id`, including the Discord `accounts` lookup and whatever `automation_getter`/`get_automation_extended` resolves to in `main.py`.
5. Only after 1–4: the functional test from Phase 1 (two organizations running concurrently, no leak at any step) — this time it has to include a genuinely concurrent run, not just two sequential ones, because finding 1 is specifically a concurrency bug.

This is why the Unified Shell plan splits this into its own item (A2–A6) rather than folding it into "add a column" — it wasn't visible without reading the orchestrator and every agent's actual queries.


## D1 (đa tenant thật) — A2b: đo thời gian thật từng bước, 2026-09-10

Bổ sung cho A2 (`floraos-core/docs/kien-truc/UNIFIED_SHELL.md` §7): thêm đo thời gian thật vào `orchestrator.py` và `publisher.py`, không phụ thuộc việc thêm `organization_id` — làm được ngay, độc lập với A3 trở đi.

### What changed

- `orchestrator.run_full_pipeline()`: mỗi trong sáu bước (scout/planner/creator/reviewer/publisher/analyst) được đo thời gian riêng qua một hàm bọc `_timed_stage`, cộng dồn vào `status.last_stage_seconds` (kèm `"total"`). In ra console mỗi bước mất bao lâu, và bản tổng kết cuối pipeline.
- `publisher.run()`: đo thời gian riêng cho từng bài đăng, cộng dồn theo nền tảng vào `platform_seconds` (vd `{"facebook": 12.3, "instagram": 8.1}`), trả về trong kết quả của bước Publisher.
- `GET /api/pipeline/status` tự động trả thêm `last_stage_seconds` — không cần sửa route, nó gọi `pipeline_status.to_dict()` vốn đã có sẵn.

### Cách lấy số đo thật

Chạy pipeline thật một lần trên máy có mạng (`POST /api/pipeline/run`, hoặc đợi lịch `APScheduler` tự chạy), rồi gọi `GET /api/pipeline/status` — trường `last_stage_seconds` cho biết mỗi bước mất bao lâu, và bên trong kết quả bước Publisher có `platform_seconds` cho biết mỗi nền tảng mất bao lâu. Số đo này dùng để chọn giới hạn song song thật ở A2 (lô song song có giới hạn), thay vì ước lượng.

### Verification performed

- `ast.parse` sạch toàn bộ `backend/` (đệ quy, trừ `venv`).
- Test giả lập (không đụng mạng/DB thật): thay `scout.run`/`planner.run`/`reviewer.run`/`analyst.run` bằng hàm giả 0.05s, `creator.run` 0.2s, `publisher.run` 0.15s — xác nhận `status.last_stage_seconds` ghi đúng từng bước, `total` ≥ tổng các bước chậm nhất, `status.running` về `False` sau khi chạy xong. Test riêng cho `publisher.run()`: hai bài giả lập hai nền tảng khác nhau (facebook 0.05s, instagram 0.1s) — `platform_seconds` ghi đúng, phân biệt đúng theo thời gian.
- Chưa đo được số THẬT (cần mạng ra ngoài tới AI provider và các nền tảng mạng xã hội, cùng giới hạn sandbox đã gặp nhiều lần trước đó) — số đo thật cần chạy trên máy anh Tony.

## D1 (đa tenant thật) — A3: PipelineStatus theo-từng-tổ-chức (bản sửa an toàn tạm thời), 2026-09-10

Trước bản sửa này, `PipelineStatus` là một biến toàn cục không phân biệt tổ chức: nếu tổ chức B gọi `run_full_pipeline()` trong lúc tổ chức A đang chạy, hàm âm thầm trả về `status.last_result` — tức là **kết quả cũ của tổ chức A**, không phải lỗi, không phải kết quả của B. B sẽ tưởng đó là dữ liệu của chính mình.

### Vì sao KHÔNG bật chạy song song thật ở bước này

A3 trong bảng lộ trình (`floraos-core/docs/kien-truc/UNIFIED_SHELL.md` §7) ghi "sửa `PipelineStatus` thành theo-từng-tổ-chức" — có thể hiểu hai cách: (1) chỉ sửa để trạng thái *báo cáo đúng* theo tổ chức, hoặc (2) cho phép *nhiều tổ chức chạy pipeline thật sự đồng thời*. Cách hiểu (2) chính là quyết định A2 (lô song song có giới hạn) — nhưng A2 chỉ an toàn sau khi A4 (thêm `organization_id` vào `posts`/`signals`/`content_plans`/`content_queue`/`assets`/`brand_config`) và A5 (các agent lọc truy vấn theo `organization_id`) hoàn tất. Bật song song thật trước A4/A5 sẽ khiến hai tổ chức ghi/đọc chung các bảng chưa cách ly tổ chức — đúng kiểu rò dữ liệu chéo tổ chức mà quyết định "đa tenant thật" (D1) được lập ra để ngăn.

Vì vậy A3 ở đây chỉ làm phần (1): sửa đúng lỗi trả nhầm kết quả, giữ nguyên pipeline chạy từng lượt một (single-flight) như hiện nay. Việc bật song song thật dời sang sau A5, gộp vào phạm vi A6 (kiểm thử đồng thời thật).

### What changed

- `agents/orchestrator.py`: `PipelineStatus` có thêm `current_organization_id` (tổ chức đang giữ lượt chạy hiện tại), lộ ra trong `to_dict()` nên `GET /api/pipeline/status` tự động trả về trường này.
- `run_full_pipeline()` nhận thêm tham số `organization_id` (mặc định `None` → `DEFAULT_ORGANIZATION_ID`, tương thích ngược). Khi có lượt chạy khác đang chạy, hàm trả về một object từ chối tường minh (`{"error": "pipeline_busy", "running_organization_id": ..., "requested_organization_id": ...}`) thay vì `status.last_result` cũ.
- `main.py`: `POST /api/pipeline/run` nhận query param `organization_id`, truyền xuống `run_full_pipeline`, và lỗi 409 khi pipeline đang bận nay nêu rõ tổ chức nào đang giữ lượt chạy.
- Pipeline vẫn single-flight — đây là bản sửa lỗi báo cáo/từ chối, không phải bật chạy song song.

### Verification performed

- `python3 -m py_compile` sạch cho `main.py` và `agents/orchestrator.py`.
- Test giả lập (stub 6 agent, không đụng mạng/DB thật): tổ chức A bắt đầu chạy, 50ms sau tổ chức B gọi cùng lúc — xác nhận B nhận đúng object từ chối với `running_organization_id="org-A"`, `requested_organization_id="org-B"`; A vẫn chạy xong bình thường và nhận đủ kết quả 6 bước; sau khi xong, `status.running` về `False` và `status.current_organization_id` về `None`.
- Chưa kiểm thử qua HTTP thật (`POST /api/pipeline/run` với hai request đồng thời) do giới hạn mạng/venv của sandbox — cần chạy trên máy anh Tony để xác nhận lỗi 409 trả về đúng qua FastAPI.

## D1 (đa tenant thật) — A4: organization_id vào bảng trong luồng sáu agent, 2026-09-10

Thêm cột `organization_id` (qua `migrations.py`, đúng khuôn Pha 1 — `ALTER TABLE ADD COLUMN`, không rewrite) vào sáu bảng nằm TRONG luồng sáu agent, phát hiện ở A1: `posts` (main.py), `signals` (agents/scout.py), `content_plans` (agents/planner.py), `content_queue` và `assets` — kho asset nội bộ của SocialFlow, khác Asset dùng chung ở floraos-core (asset_inventory.py), và `brand_config` (brand_kit.py).

### What changed

- `migrations.py`: 6 dòng `COLUMNS` mới + 6 index tương ứng (`idx_posts_org`, `idx_signals_org`, `idx_content_plans_org`, `idx_content_queue_org`, `idx_assets_org`, `idx_brand_config_org`).
- Không có bảng nào trong sáu bảng này mang ràng buộc `UNIQUE` cần rebuild kiểu `accounts` ở Pha 1 — chỉ thêm cột thuần tuý, không cần logic rebuild riêng.
- **Cột này CHƯA được dùng ở đâu cả** — chưa agent nào lọc theo `organization_id` khi đọc/ghi các bảng này (đó là A5, còn mở). Cho tới khi A5 xong, mọi dòng hiện có coi như thuộc tổ chức mặc định qua `COALESCE(organization_id, ?)`, giống cách `accounts` xử lý ở Pha 1.
- **Lưu ý riêng cho `brand_config`**: bảng này hiện là **một hàng duy nhất** dùng chung cho mọi tổ chức — logic "insert default nếu bảng rỗng" trong `brand_kit.py` chưa đổi. Thêm cột ở đây chưa tự tạo ra một hàng cấu hình thương hiệu riêng cho từng tổ chức; việc đó (đổi từ "một hàng toàn cục" sang "một hàng mỗi tổ chức", và sửa `brand_kit.py`/Creator đọc đúng hàng của tổ chức mình) thuộc A5.

### Verification performed

- `python3 -m py_compile` sạch `migrations.py` và mọi module có bảng liên quan.
- Test trên **bản sao `socialflow.db` thật** (45 posts, 11 signals, 18 content_plans, 0 content_queue, 14 assets, 1 brand_config): chạy `run_migrations()` lần 1 — cả 6 cột xuất hiện, số dòng từng bảng giữ nguyên, mọi cột cũ giữ nguyên giá trị, `organization_id` là NULL trên toàn bộ dòng cũ; chạy lần 2 — không thêm cột nào nữa (idempotent), dữ liệu không đổi giữa hai lần chạy.
- `tests/test_migrations.py` — bộ test cũ chỉ dựng sẵn bảng `assets`/`content_queue`/`campaigns` (qua `init_asset_tables()`) nên khẳng định "thêm đủ mọi cột đã khai báo" đã **âm thầm sai từ Pha 1** (khi `accounts`/`templates`/`post_metrics`/`weekly_metrics`/`content_insights` được thêm vào `COLUMNS` nhưng test không dựng các bảng đó — chạy `git blame`-tương-đương cho thấy lỗi này không phải do A4 gây ra, A4 chỉ làm nó lộ rõ hơn với 3 dòng "table missing" mới). Đã sửa test để dựng đúng hình dạng trước-migration của TOÀN BỘ ứng dụng — dùng chính hàm `init_*()` của từng module (`agents/scout.py`, `agents/planner.py`, `brand_kit.py`, `analytics_store.py`) thay vì tái tạo tay, cộng `accounts`/`templates` tối giản (giống cách test đã làm sẵn cho `posts`, vì `main.py` không import an toàn trong test — nó dựng cả FastAPI app). Sau khi sửa: 32/32 passed.

## D1 (đa tenant thật) — A5: sáu agent + endpoint lọc theo tổ chức, 2026-09-10

Việc nặng nhất của D1 Pha 2: xuyên suốt sáu agent (Scout/Planner/Creator/Reviewer/Publisher/Analyst), `orchestrator.py`, `automation.py`/`automation_extended.py`, `brand_kit.py` và các endpoint gọi chúng trong `main.py` — mọi điểm đọc/ghi bảng luồng giờ lọc/gắn theo `organization_id`.

### Quyết định thiết kế — `signals` ở lại DÙNG CHUNG (không theo tổ chức)

Trước khi viết code, đặt câu hỏi cho anh Tony (AskUserQuestion): tin tức Scout thu thập (HackerNews/GitHub) có nên tách riêng theo từng tổ chức hay dùng chung? Anh Tony chọn **dùng chung, đánh dấu riêng theo tổ chức** — lý do kỹ thuật: GitHub API công khai giới hạn 60 lượt/giờ khi không đăng nhập, Scout gọi ~6 lượt mỗi lần chạy; nếu N tổ chức có Scout riêng chạy theo lô song song (thiết kế đã chốt ở A2), chỉ một lô 10 tổ chức đã có thể chạm giới hạn này, làm hỏng Scout cho TẤT CẢ tổ chức cùng lúc.

Kết quả: `signals` KHÔNG có cột `organization_id` (cột đã thêm ở A4 bị gỡ lại — chưa từng áp dụng lên database thật nên không vi phạm quy tắc "chỉ thêm, không sửa" của `migrations.py`). Thay vào đó, bảng mới `signal_org_processed (organization_id, signal_id, processed_at)` ghi "tổ chức nào đã dùng signal nào" — thay cho cột `signals.processed` cũ vốn chỉ có MỘT giá trị dùng chung, không thể phản ánh việc nhiều tổ chức xử lý cùng một signal độc lập với nhau. `agents/scout.py`: `get_unprocessed_signals(organization_id)` dùng `NOT EXISTS` trên bảng mới; `mark_processed(ids, organization_id)` ghi vào đó, không đụng `signals`.

### Phát hiện mới trong lúc làm A5 (ngoài phạm vi bản đồ A1 gốc)

1. **File phiên đăng nhập Playwright dùng chung mọi tổ chức.** `automation.py get_session_path()` trước đây là `sessions/{platform}_session.json` — MỘT tệp cho mỗi nền tảng, không phân biệt tổ chức, dù bảng `accounts` (thông tin đăng nhập) đã tách theo tổ chức từ Pha 1. Tổ chức B đăng nhập LinkedIn sau sẽ ghi đè phiên đã lưu của tổ chức A, khiến lần đăng bài tiếp theo của A vô tình chạy bằng tài khoản đã đăng nhập của B. Sửa: `SocialAutomation.__init__` nhận `organization_id`, `get_session_path` đổi thành `sessions/{organization_id}_{platform}_session.json` — vì gần như mọi lớp con (LinkedIn/Instagram/Twitter/Facebook/Reddit/Medium/HeyGen) kế thừa `__init__` của lớp cha mà không ghi đè, một chỗ sửa này tự động áp dụng cho cả ~20 điểm gọi `save_session`/`load_session` rải rác trong `automation.py`/`automation_extended.py` mà không cần sửa từng chỗ. `SubstackAutomation`/`EmailAutomation` (có `__init__` riêng) được sửa để chuyển tiếp `organization_id` qua `super().__init__()`. `get_automation_extended(platform, headless, organization_id)` truyền tham số này xuống mọi nhà máy tạo automation.

2. **`run_migrations()` bị gọi SAI THỜI ĐIỂM trên một database hoàn toàn mới.** Lệnh gọi ở cấp module trong `main.py` (ngay sau `init_asset_tables()`) chạy khi Python NẠP file — TRƯỚC KHI ASGI lifespan khởi động. Nhưng `posts`/`accounts`/`templates`/`signals`/`content_plans`/`brand_config` chỉ được tạo BÊN TRONG `lifespan()` (qua `init_db()`, `init_brand_tables()`, `init_signals_table()`, `init_plans_table()`), tức là SAU lệnh gọi migrations cấp module. Trên một database thật đã tồn tại từ trước (như `socialflow.db` hiện tại của anh Tony) không thấy lỗi vì các bảng này đã có sẵn — nhưng trên MỘT DATABASE HOÀN TOÀN MỚI (cài đặt lần đầu cho khách hàng mới, hoặc môi trường CI/test), `run_migrations()` sẽ báo "skipped (table missing)" cho toàn bộ cột D1 của sáu bảng này và KHÔNG BAO GIỜ thử lại — vì hàm chỉ được gọi một lần. Sửa: gọi `run_migrations()` THÊM MỘT LẦN NỮA ở cuối `lifespan()`, sau khi mọi `init_*()` đã chạy xong. Migrations vốn được thiết kế idempotent ("an toàn khi chạy lại mỗi lần khởi động" — chính lời `migrations.py` tự ghi), nên gọi hai lần là đúng thiết kế, không phải giải pháp tạm.

### `brand_config` — MỘT HÀNG MỖI TỔ CHỨC (khép kín phát hiện A1)

A1 chỉ ra `creator.get_brand_config()` (dùng nội bộ trong Creator) đọc hàng mới nhất không lọc gì — đã sửa. Nhưng khi rà lại toàn bộ, năm route thật trong `brand_kit.py` mà GIAO DIỆN dùng để xem/sửa cấu hình thương hiệu (`GET/PUT /api/brand/config`, `POST /api/brand/logo`, `GET /api/brand/colors`, `GET /api/brand/image-prompt-context`) VẪN chưa được sửa — `PUT /config` cũ ghi `WHERE id = (SELECT MAX(id) FROM brand_config)`, nghĩa là tổ chức nào bấm lưu SAU CÙNG sẽ ghi đè hàng của tổ chức đã lưu trước đó, không tạo hàng riêng. Đã sửa toàn bộ năm route + thêm `get_or_create_brand_row()`: tổ chức chưa từng lưu brand kit được tạo một hàng mặc định riêng ngay khi mở trang cài đặt (không còn 404), việc sửa luôn nhắm đúng hàng của tổ chức đó. `POST /logo` cũng đổi tên tệp logo theo tổ chức (`brand-logo-{organization_id}.ext`) — trước đây một tên tệp `brand-logo.ext` dùng chung sẽ bị tổ chức sau ghi đè lên ảnh của tổ chức trước.

### Khoảng trống CHƯA đóng — cố tình để lại, không phải bỏ sót

- **Facebook Graph API dùng một Page/token chung cho server** (`facebook_graph.py`, đọc từ biến môi trường `FB_PAGE_ID`/`FB_PAGE_ACCESS_TOKEN`), không phải từ bảng `accounts` theo tổ chức như mọi nền tảng khác. Mọi tổ chức dùng chung server hiện đăng qua CÙNG MỘT Trang Facebook khi đi đường Graph API — chỉ nhánh dự phòng Playwright (`FacebookAutomation`) được cách ly theo tổ chức. Cần lưu Page ID/token riêng theo tổ chức (ví dụ trong `accounts`, như cách webhook Discord đã làm) — việc này không phải "thêm một cột", là việc riêng, chưa làm.
- **Các endpoint thao tác trực tiếp theo ID bài viết trong `main.py`** (`/api/pipeline/approve/{id}`, `/reject/{id}`, `/publish/{id}`) vẫn CHƯA kiểm tra bài viết đó có thuộc đúng tổ chức gọi hay không — tổ chức A có thể duyệt/từ chối/đăng một bài viết của tổ chức B nếu đoán đúng ID. `publish_scheduled_post`, `publish-extended`, `_publish_single_post`, `login-extended`, `login`/`check` ĐÃ được sửa (dùng `organization_id` của chính bài viết/đã lọc theo tổ chức khi gọi `get_automation_extended`). Việc soát toàn bộ endpoint thao tác-theo-ID còn lại thuộc phạm vi soát bảo mật cuối (Nhóm D, `UNIFIED_SHELL.md`).
- **Lịch tự động (`APScheduler`, 5 job cố định trong `lifespan()`) vẫn chỉ chạy cho MỘT tổ chức** (`DEFAULT_ORGANIZATION_ID`) — sáu agent giờ nhận `organization_id` được, nhưng chưa có nơi nào liệt kê "danh sách tổ chức đang hoạt động" để lịch tự động lặp qua. Đây chính là việc A6 cần làm (kiểm thử đồng thời thật cần một cách thật để chạy nhiều tổ chức) — A5 chỉ đảm bảo CÓ THỂ gọi từng agent với `organization_id` bất kỳ, chưa xây cơ chế lặp qua nhiều tổ chức.

### Verification performed

- `python3 -m py_compile` sạch toàn bộ file đã sửa.
- Test tích hợp mô phỏng hai tổ chức xuyên suốt DB thật (không mạng, không Playwright) — dựng database đúng như app thật dựng (mọi `init_*()` rồi `run_migrations()` HAI LẦN, đúng thứ tự đã sửa): 36/36 passed, xác nhận — hai tổ chức cùng thấy một signal dùng chung; tổ chức A xử lý xong thì tổ chức B vẫn thấy signal đó "chưa xử lý"; content_plans/posts cách ly đúng theo tổ chức; Reviewer chạy cho A không đụng draft của B; Publisher chỉ thấy bài đã duyệt của đúng tổ chức; tra cứu webhook Discord trả về đúng tài khoản của đúng tổ chức; Analyst trả thống kê riêng biệt; đường dẫn tệp phiên đăng nhập khác nhau giữa hai tổ chức cho cùng một nền tảng.
- Test riêng cho `brand_kit.py` (5 route): 10/10 passed — tổ chức mới mở trang cài đặt nhận cấu hình mặc định (không 404); tổ chức A sửa xong không ảnh hưởng tổ chức B; tổ chức B sửa sau không ghi đè tổ chức A; hàng gốc (tổ chức mặc định, `organization_id NULL`) giữ nguyên; đúng 3 hàng trong bảng sau toàn bộ kịch bản (gốc + A + B).
- `tests/test_migrations.py`: 31/31 passed (giảm 1 so với A4's 32/32 — đúng như dự kiến, vì danh sách `COLUMNS` mất đúng một dòng `signals.organization_id` đã gỡ).
- `bash tests/run_all.sh` (toàn bộ 7 bộ test của repo): 2 lỗi không liên quan đến A5 — (1) `test_media_store.py` "only one inventory row" copy `socialflow.db` thật làm nền rồi kỳ vọng đúng 1 hàng `origin='uploaded'`, nhưng database thật hiện đã có 13 hàng thật từ trước (dữ liệu thật đã lớn hơn giả định lúc viết test — không liên quan `organization_id`/D1, không chạm asset_inventory.py); (2) `test_slideshow.py` báo lỗi xoá tệp tạm trong `.render/` — giới hạn của sandbox này (không được phép xoá file trong thư mục đã mount), không phải lỗi thật, sẽ không xảy ra trên máy thật. Cả hai được nêu ra để anh Tony biết, không sửa trong lượt này vì ngoài phạm vi A5.

## D1 (đa tenant thật) — A6: chạy song song thật nhiều tổ chức, 2026-09-10

A5 khiến từng agent CÓ THỂ nhận `organization_id` và lọc đúng, nhưng chưa có gì thật sự GỌI nhiều tổ chức cùng lúc — lịch tự động (`APScheduler`) vẫn chỉ chạy một tổ chức mặc định, và `PipelineStatus` (A3) tuy đã ghi đúng tên tổ chức đang giữ lượt chạy nhưng vẫn là MỘT đối tượng toàn cục duy nhất, nên hai tổ chức khác nhau chạy cùng lúc vẫn bị chặn lẫn nhau bởi single-flight process-wide. A6 đóng khoảng trống thứ ba đã nêu ở cuối mục A5.

### What changed

- **`PipelineStatus` từ một biến toàn cục thành một từ điển theo tổ chức.** `orchestrator.py`: bỏ `status = PipelineStatus()` cấp module, thay bằng `_status_by_org: Dict[str, PipelineStatus] = {}` và `get_status(organization_id)` — tạo mới nếu tổ chức đó chưa có trạng thái, trả về đúng đối tượng của tổ chức đó nếu đã có. `run_full_pipeline()` gọi `get_status(org_id)` ở đầu hàm thay vì tham chiếu biến toàn cục; cơ chế từ chối lượt chạy trùng (đã có từ A3) giữ nguyên hình dạng nhưng giờ chỉ chặn trong PHẠM VI CÙNG MỘT tổ chức — tổ chức khác chạy song song không còn bị chặn nhầm.
- **`list_known_organizations()`** — SocialFlow không có bảng tổ chức riêng (tổ chức sống ở `floraos-core`), nên danh sách tổ chức đang hoạt động được suy ra từ chính dữ liệu đã có: `SELECT DISTINCT organization_id FROM accounts` (COALESCE về tổ chức mặc định cho dòng NULL cũ). Đúng với ràng buộc `UNIQUE(organization_id, platform)` đã dựng lại ở Pha 1 — mọi tổ chức đã onboard thật đều có ít nhất một dòng `accounts`. Bảng rỗng/thiếu thì trả về `[DEFAULT_ORGANIZATION_ID]`, không vỡ.
- **`run_for_organizations(async_fn, organization_ids, batch_size, **kwargs)`** — bộ thực thi lô song song có giới hạn thật, hiện thực hoá thiết kế đã chốt từ A2 (5–10 tổ chức mỗi lô) bằng `asyncio.Semaphore`. Số lô lấy từ biến môi trường mới `SOCIALFLOW_PIPELINE_BATCH_SIZE` (mặc định 8).
- **`run_scheduled_cycle(...)`** — điểm vào thật cho lịch tự động: chạy Scout đúng MỘT LẦN (dùng chung theo quyết định A5), sau đó gọi `run_for_organizations(run_full_pipeline, ..., run_scout=False)` để chạy Planner→Creator→Reviewer→[Publisher]→Analyst cho từng tổ chức trong các lô song song có giới hạn.
- **`run_full_pipeline()` thêm tham số `run_scout: bool = True`** — mặc định giữ nguyên hành vi cũ (nút "Chạy pipeline" trên dashboard, `POST /api/pipeline/run` gọi thủ công cho một tổ chức vẫn chạy Scout như trước); `run_scheduled_cycle` truyền `run_scout=False` cho từng tổ chức vì đã chạy Scout dùng chung một lần ở vòng ngoài.
- **`main.py`**: bốn job đã đăng ký sẵn trong `APScheduler` (pipeline đầy đủ, Scout riêng, Publisher riêng, Analyst riêng) viết lại để gọi `run_scheduled_cycle`/`run_for_organizations` thay vì gọi thẳng một agent cho một tổ chức mặc định; endpoint dashboard `/api/pipeline/run` và `/api/pipeline/status` đổi sang `get_status(organization_id)`.

### Verification performed

- `python3 -m py_compile` sạch `orchestrator.py` và `main.py`.
- Test đồng thời dùng agent giả lập (thay `agents` bằng bản giả có `asyncio.sleep(0.2)` nhân tạo, không mạng/Playwright/AI), dựng `accounts` với ba tổ chức + một dòng NULL cũ: 13/13 passed — xác nhận `list_known_organizations()` suy đúng danh sách; hai tổ chức khác nhau chạy thật SONG SONG (đo được mức song song ≥2, tổng thời gian < thời gian chạy tuần tự cộng dồn); single-flight CÙNG một tổ chức vẫn bị chặn đúng như A3; `run_for_organizations` với `batch_size=1` chạy tuần tự còn `batch_size=3` chạy đồng thời cả ba, đúng giới hạn semaphore; `run_scheduled_cycle` chạy Scout đúng một lần dù có ba tổ chức trong khi Planner chạy đủ ba lần; `get_status()` trả về ba đối tượng khác nhau cho ba tổ chức.
- `tests/test_migrations.py`: 31/31 passed — không đổi so với A5 (A6 không chạm `migrations.py`).
- `bash tests/run_all.sh`: cùng đúng hai lỗi đã nêu ở A5 (`test_media_store.py` số dòng thật đã lớn hơn giả định cũ, `test_slideshow.py` giới hạn xoá file của sandbox này) — không có lỗi mới phát sinh từ thay đổi của A6.

## D1 (đa tenant thật) — HOÀN TẤT, 2026-09-10

D1 (`floraos-core/claude/PRD-floraos-core.md` §12 Sổ quyết định) đóng hoàn toàn: SocialFlow là hệ đa tenant thật ở tầng dữ liệu và tầng agent, không còn bảng nào trong luồng sáu agent (Scout/Planner/Creator/Reviewer/Publisher/Analyst) trộn dữ liệu giữa các tổ chức, và lịch tự động chạy thật cho nhiều tổ chức song song thay vì một tổ chức mặc định.

### Tóm tắt bảy bước (A1–A7)

- **A1** — bản đồ luồng gọi sáu agent, hai phát hiện đổi phạm vi (PipelineStatus singleton, brand_config không lọc tổ chức).
- **A2 / A2b** — thiết kế lô song song có giới hạn (SQLite chỉ khoá lúc ghi đĩa, không khoá lúc chờ mạng) + đo thời gian thật từng bước.
- **A3** — sửa lỗi trả nhầm kết quả giữa các tổ chức (chưa bật song song thật).
- **A4** — `organization_id` vào sáu bảng còn lại trong luồng agent qua `migrations.py`.
- **A5** — sáu agent + `orchestrator.py` + `automation.py`/`automation_extended.py` + `brand_kit.py` + endpoint liên quan thật sự lọc/gắn theo tổ chức; quyết định `signals` dùng chung (giới hạn GitHub API 60 lượt/giờ); hai lỗi thật phát hiện ngoài kế hoạch (file phiên đăng nhập dùng chung, thứ tự gọi `run_migrations()`) đã sửa.
- **A6** — `PipelineStatus` theo từng tổ chức thật, `run_for_organizations()`/`run_scheduled_cycle()` chạy lô song song có giới hạn thật, bốn job lịch tự động chạy đa tổ chức.
- **A7** — mục này: đóng D1, cập nhật lại các mục nợ #2–#4 ở đầu tệp.

### Khoảng trống có chủ đích, KHÔNG thuộc phạm vi D1 — theo dõi riêng

- **Xác thực caller-identity thật (JWT/SSO)** — `organization_id` hiện vẫn là giá trị bên gọi tự khai, chưa có middleware xác thực chữ ký. Thuộc Nhóm B (`UNIFIED_SHELL.md` §3–4), sẽ đóng khi SSO nối vào.
- **Facebook Graph API dùng một Page/token chung server** (biến môi trường, chưa theo tổ chức) — nêu ở A5, chưa sửa.
- **Endpoint thao tác-theo-ID** (`approve`/`reject`/`publish`) chưa kiểm tra bài viết thuộc đúng tổ chức gọi — thuộc phạm vi soát bảo mật Nhóm D.

Chi tiết đầy đủ từng bước ở các mục "D1 (đa tenant thật) — A1" … "A6" phía trên trong chính tệp này. Trạng thái cấp lộ trình ở `floraos-core/docs/kien-truc/UNIFIED_SHELL.md` §7 và `TRANG_THAI.md`.


## M04b khai tử, 2026-09-17

Rà theo nợ #80 của `floraos-core` (`docs/dac-ta/TECHNICAL_DEBT.md`), yêu cầu bởi chủ sản phẩm cùng lượt với nợ #71 (chi phí thật M04a/M04b).

### Phát hiện

Đối chiếu bốn câu hỏi mà nợ #80 đặt ra (auth, tổ chức, credit, ghi `assets`) cho `POST /api/m04b/background-removal`:

- **Auth** — ổn. `require_org` (`org_context.py`) xác minh JWT `floraos_sso` thật do `floraos-core` ký, không còn giá trị tự khai như trước Đợt 3.
- **Tổ chức** — ổn. `BackgroundRemovalRequest` chỉ có `product_id`; `organization_id` CHỈ tới từ `require_org`, bên gọi không có trường nào để tự khai.
- **Credit/hạn mức** — KHÔNG ổn. `m04b/use_cases/background_removal.py` dùng `m04b/infra/core_client.py` (`CoreClientAdapter`) — client này CHỈ có `get_master_image`/`download_image`/`register_asset`/`list_assets`, không có phương thức ghi usage nào. `floraos_core.py` có một `CoreClient` KHÁC với `ghi_usage()` gọi `POST /integration/usage`, nhưng route M04b không dùng client đó — grep toàn repo xác nhận `ghi_usage` không được gọi ở bất kỳ đâu. Route cũng không gọi `POST /integration/capabilities/check` trước khi chạy. Kết quả: bất kỳ phiên đăng nhập hợp lệ nào của một tổ chức gọi được route này vô hạn lần, không tính vào hạn mức hay chi phí nào ở phía `floraos-core`.
- **Ghi `assets`** — ổn. `register_asset` gọi đúng `POST /integration/assets`, đúng lược đồ (`kind: MARKETING`, `parent_asset_id` trỏ Master, `approval_state` do core đặt `PENDING`, không do engine ngoài tự đặt).

### Quyết định

Khai tử, không vá. M04b thật (tách nền + ghép bối cảnh theo preset + watermark tuỳ chọn + 4 tỷ lệ, có cổng Subject Integrity đo lõi chủ thể) đã dựng xong trong `floraos-core` từ P24 (`workers/media_ai/jobs/variant_worker.py`, `POST /api/v1/media/variants`, năng lực `I4`/`I5`, đi qua RBAC + hạn mức thật của chính `floraos-core`). Route ở đây chỉ còn là bản hẹp hơn (tách nền đơn thuần) và không đo được credit/chi phí — không còn lý do giữ song song.

### Những gì đã đổi

- `backend/m04b/routes.py` — `POST /background-removal` nay luôn trả `410 Gone`, trỏ sang `POST /api/v1/media/variants` của `floraos-core`. `GET /assets/{id}/download` GIỮ NGUYÊN — asset đã tạo trước đây vẫn tải được.
- `backend/main.py` — vẫn đăng ký `m04b_router` (để giữ endpoint download), có chú thích rõ lý do.
- `frontend/index.html` (`MarketingCreative`) — thẻ "AI Background Removal" đổi thành trạng thái đã khai tử, nút và ô nhập bị vô hiệu hoá, không còn gọi được route cũ.
- `backend/tests/test_m04b.py`, `backend/tests/e2e_m04b.py` — phần kiểm route cập nhật để mong đợi `410` thay vì `200`/`502`; phần kiểm use-case thuần (`background_removal.py`, `image_processor.py`, không qua HTTP) giữ nguyên vì hai module đó không bị xoá, chỉ mất đường vào qua HTTP.

### Chưa làm (ngoài phạm vi lượt này)

- Không xoá `m04b/use_cases/background_removal.py` hay `m04b/adapters/image_processor.py` — vẫn còn được `tests/test_m04b.py` gọi trực tiếp, và xoá hẳn là việc dọn dẹp riêng, không phải một quyết định sản phẩm.
- `m04b/infra/core_client.py` (client thiếu `ghi_usage`) không bị xoá — không còn đường gọi tới nó sau khi route trả 410, nhưng dọn nó là việc dọn rác riêng.
