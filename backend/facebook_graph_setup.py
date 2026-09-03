#!/usr/bin/env python3
"""
Get the Facebook Page credentials SocialFlow needs, and write them to .env.

What you do by hand (once, ~10 minutes):
  1. developers.facebook.com → My Apps → Create App → type "Business".
  2. In the app: Settings → Basic → copy App ID and App Secret.
  3. Tools → Graph API Explorer → pick your app → "Generate Access Token",
     tick the permissions: pages_show_list, pages_manage_posts, pages_read_engagement
     → copy the token it produces (this one is short-lived, that is fine).

Then run:

    source venv/bin/activate
    cd backend
    python facebook_graph_setup.py --app-id <APP_ID> --app-secret <APP_SECRET> \\
                                   --user-token <TOKEN_FROM_EXPLORER>

It exchanges that short-lived token for a long-lived one, lists the Pages you
manage with their per-Page tokens (those do not expire), and offers to write the
chosen Page into .env. Tokens are never printed in full and never logged.
"""

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parent
ENV_PATH = BACKEND_DIR.parent / ".env"
GRAPH_HOST = "https://graph.facebook.com"


def mask(token: str) -> str:
    if not token or len(token) < 12:
        return "***"
    return f"{token[:6]}…{token[-4:]} ({len(token)} chars)"


async def exchange_long_lived(version, app_id, app_secret, short_token):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GRAPH_HOST}/{version}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": short_token,
            },
        )
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(payload["error"].get("message", "token exchange failed"))
    return payload["access_token"]


async def list_pages(version, user_token):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GRAPH_HOST}/{version}/me/accounts",
            params={"fields": "id,name,access_token,fan_count", "access_token": user_token},
        )
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(payload["error"].get("message", "could not list pages"))
    return payload.get("data", [])


def write_env(page_id: str, page_token: str, version: str):
    """Update .env in place, keeping every other line untouched."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    values = {
        "FB_PAGE_ID": page_id,
        "FB_PAGE_ACCESS_TOKEN": page_token,
        "FB_GRAPH_VERSION": version,
    }
    seen = set()
    for index, line in enumerate(lines):
        match = re.match(r"^(FB_PAGE_ID|FB_PAGE_ACCESS_TOKEN|FB_GRAPH_VERSION)=", line)
        if match:
            key = match.group(1)
            lines[index] = f"{key}={values[key]}"
            seen.add(key)
    missing = [f"{k}={v}" for k, v in values.items() if k not in seen]
    if missing:
        lines += ["", "# Facebook Page publishing (Graph API)"] + missing
    ENV_PATH.write_text("\n".join(lines) + "\n")


async def main(args):
    version = args.version

    app_id = args.app_id or os.getenv("FB_APP_ID", "")
    app_secret = args.app_secret or os.getenv("FB_APP_SECRET", "")
    if not (app_id and app_secret and args.user_token):
        print("Need --app-id, --app-secret and --user-token. See the header of this file.")
        return 1

    print("1. Exchanging the short-lived token for a long-lived one ...")
    try:
        long_token = await exchange_long_lived(version, app_id, app_secret, args.user_token)
    except Exception as e:
        print(f"   failed: {e}")
        return 1
    print(f"   long-lived user token: {mask(long_token)}")

    print("2. Listing the Pages this account manages ...")
    try:
        pages = await list_pages(version, long_token)
    except Exception as e:
        print(f"   failed: {e}")
        return 1

    if not pages:
        print("   No Pages returned. Check that pages_show_list was granted.")
        return 1

    for index, page in enumerate(pages):
        print(f"   [{index}] {page.get('name')}  id={page.get('id')}  "
              f"followers={page.get('fan_count', '?')}  token={mask(page.get('access_token',''))}")

    if args.page_id:
        chosen = next((p for p in pages if str(p.get("id")) == str(args.page_id)), None)
        if not chosen:
            print(f"   Page id {args.page_id} is not in that list.")
            return 1
    elif len(pages) == 1:
        chosen = pages[0]
    else:
        answer = input("Which index do you want to publish to? ").strip()
        try:
            chosen = pages[int(answer)]
        except Exception:
            print("   Invalid choice.")
            return 1

    print(f"3. Selected: {chosen.get('name')} (id {chosen.get('id')})")

    if args.write:
        write_env(str(chosen["id"]), chosen["access_token"], version)
        print(f"   written to {ENV_PATH}")
        print("   Restart the server so it picks the new values up.")
    else:
        print("   Re-run with --write to save these into .env, or add manually:")
        print(f"   FB_PAGE_ID={chosen['id']}")
        print("   FB_PAGE_ACCESS_TOKEN=<the page token above>")

    print("4. Verifying the Page token ...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GRAPH_HOST}/{version}/{chosen['id']}",
            params={"fields": "id,name", "access_token": chosen["access_token"]},
        )
    payload = response.json()
    if "error" in payload:
        print(f"   token check failed: {payload['error'].get('message')}")
        return 1
    print(f"   OK — token works for \"{payload.get('name')}\"")
    return 0


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Set up Facebook Page publishing.")
    parser.add_argument("--app-id")
    parser.add_argument("--app-secret")
    parser.add_argument("--user-token", help="short-lived token from the Graph API Explorer")
    parser.add_argument("--page-id", help="pick this Page instead of asking")
    parser.add_argument("--version", default=os.getenv("FB_GRAPH_VERSION", "v21.0"))
    parser.add_argument("--write", action="store_true", help="write the result into .env")
    sys.exit(asyncio.run(main(parser.parse_args())))
