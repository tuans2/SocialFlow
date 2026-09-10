"""
SocialFlow - Main Server
AI-powered social media automation with browser-based posting
"""

import os
import json
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from cryptography.fernet import Fernet

from automation import get_automation, LinkedInAutomation, InstagramAutomation, TwitterAutomation
from automation_extended import get_automation_extended
from openclaw_bridge import router as openclaw_router
from heygen_routes import router as heygen_router
from asset_inventory import get_asset, init_asset_tables, register_asset, update_asset
from assets_routes import router as assets_router
from migrations import run_migrations
from analytics_store import init_analytics_tables, register_analytics_routes
from visual_content_routes import router as visual_router
from brand_kit import router as brand_router, init_brand_tables
from agents.orchestrator import (
    run_full_pipeline, run_publish_only, run_scout_only, run_analyst_only,
    run_scheduled_cycle, run_for_organizations, list_known_organizations,
    get_status as get_pipeline_status,
)
from agents.scout import init_signals_table
from agents.planner import init_plans_table

# ============== CONFIG ==============
BACKEND_DIR = Path(__file__).resolve().parent
DATABASE_PATH = str(BACKEND_DIR / "socialflow.db")
UPLOADS_DIR = BACKEND_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# Shared media path resolution + publish quality gate (see backend/media_utils.py)
from media_store import MediaRejected, store_bytes, store_upload
import video_jobs as video_job_store
import video_providers
from video_providers import JobStatus, ProviderError
from media_utils import (
    requires_image,
    resolve_media_path,
    to_public_url,
    validate_publish_media,
)
SESSIONS_DIR = Path("sessions")
SESSIONS_DIR.mkdir(exist_ok=True)

# Organization scoping for connected platform accounts (P7 -- FloraOS
# integration layer). SocialFlow has no caller-identity/auth mechanism of its
# own yet (D1 in floraos-core's PRD is still open: whether SocialFlow becomes
# fully multi-tenant), so this is deliberately a placeholder rather than a
# real security boundary: every accounts.* endpoint now requires an
# organization_id (defaulting to this constant when the caller omits it,
# which preserves today's single-operator behaviour), but nothing yet
# verifies that the caller is entitled to the organization it names. See
# TECHNICAL_DEBT.md.
DEFAULT_ORGANIZATION_ID = os.getenv("SOCIALFLOW_DEFAULT_ORGANIZATION_ID", "default")

# Encryption for credentials
def get_or_create_key():
    key_file = Path(".secret_key")
    if key_file.exists():
        return key_file.read_bytes()
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    return key

FERNET = Fernet(get_or_create_key())

def encrypt(text: str) -> str:
    return FERNET.encrypt(text.encode()).decode()

def decrypt(text: str) -> str:
    return FERNET.decrypt(text.encode()).decode()

# Load config
def load_config():
    return {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
        "AI_PROVIDER": os.getenv("AI_PROVIDER", "ollama"),  # ollama (free), openai, anthropic, gemini
        "OLLAMA_URL": os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
        "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL", "qwen3:8b"),
        "KLING_API_KEY": os.getenv("KLING_API_KEY", ""),
        "HEYGEN_EMAIL": os.getenv("HEYGEN_EMAIL", ""),
        "HEYGEN_PASSWORD": os.getenv("HEYGEN_PASSWORD", ""),
        "HEADLESS": os.getenv("HEADLESS", "false").lower() == "true",
        "IMAGE_MODEL": os.getenv("IMAGE_MODEL", "gpt-image-1.5"),  # gpt-image-1.5 (replaces dall-e-3)
        # gpt-image-*: low | medium | high | auto ; dall-e-3: standard | hd
        "IMAGE_QUALITY": os.getenv("IMAGE_QUALITY", ""),
        "IMAGE_COST_USD": os.getenv("IMAGE_COST_USD", ""),
        "ZALO_APP_ID": os.getenv("ZALO_APP_ID", ""),
        "ZALO_SECRET_KEY": os.getenv("ZALO_SECRET_KEY", ""),
        "ZALO_REFRESH_TOKEN": os.getenv("ZALO_REFRESH_TOKEN", ""),
    }

CONFIG = load_config()

