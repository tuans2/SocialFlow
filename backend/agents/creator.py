"""
Creator Agent — Content Production

Responsibilities:
  - Take content plans from Planner → generate platform-specific content
  - Use Ollama (free) or configured AI provider for text generation
  - Use GPT Image 1.5 for branded visuals
  - Apply brand kit (colors, fonts, tone)
  - Store drafts in posts table

Runs: After Planner completes
"""

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from to_chuc import bat_buoc

DB_PATH = Path(__file__).parent.parent / "socialflow.db"

# Cung hang so voi migrations.py/main.py/orchestrator.py.

# Platform-specific prompt templates
PLATFORM_PROMPTS = {
    "linkedin": {
        "ai-news": "Write a LinkedIn post (max 500 chars) about this AI news. First person, founder voice. 3 short paragraphs. End with 2-3 relevant hashtags. No hype. No 'excited to share'. Just facts + insight.",
        "repo-promo": "Write a LinkedIn post (max 400 chars) announcing this open-source repo. First person, founder perspective. Why it matters, who it's for. End with GitHub link + 2 hashtags.",
    },
    "x": {
        "ai-news": "Write a tweet (max 270 chars) about this AI news. Direct, factual. One key takeaway. Include the source URL.",
        "repo-promo": "Write a tweet (max 270 chars) announcing this open-source repo. What it does + GitHub link.",
    },
    "discord": {
        "ai-news": "Write a short Discord message (max 300 chars) about this AI news. Direct, factual, 1-2 sentences. Max 1 emoji. End with the source link.",
        "repo-promo": "Write a Discord announcement (max 300 chars) for this new repo. What it does + link.",
    },
    "reddit": {
        "ai-news": "Write a Reddit post title (max 200 chars, factual, no clickbait) and body (3 paragraphs, max 500 chars). Include source URL at the end.",
        "repo-promo": "Write a Reddit post title and body announcing this open-source repo. Factual, useful, not promotional. Include GitHub link.",
    },
    "instagram": {
        "ai-news": "Write an Instagram caption (max 300 chars) about this AI news. Engaging, educational. 5-8 relevant hashtags. No links (use 'link in bio').",
        "repo-promo": "Write an Instagram caption (max 300 chars) for this repo launch. Include 5-8 hashtags.",
    },
}

BANNED_PHRASES = [
    "In conclusion", "it's worth noting", "This is exciting", "Exciting news",
    "thrilled to share", "game-changer", "revolutionizing", "As an AI",
    "I cannot", "I'm unable", "dive in", "dive deep", "let's dive",
    "In today's", "ensure", "notably", "utilize", "Furthermore",
    "Moreover", "Additionally", "cutting-edge", "groundbreaking",
    "innovative", "seamless", "robust", "comprehensive", "delve",
    "fostering", "leveraging", "embrace",
]


def get_brand_config(organization_id: Optional[str] = None) -> Dict:
    """Load this organization's brand config from DB.

    D1 (A5): brand_config is one row PER organization now, not one shared
    row for everybody (see migrations.py, TECHNICAL_DEBT.md — this was a
    real cross-organization content leak before: every organization's posts
    were branded with whichever organization saved their brand kit most
    recently). An organization that has never saved its own brand kit gets
    the hardcoded fallback below, not another organization's brand.
    """
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM brand_config WHERE organization_id = ? ORDER BY id DESC LIMIT 1",
            (org_id,)
        ).fetchone()
        if row:
            return dict(row)
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    # Default brand — used when this organization has not saved its own brand kit.
    return {
        "brand_name": "InBharat AI",
        "primary_color": "#0A74DA",
        "secondary_color": "#F5A623",
        "dark_bg": "#1A1A2E",
        "tone": "clear, direct, practical, builder-minded",
        "forbidden_styles": "robot heads, stock photos, neon, clipart",
    }


