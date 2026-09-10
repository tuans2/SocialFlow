"""Ánh xạ `brand_profiles` của floraos-core sang `brand_config` — Đợt 3 / F1.

Luật thuần: không sqlite, không httpx, không biến môi trường. Test được không
cần mạng lẫn cơ sở dữ liệu.

Quyết định của anh Tony (AskUserQuestion 2026-09-10): **core là nguồn cho phần
CHUNG, SocialFlow giữ phần RIÊNG.** Đúng luật cắt sở hữu dữ liệu ở PRD §8 của
core — core sở hữu entity mà nhiều hơn một module đọc.

    Phần CHUNG (core là nguồn, SocialFlow chỉ đọc):
        primary_color · secondary_color · accent_color · text_color
        light_bg ← background_color
        primary_font ← font_heading
        body_font  ← font_body
        tone ← tone_of_voice
        forbidden_styles
        hashtags_linkedin / hashtags_x / hashtags_instagram ← hashtags
        cta_template ← cta_templates

    Phần RIÊNG (ở lại SocialFlow, core không giữ):
        brand_name · tagline · dark_bg · success/warning/danger_color
        code_font · heading_weight · body_weight · products_json · logo_path

`logo_asset_id` của core CHƯA ánh xạ: nó là mã một asset trong kho của core,
muốn dùng phải xin URL ký sẵn — cùng đường với Master Image, thuộc M04b, ngoài
phạm vi Đợt 3. `logo_path` cục bộ giữ nguyên.
"""

import json
from typing import Any, Dict, Optional

# core → brand_config, cho những trường ánh xạ một-một.
ANH_XA_TRUC_TIEP = {
    "primary_color": "primary_color",
    "secondary_color": "secondary_color",
    "accent_color": "accent_color",
    "text_color": "text_color",
    "background_color": "light_bg",
    "font_heading": "primary_font",
    "font_body": "body_font",
    "tone_of_voice": "tone",
}

# Cột `brand_config` do core làm chủ. Giao diện brand kit của SocialFlow phải
# để chúng CHỈ ĐỌC (F4) — sửa ở đây thì lần đồng bộ sau ghi đè, im lặng.
COT_CORE_LAM_CHU = set(ANH_XA_TRUC_TIEP.values()) | {
    "forbidden_styles", "hashtags_linkedin", "hashtags_x",
    "hashtags_instagram", "cta_template",
}

# Tên nền tảng bên core → cột hashtag của brand_config.
COT_HASHTAG = {
    "linkedin": "hashtags_linkedin",
    "x": "hashtags_x",
    "twitter": "hashtags_x",
    "instagram": "hashtags_instagram",
}


def _doc_json(gia_tri: Any) -> Any:
    """core trả Json; qua HTTP nó đã là dict/list. Chuỗi JSON cũng chấp nhận
    được, để hàm này dùng được cả với dữ liệu đọc thẳng từ cột `Json`.

    Chỉ thử phân tích khi chuỗi TRÔNG NHƯ JSON. Bản đầu phân tích mọi chuỗi và
    trả `None` khi hỏng, nên một giá trị hoàn toàn hợp lệ như `"#AVIGIFT"`
    (hashtag LinkedIn viết dạng chuỗi thay vì mảng) biến mất không dấu vết —
    bộ test bắt được ngay lần chạy đầu.
    """
    if isinstance(gia_tri, str):
        dau = gia_tri.strip()[:1]
        if dau in ("[", "{"):
            try:
                return json.loads(gia_tri)
            except ValueError:
                return None
        return gia_tri
    return gia_tri


def chuan_hoa_forbidden_styles(raw: Any) -> str:
    """`forbidden_styles` của core là Json tự do; `brand_config` giữ một chuỗi
    ngăn cách bằng dấu phẩy. Chấp nhận ba hình dạng gặp thật: mảng chuỗi,
    `{"items": [...]}`, và đối tượng cờ bật `{"neon": true}`.

    Hình dạng lạ → chuỗi rỗng. Một hồ sơ ghi sai KHÔNG được biến thành "cấm
    mọi thứ", và cũng không được làm hỏng cả lượt đồng bộ.
    """
    raw = _doc_json(raw)
    if not raw:
        return ""
    if isinstance(raw, dict):
        if isinstance(raw.get("items"), list):
            nguon = raw["items"]
        else:
            nguon = [k for k, v in raw.items() if v is True]
    elif isinstance(raw, list):
        nguon = raw
    else:
        return ""
    sach = [str(x).strip() for x in nguon if isinstance(x, str) and str(x).strip()]
    return ", ".join(sach)


def _chuoi_hashtag(gia_tri: Any) -> Optional[str]:
    gia_tri = _doc_json(gia_tri)
    if isinstance(gia_tri, list):
        return " ".join(str(x).strip() for x in gia_tri if str(x).strip())
    if isinstance(gia_tri, str):
        return gia_tri.strip()
    return None


def anh_xa_brand_profile(ho_so: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """core `brand_profiles` → các cột `brand_config` mà core làm chủ.

    `None` (tổ chức chưa có hồ sơ bên core) → dict RỖNG, không phải một dict
    toàn null. Khác biệt quan trọng: rỗng nghĩa là "không có gì để ghi đè",
    còn toàn null sẽ XOÁ TRẮNG brand kit hiện có của tổ chức.
    """
    if not ho_so:
        return {}

    ra: Dict[str, Any] = {}
    for khoa_core, cot in ANH_XA_TRUC_TIEP.items():
        gia_tri = ho_so.get(khoa_core)
        if gia_tri is not None:
            ra[cot] = gia_tri

    cam = chuan_hoa_forbidden_styles(ho_so.get("forbidden_styles"))
    if cam:
        ra["forbidden_styles"] = cam

    hashtags = _doc_json(ho_so.get("hashtags"))
    if isinstance(hashtags, dict):
        for nen_tang, gia_tri in hashtags.items():
            cot = COT_HASHTAG.get(str(nen_tang).strip().lower())
            chuoi = _chuoi_hashtag(gia_tri)
            if cot and chuoi:
                ra[cot] = chuoi

    cta = _doc_json(ho_so.get("cta_templates"))
    if isinstance(cta, dict):
        # Lấy `default` nếu có, không thì lấy giá trị đầu tiên — `brand_config`
        # chỉ có MỘT ô CTA, nên phải chọn một.
        chon = cta.get("default") or next(
            (v for v in cta.values() if isinstance(v, str) and v.strip()), None
        )
        if isinstance(chon, str) and chon.strip():
            ra["cta_template"] = chon.strip()
    elif isinstance(cta, list) and cta:
        if isinstance(cta[0], str) and cta[0].strip():
            ra["cta_template"] = cta[0].strip()
    elif isinstance(cta, str) and cta.strip():
        ra["cta_template"] = cta.strip()

    return ra


def danh_sach_cam(brand_config_row: Dict[str, Any]) -> list:
    """`forbidden_styles` của một hàng `brand_config` thành danh sách sạch.

    Dùng ở F5: đây là RÀNG BUỘC, không phải gợi ý (đặc tả 08 mục 4b của core).
    """
    raw = (brand_config_row or {}).get("forbidden_styles") or ""
    return [x.strip() for x in str(raw).split(",") if x.strip()]
