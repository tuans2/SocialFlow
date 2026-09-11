"""
Brand Kit — API endpoints for brand configuration

Stores and serves brand identity settings:
  - Colors (primary, secondary, dark background)
  - Logo (file upload)
  - Typography (font family, heading weight)
  - Tone and voice guidelines
  - Forbidden styles for image generation
  - Platform-specific hashtags
  - CTA templates
  - Product list with URLs
"""

import json
import os
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import Depends, APIRouter, HTTPException, UploadFile, File, Form

from org_context import require_org, sso_token_tho
from pydantic import BaseModel

from to_chuc import bat_buoc
from duong_dan_db import duong_dan_db

# Đợt 3 / E3 — cổng xác thực ở mức ROUTER: mọi route trong tệp này đòi một
# JWT `floraos_sso` hợp lệ. Đây mới là lớp CHẶN; lớp LỌC theo tổ chức nằm ở
# từng route (E4), và FastAPI nhớ kết quả phụ thuộc trong một request nên gọi
# hai lần không tốn thêm gì.
router = APIRouter(
    prefix="/api/brand",
    tags=["Brand Kit"],
    dependencies=[Depends(require_org)],
)

DB_PATH = Path(duong_dan_db())
UPLOADS_DIR = Path(__file__).parent / "uploads"

# Cung hang so voi migrations.py/main.py/orchestrator.py.