# ============== DATABASE ==============
def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    
    # Posts table
    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        content_type TEXT NOT NULL,
        content TEXT NOT NULL,
        media_paths TEXT,
        scheduled_time DATETIME,
        status TEXT DEFAULT 'draft',
        post_id TEXT,
        error_message TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        published_at DATETIME
    )''')
    
    # Accounts table (encrypted credentials)
    c.execute('''CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL UNIQUE,
        username TEXT NOT NULL,
        password_encrypted TEXT NOT NULL,
        is_logged_in INTEGER DEFAULT 0,
        last_login DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Content templates
    c.execute('''CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        platform TEXT NOT NULL,
        content_type TEXT NOT NULL,
        prompt_template TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ============== MODELS ==============
class AccountCreate(BaseModel):
    platform: str
    username: str
    password: str
    organization_id: Optional[str] = None

class ContentRequest(BaseModel):
    topic: str
    platform: str
    content_type: str  # post, carousel, reel, thread
    slides: Optional[int] = 5
    custom_instructions: Optional[str] = None


class CarouselRequest(BaseModel):
    topic: str
    slides: int = 5
    style: str = "modern"  # modern, minimal, bold, colorful
    platform: str = "instagram"  # instagram, linkedin
    custom_instructions: Optional[str] = None


class VideoRequest(BaseModel):
    prompt: str
    video_type: str = "text_to_video"  # text_to_video, image_to_video
    image_url: Optional[str] = None  # Required for image_to_video
    duration: int = 5  # 5 or 10 seconds
    aspect_ratio: str = "9:16"  # 9:16 (vertical/reel), 16:9 (horizontal), 1:1 (square)
    mode: str = "std"  # std or pro
    platform: str = "instagram"  # instagram, tiktok, youtube
    provider: Optional[str] = None  # veo | slideshow | ... ; None = configured default
    # Slideshow inputs. carousel_asset_id is the useful one: it turns a
    # carousel already in the library into a vertical reel.
    image_paths: Optional[List[str]] = None
    carousel_asset_id: Optional[str] = None
    audio_asset_id: Optional[str] = None


class ReelRequest(BaseModel):
    topic: str
    style: str = "engaging"  # engaging, educational, funny, dramatic
    duration: int = 5
    platform: str = "instagram"
    custom_instructions: Optional[str] = None
    provider: Optional[str] = None


class AvatarRequest(BaseModel):
    image_url: Optional[str] = None   # /uploads URL, asset path, or absolute path
    image_path: Optional[str] = None
    text: Optional[str] = None        # script to speak
    audio_url: Optional[str] = None   # legacy; use audio_asset_id
    audio_asset_id: Optional[str] = None
    voice_id: Optional[str] = None    # from /api/avatar-catalog
    avatar_id: Optional[str] = None       # a stock avatar instead of a photo
    talking_photo_id: Optional[str] = None
    aspect_ratio: str = "9:16"
    duration: Optional[int] = None
    provider: Optional[str] = None
    prompt: str = "speaking naturally with a friendly, professional expression"
    mode: str = "std"


class AvatarScriptRequest(BaseModel):
    topic: str
    duration: int = 30  # seconds of speech
    tone: str = "professional"  # professional, casual, enthusiastic, educational
    platform: str = "instagram"
    custom_instructions: Optional[str] = None

class PostCreate(BaseModel):
    platform: str
    content_type: str
    content: str
    media_paths: Optional[List[str]] = None
    scheduled_time: Optional[datetime] = None

class PostUpdate(BaseModel):
    content: Optional[str] = None
    scheduled_time: Optional[datetime] = None
    status: Optional[str] = None

# ============== AI CONTENT GENERATION (Multi-Provider) ==============
SYSTEM_PROMPT = "You are an expert social media content creator. Create engaging, viral-worthy content optimized for each platform. Be creative, authentic, and focus on value."

async def generate_content_ai(prompt: str) -> str:
    """Generate content using the configured AI provider (OpenAI, Anthropic, Gemini, or Ollama)"""
    provider = CONFIG.get("AI_PROVIDER", "openai").lower()

    if provider == "anthropic":
        return await _generate_anthropic(prompt)
    elif provider == "gemini":
        return await _generate_gemini(prompt)
    elif provider == "ollama":
        return await _generate_ollama(prompt)
    else:
        return await _generate_openai(prompt)


async def _generate_ollama(prompt: str) -> str:
    """Generate content using local Ollama (FREE — no API key needed)"""
    import re
    ollama_url = CONFIG.get("OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model = CONFIG.get("OLLAMA_MODEL", "qwen3:8b")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": ollama_model,
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "stream": False,
                    "options": {"temperature": 0.4, "num_predict": 500}
                },
                timeout=90.0
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Ollama error: {response.text[:200]}"
                )
            text = response.json().get("response", "").strip()
            # Strip thinking tags from models that use them
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            return text
        except httpx.ConnectError:
            raise HTTPException(
                status_code=503,
                detail="Ollama is not running. Start it with: ollama serve"
            )


async def _generate_openai(prompt: str) -> str:
    """Generate content using OpenAI GPT-4o"""
    if not CONFIG["OPENAI_API_KEY"]:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add OPENAI_API_KEY to your .env file."
        )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {CONFIG['OPENAI_API_KEY']}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 2000,
                "temperature": 0.8
            },
            timeout=60.0
        )

        if response.status_code != 200:
            error_detail = response.text
            if "invalid_api_key" in error_detail:
                raise HTTPException(status_code=401, detail="Invalid OpenAI API key.")
            if "insufficient_quota" in error_detail:
                raise HTTPException(status_code=402, detail="No OpenAI credits. Add payment method at platform.openai.com/account/billing")
            raise HTTPException(status_code=response.status_code, detail=error_detail)

        return response.json()["choices"][0]["message"]["content"]


async def _generate_anthropic(prompt: str) -> str:
    """Generate content using Anthropic Claude (free trial / paid)"""
    if not CONFIG["ANTHROPIC_API_KEY"]:
        raise HTTPException(
            status_code=400,
            detail="Anthropic API key not configured. Add ANTHROPIC_API_KEY to your .env file. Get your key at console.anthropic.com"
        )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CONFIG["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 2000,
                "system": SYSTEM_PROMPT,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=60.0
        )

        if response.status_code != 200:
            error_detail = response.text
            if "authentication_error" in error_detail:
                raise HTTPException(status_code=401, detail="Invalid Anthropic API key.")
            if "credit_balance_too_low" in error_detail:
                raise HTTPException(status_code=402, detail="Insufficient Anthropic credits.")
            raise HTTPException(status_code=response.status_code, detail=error_detail)

        return response.json()["content"][0]["text"]


async def _generate_gemini(prompt: str) -> str:
    """Generate content using Google Gemini (free tier available)"""
    if not CONFIG["GEMINI_API_KEY"]:
        raise HTTPException(
            status_code=400,
            detail="Gemini API key not configured. Add GEMINI_API_KEY to your .env file. Get your key at aistudio.google.com"
        )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={CONFIG['GEMINI_API_KEY']}",
                headers={"Content-Type": "application/json"},
                json={
                    "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 2000}
                },
                timeout=60.0
            )

            if response.status_code != 200:
                error_detail = response.text
                if "API_KEY_INVALID" in error_detail:
                    raise HTTPException(status_code=401, detail="Invalid Gemini API key.")
                raise HTTPException(status_code=response.status_code, detail=error_detail)

            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Gemini API request timed out. The Google server is overloaded or blocked. Try again later.")
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Failed to connect to Gemini API: {str(e)}")


def _image_cost_usd():
    """Per-image cost, if the operator configured one. Never guessed."""
    raw = (CONFIG.get("IMAGE_COST_USD") or "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


async def generate_image_asset(prompt: str, size: str = "1024x1024",
                               save_as: str = None, title: str = "",
                               topic: str = "", campaign: str = "",
                               tags: list = None,
                               parent_asset_id: str = None) -> dict:
    """Generate an image and store it in the media library.

    Returns the media_store record: asset_id, path, url, thumb_url, sha256...
    """
    import base64
    if not CONFIG["OPENAI_API_KEY"]:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured. Add OPENAI_API_KEY to .env")

    model = CONFIG.get("IMAGE_MODEL", "gpt-image-1.5")

    # The `quality` enum differs per model family:
    #   gpt-image-*  -> low | medium | high | auto
    #   dall-e-3     -> standard | hd
    quality = (CONFIG.get("IMAGE_QUALITY") or "").strip()
    if not quality:
        quality = "high" if model.startswith("gpt-image") else "standard"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {CONFIG['OPENAI_API_KEY']}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": size,
                "quality": quality
            },
            timeout=120.0
        )

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)

        data = response.json()["data"][0]

        # GPT Image models return base64, DALL-E returned URLs
        if "b64_json" in data:
            image_bytes = base64.b64decode(data["b64_json"])
        elif "url" in data:
            # Fallback for older models
            img_resp = await client.get(data["url"], timeout=60.0)
            image_bytes = img_resp.content
        else:
            raise HTTPException(status_code=500, detail="No image data in response")

        # Hand the bytes to the media store: it hashes, deduplicates, places
        # the file under uploads/YYYY/MM/image/, thumbnails it and records an
        # inventory row. Previously this wrote a loose file that nothing tracked.
        if not save_as:
            save_as = f"generated-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"

        return store_bytes(
            image_bytes,
            declared_name=save_as,
            origin="generated",
            provider=model,
            title=title or save_as,
            topic=topic,
            campaign=campaign,
            tags=tags or [],
            parent_asset_id=parent_asset_id,
            cost_usd=_image_cost_usd(),
            extra_metadata={
                "prompt": prompt[:2000],
                "size": size,
                "quality": quality,
            },
        )


async def generate_image(prompt: str, size: str = "1024x1024", save_as: str = None) -> str:
    """Generate an image; returns its stored path (backwards-compatible shape)."""
    result = await generate_image_asset(prompt, size, save_as=save_as)
    return result["path"]


# Keep backward compat alias
async def generate_image_dalle(prompt: str, size: str = "1024x1024") -> str:
    """Backward-compatible wrapper — now uses GPT Image 1.5"""
    path = await generate_image(prompt, size)
    return path


async def download_image(url: str, filename: str) -> str:
    """Download image from URL and save locally"""
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=60.0)
        filepath = UPLOADS_DIR / filename
        with open(filepath, 'wb') as f:
            f.write(response.content)
        return str(filepath)


# ============== KLING AI VIDEO GENERATION ==============
class KlingAI:
    """Kling AI video generation integration"""
    
    # Using official Kling API endpoint
    BASE_URL = "https://api.klingai.com/v1"
    
    @staticmethod
    async def generate_video_from_text(
        prompt: str,
        duration: int = 5,
        aspect_ratio: str = "9:16",  # Vertical for reels
        mode: str = "std"  # std or pro
    ) -> dict:
        """Generate video from text prompt"""
        if not CONFIG["KLING_API_KEY"]:
            raise HTTPException(status_code=400, detail="Kling API key not configured. Add KLING_API_KEY to .env")
        
        async with httpx.AsyncClient() as client:
            # Create video generation task
            response = await client.post(
                f"{KlingAI.BASE_URL}/videos/text2video",
                headers={
                    "Authorization": f"Bearer {CONFIG['KLING_API_KEY']}",
                    "Content-Type": "application/json"
                },
                json={
                    "prompt": prompt,
                    "duration": duration,
                    "aspect_ratio": aspect_ratio,
                    "mode": mode
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            
            return response.json()
    
    @staticmethod
    async def generate_video_from_image(
        image_url: str,
        prompt: str,
        duration: int = 5,
        mode: str = "std"
    ) -> dict:
        """Generate video from image + prompt"""
        if not CONFIG["KLING_API_KEY"]:
            raise HTTPException(status_code=400, detail="Kling API key not configured")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{KlingAI.BASE_URL}/videos/image2video",
                headers={
                    "Authorization": f"Bearer {CONFIG['KLING_API_KEY']}",
                    "Content-Type": "application/json"
                },
                json={
                    "image_url": image_url,
                    "prompt": prompt,
                    "duration": duration,
                    "mode": mode
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            
            return response.json()
    
    @staticmethod
    async def check_task_status(task_id: str) -> dict:
        """Check video generation task status"""
        if not CONFIG["KLING_API_KEY"]:
            raise HTTPException(status_code=400, detail="Kling API key not configured")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{KlingAI.BASE_URL}/videos/tasks/{task_id}",
                headers={
                    "Authorization": f"Bearer {CONFIG['KLING_API_KEY']}",
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            
            return response.json()
    
    @staticmethod
    async def wait_for_video(task_id: str, max_wait: int = 300) -> dict:
        """Poll until video is ready (max 5 minutes)"""
        import asyncio
        
        start_time = datetime.now()
        while (datetime.now() - start_time).seconds < max_wait:
            result = await KlingAI.check_task_status(task_id)
            status = result.get("status", "").lower()
            
            if status == "completed":
                return result
            elif status in ["failed", "error"]:
                raise HTTPException(status_code=500, detail=f"Video generation failed: {result}")
            
            # Wait 5 seconds before polling again
            await asyncio.sleep(5)
        
        raise HTTPException(status_code=408, detail="Video generation timed out")
    
    @staticmethod
    async def generate_avatar_video(
        image_url: str,
        audio_url: str = None,
        text: str = None,
        voice_id: str = "en_female_1",
        prompt: str = "speaking naturally with a friendly expression",
        mode: str = "std"
    ) -> dict:
        """Generate talking avatar video from photo + audio/text"""
        if not CONFIG["KLING_API_KEY"]:
            raise HTTPException(status_code=400, detail="Kling API key not configured")
        
        async with httpx.AsyncClient() as client:
            payload = {
                "image_url": image_url,
                "prompt": prompt,
                "mode": mode
            }
            
            # Either use provided audio or generate from text
            if audio_url:
                payload["audio_url"] = audio_url
            elif text:
                payload["text"] = text
                payload["voice_id"] = voice_id
            else:
                raise HTTPException(status_code=400, detail="Either audio_url or text is required")
            
            response = await client.post(
                f"{KlingAI.BASE_URL}/videos/avatar",
                headers={
                    "Authorization": f"Bearer {CONFIG['KLING_API_KEY']}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=30.0
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            
            return response.json()
    
    @staticmethod
    async def generate_lip_sync(
        video_url: str,
        audio_url: str = None,
        text: str = None,
        voice_id: str = "en_female_1"
    ) -> dict:
        """Add lip sync to existing video"""
        if not CONFIG["KLING_API_KEY"]:
            raise HTTPException(status_code=400, detail="Kling API key not configured")
        
        async with httpx.AsyncClient() as client:
            payload = {"video_url": video_url}
            
            if audio_url:
                payload["audio_url"] = audio_url
            elif text:
                payload["text"] = text
                payload["voice_id"] = voice_id
            
            response = await client.post(
                f"{KlingAI.BASE_URL}/videos/lip-sync",
                headers={
                    "Authorization": f"Bearer {CONFIG['KLING_API_KEY']}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=30.0
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            
            return response.json()

# ============== SCHEDULER ==============
scheduler = AsyncIOScheduler()

async def publish_scheduled_post(post_id: int):
    """Publish a scheduled post using browser automation"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
    post = dict(c.fetchone())
    post_org_id = post.get("organization_id") or DEFAULT_ORGANIZATION_ID

    # D1 (đa tenant thật, A5): scoped to the post's own organization — before
    # this, any organization's scheduled post would publish through
    # whichever organization's account happened to match the platform name
    # (only safe by accident, because `accounts.platform` used to be
    # globally unique before Phase 1 rebuilt it as UNIQUE(organization_id,
    # platform)). See TECHNICAL_DEBT.md.
    c.execute(
        "SELECT * FROM accounts WHERE platform = ? AND COALESCE(organization_id, ?) = ?",
        (post["platform"], DEFAULT_ORGANIZATION_ID, post_org_id)
    )
    account_row = c.fetchone()
    
    if not account_row:
        c.execute("UPDATE posts SET status = 'failed', error_message = 'No account connected' WHERE id = ?", (post_id,))
        conn.commit()
        conn.close()
        return
    
    account = dict(account_row)

    # ---- PUBLISH QUALITY GATE (backend enforcement, before any browser) ----
    gate_ok, gate_error, media_paths = validate_publish_media(
        post["platform"], post.get("content_type"), post.get("media_paths")
    )
    if not gate_ok:
        print(f"[QualityGate] BLOCKED post #{post_id}: {gate_error}")
        c.execute(
            "UPDATE posts SET status = 'failed', error_message = ? WHERE id = ?",
            (gate_error, post_id)
        )
        conn.commit()
        conn.close()
        return

    try:
        # Get automation instance, scoped to this post's organization
        auto = get_automation_extended(post["platform"], headless=CONFIG["HEADLESS"],
                                        organization_id=post_org_id)

        # Post content (media_paths already resolved to absolute, existing files)
        result = await auto.post(post["content"], media_paths or None)
        
        if result["success"]:
            c.execute(
                "UPDATE posts SET status = 'published', published_at = ? WHERE id = ?",
                (datetime.now().isoformat(), post_id)
            )
        else:
            c.execute(
                "UPDATE posts SET status = 'failed', error_message = ? WHERE id = ?",
                (result["message"], post_id)
            )
    except Exception as e:
        c.execute(
            "UPDATE posts SET status = 'failed', error_message = ? WHERE id = ?",
            (str(e), post_id)
        )
    
    conn.commit()
    conn.close()

# ============== APP SETUP ==============
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_brand_tables()
    init_signals_table()
    init_plans_table()
    # D1 (đa tenant thật, A5) — run_migrations() ALSO runs once at module
    # import time (below, right after init_asset_tables()), but posts/
    # accounts/templates/signals/content_plans/brand_config only exist from
    # THIS point on — they are created above, inside lifespan(), which fires
    # after import. On a genuinely fresh database, the module-level call
    # would find none of those tables yet and silently skip every column it
    # was supposed to add to them (reported in "skipped", never retried).
    # migrations.py's own rules say every migration is idempotent and safe
    # to run more than once, so calling it again here — now that every
    # table actually exists — is the fix, not a workaround.
    run_migrations()
    scheduler.start()

    # ── Schedule autonomous pipeline jobs ──
    from apscheduler.triggers.cron import CronTrigger

    # Morning pipeline: Scout → Planner → Creator → Reviewer (no auto-publish)
    scheduler.add_job(
        _run_pipeline_job,
        CronTrigger(hour=8, minute=7),
        id="daily_pipeline_morning",
        replace_existing=True,
        kwargs={"skip_publish": True}
    )

    # Afternoon news refresh
    scheduler.add_job(
        _run_scout_job,
        CronTrigger(hour=14, minute=0),
        id="afternoon_scout",
        replace_existing=True
    )

    # Publish windows: 11 AM and 11 PM
    scheduler.add_job(
        _run_publish_job,
        CronTrigger(hour=11, minute=0),
        id="publish_11am",
        replace_existing=True
    )
    scheduler.add_job(
        _run_publish_job,
        CronTrigger(hour=23, minute=0),
        id="publish_11pm",
        replace_existing=True
    )

    # Daily analytics: end of day
    scheduler.add_job(
        _run_analyst_job,
        CronTrigger(hour=23, minute=30),
        id="daily_analyst",
        replace_existing=True
    )

    # Reschedule pending posts
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, scheduled_time FROM posts WHERE status = 'scheduled' AND scheduled_time > ?",
              (datetime.now().isoformat(),))
    for row in c.fetchall():
        post_id, scheduled_time = row
        scheduler.add_job(
            publish_scheduled_post,
            DateTrigger(run_date=datetime.fromisoformat(scheduled_time)),
            args=[post_id],
            id=f"post_{post_id}",
            replace_existing=True
        )
    conn.close()

    print("SocialFlow started — autonomous pipeline scheduled")
    print("  8:07 AM  — Full pipeline (Scout → Planner → Creator → Reviewer)")
    print("  11:00 AM — Publish approved content")
    print("  2:00 PM  — News refresh (Scout only)")
    print("  11:00 PM — Publish remaining content")
    print("  11:30 PM — Daily analytics report")

    yield
    scheduler.shutdown()


# ── Scheduler job wrappers (A6) ──
# Real multi-organization execution: every job below loops over
# list_known_organizations() (the distinct organization_ids in `accounts` —
# SocialFlow has no organizations table of its own) in bounded-parallel
# batches (run_for_organizations / run_scheduled_cycle, A2's design actually
# wired up), not just DEFAULT_ORGANIZATION_ID as before A6. A single-org
# install (only one organization_id ever appears in `accounts`, or none yet)
# behaves exactly as before — the loop just has one organization in it.
async def _run_pipeline_job(skip_publish=True):
    await run_scheduled_cycle(ai_generate_fn=generate_content_ai, skip_publish=skip_publish)

async def _run_scout_job():
    # Scout is shared across every organization (D1 A5) — runs once, not once
    # per organization.
    await run_scout_only()

async def _run_publish_job():
    await run_for_organizations(
        run_publish_only,
        automation_getter=lambda p, org: get_automation_extended(p, organization_id=org),
    )

async def _run_analyst_job():
    await run_for_organizations(run_analyst_only)

app = FastAPI(title="SocialFlow", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register OpenClaw bridge routes
app.include_router(openclaw_router)
# Register HeyGen video + asset inventory routes
app.include_router(heygen_router)
# Initialize asset inventory tables, then apply any pending column additions.
# CREATE TABLE IF NOT EXISTS is a no-op once a table exists, so new columns on
# an existing assets table only arrive through run_migrations().
init_asset_tables()
run_migrations()
# Register the media library (upload / browse / reuse)
app.include_router(assets_router)
# Initialize analytics tables and routes
init_analytics_tables()
register_analytics_routes(app)
# Register visual content routes
app.include_router(visual_router)
# Register brand kit routes
app.include_router(brand_router)

# Serve frontend
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

# ============== API ROUTES ==============

@app.get("/")
async def serve_frontend():
    return FileResponse("../frontend/index.html")

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# ============== PIPELINE API ==============

@app.post("/api/pipeline/run")
async def api_run_pipeline(background_tasks: BackgroundTasks, organization_id: Optional[str] = None):
    """Run the full autonomous pipeline in background, for ONE organization
    (the dashboard button / manual on-demand run — the automatic schedule
    goes through run_scheduled_cycle() instead, see the scheduler jobs
    below). Busy-checking is per organization (A6) — this only 409s if
    THIS SAME organization already has a run in flight; a different
    organization's concurrent run never blocks it."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    status = get_pipeline_status(org_id)
    if status.running:
        raise HTTPException(
            status_code=409,
            detail=f"Pipeline dang chay cho to chuc '{org_id}'. Vui long thu lai sau.",
        )
    background_tasks.add_task(
        run_full_pipeline,
        ai_generate_fn=generate_content_ai,
        skip_publish=True,  # Don't auto-publish — let user review in queue
        organization_id=org_id,
    )
    return {"status": "started", "message": "Pipeline running in background", "organization_id": org_id}


@app.get("/api/pipeline/status")
async def api_pipeline_status(organization_id: Optional[str] = None):
    """Get this organization's pipeline status (A6 — status is per organization now)."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    return get_pipeline_status(org_id).to_dict()


@app.get("/api/pipeline/queue")
async def api_pipeline_queue(status_filter: str = None, platform: str = None):
    """Get content queue (drafts, approved, posted, failed)."""
    conn = get_db()
    query = "SELECT * FROM posts WHERE 1=1"
    params = []
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    query += " ORDER BY created_at DESC LIMIT 50"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/pipeline/approve/{post_id}")
async def api_approve_post(post_id: int):
    """Manually approve a post."""
    conn = get_db()
    conn.execute("UPDATE posts SET status = 'approved' WHERE id = ? AND status IN ('draft', 'review')", (post_id,))
    conn.commit()
    conn.close()
    return {"status": "approved", "post_id": post_id}


@app.post("/api/pipeline/reject/{post_id}")
async def api_reject_post(post_id: int):
    """Reject a post."""
    conn = get_db()
    conn.execute("UPDATE posts SET status = 'blocked' WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    return {"status": "blocked", "post_id": post_id}


@app.post("/api/pipeline/publish/{post_id}")
async def api_publish_post(post_id: int, background_tasks: BackgroundTasks):
    """Publish a specific approved post immediately."""
    conn = get_db()
    row = conn.execute("SELECT * FROM posts WHERE id = ? AND status = 'approved'", (post_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Post not found or not approved")

    post_row = dict(row)

    # ---- PUBLISH QUALITY GATE ----
    gate_ok, gate_error, _ = validate_publish_media(
        post_row["platform"], post_row.get("content_type"), post_row.get("media_paths")
    )
    if not gate_ok:
        conn.execute(
            "UPDATE posts SET status = 'failed', error_message = ? WHERE id = ?",
            (gate_error, post_id)
        )
        conn.commit()
        conn.close()
        print(f"[QualityGate] BLOCKED post #{post_id}: {gate_error}")
        raise HTTPException(status_code=400, detail=gate_error)
    conn.close()
    # Publish in background
    background_tasks.add_task(_publish_single_post, dict(row))
    return {"status": "publishing", "post_id": post_id}


async def _publish_single_post(post: dict):
    """Publish a single post (background task)."""
    from agents.publisher import publish_post
    await publish_post(post, automation_getter=lambda p, org: get_automation_extended(p, organization_id=org))


@app.get("/api/pipeline/signals")
async def api_get_signals(limit: int = 20):
    """Get recent intelligence signals from Scout."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM signals ORDER BY fetched_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        conn.close()
        return []


@app.get("/api/pipeline/analytics")
async def api_get_analytics(organization_id: Optional[str] = None):
    """Get this organization's daily analytics summary."""
    from agents.analyst import get_daily_stats, get_weekly_stats
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    return {
        "today": get_daily_stats(organization_id=org_id),
        "week": get_weekly_stats(organization_id=org_id),
    }


# --- Accounts ---
@app.get("/api/accounts")
async def get_accounts(organization_id: Optional[str] = None):
    """Get connected accounts for one organization (without passwords)."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """SELECT id, platform, username, is_logged_in, last_login, organization_id
           FROM accounts WHERE COALESCE(organization_id, ?) = ?""",
        (DEFAULT_ORGANIZATION_ID, org_id)
    )
    accounts = [dict(row) for row in c.fetchall()]
    conn.close()
    return {"accounts": accounts}

@app.post("/api/accounts")
async def add_account(account: AccountCreate):
    """Add account credentials for one organization.

    D1 (đa tenant thật, 2026-09-10): `accounts` was rebuilt with
    UNIQUE(organization_id, platform) (migrations.py), replacing the old
    global UNIQUE(platform). Two different organizations can each hold their
    own credentials for the same platform now — that used to be refused with
    409 only because the old constraint made it impossible to store safely,
    not because it was undesirable. INSERT OR REPLACE keys off the new
    composite constraint: the same organization re-adding the same platform
    still replaces its own row; a different organization gets its own row.
    """
    org_id = account.organization_id or DEFAULT_ORGANIZATION_ID
    platform = account.platform.lower()
    conn = get_db()
    c = conn.cursor()

    # Encrypt password
    encrypted_pw = encrypt(account.password)

    c.execute(
        """INSERT OR REPLACE INTO accounts
               (platform, username, password_encrypted, is_logged_in, organization_id)
           VALUES (?, ?, ?, 0, ?)""",
        (platform, account.username, encrypted_pw, org_id)
    )
    conn.commit()
    conn.close()

    return {"success": True, "message": f"Account added for {account.platform}"}

@app.post("/api/accounts/{platform}/login")
async def login_account(platform: str, organization_id: Optional[str] = None):
    """Login to a platform using saved credentials, scoped to one organization."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM accounts WHERE platform = ? AND COALESCE(organization_id, ?) = ?",
        (platform.lower(), DEFAULT_ORGANIZATION_ID, org_id)
    )
    row = c.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Account not found")

    account = dict(row)
    password = decrypt(account["password_encrypted"])

    try:
        auto = get_automation_extended(platform, headless=False, organization_id=org_id)  # Always visible for login
        result = await auto.login(account["username"], password)
        await auto.close()

        if result["success"]:
            c.execute(
                "UPDATE accounts SET is_logged_in = 1, last_login = ? WHERE platform = ? AND COALESCE(organization_id, ?) = ?",
                (datetime.now().isoformat(), platform.lower(), DEFAULT_ORGANIZATION_ID, org_id)
            )
            conn.commit()

        conn.close()
        return result

    except Exception as e:
        conn.close()
        return {"success": False, "message": str(e)}

@app.post("/api/accounts/{platform}/check")
async def check_login_status(platform: str, organization_id: Optional[str] = None):
    """Check if still logged in to a platform, scoped to one organization."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT id FROM accounts WHERE platform = ? AND COALESCE(organization_id, ?) = ?",
        (platform.lower(), DEFAULT_ORGANIZATION_ID, org_id)
    )
    if not c.fetchone():
        conn.close()
        return {"logged_in": False, "error": "Account not found"}

    try:
        auto = get_automation_extended(platform, headless=True, organization_id=org_id)
        is_logged_in = await auto.check_login()
        await auto.close()

        c.execute(
            "UPDATE accounts SET is_logged_in = ? WHERE platform = ? AND COALESCE(organization_id, ?) = ?",
            (1 if is_logged_in else 0, platform.lower(), DEFAULT_ORGANIZATION_ID, org_id)
        )
        conn.commit()
        conn.close()

        return {"logged_in": is_logged_in}
    except Exception as e:
        conn.close()
        return {"logged_in": False, "error": str(e)}

@app.delete("/api/accounts/{platform}")
async def delete_account(platform: str, organization_id: Optional[str] = None):
    """Delete an account and its session, scoped to one organization."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "DELETE FROM accounts WHERE platform = ? AND COALESCE(organization_id, ?) = ?",
        (platform.lower(), DEFAULT_ORGANIZATION_ID, org_id)
    )
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()

    if not deleted:
        raise HTTPException(status_code=404, detail="Account not found")

    # Delete session file
    session_file = SESSIONS_DIR / f"{platform.lower()}_session.json"
    if session_file.exists():
        session_file.unlink()

    return {"deleted": True}

# --- Content Generation ---
def build_post_image_prompt(platform: str, topic: str, content: str,
                            custom_instructions: str = None) -> str:
    """Build the visual prompt for a single social post image.

    Derived from platform + topic + the generated caption + custom instructions.
    """
    excerpt = " ".join((content or "").split())
    if len(excerpt) > 600:
        excerpt = excerpt[:600] + "..."
    extra = ""
    if custom_instructions and custom_instructions.strip():
        extra = f"\nAdditional creative direction: {custom_instructions.strip()}"

    return f"""Create a high-quality, visual-first social media image for a {platform} post.

Topic: {topic}
The image must match the meaning and the emotion of this caption: "{excerpt}"{extra}

Requirements:
- Square 1:1 composition, optimized for social media feeds
- Visual storytelling only: no paragraphs, no long sentences, no dense text blocks
- Do NOT invent logos, brand marks, or fake company names
- Photographic or clean modern illustration, vibrant but uncluttered
- Professional, high production quality, appropriate for a business audience"""


@app.post("/api/generate")
async def generate_content(request: ContentRequest):
    """Generate AI content"""
    
    # Build prompt based on platform and content type
    platform_tips = {
        "linkedin": "Professional tone, thought leadership, industry insights. Use line breaks for readability. Include 3-5 relevant hashtags at the end.",
        "instagram": "Casual, engaging, visual-first. Use emojis strategically. Include 20-30 hashtags in a comment or at the end.",
        "twitter": "Concise, punchy, conversation-starting. Max 280 characters for single tweets. Use threads for longer content.",
        "zalo": "Viết bằng tiếng Việt, thân thiện, gần gũi. Phù hợp với đối tượng người dùng Việt Nam. Tiêu đề hấp dẫn, nội dung súc tích. Dùng emoji vừa phải.",
        "tiktok": "Năng động, Gen Z, bắt trend. Rất ngắn gọn. Caption chỉ khoảng 1-2 câu để kích thích comment. Thêm 3-5 hashtag đang thịnh hành."
    }
    
    content_formats = {
        "post": f"Create a single engaging {request.platform} post",
        "carousel": f"Create a {request.slides}-slide carousel. For EACH slide provide:\n- **Slide N:** [Headline]\n- Content (2-3 short sentences)\n- Visual suggestion",
        "reel": "Create a 30-60 second video script with:\n- Hook (first 3 sec)\n- Main points (3-5 quick tips)\n- CTA\n- Caption\n- Hashtags\n- Audio/music suggestion",
        "thread": "Create a Twitter/X thread with 5-8 tweets. Number each tweet. First tweet is the hook. Last tweet is the CTA."
    }
    
    # A single post is published verbatim, so it must not contain production
    # notes. carousel / reel / thread keep their structured section format.
    output_rules = ""
    if request.content_type == "post":
        output_rules = """

Output rules:
- Return ONLY the final post text, ready to publish as-is.
- No placeholders or production notes: never write things like
  [Attached Image: ...], [Insert Call-to-Action Button: ...], "Visual suggestion:",
  "Caption:" or any bracketed instruction.
- Do not describe or refer to an attached image — a separate visual is generated
  automatically and will be uploaded with the post."""

    prompt = f"""{content_formats.get(request.content_type, 'Create a post')} about: {request.topic}

Platform: {request.platform.upper()}
Tips: {platform_tips.get(request.platform.lower(), '')}

{request.custom_instructions or ''}

Make it engaging, authentic, and optimized for maximum reach.{output_rules}"""

    content = await generate_content_ai(prompt)

    response = {
        "content": content,
        "platform": request.platform,
        "content_type": request.content_type,
        "media_paths": [],
        "generated_at": datetime.now().isoformat()
    }

    # --- Visual: a single post on an image-required platform also gets one image ---
    # carousel keeps its own /api/generate-carousel slide pipeline,
    # reel keeps the video pipeline, thread stays text-only.
    if requires_image(request.platform, request.content_type):
        image_prompt = build_post_image_prompt(
            platform=request.platform,
            topic=request.topic,
            content=content,
            custom_instructions=request.custom_instructions
        )
        filename = f"post_{request.platform.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        try:
            asset = await generate_image_asset(
                image_prompt, "1024x1024", save_as=filename,
                title=request.topic or filename,
                topic=request.topic or "",
                tags=["post", request.platform.lower()],
            )
            abs_path = resolve_media_path(asset["path"])
            if not abs_path:
                raise RuntimeError("generated image file not found after saving")
            response["media_paths"] = [abs_path]
            response["image_path"] = abs_path
            response["image_url"] = asset["url"]
            response["asset_id"] = asset["asset_id"]
            response["thumb_url"] = asset.get("thumb_url")
        except Exception as e:
            detail = e.detail if isinstance(e, HTTPException) else str(e)
            response["image_required"] = True
            response["image_error"] = (
                f"Content generated successfully, but image generation failed: {detail}"
            )

    return response


# --- Carousel Generation ---
@app.post("/api/generate-carousel")
async def generate_carousel(request: CarouselRequest):
    """Generate a complete carousel with AI images and text"""
    
    # Step 1: Generate slide content with GPT-4
    content_prompt = f"""Create a {request.slides}-slide carousel for {request.platform} about: {request.topic}

For EACH slide, provide in this exact JSON format:
{{
    "slides": [
        {{
            "slide_number": 1,
            "headline": "Catchy headline (max 6 words)",
            "body": "Supporting text (1-2 short sentences)",
            "image_prompt": "Detailed image description for AI generation - describe visual style, colors, elements"
        }}
    ],
    "caption": "Instagram/LinkedIn caption with hashtags"
}}

Slide 1 = Hook/Title slide (attention grabbing)
Last slide = CTA (follow, like, save, etc.)
Style: {request.style}

{request.custom_instructions or ''}

Return ONLY valid JSON, no other text."""

    content_response = await generate_content_ai(content_prompt)
    
    # Parse the JSON response
    try:
        # Clean up response - remove markdown code blocks if present
        clean_response = content_response.strip()
        if clean_response.startswith("```"):
            clean_response = clean_response.split("```")[1]
            if clean_response.startswith("json"):
                clean_response = clean_response[4:]
        clean_response = clean_response.strip()
        
        carousel_data = json.loads(clean_response)
    except json.JSONDecodeError:
        return {"error": "Failed to parse AI response", "raw": content_response}
    
    # Step 2: Generate images for each slide.
    # The carousel itself gets a parent asset row (no file of its own) so the
    # set can be pulled back out of the library as one thing later; each slide
    # links to it via parent_asset_id.
    generated_images = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    carousel_asset = register_asset(
        asset_type="carousel",
        source="carousel-maker",
        title=request.topic,
        description=carousel_data.get("caption", "")[:500],
        topic=request.topic,
        tags=["carousel", request.platform, request.style],
        origin="generated",
        provider=CONFIG.get("IMAGE_MODEL", "gpt-image-1.5"),
        metadata={
            "platform": request.platform,
            "style": request.style,
            "slide_count": len(carousel_data.get("slides", [])),
            "caption": carousel_data.get("caption", ""),
        },
    )
    carousel_asset_id = carousel_asset["asset_id"]
    
    for i, slide in enumerate(carousel_data.get("slides", [])):
        image_prompt = slide.get("image_prompt", request.topic)
        
        # Enhance prompt for better carousel images
        enhanced_prompt = f"""Create a clean, professional social media carousel slide image.
Style: {request.style}, modern, eye-catching
Content hint: {image_prompt}
Requirements: 
- Bold, readable text area (leave space for overlay text)
- Vibrant but not cluttered
- Instagram/LinkedIn optimized (square format works best)
- Professional, high-quality look
Do NOT include any text in the image."""

        try:
            filename = f"carousel_{timestamp}_slide_{i+1}.png"
            asset = await generate_image_asset(
                enhanced_prompt, "1024x1024", save_as=filename,
                title=f"{request.topic} — slide {i+1}",
                topic=request.topic,
                tags=["carousel", "slide", request.platform],
                parent_asset_id=carousel_asset_id,
            )

            generated_images.append({
                "slide_number": i + 1,
                "headline": slide.get("headline", ""),
                "body": slide.get("body", ""),
                "image_path": asset["path"],
                "image_url": asset["url"],
                "thumb_url": asset.get("thumb_url"),
                "asset_id": asset["asset_id"],
            })
        except Exception as e:
            generated_images.append({
                "slide_number": i + 1,
                "headline": slide.get("headline", ""),
                "body": slide.get("body", ""),
                "error": str(e)
            })
    
    # Record how many slides actually made it, so a partially failed carousel
    # is visible in the library rather than silently looking complete.
    ok_slides = [g for g in generated_images if g.get("image_path")]
    update_asset(
        carousel_asset_id,
        description=(carousel_data.get("caption", "") or "")[:500],
        state="available" if len(ok_slides) == len(generated_images) else "partial",
        # The carousel owns its slide list. parent_asset_id alone cannot: when
        # a generated image is byte-identical to one already stored, the media
        # store reuses that asset — and its parent still points at the FIRST
        # carousel that produced it. The slide would silently vanish from this
        # one while the response still claimed it. An ordered list here is
        # authoritative and unaffected by deduplication.
        metadata={"slide_asset_ids": [g["asset_id"] for g in ok_slides]},
    )

    return {
        "success": True,
        "carousel": {
            "topic": request.topic,
            "platform": request.platform,
            "style": request.style,
            "slides": generated_images,
            "caption": carousel_data.get("caption", ""),
            "asset_id": carousel_asset_id,
            "slides_ok": len(ok_slides),
            "slides_total": len(generated_images),
            "generated_at": datetime.now().isoformat()
        }
    }


# --- Video Generation (Kling AI) ---
@app.get("/api/video-providers")
async def list_video_providers():
    """Which video backends exist and which are actually usable."""
    return {
        "providers": video_providers.available(),
        "default": video_providers.default_name(),
    }


async def _read_local_image(image_url: str) -> tuple:
    """Turn an image reference into bytes for image-to-video.

    Accepts anything the media resolver understands — an asset path, an
    /uploads URL, an absolute path — so a picture already in the library can
    seed a video without a round trip through the network.
    """
    if not image_url:
        return None, None
    candidate = image_url
    if "/uploads/" in candidate:
        candidate = candidate[candidate.index("/uploads/"):]
    resolved = resolve_media_path(candidate)
    if resolved:
        suffix = Path(resolved).suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}.get(suffix, "image/png")
        return Path(resolved).read_bytes(), mime
    if image_url.startswith(("http://", "https://")):
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(image_url, timeout=60.0)
        if resp.status_code == 200:
            return resp.content, resp.headers.get("content-type", "image/png")
    raise HTTPException(status_code=400, detail=f"Could not read image: {image_url}")


def _carousel_slide_records(carousel_asset_id: str) -> List[dict]:
    """Slide assets of a carousel, in order.

    Prefers the ordered id list stored on the carousel, which survives
    deduplication; falls back to the parent_asset_id back-pointer for
    carousels created before that list existed.
    """
    carousel = get_asset(carousel_asset_id)
    if carousel:
        try:
            meta = json.loads(carousel.get("metadata") or "{}")
        except (TypeError, ValueError):
            meta = {}
        ids = meta.get("slide_asset_ids") or []
        if ids:
            found = [get_asset(i) for i in ids]
            return [f for f in found if f]

    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM assets WHERE parent_asset_id = ? "
            "AND asset_type = 'image' ORDER BY id",
            (carousel_asset_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _slides_of_carousel(carousel_asset_id: str) -> List[str]:
    """Every slide of a carousel, in order, as absolute file paths."""
    resolved = []
    for row in _carousel_slide_records(carousel_asset_id):
        path = resolve_media_path(row.get("file_path"))
        if path:
            resolved.append(path)
    return resolved


def _asset_file(asset_id: str) -> Optional[str]:
    row = get_asset(asset_id) if asset_id else None
    return resolve_media_path(row["file_path"]) if row and row.get("file_path") else None


def _slideshow_inputs(request) -> dict:
    """Turn library references into the file paths the renderer needs."""
    images: List[str] = []

    if getattr(request, "carousel_asset_id", None):
        images = _slides_of_carousel(request.carousel_asset_id)
        if not images:
            raise HTTPException(
                status_code=400,
                detail=f"Carousel {request.carousel_asset_id} has no slides on disk",
            )

    for ref in (getattr(request, "image_paths", None) or []):
        path = _asset_file(ref) if str(ref).startswith("asset-") else resolve_media_path(ref)
        if not path:
            raise HTTPException(status_code=400, detail=f"Image not found: {ref}")
        images.append(path)

    audio = None
    if getattr(request, "audio_asset_id", None):
        audio = _asset_file(request.audio_asset_id)
        if not audio:
            raise HTTPException(
                status_code=400,
                detail=f"Audio asset {request.audio_asset_id} not found",
            )

    return {"image_paths": images, "audio_path": audio}


@app.post("/api/generate-video")
async def generate_video(request: VideoRequest, background_tasks: BackgroundTasks = None):
    """Start a video generation on the configured provider."""
    try:
        provider = video_providers.get(getattr(request, "provider", None))

        image_bytes = image_mime = None
        if request.video_type == "image_to_video":
            if not request.image_url:
                raise HTTPException(
                    status_code=400,
                    detail="image_url is required for image_to_video",
                )
            if not provider.supports_image_to_video:
                raise HTTPException(
                    status_code=400,
                    detail=f"{provider.label} does not support image-to-video",
                )
            image_bytes, image_mime = await _read_local_image(request.image_url)

        extras = _slideshow_inputs(request) if provider.name == "slideshow" else {}

        job = await provider.submit(
            request.prompt,
            duration=request.duration,
            aspect_ratio=request.aspect_ratio,
            image_bytes=image_bytes,
            image_mime=image_mime or "image/png",
            **extras,
        )

        duration = job.raw.get("duration", provider.normalise_duration(request.duration))
        record = video_job_store.create(
            provider.name, job.provider_job_id,
            prompt=request.prompt, kind="video",
            params={"aspect_ratio": job.raw.get("aspect_ratio"),
                    "duration": duration,
                    "platform": request.platform,
                    "model": job.raw.get("model"),
                    "slides": job.raw.get("slides"),
                    "has_audio": job.raw.get("has_audio")},
            cost_usd=provider.estimated_cost(duration),
        )

        return {
            "success": True,
            "task_id": record["job_id"],
            "job_id": record["job_id"],
            "provider": provider.name,
            "status": "processing",
            "estimated_cost_usd": record["cost_usd"],
            "message": f"{provider.label} is generating. "
                       f"Poll /api/video-status/{record['job_id']}.",
        }

    except ProviderError as e:
        return {"success": False, "error": e.reason}
    except HTTPException as e:
        return {"success": False, "error": e.detail}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/video-status/{task_id}")
async def check_video_status(task_id: str):
    """Poll a generation, and store the video the moment it is ready.

    Downloading on completion is not an optimisation — Veo deletes generated
    files after about two days, so a provider URL handed to the browser is a
    link that dies over the weekend. The bytes go into the media library and
    every later reference points at our own copy.
    """
    record = video_job_store.get(task_id) or video_job_store.get_by_provider_job(task_id)
    if not record:
        return {"success": False, "error": f"Unknown video job '{task_id}'"}

    # Already finished: answer from the database, do not re-charge the provider.
    if record["status"] in (JobStatus.SUCCEEDED, JobStatus.FAILED):
        return _video_status_response(record)

    provider = video_providers.get_optional(record["provider"])
    if provider is None:
        return {"success": False, "task_id": task_id,
                "error": f"Provider '{record['provider']}' is no longer registered"}

    try:
        job = await provider.poll(record["provider_job_id"])
    except ProviderError as e:
        return {"success": False, "task_id": task_id, "status": record["status"],
                "error": e.reason}

    if job.status == JobStatus.FAILED:
        record = video_job_store.update(task_id, status=JobStatus.FAILED,
                                        error=job.error or "Generation failed")
        return _video_status_response(record)

    if job.status != JobStatus.SUCCEEDED:
        video_job_store.update(task_id, status=JobStatus.RUNNING)
        return {"success": True, "task_id": task_id, "job_id": task_id,
                "provider": record["provider"], "status": "processing",
                "video_url": None}

    # Succeeded — fetch the bytes and register them before the provider expires them.
    try:
        data = await provider.download(job)
        params = record.get("params") or {}
        asset = store_bytes(
            data,
            declared_name=f"{record['job_id']}.mp4",
            origin="generated",
            provider=f"{provider.name}:{params.get('model') or ''}".rstrip(":"),
            title=(record.get("prompt") or "Generated video")[:120],
            tags=["video", record.get("kind") or "video", provider.name],
            cost_usd=record.get("cost_usd"),
            extra_metadata={"prompt": (record.get("prompt") or "")[:2000],
                            "provider_job_id": record["provider_job_id"],
                            **params},
        )
    except (ProviderError, MediaRejected) as e:
        reason = getattr(e, "reason", str(e))
        record = video_job_store.update(task_id, status=JobStatus.FAILED,
                                        error=f"Generated, but could not be stored: {reason}",
                                        video_url=job.video_url)
        return _video_status_response(record)

    record = video_job_store.update(task_id, status=JobStatus.SUCCEEDED,
                                    asset_id=asset["asset_id"], video_url=asset["url"])
    return _video_status_response(record, asset=asset)


# Cost accounting note: when a generation produces bytes we already hold, the
# media store returns the EXISTING asset — so assets.cost_usd carries only what
# the first generation cost. Every run is still billed, so spend must be summed
# from video_jobs, which has one row per generation. summarise_video_spend() is
# the answer to "what did video cost me", not a query over assets.


def _video_status_response(record: dict, asset: dict = None) -> dict:
    """One response shape for every poll.

    `status` stays "completed"/"failed"/"processing" because the existing
    frontend polls on those strings.
    """
    wire_status = {
        JobStatus.SUCCEEDED: "completed",
        JobStatus.FAILED: "failed",
    }.get(record["status"], "processing")

    body = {
        "success": record["status"] != JobStatus.FAILED,
        "task_id": record["job_id"],
        "job_id": record["job_id"],
        "provider": record["provider"],
        "status": wire_status,
        "video_url": record.get("video_url"),
        "asset_id": record.get("asset_id"),
        "cost_usd": record.get("cost_usd"),
    }
    if record.get("error"):
        body["error"] = record["error"]
    if asset:
        body["thumb_url"] = asset.get("thumb_url")
        body["duration_seconds"] = asset.get("duration_seconds")
        # Surfaced rather than hidden: an identical result was already held, so
        # this job reuses that file instead of storing a second copy.
        body["reused_existing_file"] = bool(asset.get("deduped"))
    return body


@app.get("/api/video-jobs")
async def list_video_jobs(limit: int = 25, status: Optional[str] = None):
    """Recent generations — what was made, by whom, and what it cost."""
    return {
        "jobs": video_job_store.recent(limit=limit, status=status),
        "spend": video_job_store.spend_summary(),
    }


@app.post("/api/generate-reel")
async def generate_reel(request: ReelRequest):
    """Generate a complete reel: script + video"""
    
    # Step 1: Generate script with GPT-4
    script_prompt = f"""Create a viral {request.duration}-second reel script about: {request.topic}

Style: {request.style}
Platform: {request.platform}

Provide in JSON format:
{{
    "hook": "First 2-3 seconds - attention grabber (max 10 words)",
    "main_content": "Main message - quick, punchy points",
    "cta": "Call to action at the end",
    "video_prompt": "Detailed visual description for AI video generation - describe scene, movement, style, mood",
    "caption": "Post caption with emojis and hashtags",
    "audio_suggestion": "Type of music/sound that would fit"
}}

{request.custom_instructions or ''}

Make it engaging and optimized for {request.platform}. Return ONLY valid JSON."""

    try:
        script_response = await generate_content_ai(script_prompt)
        
        # Parse JSON
        clean_response = script_response.strip()
        if clean_response.startswith("```"):
            clean_response = clean_response.split("```")[1]
            if clean_response.startswith("json"):
                clean_response = clean_response[4:]
        clean_response = clean_response.strip()
        
        script_data = json.loads(clean_response)
        
        # Step 2: Generate the video on the configured provider
        video_prompt = script_data.get("video_prompt", request.topic)

        enhanced_video_prompt = f"""{video_prompt}
Style: Vertical video (9:16), dynamic, engaging for social media reels.
Movement: Smooth camera movements, eye-catching transitions.
Quality: High quality, vibrant colors, professional look."""

        provider = video_providers.get(getattr(request, "provider", None))
        job = await provider.submit(
            enhanced_video_prompt,
            duration=request.duration,
            aspect_ratio="9:16",
        )

        duration = job.raw.get("duration", provider.normalise_duration(request.duration))
        record = video_job_store.create(
            provider.name, job.provider_job_id,
            prompt=enhanced_video_prompt, kind="reel",
            params={"aspect_ratio": "9:16", "duration": duration,
                    "platform": request.platform, "topic": request.topic,
                    "model": job.raw.get("model")},
            cost_usd=provider.estimated_cost(duration),
        )

        return {
            "success": True,
            "reel": {
                "topic": request.topic,
                "platform": request.platform,
                "script": script_data,
                "video_task_id": record["job_id"],
                "video_status": "processing",
                "provider": provider.name,
                "estimated_cost_usd": record["cost_usd"],
                "generated_at": datetime.now().isoformat()
            }
        }

    except ProviderError as e:
        return {"success": False, "error": e.reason}
        
    except json.JSONDecodeError:
        return {"success": False, "error": "Failed to parse script", "raw": script_response}
    except Exception as e:
        return {"success": False, "error": str(e)}


# --- Avatar Video Generation ---
@app.get("/api/avatar-catalog")
async def avatar_catalog():
    """Avatars and voices this account can use.

    Also the cheapest proof that the key works: it is an authenticated read,
    so a successful response means real credentials, unlike a public model
    listing.
    """
    try:
        provider = video_providers.get("heygen")
        avatars = await provider.list_avatars()
        voices = await provider.list_voices()
        return {
            "success": True,
            "avatars": [
                {"id": a.get("avatar_id") or a.get("talking_photo_id"),
                 "name": a.get("avatar_name") or a.get("talking_photo_name"),
                 "gender": a.get("gender"),
                 "preview": a.get("preview_image_url")}
                for a in avatars
            ][:200],
            "voices": [
                {"id": v.get("voice_id"), "name": v.get("name"),
                 "language": v.get("language"), "gender": v.get("gender")}
                for v in voices
            ][:400],
        }
    except ProviderError as e:
        return {"success": False, "error": e.reason, "avatars": [], "voices": []}
    except Exception as e:
        return {"success": False, "error": str(e), "avatars": [], "voices": []}


@app.post("/api/generate-avatar")
async def generate_avatar(request: AvatarRequest):
    """Generate a talking avatar video.

    Routed through the provider registry like every other video path. The
    previous implementation called an unverified Kling endpoint that does not
    exist in that API, behind a key that was never configured — so this button
    has never produced a video.
    """
    try:
        provider = video_providers.get(getattr(request, "provider", None) or "heygen")

        # A photo already in the library, an /uploads URL, or an absolute path.
        image_bytes = image_mime = None
        source = request.image_url or request.image_path
        if source and not (request.avatar_id or request.talking_photo_id):
            image_bytes, image_mime = await _read_local_image(source)

        audio_asset_id = request.audio_asset_id
        if not audio_asset_id and request.audio_url:
            raise HTTPException(
                status_code=400,
                detail="Pass audio_asset_id (a HeyGen asset) rather than a local "
                       "audio URL — HeyGen cannot read a file on this machine.",
            )

        job = await provider.submit(
            request.text or "",
            duration=request.duration,
            aspect_ratio=request.aspect_ratio,
            image_bytes=image_bytes,
            image_mime=image_mime or "image/png",
            avatar_id=request.avatar_id,
            talking_photo_id=request.talking_photo_id,
            voice_id=request.voice_id,
            audio_asset_id=audio_asset_id,
        )

        duration = job.raw.get("duration") or provider.normalise_duration(request.duration)
        record = video_job_store.create(
            provider.name, job.provider_job_id,
            prompt=request.text or "", kind="avatar",
            params={"aspect_ratio": job.raw.get("aspect_ratio"),
                    "duration": duration,
                    "model": job.raw.get("model"),
                    "character": job.raw.get("character")},
            cost_usd=provider.estimated_cost(duration),
        )

        return {
            "success": True,
            "task_id": record["job_id"],
            "job_id": record["job_id"],
            "provider": provider.name,
            "status": "processing",
            "estimated_cost_usd": record["cost_usd"],
            "message": f"{provider.label} is generating. "
                       f"Poll /api/video-status/{record['job_id']}.",
        }

    except ProviderError as e:
        return {"success": False, "error": e.reason}
    except HTTPException as e:
        return {"success": False, "error": e.detail}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/generate-avatar-with-script")
async def generate_avatar_with_script(request: AvatarScriptRequest):
    """Generate script + create avatar video"""
    
    # Step 1: Generate script with GPT-4
    script_prompt = f"""Write a {request.duration}-second speaking script about: {request.topic}

Tone: {request.tone}
Platform: {request.platform}

Requirements:
- Natural, conversational speech
- About {request.duration * 2} words (average speaking pace)
- Engaging opening hook
- Clear main message
- Call to action at the end

{request.custom_instructions or ''}

Return ONLY the script text that will be spoken. No formatting, no stage directions, just the words to be said."""

    try:
        script = await generate_content_ai(script_prompt)
        
        return {
            "success": True,
            "script": script.strip(),
            "word_count": len(script.split()),
            "estimated_duration": len(script.split()) / 2,  # ~2 words per second
            "message": "Script generated. Upload your photo and use /api/generate-avatar to create the video."
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# --- Uploads -------------------------------------------------------------
# All three legacy endpoints below now funnel into media_store.store_upload(),
# which sniffs the real type from the file's bytes, enforces a size cap while
# streaming, deduplicates by sha256 and registers the result in the asset
# inventory. Their URLs and response shapes are unchanged so the existing
# frontend keeps working; each response just gains an asset_id.

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def _upload_response(result: dict) -> dict:
    """Legacy response shape, plus the new asset_id."""
    url = result.get("url") or ""
    return {
        "success": True,
        "filename": Path(result.get("path") or url).name,
        "path": result.get("path"),
        "url": url,
        "full_url": f"{PUBLIC_BASE_URL}{url}",
        "asset_id": result.get("asset_id"),
        "deduped": result.get("deduped", False),
        "thumb_url": result.get("thumb_url"),
    }


async def _ingest(file: UploadFile, *, expect: str = None, tags=None) -> dict:
    """Shared ingest with a friendly error when the type is not what we want."""
    try:
        result = await store_upload(file, origin="uploaded", provider="user",
                                    tags=tags or [])
    except MediaRejected as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    if expect and result.get("asset_type") != expect:
        raise HTTPException(
            status_code=400,
            detail=f"Expected {expect}, got {result.get('asset_type')} "
                   f"(type detected from the file contents, not its name)",
        )
    return result


@app.post("/api/upload-avatar-photo")
async def upload_avatar_photo(file: UploadFile = File(...)):
    """Upload photo for avatar generation"""
    result = await _ingest(file, expect="image", tags=["avatar"])
    return _upload_response(result)


@app.post("/api/upload-audio")
async def upload_audio(file: UploadFile = File(...)):
    """Upload audio for avatar/lip-sync"""
    result = await _ingest(file, expect="audio", tags=["voiceover"])
    return _upload_response(result)


# --- Posts CRUD ---
@app.get("/api/posts")
async def get_posts(status: Optional[str] = None, platform: Optional[str] = None):
    conn = get_db()
    c = conn.cursor()
    
    query = "SELECT * FROM posts WHERE 1=1"
    params = []
    
    if status:
        query += " AND status = ?"
        params.append(status)
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    
    query += " ORDER BY created_at DESC"
    c.execute(query, params)
    
    posts = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return {"posts": posts}

@app.post("/api/posts")
async def create_post(post: PostCreate):
    conn = get_db()
    c = conn.cursor()
    
    status = "scheduled" if post.scheduled_time else "draft"
    
    c.execute(
        """INSERT INTO posts (platform, content_type, content, media_paths, scheduled_time, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            post.platform,
            post.content_type,
            post.content,
            json.dumps(post.media_paths) if post.media_paths else None,
            post.scheduled_time.isoformat() if post.scheduled_time else None,
            status
        )
    )
    
    post_id = c.lastrowid
    conn.commit()
    conn.close()
    
    if post.scheduled_time and post.scheduled_time > datetime.now():
        scheduler.add_job(
            publish_scheduled_post,
            DateTrigger(run_date=post.scheduled_time),
            args=[post_id],
            id=f"post_{post_id}",
            replace_existing=True
        )
    
    return {"id": post_id, "status": status}

@app.put("/api/posts/{post_id}")
async def update_post(post_id: int, update: PostUpdate):
    conn = get_db()
    c = conn.cursor()
    
    updates = []
    params = []
    
    if update.content is not None:
        updates.append("content = ?")
        params.append(update.content)
    if update.scheduled_time is not None:
        updates.append("scheduled_time = ?")
        params.append(update.scheduled_time.isoformat())
        updates.append("status = 'scheduled'")
    if update.status is not None:
        updates.append("status = ?")
        params.append(update.status)
    
    if updates:
        params.append(post_id)
        c.execute(f"UPDATE posts SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    
    conn.close()
    return {"updated": True}

@app.delete("/api/posts/{post_id}")
async def delete_post(post_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    
    try:
        scheduler.remove_job(f"post_{post_id}")
    except Exception:
        pass
    
    return {"deleted": True}

@app.post("/api/posts/{post_id}/publish")
async def publish_now(post_id: int, background_tasks: BackgroundTasks):
    """Publish a post immediately"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Post not found")

    post = dict(row)

    # ---- PUBLISH QUALITY GATE (before the publisher is ever scheduled) ----
    gate_ok, gate_error, _ = validate_publish_media(
        post["platform"], post.get("content_type"), post.get("media_paths")
    )
    if not gate_ok:
        c.execute(
            "UPDATE posts SET status = 'failed', error_message = ? WHERE id = ?",
            (gate_error, post_id)
        )
        conn.commit()
        conn.close()
        print(f"[QualityGate] BLOCKED post #{post_id}: {gate_error}")
        raise HTTPException(status_code=400, detail=gate_error)

    c.execute("UPDATE posts SET status = 'publishing' WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    
    background_tasks.add_task(publish_scheduled_post, post_id)
    return {"message": "Publishing started"}

# --- File Upload ---
@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload media file (any supported type)"""
    result = await _ingest(file)
    return _upload_response(result)

# --- Config ---
@app.get("/api/config")
async def get_config():
    return {
        "openai_configured": bool(CONFIG["OPENAI_API_KEY"]),
        "anthropic_configured": bool(CONFIG["ANTHROPIC_API_KEY"]),
        "gemini_configured": bool(CONFIG["GEMINI_API_KEY"]),
        "ai_provider": CONFIG.get("AI_PROVIDER", "openai"),
        "kling_configured": bool(CONFIG["KLING_API_KEY"]),
        "headless_mode": CONFIG["HEADLESS"],
        "zalo_configured": bool(CONFIG.get("ZALO_APP_ID") and CONFIG.get("ZALO_SECRET_KEY") and CONFIG.get("ZALO_REFRESH_TOKEN"))
    }

class ConfigUpdate(BaseModel):
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    AI_PROVIDER: Optional[str] = None
    KLING_API_KEY: Optional[str] = None
    HEADLESS: Optional[bool] = None
    ZALO_APP_ID: Optional[str] = None
    ZALO_SECRET_KEY: Optional[str] = None
    ZALO_REFRESH_TOKEN: Optional[str] = None

@app.post("/api/config")
async def update_config(new_config: ConfigUpdate):
    global CONFIG
    for key, value in new_config.model_dump(exclude_none=True).items():
        CONFIG[key] = value
        os.environ[key] = str(value)
    return {"updated": True}


# --- Extended Platform Login/Post (Facebook, Reddit, Medium, Substack, Discord, HeyGen, Email) ---
@app.post("/api/accounts/{platform}/login-extended")
async def login_extended(platform: str, organization_id: Optional[str] = None):
    """Login to extended platforms (Facebook, Reddit, Medium, Substack, HeyGen, beehiiv, etc.),
    scoped to one organization."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM accounts WHERE platform = ? AND COALESCE(organization_id, ?) = ?",
        (platform.lower(), DEFAULT_ORGANIZATION_ID, org_id)
    )
    row = c.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"No account saved for {platform}")

    account = dict(row)
    password = decrypt(account["password_encrypted"])

    try:
        auto = get_automation_extended(platform, headless=False, organization_id=org_id)
        result = await auto.login(account["username"], password)
        if hasattr(auto, 'close'):
            await auto.close()

        if result.get("success"):
            c.execute(
                "UPDATE accounts SET is_logged_in = 1, last_login = ? WHERE platform = ? AND COALESCE(organization_id, ?) = ?",
                (datetime.now().isoformat(), platform.lower(), DEFAULT_ORGANIZATION_ID, org_id)
            )
            conn.commit()

        conn.close()
        return result

    except Exception as e:
        conn.close()
        return {"success": False, "message": str(e)}


@app.post("/api/posts/{post_id}/publish-extended")
async def publish_extended(post_id: int, background_tasks: BackgroundTasks):
    """Publish a post via extended platform automation"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
    post = c.fetchone()
    if not post:
        conn.close()
        raise HTTPException(status_code=404, detail="Post not found")

    post = dict(post)

    # ---- PUBLISH QUALITY GATE ----
    gate_ok, gate_error, gate_media = validate_publish_media(
        post["platform"], post.get("content_type"), post.get("media_paths")
    )
    if not gate_ok:
        c.execute(
            "UPDATE posts SET status = 'failed', error_message = ? WHERE id = ?",
            (gate_error, post_id)
        )
        conn.commit()
        conn.close()
        print(f"[QualityGate] BLOCKED post #{post_id}: {gate_error}")
        raise HTTPException(status_code=400, detail=gate_error)

    c.execute("UPDATE posts SET status = 'publishing' WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()

    async def do_publish():
        conn2 = get_db()
        c2 = conn2.cursor()
        try:
            post_org_id = post.get("organization_id") or DEFAULT_ORGANIZATION_ID
            auto = get_automation_extended(post["platform"], headless=CONFIG["HEADLESS"],
                                            organization_id=post_org_id)
            result = await auto.post(post["content"], gate_media or None)
            if hasattr(auto, 'close'):
                await auto.close()

            if result.get("success"):
                c2.execute("UPDATE posts SET status = 'published', published_at = ? WHERE id = ?",
                          (datetime.now().isoformat(), post_id))
            else:
                c2.execute("UPDATE posts SET status = 'failed', error_message = ? WHERE id = ?",
                          (result.get("message", "Unknown error"), post_id))
        except Exception as e:
            c2.execute("UPDATE posts SET status = 'failed', error_message = ? WHERE id = ?",
                      (str(e), post_id))
        conn2.commit()
        conn2.close()

    background_tasks.add_task(do_publish)
    return {"message": f"Publishing to {post['platform']} started"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
