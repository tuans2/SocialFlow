#!/usr/bin/env python3
"""
Assisted login helper — auto-fills the saved credentials, then WAITS for you.

The dashboard's Login button fills the form and gives up a few seconds later,
which is not enough when the platform asks for a code, a device confirmation or
a captcha; the window closes before you can type. This script does the same
auto-fill using the account already saved in the dashboard, then simply polls
until it sees a logged-in page and only then saves the session file the
publisher uses. Nothing is bypassed — any verification step is done by you.

Usage (from the repository root):

    source venv/bin/activate
    cd backend
    python manual_login.py facebook               # auto-fill + wait 10 minutes
    python manual_login.py instagram --wait 900
    python manual_login.py facebook --no-autofill # type everything yourself

The window closes by itself once the session is saved.
"""

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
os.chdir(BACKEND_DIR)                 # sessions/ and .secret_key are relative
sys.path.insert(0, str(BACKEND_DIR))

from automation_extended import get_automation_extended  # noqa: E402

DB_PATH = BACKEND_DIR / "socialflow.db"

BLOCKED_URL_MARKERS = ("login", "checkpoint", "two_step_verification",
                       "signup", "recover", "auth_platform", "pre_authentication",
                       "/challenge", "consent")

# The cookie each platform only sets once the account is really authenticated.
# This is the reliable signal — an intermediate auth screen has no login form
# either, so the absence of input fields is not enough.
AUTH_COOKIES = {
    "facebook":  ("c_user",),
    "instagram": ("sessionid", "ds_user_id"),
    "linkedin":  ("li_at",),
    "twitter":   ("auth_token",),
    "x":         ("auth_token",),
}

LOGIN_FIELD_SELECTORS = (
    'input#email:visible',
    'input[name="email"]:visible',
    'input[name="pass"]:visible',
    'input[name="username"]:visible',
    'input[name="password"]:visible',
    'input[name="session_key"]:visible',
)

# (username field, password field) candidates, tried in order.
CREDENTIAL_FIELDS = {
    "facebook":  [('input#email, input[name="email"]', 'input#pass, input[name="pass"]')],
    "instagram": [('input[name="username"]', 'input[name="password"]')],
    "linkedin":  [('input#username, input[name="session_key"]',
                   'input#password, input[name="session_password"]')],
    "twitter":   [('input[name="text"], input[autocomplete="username"]',
                   'input[name="password"]')],
}
CREDENTIAL_FIELDS["x"] = CREDENTIAL_FIELDS["twitter"]

COOKIE_BANNER = ('button[data-cookiebanner="accept_button"], '
                 'button:has-text("Allow all cookies"), '
                 'button:has-text("Accept All")')


def get_account(platform: str):
    """Return (username, password) from the dashboard's accounts table."""
    if not DB_PATH.exists():
        return None, None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM accounts WHERE platform = ?",
                           (platform.lower(),)).fetchone()
        conn.close()
        if not row:
            return None, None

        from cryptography.fernet import Fernet
        key_file = BACKEND_DIR / ".secret_key"
        if not key_file.exists():
            return row["username"], None
        fernet = Fernet(key_file.read_bytes())
        return row["username"], fernet.decrypt(row["password_encrypted"].encode()).decode()
    except Exception as e:
        print(f"  (could not read the saved account: {e})")
        return None, None


def mark_logged_in(platform: str):
    if not DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE accounts SET is_logged_in = 1, last_login = ? WHERE platform = ?",
            (datetime.now().isoformat(), platform.lower())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  (could not update the accounts table: {e})")


async def has_auth_cookie(context, platform: str) -> bool:
    """True when the platform's authenticated-session cookie is present."""
    names = AUTH_COOKIES.get(platform)
    if not names:
        return True  # unknown platform: fall back to the page heuristics only
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    present = {c.get("name") for c in cookies if c.get("value")}
    return all(name in present for name in names)


async def looks_logged_in(context, page, platform: str) -> bool:
    """Authenticated cookie present, not on an auth URL, no visible login field."""
    if not await has_auth_cookie(context, platform):
        return False

    try:
        url = (page.url or "").lower()
    except Exception:
        return False

    if any(marker in url for marker in BLOCKED_URL_MARKERS):
        return False

    for selector in LOGIN_FIELD_SELECTORS:
        try:
            if await page.locator(selector).first.count() > 0:
                return False
        except Exception:
            continue
    return True


