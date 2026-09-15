import asyncio
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from m07.domain.models import (
    ChannelPostContent,
    ContentGenerationRequest,
    ContentGenerationResponse,
)
from m07.adapters.flower_prompts import (
    FLOWER_BANNED_PHRASES,
    build_channel_prompt,
)
from agents.creator import get_brand_config

DB_PATH = Path(__file__).resolve().parent.parent.parent / "socialflow.db"


def _clean_banned_words(text: str) -> str:
    """Tự động làm sạch các cụm từ cấm nếu AI vô tình sinh ra."""
    cleaned = text
    for phrase in FLOWER_BANNED_PHRASES:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()


def _parse_channel_output(raw_text: str, channel: str) -> ChannelPostContent:
    """Phân rã văn bản sinh ra thành các trường nguyên tử (Atomic Fields)."""
    cleaned_text = _clean_banned_words(raw_text)
    lines = [line.strip() for line in cleaned_text.split("\n") if line.strip()]

    title = ""
    cta = ""
    body_lines = []
    hashtags = []

    # Tìm tất cả hashtag
    all_hashtags = re.findall(r"#\w+", cleaned_text)
    hashtags = list(dict.fromkeys(all_hashtags))  # Loại trùng lặp, giữ thứ tự

    if lines:
        title = lines[0].lstrip("#* \t")

    # Tìm CTA hoặc câu cuối
    for line in reversed(lines):
        if any(keyword in line.lower() for keyword in ["inbox", "nhắn tin", "liên hệ", "đặt hoa", "link in bio", "hotline", "direct"]):
            cta = line.lstrip("#* \t")
            break

    # Thân bài là các dòng ở giữa (loại bỏ dòng chỉ chứa hashtag)
    for line in lines[1:]:
        if line.startswith("#") and len(line.split()) <= 4:
            continue
        if line == cta:
            continue
        body_lines.append(line)

    body = "\n\n".join(body_lines) if body_lines else cleaned_text

    script_data = None
    if channel.lower() == "tiktok":
        # Bóc tách kịch bản quay cho TikTok
        script_data = {
            "hook": lines[0] if lines else "Cận cảnh bó hoa thiết kế độc bản...",
            "duration": "30-45 giây",
            "audio_suggestion": "Nhạc Lofi nhẹ nhàng, du dương hoặc Sound Trending TikTok",
            "visual_cues": [
                "0-3s: Florist cầm cành hoa chủ đạo đặt vào giấy gói",
                "4-15s: Thao tác phối màu hoa phụ và nẹp nơ ruy băng",
                "16-30s: Góc quay 360 độ bó hoa hoàn thiện dưới ánh sáng tự nhiên"
            ]
        }

    return ChannelPostContent(
        channel=channel,
        title=title[:200] if title else f"Bó hoa {channel.upper()}",
        body=body,
        hashtags=hashtags[:20],
        cta=cta if cta else "Nhắn tin cho tiệm để được tư vấn thiết kế hoa riêng nhé!",
        script=script_data,
        status="DRAFT"
    )


async def execute_generate_content(
    request: ContentGenerationRequest,
    organization_id: str,
    ai_call_fn = None
) -> ContentGenerationResponse:
    """Điều phối sinh nội dung đa kênh cho sản phẩm hoa tươi."""
    # 1. Lấy thông tin Brand Kit của tổ chức
    brand = get_brand_config(organization_id)
    brand_tone = brand.get("tone", "ấm áp, tinh tế, sang trọng")
    forbidden_styles = brand.get("forbidden_styles", "")

    # 2. Chuẩn bị AI call function
    if ai_call_fn is None:
        try:
            from main import generate_content_ai
            ai_call_fn = generate_content_ai
        except ImportError:
            # Mock fallback nếu chạy độc lập mà không có main
            async def fallback_ai(p): return f"Nội dung hoa mẫu cho {request.product_name or 'sản phẩm'}\n\nHoa tươi cao cấp thiết kế riêng.\n\n#HoaTuoi #Florist"
            ai_call_fn = fallback_ai

    channels_data: Dict[str, ChannelPostContent] = {}
    target_channels = request.channels or ["facebook", "instagram", "tiktok", "zalo"]

    # 3. Sinh bài đồng thời cho các kênh đã chọn qua asyncio.gather (Tối ưu tốc độ siêu tốc)
    async def _generate_for_channel(ch: str):
        prompt = build_channel_prompt(
            channel=ch,
            product_name=request.product_name or "",
            flower_details=request.flower_details or "",
            colors=request.colors or "",
            price=request.price,
            occasion=request.occasion,
            angle=request.angle,
            brand_tone=brand_tone,
            forbidden_styles=forbidden_styles,
            custom_instructions=request.custom_instructions
        )

        try:
            raw_output = await ai_call_fn(prompt, provider=request.provider, model=request.model)
        except TypeError:
            raw_output = await ai_call_fn(prompt)
        channel_post = _parse_channel_output(raw_output, ch)
        if request.image_url:
            channel_post.media_url = request.image_url

        # Lưu bản nháp vào CSDL SQLite của SocialFlow (bảng posts)
        try:
            import json
            from duong_dan_db import duong_dan_db
            conn = sqlite3.connect(duong_dan_db())
            cur = conn.cursor()
            media_json = json.dumps([request.image_url]) if request.image_url else None
            content_type = "image" if request.image_url else "post"
            cur.execute(
                """
                INSERT INTO posts (
                    organization_id, platform, content_type, content, media_paths, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    ch.lower(),
                    content_type,
                    (chr(10) + chr(10)).join([x for x in [channel_post.title, channel_post.body, channel_post.cta, " ".join(channel_post.hashtags)] if x]),
                    media_json,
                    "draft",
                    datetime.utcnow().isoformat(),
                ),
            )
            if cur.lastrowid:
                channel_post.post_id = str(cur.lastrowid)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[M07] Lỗi lưu DB posts ({ch}): {e}", flush=True)

        return ch, channel_post

    # Chạy song song tất cả các kênh được chọn
    tasks = [_generate_for_channel(ch) for ch in target_channels]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for item in results:
        if isinstance(item, Exception):
            print(f"[M07] Lỗi sinh bài một kênh: {item}", flush=True)
            continue
        ch_key, post_data = item
        channels_data[ch_key] = post_data

    prov = (request.provider or "ollama").lower()
    if request.model:
        mdl = request.model
    elif prov == "ollama":
        mdl = "qwen2.5:7b"
    elif prov in ("openai", "gpt"):
        mdl = "gpt-4o"
    elif prov == "gemini":
        mdl = "gemini-3.6-flash"
    elif prov == "anthropic":
        mdl = "claude-haiku-4-5-20251001"
    else:
        mdl = "default"

    return ContentGenerationResponse(
        organization_id=organization_id,
        product_id=request.product_id,
        angle=request.angle,
        channels=channels_data,
        provider=prov,
        model_name=mdl,
        created_at=datetime.utcnow().isoformat(),
    )
