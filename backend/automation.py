"""
SocialFlow - Browser Automation Module
Handles login and posting for LinkedIn, Instagram, and X (Twitter)
Uses Playwright for reliable browser automation
"""

import asyncio
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# Session storage directory
SESSIONS_DIR = Path("sessions")
SESSIONS_DIR.mkdir(exist_ok=True)

# Cung hang so voi migrations.py/main.py/orchestrator.py.
DEFAULT_ORGANIZATION_ID = os.getenv("SOCIALFLOW_DEFAULT_ORGANIZATION_ID", "default")

class SocialAutomation:
    """Base class for social media automation"""
    
    def __init__(self, headless: bool = False, organization_id: Optional[str] = None):
        self.headless = headless
        # D1 (da tenant that, A5): moi to chuc dang nhap MOT tai khoan MangXH
        # rieng cua ho -- phien Playwright (cookie) phai tach theo to chuc,
        # khong the dung chung MOT tep phien cho ca nen tang nhu truoc day
        # (xem get_session_path). Neu khong dat, coi nhu DEFAULT_ORGANIZATION_ID
        # de tuong thich nguoc voi cac cho goi cu chua truyen organization_id.
        self.organization_id = organization_id or DEFAULT_ORGANIZATION_ID
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None
    
    async def init_browser(self):
        """Initialize the browser"""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
    
    async def close(self):
        """Close browser and cleanup"""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
    
    def get_session_path(self, platform: str) -> Path:
        """Get path to THIS ORGANIZATION's session file for a platform.

        Before D1 A5 this was one file per platform, shared by every
        organization -- organization B logging in to LinkedIn would silently
        overwrite organization A's saved LinkedIn session, so organization
        A's next publish could run through organization B's logged-in
        account. Scoping the filename by organization_id fixes every call
        site that goes through save_session/load_session (~20 of them across
        automation.py and automation_extended.py) without needing to touch
        each one individually.
        """
        return SESSIONS_DIR / f"{self.organization_id}_{platform}_session.json"
    
    async def save_session(self, platform: str):
        """Save browser cookies/session"""
        if self.context:
            cookies = await self.context.cookies()
            storage = await self.context.storage_state()
            session_data = {
                "cookies": cookies,
                "storage": storage,
                "saved_at": datetime.now().isoformat()
            }
            with open(self.get_session_path(platform), 'w') as f:
                json.dump(session_data, f)
    
    async def load_session(self, platform: str) -> bool:
        """Load saved session if exists"""
        session_path = self.get_session_path(platform)
        if session_path.exists():
            try:
                with open(session_path, 'r') as f:
                    session_data = json.load(f)
                self.context = await self.browser.new_context(
                    storage_state=session_data["storage"],
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                self.page = await self.context.new_page()
                return True
            except Exception as e:
                print(f"Failed to load session: {e}")
        return False
    
    async def create_new_context(self):
        """Create a fresh browser context"""
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.page = await self.context.new_page()


class LinkedInAutomation(SocialAutomation):
    """LinkedIn browser automation"""
    
    PLATFORM = "linkedin"
    LOGIN_URL = "https://www.linkedin.com/login"
    FEED_URL = "https://www.linkedin.com/feed/"
    
    async def login(self, email: str, password: str) -> dict:
        """Login to LinkedIn"""
        await self.init_browser()
        await self.create_new_context()
        
        try:
            await self.page.goto(self.LOGIN_URL)
            await self.page.wait_for_load_state("domcontentloaded")
            
            # Fill credentials
            await self.page.fill('input[type="email"]:visible', email)
            await self.page.fill('input[type="password"]:visible', password)
            await self.page.get_by_role('button', name='Đăng nhập', exact=True).click(no_wait_after=True)
            
            # Wait for navigation or security challenge
            await self.page.wait_for_timeout(3000)
            
            # Check for security verification
            if "checkpoint" in self.page.url or "challenge" in self.page.url:
                # Wait for user to complete verification manually
                print("⚠️ Security verification required. Please complete it in the browser...")
                await self.page.wait_for_url("**/feed/**", timeout=120000)
            
            # Verify login success
            if "/feed" in self.page.url:
                await self.save_session(self.PLATFORM)
                return {"success": True, "message": "Logged in successfully"}
            else:
                return {"success": False, "message": "Login failed - check credentials"}
                
        except Exception as e:
            return {"success": False, "message": str(e)}
    
    async def check_login(self) -> bool:
        """Check if already logged in"""
        await self.init_browser()
        
        if await self.load_session(self.PLATFORM):
            try:
                await self.page.goto(self.FEED_URL)
                await self.page.wait_for_load_state("domcontentloaded")
                return "/feed" in self.page.url
            except Exception:
                pass
        return False
    
    async def post(self, content: str, image_paths: List[str] = None) -> dict:
        """Create a post on LinkedIn"""
        await self.init_browser()
        
        if not await self.load_session(self.PLATFORM):
            return {"success": False, "message": "Not logged in"}
        
        try:
            await self.page.goto(self.FEED_URL)
            await self.page.wait_for_load_state("domcontentloaded")
            await self.page.wait_for_timeout(2000)
            
            # Click "Start a post" button
            start_post_btn = self.page.get_by_role(
                "button", name="Start a post", exact=True
            )
            await start_post_btn.wait_for(state="visible", timeout=10000)
            await start_post_btn.click(no_wait_after=True)
            await self.page.wait_for_timeout(1500)
            
            # Find the post editor
            editor = self.page.get_by_role(
                "textbox",
                name="Text editor for creating content",
                exact=True
            )
            await editor.wait_for(state="visible", timeout=10000)
            
            # Type content
            await editor.click()
            await self.page.keyboard.type(content, delay=20)
            await self.page.wait_for_timeout(1000)
            
            # Handle images if provided
            if image_paths:
                # Click media button
                media_btn = await self.page.query_selector('button[aria-label*="Add media"], button:has-text("Media")')
                if media_btn:
                    await media_btn.click()
                    await self.page.wait_for_timeout(500)
                    
                    # Upload images
                    file_input = await self.page.wait_for_selector('input[type="file"]')
                    await file_input.set_input_files(image_paths)
                    await self.page.wait_for_timeout(2000)
            
            # Click Post button
            post_btn = self.page.get_by_role(
                "button", name="Post", exact=True
            )
            await post_btn.wait_for(state="visible", timeout=10000)
            await post_btn.click(no_wait_after=True)
            
            # Wait for post to complete
            await self.page.wait_for_timeout(3000)
            
            await self.save_session(self.PLATFORM)
            return {"success": True, "message": "Posted successfully to LinkedIn"}
            
        except Exception as e:
            return {"success": False, "message": f"Failed to post: {str(e)}"}
        finally:
            await self.close()


class InstagramAutomation(SocialAutomation):
    """Instagram browser automation"""
    
    PLATFORM = "instagram"
    LOGIN_URL = "https://www.instagram.com/accounts/login/"
    HOME_URL = "https://www.instagram.com/"
    
    async def login(self, username: str, password: str) -> dict:
        """Login to Instagram with human-in-the-loop security verification."""
        await self.init_browser()
        await self.create_new_context()

        try:
            await self.page.goto(self.LOGIN_URL)
            await self.page.wait_for_load_state("domcontentloaded")
            await self.page.wait_for_timeout(2000)

            # Handle cookie popup
            try:
                cookie_btn = self.page.locator(
                    'button:has-text("Allow"), button:has-text("Accept")'
                ).first
                if await cookie_btn.is_visible(timeout=2000):
                    await cookie_btn.click(no_wait_after=True)
                    await self.page.wait_for_timeout(1000)
            except Exception:
                pass

            # Current Instagram login fields
            await self.page.locator(
                'input[name="email"]:visible'
            ).fill(username)

            await self.page.locator(
                'input[name="pass"]:visible'
            ).fill(password)

            # Submit login without waiting for navigation.
            # Instagram currently exposes the login submit input in the DOM.
            submit_btn = self.page.locator('input[type="submit"]').first

            await submit_btn.wait_for(state="attached", timeout=10000)
            await submit_btn.evaluate("(el) => el.click()")

            await self.page.wait_for_timeout(3000)

            # Instagram security verification / CAPTCHA
            if (
                "/auth_platform/recaptcha/" in self.page.url
                or "challenge" in self.page.url
            ):
                print("⚠️ Instagram security verification required.")
                print("👉 Complete the CAPTCHA/security check manually.")
                print("⏳ Waiting up to 120 seconds...")

                for _ in range(120):
                    await self.page.wait_for_timeout(1000)

                    current_url = self.page.url

                    if (
                        "/auth_platform/recaptcha/" not in current_url
                        and "challenge" not in current_url
                        and "login" not in current_url
                    ):
                        break

                await self.page.wait_for_timeout(3000)

            # Handle "Save Login Info" / notification popups
            for text in ["Save Info", "Not Now"]:
                try:
                    btn = self.page.get_by_role(
                        "button",
                        name=text,
                        exact=True
                    ).first

                    if await btn.is_visible(timeout=1500):
                        await btn.click(no_wait_after=True)
                        await self.page.wait_for_timeout(1000)
                except Exception:
                    pass

            await self.page.wait_for_timeout(2000)

            # Verify login
            current_url = self.page.url

            if (
                "login" not in current_url
                and "/auth_platform/recaptcha/" not in current_url
                and "challenge" not in current_url
            ):
                await self.save_session(self.PLATFORM)
                return {
                    "success": True,
                    "message": "Logged in successfully"
                }

            return {
                "success": False,
                "message": "Instagram login failed or verification was not completed"
            }

        except Exception as e:
            return {
                "success": False,
                "message": str(e)
            }

    async def check_login(self) -> bool:
        """Check if already logged in"""
        await self.init_browser()
        
        if await self.load_session(self.PLATFORM):
            try:
                await self.page.goto(self.HOME_URL)
                await self.page.wait_for_load_state("networkidle")
                return "login" not in self.page.url
            except Exception:
                pass
        return False
    
    # The composer is a modal; every step below is "find one of several
    # candidates" because Instagram renames labels often. Each stage reports a
    # distinct failure so a broken run says WHICH step broke, not just "timeout".
    CREATE_BUTTON_SELECTORS = [
        'svg[aria-label="New post"]',
        'svg[aria-label="New Post"]',
        'a[href="/create/select/"]',
        'div[role="button"]:has(svg[aria-label*="New post" i])',
        'span:has-text("Create")',
    ]
    SELECT_FROM_COMPUTER_SELECTORS = [
        'button:has-text("Select from computer")',
        'button:has-text("Select From Computer")',
        'div[role="button"]:has-text("Select from computer")',
    ]
    CAPTION_SELECTORS = [
        'textarea[aria-label*="caption" i]',
        'textarea[placeholder*="caption" i]',
        'div[role="textbox"][aria-label*="caption" i]',
        'div[contenteditable="true"][aria-label*="caption" i]',
        'textarea',
    ]

    async def _click_first(self, selectors, timeout=8000):
        """Click the first selector that resolves. Returns True on success."""
        for sel in selectors:
            try:
                el = await self.page.wait_for_selector(sel, timeout=timeout, state="visible")
                if el:
                    await el.click()
                    return True
            except Exception:
                continue
        return False

    async def _debug_shot(self, tag: str):
        """Best-effort screenshot so a failed run is diagnosable afterwards."""
        try:
            path = Path(__file__).resolve().parent / f"debug_ig_{tag}.png"
            await self.page.screenshot(path=str(path))
            return str(path)
        except Exception:
            return None

    async def post(self, content: str, image_paths: List[str]) -> dict:
        """Create a post on Instagram (requires at least one image)"""
        if not image_paths:
            return {"success": False, "message": "Instagram requires at least one image"}

        await self.init_browser()

        if not await self.load_session(self.PLATFORM):
            return {"success": False, "message": "Not logged in"}

        stage = "start"
        caption_input = None
        try:
            stage = "open home"
            await self.page.goto(self.HOME_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(2000)

            if "login" in self.page.url:
                return {
                    "success": False,
                    "message": "Instagram session expired - re-run manual_login.py for instagram",
                }

            stage = "open composer"
            if not await self._click_first(self.CREATE_BUTTON_SELECTORS, timeout=10000):
                shot = await self._debug_shot("1_no_create_button")
                return {
                    "success": False,
                    "message": f"Could not find the 'New post' button (screenshot: {shot})",
                }
            await self.page.wait_for_timeout(1500)

            # The dialog sometimes needs "Select from computer" clicked before the
            # hidden <input type=file> is attached. Optional - ignore if already there.
            await self._click_first(self.SELECT_FROM_COMPUTER_SELECTORS, timeout=3000)

            stage = "attach files"
            # THE BUG THIS REPLACES: wait_for_selector() defaults to state="visible",
            # but Instagram's file input is display:none - it is never visible, so
            # the old code always timed out here. Wait for "attached" instead.
            file_input = await self.page.wait_for_selector(
                'input[type="file"]', timeout=15000, state="attached"
            )
            await file_input.set_input_files(image_paths)
            await self.page.wait_for_timeout(3000)

            stage = "crop/filter steps"
            next_selectors = [
                'div[role="button"]:has-text("Next")',
                'button:has-text("Next")',
            ]
            for _ in range(2):
                if not await self._click_first(next_selectors, timeout=8000):
                    break
                await self.page.wait_for_timeout(1500)

            stage = "caption"
            for sel in self.CAPTION_SELECTORS:
                try:
                    caption_input = await self.page.wait_for_selector(
                        sel, timeout=5000, state="visible"
                    )
                    if caption_input:
                        break
                except Exception:
                    continue

            if caption_input:
                await caption_input.click()
                await self.page.keyboard.type(content, delay=10)
                await self.page.wait_for_timeout(1000)
            else:
                await self._debug_shot("2_no_caption_field")

            stage = "share"
            share_selectors = [
                'div[role="button"]:has-text("Share")',
                'button:has-text("Share")',
            ]
            if not await self._click_first(share_selectors, timeout=10000):
                shot = await self._debug_shot("3_no_share_button")
                return {
                    "success": False,
                    "message": f"Composer filled but the Share button was not found (screenshot: {shot})",
                }

            await self.page.wait_for_timeout(6000)

            await self.save_session(self.PLATFORM)
            return {
                "success": True,
                "message": f"Posted successfully to Instagram ({len(image_paths)} image(s))",
                "caption_set": bool(caption_input),
            }

        except Exception as e:
            tag = stage.replace(" ", "_").replace("/", "_")
            shot = await self._debug_shot(f"error_{tag}")
            return {
                "success": False,
                "message": f"Failed to post at stage '{stage}': {e}",
                "screenshot": shot,
            }
        finally:
            await self.close()


class TwitterAutomation(SocialAutomation):
    """X (Twitter) browser automation"""
    
    PLATFORM = "twitter"
    LOGIN_URL = "https://twitter.com/i/flow/login"
    HOME_URL = "https://twitter.com/home"
    
    async def login(self, username: str, password: str) -> dict:
        """Login to X (Twitter)"""
        await self.init_browser()
        await self.create_new_context()
        
        try:
            await self.page.goto(self.LOGIN_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(3000)
            
            # Enter username/email
            username_input = await self.page.wait_for_selector(
                'input[autocomplete="username"], input[name="text"]',
                timeout=15000
            )
            await username_input.fill(username)
            
            # Click Next
            next_btn = await self.page.wait_for_selector('button:has-text("Next"), div[role="button"]:has-text("Next")')
            await next_btn.click()
            await self.page.wait_for_timeout(2000)
            
            # Check for "unusual activity" - may ask for phone/username verification
            try:
                unusual_input = await self.page.query_selector('input[data-testid="ocfEnterTextTextInput"]')
                if unusual_input:
                    print("⚠️ Twitter requires additional verification. Please complete it in the browser...")
                    # Wait for user to complete
                    await self.page.wait_for_timeout(30000)
            except Exception:
                pass
            
            # Enter password
            password_input = await self.page.wait_for_selector(
                'input[name="password"], input[type="password"]',
                timeout=10000
            )
            await password_input.fill(password)
            
            # Click Login
            login_btn = await self.page.wait_for_selector(
                'button[data-testid="LoginForm_Login_Button"], button:has-text("Log in")'
            )
            await login_btn.click()
            
            await self.page.wait_for_timeout(5000)
            
            # Verify login
            if "/home" in self.page.url or "twitter.com" in self.page.url and "login" not in self.page.url:
                await self.save_session(self.PLATFORM)
                return {"success": True, "message": "Logged in successfully"}
            else:
                return {"success": False, "message": "Login failed - check credentials"}
                
        except Exception as e:
            return {"success": False, "message": str(e)}
    
    async def check_login(self) -> bool:
        """Check if already logged in"""
        await self.init_browser()
        
        if await self.load_session(self.PLATFORM):
            try:
                await self.page.goto(self.HOME_URL)
                await self.page.wait_for_load_state("networkidle")
                return "login" not in self.page.url and "/home" in self.page.url
            except Exception:
                pass
        return False
    
    async def post(self, content: str, image_paths: List[str] = None) -> dict:
        """Create a post (tweet) on X"""
        await self.init_browser()
        
        if not await self.load_session(self.PLATFORM):
            return {"success": False, "message": "Not logged in"}
        
        try:
            await self.page.goto(self.HOME_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(2000)
            
            # Find tweet composer
            composer = await self.page.wait_for_selector(
                'div[data-testid="tweetTextarea_0"], div[role="textbox"]',
                timeout=10000
            )
            await composer.click()
            await self.page.wait_for_timeout(500)
            
            # Type content
            await self.page.keyboard.type(content, delay=30)
            await self.page.wait_for_timeout(1000)
            
            # Handle images if provided
            if image_paths:
                file_input = await self.page.query_selector('input[data-testid="fileInput"], input[type="file"]')
                if file_input:
                    await file_input.set_input_files(image_paths)
                    await self.page.wait_for_timeout(2000)
            
            # Click Post button
            post_btn = await self.page.wait_for_selector(
                'button[data-testid="tweetButtonInline"], button:has-text("Post")',
                timeout=10000
            )
            await post_btn.click()
            
            await self.page.wait_for_timeout(3000)
            
            await self.save_session(self.PLATFORM)
            return {"success": True, "message": "Posted successfully to X"}
            
        except Exception as e:
            return {"success": False, "message": f"Failed to post: {str(e)}"}
        finally:
            await self.close()


# Factory function to get the right automation class
def get_automation(platform: str, headless: bool = False) -> SocialAutomation:
    """Get automation instance for a platform"""
    platforms = {
        "linkedin": LinkedInAutomation,
        "instagram": InstagramAutomation,
        "twitter": TwitterAutomation,
        "x": TwitterAutomation
    }
    
    automation_class = platforms.get(platform.lower())
    if not automation_class:
        raise ValueError(f"Unsupported platform: {platform}")
    
    return automation_class(headless=headless)


# Test functions
async def test_login(platform: str, username: str, password: str):
    """Test login for a platform"""
    auto = get_automation(platform)
    result = await auto.login(username, password)
    await auto.close()
    return result


async def test_post(platform: str, content: str, images: List[str] = None):
    """Test posting to a platform"""
    auto = get_automation(platform)
    result = await auto.post(content, images)
    await auto.close()
    return result