def build_prompt(plan: Dict, organization_id: Optional[str] = None) -> str:
    """Build the AI generation prompt from plan + this organization's brand config."""
    platform = plan["platform"]
    content_type = plan["content_type"]
    title = plan.get("signal_title", "")
    url = plan.get("signal_url", "")
    summary = plan.get("signal_summary", "")
    brand = get_brand_config(organization_id)

    template = PLATFORM_PROMPTS.get(platform, {}).get(content_type, "")
    if not template:
        template = f"Write a {platform} post about this topic. Keep it short and engaging."

    banned_str = ", ".join(BANNED_PHRASES[:15])

    prompt = f"""{template}

Topic: {title}
Source URL: {url}
Summary: {summary}

Brand voice: {brand.get('tone', 'direct and practical')}
Banned phrases: {banned_str}
"""
    return prompt


def clean_generated_text(text: str) -> str:
    """Clean AI-generated text — remove thinking tags, banned phrases, artifacts."""
    # Strip thinking tags
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    # Strip markdown code fences
    text = re.sub(r'^```\w*\n?', '', text, flags=re.MULTILINE)
    text = re.sub(r'```$', '', text, flags=re.MULTILINE)
    # Strip "Here's..." preamble
    text = re.sub(r'^(Here\'s|Here is|Sure,?|Of course,?).*?:\s*\n', '', text, flags=re.IGNORECASE)
    return text.strip()


async def generate_post_content(plan: Dict, ai_generate_fn, organization_id: Optional[str] = None) -> Optional[str]:
    """Generate content for a single plan using the configured AI provider."""
    prompt = build_prompt(plan, organization_id)
    try:
        raw = await ai_generate_fn(prompt)
        return clean_generated_text(raw)
    except Exception as e:
        print(f"[Creator] AI generation failed for plan {plan['id']}: {e}")
        return None


def save_draft(platform: str, content: str, content_type: str,
               signal_url: str = "", plan_id: int = None,
               organization_id: Optional[str] = None):
    """Save a draft post to the posts table, tagged to the organization that requested it."""
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """INSERT INTO posts (platform, content_type, content, status, created_at, organization_id)
           VALUES (?, ?, ?, 'draft', ?, ?)""",
        (platform, content_type, content, datetime.now().isoformat(), org_id)
    )
    post_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return post_id


async def run(ai_generate_fn=None, organization_id: Optional[str] = None):
    """Main creator run — generate content for this organization's pending plans.

    Args:
        ai_generate_fn: Async function that takes a prompt string and returns generated text.
                        If None, uses a placeholder.
        organization_id: Which organization's plans to turn into drafts, and whose
                          brand config to apply. Defaults to DEFAULT_ORGANIZATION_ID.
    """
    from agents.planner import get_pending_plans, mark_plan_completed

    org_id = bat_buoc(organization_id)
    print(f"[Creator] Starting at {datetime.now().strftime('%H:%M:%S')} for org '{org_id}'")

    plans = get_pending_plans(organization_id=org_id, limit=10)
    print(f"[Creator] {len(plans)} pending plans to process")

    created = 0
    for plan in plans:
        if ai_generate_fn:
            content = await generate_post_content(plan, ai_generate_fn, organization_id=org_id)
        else:
            # Fallback: use the signal as-is
            content = f"{plan.get('signal_title', '')}\n\n{plan.get('signal_summary', '')}\n\n{plan.get('signal_url', '')}"

        if content and len(content.strip()) > 20:
            post_id = save_draft(
                platform=plan["platform"],
                content=content,
                content_type=plan["content_type"],
                signal_url=plan.get("signal_url", ""),
                plan_id=plan["id"],
                organization_id=org_id,
            )
            mark_plan_completed(plan["id"])
            created += 1
            print(f"[Creator] Draft #{post_id} for {plan['platform']}: {content[:60]}...")
        else:
            print(f"[Creator] Skipped plan {plan['id']} — generation too short or failed")

    print(f"[Creator] Done — {created} drafts created")
    return created