def init_brand_tables():
    """Create brand_config table."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('''CREATE TABLE IF NOT EXISTS brand_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brand_name TEXT DEFAULT 'InBharat AI',
        tagline TEXT DEFAULT 'Building practical, accessible AI tools for India',
        primary_color TEXT DEFAULT '#0A74DA',
        secondary_color TEXT DEFAULT '#F5A623',
        dark_bg TEXT DEFAULT '#1A1A2E',
        light_bg TEXT DEFAULT '#FFFFFF',
        success_color TEXT DEFAULT '#10B981',
        warning_color TEXT DEFAULT '#F59E0B',
        danger_color TEXT DEFAULT '#E94560',
        logo_path TEXT DEFAULT '',
        primary_font TEXT DEFAULT 'Roboto',
        heading_weight TEXT DEFAULT '700',
        body_weight TEXT DEFAULT '400',
        code_font TEXT DEFAULT 'JetBrains Mono',
        tone TEXT DEFAULT 'clear, direct, knowledgeable, practical, builder-minded',
        forbidden_styles TEXT DEFAULT 'robot heads, stock photos, neon, clipart, sci-fi dystopia, childish cartoons',
        hashtags_linkedin TEXT DEFAULT '#AI #InBharat #MachineLearning',
        hashtags_x TEXT DEFAULT '#AI #InBharat',
        hashtags_instagram TEXT DEFAULT '#AI #ArtificialIntelligence #InBharat #IndiaAI #MachineLearning #Tech #Startups',
        cta_template TEXT DEFAULT 'Follow @inbharat.ai for daily AI updates',
        products_json TEXT DEFAULT '[]',
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    # Hàng mầm này có `organization_id` NULL. Từ Đợt 3/E7 mọi truy vấn lọc
    # theo tổ chức chặt, nên nó VÔ HÌNH cho tới khi H1 gán nó cho một tổ chức
    # thật. Nó mang danh mục sản phẩm của thương hiệu gốc (InBharat AI) — sau
    # H1 nó sẽ thành brand kit của AVI GIFT, cần rà lại nội dung.
    # D1 A5: brand_config is one row
    # PER ORGANIZATION now, not one shared row -- an organization other than
    # the default one gets its own row lazily, the first time it saves or
    # reads its brand kit (see get_or_create_brand_row / the /config routes
    # below), not seeded here at startup (there is no list of organizations
    # to seed for).
    count = conn.execute("SELECT COUNT(*) FROM brand_config").fetchone()[0]
    if count == 0:
        products = json.dumps([
            {"name": "InBharat AI", "url": "https://inbharat.ai", "description": "AI Consulting & Solutions"},
            {"name": "Phoring", "url": "https://phoring.in", "description": "Decision intelligence platform"},
            {"name": "UniAssist", "url": "https://uniassist.ai", "description": "University guidance AI"},
            {"name": "Testsprep", "url": "https://testsprep.in", "description": "AI test preparation"},
            {"name": "Sahaayak AI", "url": "https://inbharat.ai", "description": "Personal AI assistant for India"},
            {"name": "CodeIn", "url": "https://codein.pro", "description": "Developer tools"},
        ])
        conn.execute("INSERT INTO brand_config (products_json) VALUES (?)", (products,))
        conn.commit()
    conn.close()


def get_or_create_brand_row(conn: sqlite3.Connection, organization_id: str) -> int:
    """Return the id of this organization's brand_config row, creating a
    fresh default one if it does not have one yet.

    D1 A5: before this, every route below read/wrote "the most recent row"
    with no organization filter at all -- a real cross-organization leak
    (A1 finding 2): whichever organization saved their brand kit most
    recently silently became the brand every OTHER organization's Creator
    used, and whichever organization called PUT /config most recently would
    overwrite that same shared row for everyone.
    """
    row = conn.execute(
        "SELECT id FROM brand_config WHERE organization_id = ? ORDER BY id DESC LIMIT 1",
        (organization_id,)
    ).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO brand_config (organization_id, products_json) VALUES (?, ?)",
        (organization_id, "[]")
    )
    conn.commit()
    return cur.lastrowid


class BrandUpdate(BaseModel):
    brand_name: Optional[str] = None
    tagline: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    dark_bg: Optional[str] = None
    light_bg: Optional[str] = None
    primary_font: Optional[str] = None
    tone: Optional[str] = None
    forbidden_styles: Optional[str] = None
    hashtags_linkedin: Optional[str] = None
    hashtags_x: Optional[str] = None
    hashtags_instagram: Optional[str] = None
    cta_template: Optional[str] = None
    products_json: Optional[str] = None


class ProductItem(BaseModel):
    name: str
    url: str
    description: str = ""


TTL_DONG_BO_GIAY = int(os.getenv("FLORAOS_BRAND_SYNC_TTL_SECONDS", "300"))


def dong_bo_brand_tu_core(organization_id: str, *, sso_token: str = None,
                          bat_buoc_dong_bo: bool = False) -> bool:
    """Kéo phần CHUNG của brand từ core về, ghi đè các cột core làm chủ.

    Trả `True` nếu vừa đồng bộ, `False` nếu bỏ qua (còn trong TTL) hoặc không
    gọi được core.

    **Core không gọi được thì KHÔNG chặn.** Đây là chỗ cố ý làm ngược với
    `LocalBudd`: ở đó lời gọi core là cổng hạn mức, chặn được thì phải chặn.
    Ở đây thứ bị chặn sẽ là một bài đăng ĐÃ DUYỆT — dừng nó vì core mất mạng
    là đổi một sự cố nhỏ lấy một sự cố lớn hơn. Dùng bản đã lưu, ghi log, đi
    tiếp.

    Chỉ ghi đè các cột trong `COT_CORE_LAM_CHU`. Phần riêng của SocialFlow
    (brand_name, tagline, products_json, màu trạng thái…) không bị đụng tới.
    """
    from brand_dong_bo import COT_CORE_LAM_CHU, anh_xa_brand_profile
    from floraos_core import (CoreClient, CoreKhongGoiDuoc, CoreTuChoi,
                              client_chay_nen, client_thay_mat_nguoi_dung)

    org_id = bat_buoc(organization_id)
    init_brand_tables()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row_id = get_or_create_brand_row(conn, org_id)
        if not bat_buoc_dong_bo:
            hang = conn.execute(
                "SELECT core_synced_at FROM brand_config WHERE id = ?", (row_id,)
            ).fetchone()
            moc = hang["core_synced_at"] if hang and "core_synced_at" in hang.keys() else None
            if moc:
                try:
                    da_qua = (datetime.now() - datetime.fromisoformat(moc)).total_seconds()
                    if da_qua < TTL_DONG_BO_GIAY:
                        return False
                except ValueError:
                    pass  # mốc hỏng thì cứ đồng bộ lại

        try:
            client = (client_thay_mat_nguoi_dung(sso_token) if sso_token
                      else client_chay_nen(org_id))
            ho_so = client.lay_brand_profile()
        except (CoreKhongGoiDuoc, CoreTuChoi) as e:
            print(f"[brand] không đồng bộ được từ core cho {org_id}: {e} "
                  f"— dùng bản đã lưu", flush=True)
            return False

        cot = anh_xa_brand_profile(ho_so)
        # Hồ sơ trống bên core → KHÔNG ghi gì. Ghi một dict toàn null sẽ xoá
        # trắng brand kit đang dùng được.
        if not cot:
            return False

        co_that = {r[1] for r in conn.execute("PRAGMA table_info(brand_config)")}
        cot = {k: v for k, v in cot.items() if k in co_that and k in COT_CORE_LAM_CHU}
        if not cot:
            return False

        cot["core_synced_at"] = datetime.now().isoformat()
        menh_de = ", ".join(f"{k} = ?" for k in cot)
        conn.execute(f"UPDATE brand_config SET {menh_de} WHERE id = ?",
                     list(cot.values()) + [row_id])
        conn.commit()
        return True
    finally:
        conn.close()


@router.get("/config")
async def get_brand_config(organization_id: str = Depends(require_org),
                           sso_token: Optional[str] = Depends(sso_token_tho)):
    """Get this organization's brand configuration.

    An organization that has never saved its own brand kit gets a fresh
    default row created on the spot (get_or_create_brand_row), the same way
    the single shared row used to always exist before D1 A5 -- so opening
    the brand settings page for a brand-new organization still shows
    editable defaults instead of a 404.
    """
    org_id = bat_buoc(organization_id)
    init_brand_tables()
    # Đợt 3 / F3: kéo phần chung từ core trước khi đọc, có TTL. Không gọi được
    # core thì hàm này trả False và ta đọc bản đã lưu — không chặn.
    dong_bo_brand_tu_core(org_id, sso_token=sso_token)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    get_or_create_brand_row(conn, org_id)
    row = conn.execute(
        "SELECT * FROM brand_config WHERE organization_id = ? ORDER BY id DESC LIMIT 1",
        (org_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="No brand config found")
    config = dict(row)
    # Parse products JSON
    try:
        config["products"] = json.loads(config.get("products_json", "[]"))
    except json.JSONDecodeError:
        config["products"] = []
    return config


@router.put("/config")
async def update_brand_config(update: BrandUpdate, organization_id: str = Depends(require_org)):
    """Update this organization's brand configuration.

    D1 A5: writes now target this organization's OWN row (created on first
    write if it does not have one yet) -- before this fix, every
    organization's edits landed on the single most-recently-saved row,
    silently overwriting whichever organization had saved last.
    """
    org_id = bat_buoc(organization_id)
    init_brand_tables()
    conn = sqlite3.connect(str(DB_PATH))
    row_id = get_or_create_brand_row(conn, org_id)

    # Build dynamic UPDATE query with only provided fields
    fields = {}
    for key, value in update.dict(exclude_unset=True).items():
        if value is not None:
            fields[key] = value

    # Đợt 3 / F4: các cột do core làm chủ KHÔNG sửa được ở đây. Cho sửa thì
    # lần đồng bộ kế tiếp ghi đè lặng lẽ, và người dùng mất công vô ích mà
    # không hiểu vì sao — tệ hơn là bị từ chối thẳng.
    from brand_dong_bo import COT_CORE_LAM_CHU
    bi_khoa = sorted(set(fields) & COT_CORE_LAM_CHU)
    if bi_khoa:
        conn.close()
        raise HTTPException(
            status_code=409,
            detail=(
                "Các trường này do FloraOS làm chủ, sửa ở FloraOS chứ không ở "
                f"đây: {', '.join(bi_khoa)}"
            ),
        )

    if not fields:
        conn.close()
        raise HTTPException(status_code=400, detail="No fields to update")

    fields["updated_at"] = datetime.now().isoformat()

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [row_id]

    conn.execute(f"UPDATE brand_config SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()

    return {"status": "updated", "fields": list(fields.keys())}


@router.post("/logo")
async def upload_logo(file: UploadFile = File(...), organization_id: str = Depends(require_org)):
    """Upload this organization's brand logo."""
    org_id = bat_buoc(organization_id)
    UPLOADS_DIR.mkdir(exist_ok=True)
    # Filename includes the organization so two organizations' logos don't
    # overwrite the same file on disk (D1 A5 — was a single shared filename).
    logo_path = UPLOADS_DIR / f"brand-logo-{org_id}{Path(file.filename).suffix}"
    with open(logo_path, "wb") as f:
        content = await file.read()
        f.write(content)

    # Save path in this organization's brand config row
    conn = sqlite3.connect(str(DB_PATH))
    init_brand_tables()
    row_id = get_or_create_brand_row(conn, org_id)
    conn.execute(
        "UPDATE brand_config SET logo_path = ?, updated_at = ? WHERE id = ?",
        (str(logo_path), datetime.now().isoformat(), row_id)
    )
    conn.commit()
    conn.close()

    return {"status": "uploaded", "path": str(logo_path)}


@router.get("/colors")
async def get_brand_colors(organization_id: str = Depends(require_org)):
    """Get this organization's brand color palette (for image generation)."""
    org_id = bat_buoc(organization_id)
    init_brand_tables()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT primary_color, secondary_color, dark_bg, light_bg, success_color, warning_color, danger_color "
        "FROM brand_config WHERE organization_id = ? ORDER BY id DESC LIMIT 1",
        (org_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


@router.get("/image-prompt-context")
async def get_image_prompt_context(organization_id: str = Depends(require_org)):
    """Get this organization's brand context for enriching image generation prompts."""
    org_id = bat_buoc(organization_id)
    init_brand_tables()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT brand_name, primary_color, secondary_color, dark_bg, tone, forbidden_styles "
        "FROM brand_config WHERE organization_id = ? ORDER BY id DESC LIMIT 1",
        (org_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {}
    config = dict(row)
    return {
        "brand": config["brand_name"],
        "style_direction": f"Use colors: {config['primary_color']} (primary), {config['secondary_color']} (accent), {config['dark_bg']} (background). Style: modern, clean, professional.",
        "forbidden": config["forbidden_styles"],
        "tone": config["tone"],
    }
