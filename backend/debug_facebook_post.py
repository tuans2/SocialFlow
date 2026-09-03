#!/usr/bin/env python3
"""
Step-by-step diagnosis of the Facebook composer.

Runs the exact same automation the publisher uses, but prints what it finds at
every stage and writes a screenshot per stage, so a failure can be read instead
of guessed. It does NOT publish unless --post is given.

    source venv/bin/activate
    cd backend
    python debug_facebook_post.py 29          # dry run, no publishing
    python debug_facebook_post.py 29 --post   # actually click Post
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from automation_extended import get_automation_extended  # noqa: E402
from media_utils import validate_publish_media  # noqa: E402


def log(*parts):
    print(*parts, flush=True)


def load_post(post_id: int):
    conn = sqlite3.connect("socialflow.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


async def shot(auto, name):
    path = BACKEND_DIR / f"debug_fb_{name}.png"
    try:
        await auto.page.screenshot(path=str(path))
        log(f"    screenshot -> {path.name}")
    except Exception as e:
        log(f"    screenshot failed: {e}")


async def describe_post_buttons(auto):
    """Print every candidate Post button and why it is or is not usable."""
    log("  Post button candidates:")
    selectors = (
        'div[role="dialog"] div[aria-label="Post"]',
        'div[role="dialog"] div[aria-label="Đăng"]',
        'div[aria-label="Post"]',
        'div[role="dialog"] div[role="button"]:has(span:text-is("Post"))',
        'div[role="dialog"] span:text-is("Post")',
    )
    for selector in selectors:
        try:
            locator = auto.page.locator(selector)
            count = await locator.count()
            if count == 0:
                log(f"    {selector} -> 0 elements")
                continue
            for index in range(min(count, 4)):
                element = locator.nth(index)
                visible = await element.is_visible()
                disabled = await element.get_attribute("aria-disabled")
                role = await element.get_attribute("role")
                log(f"    {selector} [{index}] visible={visible} "
                    f"aria-disabled={disabled} role={role}")
        except Exception as e:
            log(f"    {selector} -> error {e}")


async def main(post_id: int, do_post: bool):
    post = load_post(post_id)
    if not post:
        log(f"Post {post_id} not found")
        return 1

    ok, error, media = validate_publish_media(
        post["platform"], post.get("content_type"), post.get("media_paths"))
    log(f"Post #{post_id} on {post['platform']} — quality gate ok={ok} error={error}")
    log(f"  media resolved: {media}")
    if not ok:
        return 1

    content = post["content"]
    log(f"  caption source length: {len(content.strip())} chars")

    auto = get_automation_extended(post["platform"], headless=False)
    await auto.init_browser()
    if not await auto.load_session(post["platform"]):
        log("No saved session — run manual_login.py first")
        return 1

    await auto.page.goto(auto.HOME_URL)
    await auto.page.wait_for_load_state("domcontentloaded")
    await auto.page.wait_for_timeout(3000)
    log("1. Home page loaded:", auto.page.url)
    await shot(auto, "1_home")

    # open the composer
    try:
        trigger = await auto.page.wait_for_selector(
            "text=/What's on your mind|Bạn đang nghĩ gì/i", timeout=7000)
        await trigger.click()
    except Exception:
        await auto.page.keyboard.press("Escape")
        await auto.page.wait_for_timeout(500)
        await auto.page.keyboard.press("p")
    await auto.page.wait_for_timeout(2500)
    log("2. Composer opened")
    await shot(auto, "2_composer")

    editor = auto.page.locator(
        'div[contenteditable="true"]:visible, [role="textbox"][contenteditable="true"]:visible'
    ).first
    await editor.wait_for(state="visible", timeout=15000)
    await editor.click()

    # caption first (focus is reliable right after the composer opens)
    log("3. Entering caption ...")
    await auto._enter_content(editor, content)
    await auto.page.wait_for_timeout(1000)
    await auto._dismiss_typeahead()
    log(f"   caption in composer: {len(await auto._caption_text())} chars")
    await shot(auto, "3_caption")

    log("4. Attaching image ...")
    attached = await auto._attach_images(media)
    log(f"   attached={attached}  preview_visible={await auto._image_preview_visible()}")
    log(f"   caption after upload: {len(await auto._caption_text())} chars")
    await shot(auto, "4_image")

    log("5. Guards")
    log(f"   image preview still visible: {await auto._image_preview_visible()}")
    log(f"   caption guard passes       : {await auto._ensure_caption(content)}")
    await describe_post_buttons(auto)
    await shot(auto, "5_before_post")

    if not do_post:
        log("")
        log("Dry run — Post was NOT clicked. The browser stays open for 60s so you can look.")
        await auto.page.wait_for_timeout(60000)
        await auto.close()
        return 0

    log("6. Clicking Post ...")
    clicked = await auto._click_post_button()
    log(f"   clicked={clicked}")
    await auto.page.wait_for_timeout(5000)
    await shot(auto, "6_after_post")
    closed = await auto._post_submitted(content, bool(media), timeout_ms=25000)
    log(f"   post submitted={closed}")
    await shot(auto, "7_final")
    await auto.close()
    return 0 if clicked and closed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("post_id", type=int)
    parser.add_argument("--post", action="store_true", help="actually click Post")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.post_id, args.post)))
