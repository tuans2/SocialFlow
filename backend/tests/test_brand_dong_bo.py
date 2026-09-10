"""Ánh xạ brand_profiles → brand_config — Đợt 3 / F1.

Run:  cd backend && python3 tests/test_brand_dong_bo.py

Thuần: không mạng, không cơ sở dữ liệu.
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from brand_dong_bo import (  # noqa: E402
    COT_CORE_LAM_CHU,
    anh_xa_brand_profile,
    chuan_hoa_forbidden_styles,
    danh_sach_cam,
)

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


HO_SO = {
    "primary_color": "#E91E63",
    "secondary_color": "#4CAF50",
    "accent_color": "#FFC107",
    "background_color": "#FFFFFF",
    "text_color": "#212121",
    "font_heading": "Playfair Display",
    "font_body": "Inter",
    "logo_asset_id": "asset-123",
    "tone_of_voice": "ấm áp, gần gũi",
    "hashtags": {"instagram": ["#hoa", "#quatang"], "linkedin": "#AVIGIFT", "x": ["#hoa"]},
    "cta_templates": {"default": "Đặt hoa tại siin.store"},
    "forbidden_styles": {"items": ["neon", "clipart"]},
}

# ── Ánh xạ đầy đủ ───────────────────────────────────────────────────────────
ra = anh_xa_brand_profile(HO_SO)
check("primary_color", ra["primary_color"], "#E91E63")
check("background_color → light_bg", ra["light_bg"], "#FFFFFF")
check("font_heading → primary_font", ra["primary_font"], "Playfair Display")
check("font_body → body_font", ra["body_font"], "Inter")
check("tone_of_voice → tone", ra["tone"], "ấm áp, gần gũi")
check("accent_color giữ được", ra["accent_color"], "#FFC107")
check("text_color giữ được", ra["text_color"], "#212121")
check("hashtag instagram nối bằng khoảng trắng", ra["hashtags_instagram"], "#hoa #quatang")
check("hashtag linkedin nhận cả chuỗi", ra["hashtags_linkedin"], "#AVIGIFT")
check("hashtag x", ra["hashtags_x"], "#hoa")
check("cta_templates lấy default", ra["cta_template"], "Đặt hoa tại siin.store")
check("forbidden_styles thành chuỗi phẩy", ra["forbidden_styles"], "neon, clipart")
check("logo_asset_id KHÔNG ánh xạ (chưa có URL ký sẵn)", "logo_path" in ra, False)

# Không trường nào của core được phép rơi vào cột thuộc phần RIÊNG.
rieng = {"brand_name", "tagline", "dark_bg", "success_color", "warning_color",
         "danger_color", "code_font", "heading_weight", "body_weight",
         "products_json", "logo_path"}
check("không đụng cột phần riêng", sorted(set(ra) & rieng), [])
check("mọi cột trả về đều thuộc phần core làm chủ",
      sorted(set(ra) - COT_CORE_LAM_CHU), [])

# ── Hồ sơ vắng mặt ──────────────────────────────────────────────────────────
# Quan trọng: RỖNG, không phải toàn null. Toàn null sẽ xoá trắng brand kit.
check("hồ sơ None → dict rỗng", anh_xa_brand_profile(None), {})
check("hồ sơ rỗng → dict rỗng", anh_xa_brand_profile({}), {})
check("trường null bị bỏ qua, không ghi đè thành null",
      anh_xa_brand_profile({"primary_color": None, "secondary_color": "#000"}),
      {"secondary_color": "#000"})

# ── forbidden_styles: ba hình dạng ─────────────────────────────────────────
check("mảng chuỗi", chuan_hoa_forbidden_styles(["neon", "grunge"]), "neon, grunge")
check("đối tượng items", chuan_hoa_forbidden_styles({"items": ["neon"]}), "neon")
check("đối tượng cờ bật", chuan_hoa_forbidden_styles({"neon": True, "pastel": False}), "neon")
check("chuỗi JSON cũng đọc được", chuan_hoa_forbidden_styles('["neon"]'), "neon")
check("null → rỗng", chuan_hoa_forbidden_styles(None), "")
check("hình dạng lạ → rỗng, KHÔNG phải cấm mọi thứ",
      chuan_hoa_forbidden_styles(12345), "")
check("bỏ phần tử không phải chuỗi",
      chuan_hoa_forbidden_styles({"items": ["neon", 3, "  ", None]}), "neon")

# ── cta_templates: các hình dạng khác ──────────────────────────────────────
check("cta không có default thì lấy giá trị đầu",
      anh_xa_brand_profile({"cta_templates": {"instagram": "Nhắn tin đặt hoa"}})["cta_template"],
      "Nhắn tin đặt hoa")
check("cta là mảng",
      anh_xa_brand_profile({"cta_templates": ["Gọi ngay"]})["cta_template"], "Gọi ngay")
check("cta là chuỗi",
      anh_xa_brand_profile({"cta_templates": "Ghé cửa hàng"})["cta_template"], "Ghé cửa hàng")

# ── Nền tảng lạ ─────────────────────────────────────────────────────────────
check("hashtag của nền tảng chưa có cột thì bỏ qua, không ném",
      "hashtags_tiktok" in anh_xa_brand_profile({"hashtags": {"tiktok": ["#x"]}}), False)

# ── danh_sach_cam ───────────────────────────────────────────────────────────
check("đọc lại danh sách cấm từ hàng brand_config",
      danh_sach_cam({"forbidden_styles": "neon, clipart ,  "}), ["neon", "clipart"])
check("hàng không có cột → rỗng", danh_sach_cam({}), [])
check("hàng None → rỗng", danh_sach_cam(None), [])

print("=" * 62)
for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got={g!r} want={w!r}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 62)
raise SystemExit(1 if FAIL else 0)