async def confirm_on_home(auto, platform: str, home_url: str) -> bool:
    """Final check: load the real home page and verify it there before saving."""
    try:
        await auto.page.goto(home_url)
        await auto.page.wait_for_timeout(4000)
    except Exception:
        return False
    return await looks_logged_in(auto.context, auto.page, platform)


async def autofill(page, platform: str, username: str, password: str) -> bool:
    """Fill the saved credentials and submit. Returns True when a form was filled."""
    try:
        banner = await page.query_selector(COOKIE_BANNER)
        if banner:
            await banner.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    for user_sel, pass_sel in CREDENTIAL_FIELDS.get(platform, []):
        try:
            if await page.locator(user_sel).first.count() == 0:
                continue
            await page.fill(user_sel, username, timeout=5000)
            await page.wait_for_timeout(400)
            if await page.locator(pass_sel).first.count() > 0:
                await page.fill(pass_sel, password, timeout=5000)
                await page.wait_for_timeout(400)
                await page.press(pass_sel, "Enter")
            else:
                # Multi-step forms (X/Twitter): submit the username step only.
                await page.press(user_sel, "Enter")
            return True
        except Exception:
            continue
    return False


async def main(platform: str, wait_seconds: int, no_autofill: bool):
    platform = platform.lower()
    auto = get_automation_extended(platform, headless=False)

    login_url = getattr(auto, "LOGIN_URL", None)
    home_url = getattr(auto, "HOME_URL", None)
    if not login_url and not home_url:
        print(f"{platform} has no browser login (API-based or webhook platform).")
        return 1

    username, password = (None, None) if no_autofill else get_account(platform)

    await auto.init_browser()

    # Reuse the saved session first: often only a re-verification is needed.
    reused = await auto.load_session(platform)
    if not reused:
        await auto.create_new_context()
    print(f"Saved session reused: {reused}")

    await auto.page.goto(home_url or login_url)
    await auto.page.wait_for_timeout(3000)

    if await looks_logged_in(auto.context, auto.page, platform):
        print("Already logged in — refreshing the saved session.")
    else:
        if reused:
            # The stored session is not authenticated (often a stale checkpoint
            # cookie). Start from a clean context so it does not drag us back
            # into the verification flow.
            print("Stored session is NOT authenticated — starting a clean one.")
            try:
                await auto.context.close()
            except Exception:
                pass
            await auto.create_new_context()

        await auto.page.goto(login_url or home_url)
        await auto.page.wait_for_timeout(2500)

        if username and password:
            filled = await autofill(auto.page, platform, username, password)
            print(f"Auto-filled credentials for {username}: {filled}")
            if filled:
                await auto.page.wait_for_timeout(5000)
        else:
            print("No saved credentials — type them in the browser window.")

    print("")
    print(f"  The browser window is open. Finish anything {platform} asks for:")
    print("  a verification code, a device confirmation, a captcha.")
    print(f"  Waiting up to {wait_seconds}s. The window closes by itself when done.")
    print("")

    loop = asyncio.get_event_loop()
    deadline = loop.time() + wait_seconds
    stable = 0
    last_report = 0.0

    while loop.time() < deadline:
        try:
            ok = await looks_logged_in(auto.context, auto.page, platform)
        except Exception:
            print("The browser window was closed. Nothing was saved.")
            return 1

        if ok:
            stable += 1
            # Two consecutive positive checks, then one final check on the real
            # home page, so an intermediate auth screen is never saved.
            if stable >= 2:
                if not await confirm_on_home(auto, platform, home_url or login_url):
                    print("  ... auth cookie seen but the home page is not logged in yet.")
                    stable = 0
                    await asyncio.sleep(3)
                    continue
                await auto.save_session(platform)
                mark_logged_in(platform)
                print(f"OK — session saved to sessions/{platform}_session.json")
                print(f"   URL: {auto.page.url}")
                await auto.close()
                return 0
        else:
            stable = 0

        now = loop.time()
        if now - last_report > 30:
            print(f"  ... waiting ({int(deadline - now)}s left) — URL: {auto.page.url}")
            last_report = now

        await asyncio.sleep(3)

    print("Timed out — no session saved. Run the script again if you need more time.")
    await auto.close()
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Auto-fill the saved credentials, then wait for you to finish logging in.")
    parser.add_argument("platform", help="facebook, instagram, linkedin, twitter, ...")
    parser.add_argument("--wait", type=int, default=600, help="seconds to wait (default 600)")
    parser.add_argument("--no-autofill", action="store_true",
                        help="do not fill the saved credentials")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.platform, args.wait, args.no_autofill)))
