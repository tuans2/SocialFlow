"""
SocialFlow Extended - Additional Platform Automations
Facebook, Discord, Reddit, Medium, Substack, HeyGen, Email/Newsletter
Uses Playwright browser automation — login with email/password like a human
"""

import asyncio
import json
import os
import httpx
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
from automation import SocialAutomation, SESSIONS_DIR


class FacebookAutomation(SocialAutomation):
    """Facebook browser automation"""

    PLATFORM = "facebook"
    LOGIN_URL = "https://www.facebook.com/login"
    HOME_URL = "https://www.facebook.com/"

    async def login(self, email: str, password: str) -> dict:
        await self.init_browser()
        await self.create_new_context()

        try:
            await self.page.goto(self.LOGIN_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(2000)

            # Handle cookie consent
            try:
                cookie_btn = await self.page.query_selector(
                    'button[data-cookiebanner="accept_button"], '
                    'button:has-text("Allow"), button:has-text("Accept All")'
                )
                if cookie_btn:
                    await cookie_btn.click()
                    await self.page.wait_for_timeout(1000)
            except Exception:
                pass

            try:
                await self.page.fill('input#email, input[name="email"]', email, timeout=5000)
                await self.page.fill('input#pass, input[name="pass"]', password, timeout=5000)
                await self.page.press('input#pass, input[name="pass"]', 'Enter')
            except Exception:
                pass

            await self.page.wait_for_timeout(5000)

            # Handle security checkpoint or 2FA
            if "checkpoint" in self.page.url or "two_step_verification" in self.page.url:
                print("Security verification required. Complete it in the browser...")
                try:
                    # Wait until the URL changes to something else (user completed 2FA)
                    # We use a custom function to wait until "checkpoint" and "two_step_verification" are gone
                    await self.page.wait_for_url(
                        lambda url: "checkpoint" not in url and "two_step_verification" not in url and "login" not in url, 
                        timeout=120000
                    )
                except Exception:
                    pass

            # IMPORTANT: Facebook can stay on "/" while still showing
            # the public login page. URL alone is NOT enough to confirm login.
            login_form = self.page.locator(
                'input#email:visible, input[name="email"]:visible'
            ).first

            password_form = self.page.locator(
                'input#pass:visible, input[name="pass"]:visible'
            ).first

            still_on_login = (
                await login_form.count() > 0
                or await password_form.count() > 0
                or "login" in self.page.url
                or "checkpoint" in self.page.url
                or "two_step_verification" in self.page.url
            )

            if still_on_login:
                return {
                    "success": False,
                    "message": "Facebook login failed or verification was not completed."
                }

            # Verify authentication using the actual page content.
            # Facebook can remain on "/" even when logged out.
            body_text = await self.page.locator("body").inner_text()
            body_text_lower = body_text.lower()

            login_indicators = [
                "log in to facebook",
                "email address or mobile number",
                "forgotten password?",
                "create new account",
            ]

            still_logged_out = any(
                indicator in body_text_lower
                for indicator in login_indicators
            )

            if still_logged_out:
                return {
                    "success": False,
                    "message": "Facebook login could not be verified."
                }

            await self.save_session(self.PLATFORM)

            return {
                "success": True,
                "message": "Logged in to Facebook"
            }

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def check_login(self) -> bool:
        await self.init_browser()
        if await self.load_session(self.PLATFORM):
            try:
                await self.page.goto(self.HOME_URL)
                await self.page.wait_for_load_state("networkidle")
                login_form = self.page.locator(
                    'input#email:visible, input[name="email"]:visible'
                ).first

                password_form = self.page.locator(
                    'input#pass:visible, input[name="pass"]:visible'
                ).first

                if (
                    "login" in self.page.url
                    or await login_form.count() > 0
                    or await password_form.count() > 0
                ):
                    return False

                return True
            except Exception:
                pass
        return False

    async def post(self, content: str, image_paths: List[str] = None) -> dict:
        await self.init_browser()

        if not await self.load_session(self.PLATFORM):
            return {"success": False, "message": "Not logged in to Facebook"}

        try:
            await self.page.goto(self.HOME_URL)
            await self.page.wait_for_load_state("domcontentloaded")
            await self.page.wait_for_timeout(3000)

            # Verify the saved Facebook session is still authenticated.
            login_form = self.page.locator(
                'input#email:visible, input[name="email"]:visible'
            ).first

            password_form = self.page.locator(
                'input#pass:visible, input[name="pass"]:visible'
            ).first

            if (
                "login" in self.page.url
                or await login_form.count() > 0
                or await password_form.count() > 0
            ):
                return {
                    "success": False,
                    "message": "Facebook session expired. Please login again."
                }

            # Click "What's on your mind?" composer (Supports English & Vietnamese + Keyboard Shortcut fallback)
            try:
                # Use regex text match to ignore DOM structure changes and support multiple languages
                composer_trigger = await self.page.wait_for_selector(
                    'text=/What\'s on your mind|Bạn đang nghĩ gì/i',
                    timeout=7000
                )
                await composer_trigger.click()
            except Exception:
                # Fallback: Facebook keyboard shortcut 'p' opens the composer on the News Feed
                await self.page.keyboard.press('Escape')
                await self.page.wait_for_timeout(500)
                await self.page.keyboard.press('p')
            
            await self.page.wait_for_timeout(2000)

            # Find Facebook composer editor.
            # Facebook changes the DOM frequently, so use multiple compatible selectors.
            editor = self.page.locator(
                'div[contenteditable="true"]:visible, '
                '[role="textbox"][contenteditable="true"]:visible'
            ).first

            await editor.wait_for(state="visible", timeout=15000)
            await editor.click()

            # ORDER: caption first. The editor is freshly focused right after the
            # composer opens, which is when insert_text reliably lands; after a
            # photo is attached the editor is re-mounted and focus is unreliable.
            # The caption is re-checked (and restored) after the upload instead.
            await self._enter_content(editor, content)
            await self.page.wait_for_timeout(1000)

            # Facebook shows a hashtag/mention suggestion list while the caret sits
            # inside the last token. That list overlays the photo button and the
            # Post button, so it has to go before anything is clicked.
            await self._dismiss_typeahead()
            print(f"[Facebook] caption entered: {len(await self._caption_text())} chars "
                  f"(source: {len((content or '').strip())})", flush=True)

            # Handle images — never publish text-only when an image was supplied
            if image_paths:
                if not await self._attach_images(image_paths):
                    await self._debug_screenshot("facebook_image_not_attached.png")
                    return {
                        "success": False,
                        "message": ("Facebook composer did not accept the image. "
                                    "Nothing was published (an image post is never "
                                    "sent as text-only).")
                    }
                print("[Facebook] image attached", flush=True)

            # Attaching a photo re-renders the composer — the caption must still
            # be there, otherwise nothing gets published.
            if not await self._ensure_caption(content):
                await self._debug_screenshot("facebook_caption_lost.png")
                return {
                    "success": False,
                    "message": ("The Facebook composer lost the caption text, so nothing "
                                "was published (an image-only post is not what was scheduled).")
                }

            # Click the Post button (English + Vietnamese UI, visible only)
            if not await self._click_post_button():
                await self._debug_screenshot("facebook_post_button_not_found.png")
                return {
                    "success": False,
                    "message": ("Could not find an enabled Post button in the Facebook "
                                "composer (screenshot: backend/facebook_post_button_not_found.png).")
                }

            if not await self._post_submitted(content, bool(image_paths), timeout_ms=25000):
                await self._debug_screenshot("facebook_post_not_confirmed.png")
                return {
                    "success": False,
                    "message": ("Clicked Post but the composer still holds the content — "
                                "the post was most likely not published.")
                }

            await self.page.wait_for_timeout(2000)
            await self.save_session(self.PLATFORM)
            return {"success": True, "message": "Posted to Facebook"}

        except Exception as e:
            return {"success": False, "message": f"Facebook post failed: {str(e)}"}
        finally:
            await self.close()


    # ---- composer helpers -------------------------------------------------

    PHOTO_BUTTON_SELECTORS = (
        'div[role="dialog"] div[aria-label="Photo/video"]',
        'div[role="dialog"] div[aria-label="Ảnh/video"]',
        'div[role="dialog"] div[aria-label*="Photo/video"]',
        'div[role="dialog"] div[aria-label*="Ảnh/video"]',
    )

    IMAGE_PREVIEW_SELECTORS = (
        'div[role="dialog"] img[src^="blob:"]',
        'div[role="dialog"] img[src^="data:"]',
        'div[role="dialog"] div[aria-label*="Remove photo"]',
        'div[role="dialog"] div[aria-label*="Edit photo"]',
        'div[role="dialog"] div[aria-label*="Xóa ảnh"]',
        'div[role="dialog"] div[aria-label*="Chỉnh sửa ảnh"]',
    )

    POST_BUTTON_SELECTORS = (
        'div[role="dialog"] div[aria-label="Post"][role="button"]',
        'div[role="dialog"] div[aria-label="Đăng"][role="button"]',
        'div[aria-label="Post"][role="button"]',
        'div[aria-label="Đăng"][role="button"]',
    )

    async def _caption_text(self) -> str:
        """Read the composer text.

        The Facebook dialog contains several contenteditable nodes (the caption
        editor, and empty ones used by other widgets), and which one comes first
        changes with the layout — reading only the first one returns an empty
        string and makes the caller believe the caption failed to go in. So: read
        every visible one and keep the longest.
        """
        best = ""
        for selector in ('div[role="dialog"] div[contenteditable="true"]',
                         'div[contenteditable="true"]'):
            try:
                locator = self.page.locator(selector)
                count = await locator.count()
            except Exception:
                continue

            for index in range(min(count, 6)):
                try:
                    element = locator.nth(index)
                    if not await element.is_visible():
                        continue
                    text = (await element.inner_text()).strip()
                except Exception:
                    continue
                if len(text) > len(best):
                    best = text

            if best:
                return best
        return best

    async def _focus_editor(self):
        """Click the real caption editor (largest visible contenteditable)."""
        for selector in ('div[role="dialog"] div[contenteditable="true"][role="textbox"]',
                         'div[role="dialog"] div[contenteditable="true"]',
                         'div[contenteditable="true"]'):
            try:
                locator = self.page.locator(selector)
                count = await locator.count()
            except Exception:
                continue

            best_element, best_area = None, -1
            for index in range(min(count, 6)):
                try:
                    element = locator.nth(index)
                    if not await element.is_visible():
                        continue
                    box = await element.bounding_box()
                    area = (box["width"] * box["height"]) if box else 0
                except Exception:
                    continue
                if area > best_area:
                    best_element, best_area = element, area

            if best_element is not None:
                try:
                    await best_element.click()
                    await self.page.wait_for_timeout(400)
                    return True
                except Exception:
                    continue
        return False

    async def _enter_content(self, editor, content: str):
        """Insert the caption in one operation.

        Typing key by key makes Facebook pop up page/hashtag suggestions, and the
        Enter that ends a line accepts the highlighted one — that is how an
        unrelated page name gets spliced into the caption. insert_text writes the
        whole text at once, so no suggestion is ever confirmed. Only the text
        LENGTH is verified: Facebook's editor normalises blank lines, so counting
        line breaks would reject a perfectly good caption and trigger a needless
        retype.
        """
        content = content or ""
        target_len = len(content.strip())
        if target_len == 0:
            return

        try:
            try:
                await editor.click()
                await self.page.wait_for_timeout(300)
            except Exception:
                await self._focus_editor()
            await self.page.keyboard.insert_text(content)
            await self.page.wait_for_timeout(1500)
            if len(await self._caption_text()) >= target_len * 0.8:
                return
        except Exception:
            pass

        # Partial insert: clear (with the caret back in the editor) and retry
        # line by line, using Shift+Enter, which does not confirm a suggestion.
        await self._focus_editor()
        try:
            await self.page.keyboard.press("ControlOrMeta+a")
            await self.page.keyboard.press("Backspace")
            await self.page.wait_for_timeout(400)
            for index, line in enumerate(content.split("\n")):
                if line:
                    await self.page.keyboard.insert_text(line)
                await self.page.keyboard.press("Shift+Enter")
                await self.page.wait_for_timeout(100)
            if len(await self._caption_text()) >= target_len * 0.8:
                return
        except Exception:
            pass

        await self._focus_editor()
        try:
            await self.page.keyboard.press("ControlOrMeta+a")
            await self.page.keyboard.press("Backspace")
            await self.page.keyboard.type(content, delay=8)
        except Exception:
            pass

    async def _ensure_caption(self, content: str) -> bool:
        """Last line of defence before posting: the caption must still be there.

        Attaching a photo makes Facebook re-render the composer, and the text can
        be dropped in the process. An image-only post is not what was scheduled,
        so if the caption cannot be restored the post is abandoned.
        """
        target_len = len((content or "").strip())
        if target_len == 0:
            return True

        if len(await self._caption_text()) >= target_len * 0.5:
            return True

        if await self._focus_editor():
            try:
                await self.page.keyboard.insert_text(content)
                await self.page.wait_for_timeout(1500)
                await self._dismiss_typeahead()
            except Exception:
                pass

        return len(await self._caption_text()) >= target_len * 0.5

    async def _debug_screenshot(self, filename: str):
        try:
            from pathlib import Path as _Path
            filename = str(_Path(__file__).resolve().parent / filename)
            await self.page.screenshot(path=filename, full_page=False)
            print(f"[Facebook] debug screenshot saved: {filename}", flush=True)
        except Exception:
            pass

    async def _typeahead_open(self) -> bool:
        for selector in ('[role="listbox"]:visible', '[role="option"]:visible'):
            try:
                if await self.page.locator(selector).first.count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _dismiss_typeahead(self):
        """Close the hashtag/mention suggestion list without closing the composer.

        Escape is not usable here: with no list open it closes the whole composer.
        A trailing space ends the current token instead, and a click on the dialog
        heading blurs the editor if the list is still up. Only runs when a list is
        actually open, so repeated calls do not keep appending spaces.
        """
        if not await self._typeahead_open():
            return

        try:
            await self.page.keyboard.insert_text(" ")
            await self.page.wait_for_timeout(700)
        except Exception:
            pass

        for selector in ('div[role="dialog"] h2', 'div[role="dialog"] h2 span'):
            try:
                heading = self.page.locator(selector).first
                if await heading.count() > 0 and await heading.is_visible():
                    await heading.click()
                    await self.page.wait_for_timeout(700)
                    return
            except Exception:
                continue

    async def _image_preview_visible(self) -> bool:
        for selector in self.IMAGE_PREVIEW_SELECTORS:
            try:
                if await self.page.locator(selector).first.count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _set_files(self, image_paths) -> bool:
        """Feed the files to the composer's file input (it may be hidden)."""
        # SCOPE MATTERS: the feed page carries other file inputs (the inline feed
        # composer, Story creation). Feeding the photo to one of those attaches it
        # to a different composer — Facebook then publishes the image as its own
        # post while the caption stays behind in this dialog. Always prefer the
        # input that lives inside the open dialog.
        for selector in ('div[role="dialog"] input[type="file"][accept*="image"]',
                         'div[role="dialog"] input[type="file"]',
                         'input[type="file"][accept*="image"]',
                         'input[type="file"]'):
            try:
                file_input = self.page.locator(selector).first
                if await file_input.count() == 0:
                    continue
                await file_input.set_input_files(image_paths)
                for _ in range(10):
                    await self.page.wait_for_timeout(1000)
                    if await self._image_preview_visible():
                        return True
            except Exception:
                continue
        return False

    async def _attach_images(self, image_paths) -> bool:
        """Attach the images and confirm the composer really shows a preview.

        The hidden file input is tried first: clicking the Photo/video button is
        unnecessary when the input already exists, and that button is often
        covered by a suggestion popup.
        """
        if await self._set_files(image_paths):
            return True

        await self._dismiss_typeahead()

        for selector in self.PHOTO_BUTTON_SELECTORS:
            try:
                button = self.page.locator(selector).first
                if await button.count() > 0 and await button.is_visible():
                    await button.click()
                    await self.page.wait_for_timeout(1500)
                    break
            except Exception:
                continue

        if await self._set_files(image_paths):
            return True

        return await self._image_preview_visible()

    async def _click_post_button(self, timeout_ms: int = 45000) -> bool:
        """Click Post, waiting for it to become enabled.

        Right after an image is attached Facebook keeps the button disabled while
        the upload finishes, so a single check is not enough.
        """
        import re

        await self._dismiss_typeahead()
        waited = 0

        while waited < timeout_ms:
            # Anchor on the composer dialog itself, so no button belonging to the
            # feed or to another widget can ever be clicked.
            dialog = self.page.locator(
                'div[role="dialog"]:has(div[contenteditable="true"])'
            ).last
            candidates = []
            try:
                candidates.append(
                    dialog.get_by_role("button", name=re.compile(r"^\s*(Post|Đăng)\s*$", re.I))
                )
            except Exception:
                pass
            candidates.append(dialog.locator('div[aria-label="Post"][role="button"], '
                                             'div[aria-label="Đăng"][role="button"]'))
            candidates.append(dialog.locator('div[role="button"]:has(span:text-is("Post")), '
                                             'div[role="button"]:has(span:text-is("Đăng"))'))

            for candidate in candidates:
                # Facebook renders MORE THAN ONE element with aria-label="Post":
                # the first one is permanently aria-disabled, the real button
                # comes after it. Every match has to be examined, not just .first.
                try:
                    count = await candidate.count()
                except Exception:
                    continue

                for index in range(min(count, 8)):
                    element = candidate.nth(index)
                    try:
                        if not await element.is_visible():
                            continue
                        if (await element.get_attribute("aria-disabled")) == "true":
                            continue
                        await element.scroll_into_view_if_needed(timeout=3000)
                        await element.click(timeout=5000)
                        return True
                    except Exception:
                        continue

            await self._dismiss_typeahead()
            await self.page.wait_for_timeout(2000)
            waited += 2000

        return False

    async def _post_submitted(self, content: str = "", had_image: bool = False,
                              timeout_ms: int = 25000) -> bool:
        """Was the post actually submitted?

        Two valid outcomes: the composer disappears, or Facebook re-opens an
        empty composer right after publishing. An empty composer counts only
        because this is called after the caption and the image were verified to
        be in it — losing both at once means they were sent.
        """
        waited = 0
        while waited < timeout_ms:
            try:
                editor = self.page.locator(
                    'div[role="dialog"] div[contenteditable="true"]:visible'
                )
                if await editor.count() == 0:
                    return True

                caption_gone = len(await self._caption_text()) < 20
                image_gone = not await self._image_preview_visible() if had_image else True
                if caption_gone and image_gone:
                    # Composer reset: the content left the dialog, i.e. it posted.
                    return True
            except Exception:
                return False

            await self.page.wait_for_timeout(1000)
            waited += 1000
        return False


class RedditAutomation(SocialAutomation):
    """Reddit browser automation — manual-first, draft support"""

    PLATFORM = "reddit"
    LOGIN_URL = "https://www.reddit.com/login"
    HOME_URL = "https://www.reddit.com/"

    async def login(self, username: str, password: str) -> dict:
        await self.init_browser()
        await self.create_new_context()

        try:
            await self.page.goto(self.LOGIN_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(2000)

            username_input = await self.page.wait_for_selector(
                'input#loginUsername, input[name="username"]', timeout=10000
            )
            await username_input.fill(username)

            password_input = await self.page.wait_for_selector(
                'input#loginPassword, input[name="password"]'
            )
            await password_input.fill(password)

            login_btn = await self.page.wait_for_selector(
                'button[type="submit"]:has-text("Log In"), button:has-text("Log In")'
            )
            await login_btn.click()
            await self.page.wait_for_timeout(5000)

            if "login" not in self.page.url:
                await self.save_session(self.PLATFORM)
                return {"success": True, "message": "Logged in to Reddit"}
            else:
                return {"success": False, "message": "Reddit login failed"}

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def check_login(self) -> bool:
        await self.init_browser()
        if await self.load_session(self.PLATFORM):
            try:
                await self.page.goto(self.HOME_URL)
                await self.page.wait_for_load_state("networkidle")
                return "login" not in self.page.url
            except Exception:
                pass
        return False

    async def post(self, content: str, image_paths: List[str] = None,
                   subreddit: str = None, title: str = None) -> dict:
        """Post to a subreddit. Requires subreddit name and title."""
        if not subreddit:
            return {"success": False, "message": "Subreddit is required for Reddit posts"}
        if not title:
            return {"success": False, "message": "Title is required for Reddit posts"}

        await self.init_browser()

        if not await self.load_session(self.PLATFORM):
            return {"success": False, "message": "Not logged in to Reddit"}

        try:
            submit_url = f"https://www.reddit.com/r/{subreddit}/submit"
            await self.page.goto(submit_url)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(3000)

            # Fill title
            title_input = await self.page.wait_for_selector(
                'textarea[placeholder*="Title"], input[placeholder*="Title"], '
                'div[data-placeholder*="Title"]',
                timeout=10000
            )
            await title_input.fill(title)
            await self.page.wait_for_timeout(500)

            # Switch to text tab if needed and fill body
            try:
                text_tab = await self.page.query_selector(
                    'button:has-text("Text"), span:has-text("Text")'
                )
                if text_tab:
                    await text_tab.click()
                    await self.page.wait_for_timeout(500)
            except Exception:
                pass

            body_editor = await self.page.wait_for_selector(
                'div[contenteditable="true"], textarea[placeholder*="Text"]',
                timeout=5000
            )
            await body_editor.click()
            await self.page.keyboard.type(content, delay=20)
            await self.page.wait_for_timeout(1000)

            # Click Post
            post_btn = await self.page.wait_for_selector(
                'button:has-text("Post"), button[type="submit"]:has-text("Post")',
                timeout=10000
            )
            await post_btn.click()
            await self.page.wait_for_timeout(5000)

            await self.save_session(self.PLATFORM)
            return {"success": True, "message": f"Posted to r/{subreddit}"}

        except Exception as e:
            return {"success": False, "message": f"Reddit post failed: {str(e)}"}
        finally:
            await self.close()


class MediumAutomation(SocialAutomation):
    """Medium browser automation"""

    PLATFORM = "medium"
    LOGIN_URL = "https://medium.com/m/signin"
    HOME_URL = "https://medium.com/"
    NEW_STORY_URL = "https://medium.com/new-story"

    async def login(self, email: str, password: str) -> dict:
        await self.init_browser()
        await self.create_new_context()

        try:
            await self.page.goto(self.LOGIN_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(2000)

            # Medium uses Google/email sign-in flow
            # Click "Sign in with email"
            email_btn = await self.page.query_selector(
                'button:has-text("Sign in with email"), '
                'a:has-text("Sign in with email")'
            )
            if email_btn:
                await email_btn.click()
                await self.page.wait_for_timeout(2000)

            # Enter email
            email_input = await self.page.wait_for_selector(
                'input[type="email"], input[placeholder*="email"]',
                timeout=10000
            )
            await email_input.fill(email)

            # Look for continue/submit
            continue_btn = await self.page.query_selector(
                'button:has-text("Continue"), button[type="submit"]'
            )
            if continue_btn:
                await continue_btn.click()

            await self.page.wait_for_timeout(5000)

            # Medium may send magic link or show password field
            try:
                pw_input = await self.page.query_selector('input[type="password"]')
                if pw_input:
                    await pw_input.fill(password)
                    submit_btn = await self.page.query_selector(
                        'button[type="submit"], button:has-text("Sign in")'
                    )
                    if submit_btn:
                        await submit_btn.click()
                    await self.page.wait_for_timeout(5000)
            except Exception:
                pass

            if "signin" not in self.page.url and "login" not in self.page.url:
                await self.save_session(self.PLATFORM)
                return {"success": True, "message": "Logged in to Medium"}
            else:
                return {"success": False,
                        "message": "Medium login may require magic link. Check your email."}

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def check_login(self) -> bool:
        await self.init_browser()
        if await self.load_session(self.PLATFORM):
            try:
                await self.page.goto(self.HOME_URL)
                await self.page.wait_for_load_state("networkidle")
                # Check if we can access new-story page
                await self.page.goto(self.NEW_STORY_URL)
                await self.page.wait_for_timeout(2000)
                return "new-story" in self.page.url or "p/" in self.page.url
            except Exception:
                pass
        return False

    async def post(self, content: str, image_paths: List[str] = None,
                   title: str = None) -> dict:
        """Create a draft story on Medium"""
        await self.init_browser()

        if not await self.load_session(self.PLATFORM):
            return {"success": False, "message": "Not logged in to Medium"}

        try:
            await self.page.goto(self.NEW_STORY_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(3000)

            # Title field
            title_field = await self.page.wait_for_selector(
                'h3[data-testid="editorTitle"], '
                'div[data-placeholder="Title"], '
                'h3[contenteditable="true"]',
                timeout=10000
            )
            await title_field.click()
            await self.page.keyboard.type(title or "Untitled", delay=20)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(500)

            # Body — type content line by line for proper formatting
            lines = content.split('\n')
            for line in lines:
                await self.page.keyboard.type(line, delay=10)
                await self.page.keyboard.press("Enter")

            await self.page.wait_for_timeout(2000)

            # Medium auto-saves drafts
            await self.save_session(self.PLATFORM)
            return {"success": True,
                    "message": "Draft created on Medium (auto-saved). Review and publish manually."}

        except Exception as e:
            return {"success": False, "message": f"Medium draft failed: {str(e)}"}
        finally:
            await self.close()


class SubstackAutomation(SocialAutomation):
    """Substack browser automation"""

    PLATFORM = "substack"
    LOGIN_URL = "https://substack.com/sign-in"

    def __init__(self, headless: bool = False, publication_url: str = None):
        super().__init__(headless)
        self.publication_url = publication_url or ""

    async def login(self, email: str, password: str) -> dict:
        await self.init_browser()
        await self.create_new_context()

        try:
            await self.page.goto(self.LOGIN_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(2000)

            # Enter email
            email_input = await self.page.wait_for_selector(
                'input[type="email"], input[name="email"], input[placeholder*="email"]',
                timeout=10000
            )
            await email_input.fill(email)

            # Click continue
            continue_btn = await self.page.query_selector(
                'button:has-text("Continue"), button:has-text("Sign in"), '
                'button[type="submit"]'
            )
            if continue_btn:
                await continue_btn.click()
            await self.page.wait_for_timeout(2000)

            # Password field
            try:
                pw_input = await self.page.wait_for_selector(
                    'input[type="password"]', timeout=5000
                )
                await pw_input.fill(password)
                submit_btn = await self.page.query_selector(
                    'button:has-text("Sign in"), button[type="submit"]'
                )
                if submit_btn:
                    await submit_btn.click()
                await self.page.wait_for_timeout(5000)
            except Exception:
                return {"success": False,
                        "message": "Substack may use magic link. Check your email."}

            if "sign-in" not in self.page.url:
                await self.save_session(self.PLATFORM)
                return {"success": True, "message": "Logged in to Substack"}
            else:
                return {"success": False, "message": "Substack login failed"}

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def check_login(self) -> bool:
        await self.init_browser()
        if await self.load_session(self.PLATFORM):
            try:
                await self.page.goto("https://substack.com/home")
                await self.page.wait_for_load_state("networkidle")
                return "sign-in" not in self.page.url
            except Exception:
                pass
        return False

    async def post(self, content: str, image_paths: List[str] = None,
                   title: str = None, subtitle: str = None) -> dict:
        """Create a draft post on Substack"""
        await self.init_browser()

        if not await self.load_session(self.PLATFORM):
            return {"success": False, "message": "Not logged in to Substack"}

        try:
            # Navigate to new post editor
            new_post_url = f"{self.publication_url}/publish/post" if self.publication_url else "https://substack.com/home"
            await self.page.goto(new_post_url)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(3000)

            # Title
            title_field = await self.page.wait_for_selector(
                'div[data-placeholder="Title"], '
                'textarea[placeholder*="Title"], '
                'div[role="textbox"]',
                timeout=10000
            )
            await title_field.click()
            await self.page.keyboard.type(title or "Newsletter Draft", delay=20)
            await self.page.keyboard.press("Tab")
            await self.page.wait_for_timeout(500)

            # Subtitle if available
            if subtitle:
                try:
                    subtitle_field = await self.page.query_selector(
                        'div[data-placeholder="Subtitle"], textarea[placeholder*="subtitle"]'
                    )
                    if subtitle_field:
                        await subtitle_field.click()
                        await self.page.keyboard.type(subtitle, delay=15)
                        await self.page.keyboard.press("Tab")
                except Exception:
                    pass

            # Body content
            await self.page.wait_for_timeout(500)
            lines = content.split('\n')
            for line in lines:
                await self.page.keyboard.type(line, delay=10)
                await self.page.keyboard.press("Enter")

            await self.page.wait_for_timeout(2000)

            # Substack auto-saves drafts
            await self.save_session(self.PLATFORM)
            return {"success": True,
                    "message": "Draft created on Substack (auto-saved). Review and send from dashboard."}

        except Exception as e:
            return {"success": False, "message": f"Substack draft failed: {str(e)}"}
        finally:
            await self.close()


class DiscordAutomation:
    """Discord posting via webhook — no browser needed"""

    PLATFORM = "discord"

    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url

    async def post(self, content: str, image_paths: List[str] = None,
                   username: str = "OpenClaw CMO") -> dict:
        if not self.webhook_url:
            return {"success": False, "message": "Discord webhook URL not configured"}

        try:
            payload = {
                "username": username,
                "content": content[:2000]  # Discord limit
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    timeout=15.0
                )

            if response.status_code in (200, 204):
                return {"success": True, "message": "Posted to Discord"}
            else:
                return {"success": False,
                        "message": f"Discord webhook failed: HTTP {response.status_code}"}

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def login(self, *args, **kwargs) -> dict:
        return {"success": True, "message": "Discord uses webhook — no login needed"}

    async def check_login(self) -> bool:
        return bool(self.webhook_url)

    async def close(self):
        pass


class HeyGenAutomation(SocialAutomation):
    """HeyGen browser automation for video creation"""

    PLATFORM = "heygen"
    LOGIN_URL = "https://app.heygen.com/login"
    HOME_URL = "https://app.heygen.com/home"

    async def login(self, email: str, password: str) -> dict:
        await self.init_browser()
        await self.create_new_context()

        try:
            await self.page.goto(self.LOGIN_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(3000)

            # Fill email
            email_input = await self.page.wait_for_selector(
                'input[type="email"], input[name="email"], input[placeholder*="email"]',
                timeout=10000
            )
            await email_input.fill(email)

            # Fill password
            pw_input = await self.page.wait_for_selector(
                'input[type="password"], input[name="password"]'
            )
            await pw_input.fill(password)

            # Click sign in
            login_btn = await self.page.wait_for_selector(
                'button:has-text("Sign in"), button:has-text("Log in"), '
                'button[type="submit"]'
            )
            await login_btn.click()
            await self.page.wait_for_timeout(5000)

            if "login" not in self.page.url:
                await self.save_session(self.PLATFORM)
                return {"success": True, "message": "Logged in to HeyGen"}
            else:
                return {"success": False, "message": "HeyGen login failed"}

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def check_login(self) -> bool:
        await self.init_browser()
        if await self.load_session(self.PLATFORM):
            try:
                await self.page.goto(self.HOME_URL)
                await self.page.wait_for_load_state("networkidle")
                return "login" not in self.page.url
            except Exception:
                pass
        return False

    async def post(self, content: str, image_paths: List[str] = None) -> dict:
        """Navigate to HeyGen and prepare a video project from a script brief"""
        await self.init_browser()

        if not await self.load_session(self.PLATFORM):
            return {"success": False, "message": "Not logged in to HeyGen"}

        try:
            # Go to create new video
            await self.page.goto("https://app.heygen.com/create-v3/new")
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(5000)

            # HeyGen's editor requires visual interaction
            # We can navigate to the editor but script input varies by UI version
            # Best approach: open the editor and let the brief guide manual work

            await self.save_session(self.PLATFORM)
            return {"success": True,
                    "message": "HeyGen editor opened. Script brief is ready for manual video creation.",
                    "brief": content[:2000]}

        except Exception as e:
            return {"success": False, "message": f"HeyGen failed: {str(e)}"}
        finally:
            await self.close()


class EmailAutomation(SocialAutomation):
    """Generic email/newsletter platform browser automation
    Supports: beehiiv, MailerLite, Brevo via browser login
    """

    PLATFORMS = {
        "beehiiv": {
            "login_url": "https://app.beehiiv.com/login",
            "home_url": "https://app.beehiiv.com/",
            "new_post_url": "https://app.beehiiv.com/posts/new",
        },
        "mailerlite": {
            "login_url": "https://app.mailerlite.com/login",
            "home_url": "https://app.mailerlite.com/",
        },
        "brevo": {
            "login_url": "https://app.brevo.com/login",
            "home_url": "https://app.brevo.com/",
        },
    }

    def __init__(self, headless: bool = False, email_platform: str = "beehiiv"):
        super().__init__(headless)
        self.email_platform = email_platform.lower()
        config = self.PLATFORMS.get(self.email_platform, {})
        self.login_url = config.get("login_url", "")
        self.home_url = config.get("home_url", "")
        self.PLATFORM = f"email_{self.email_platform}"

    async def login(self, email: str, password: str) -> dict:
        if not self.login_url:
            return {"success": False, "message": f"Unknown email platform: {self.email_platform}"}

        await self.init_browser()
        await self.create_new_context()

        try:
            await self.page.goto(self.login_url)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(2000)

            # Generic email/password login
            email_input = await self.page.wait_for_selector(
                'input[type="email"], input[name="email"], '
                'input[placeholder*="email"], input[placeholder*="Email"]',
                timeout=10000
            )
            await email_input.fill(email)

            pw_input = await self.page.wait_for_selector(
                'input[type="password"], input[name="password"]'
            )
            await pw_input.fill(password)

            submit_btn = await self.page.wait_for_selector(
                'button[type="submit"], button:has-text("Log in"), '
                'button:has-text("Sign in")'
            )
            await submit_btn.click()
            await self.page.wait_for_timeout(5000)

            if "login" not in self.page.url and "sign" not in self.page.url:
                await self.save_session(self.PLATFORM)
                return {"success": True, "message": f"Logged in to {self.email_platform}"}
            else:
                return {"success": False, "message": f"{self.email_platform} login failed"}

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def check_login(self) -> bool:
        await self.init_browser()
        if await self.load_session(self.PLATFORM):
            try:
                await self.page.goto(self.home_url)
                await self.page.wait_for_load_state("networkidle")
                return "login" not in self.page.url
            except Exception:
                pass
        return False

    async def post(self, content: str, image_paths: List[str] = None,
                   subject: str = None) -> dict:
        """Create a newsletter draft on the email platform"""
        await self.init_browser()

        if not await self.load_session(self.PLATFORM):
            return {"success": False, "message": f"Not logged in to {self.email_platform}"}

        try:
            # Navigate to compose/new post area
            if self.email_platform == "beehiiv":
                await self.page.goto("https://app.beehiiv.com/posts/new")
            else:
                await self.page.goto(self.home_url)

            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(3000)

            await self.save_session(self.PLATFORM)
            return {"success": True,
                    "message": f"Opened {self.email_platform} editor. Draft content ready for review.",
                    "subject": subject,
                    "content_preview": content[:500]}

        except Exception as e:
            return {"success": False, "message": f"{self.email_platform} failed: {str(e)}"}
        finally:
            await self.close()


# Extended factory function
def get_automation_extended(platform: str, headless: bool = False, **kwargs):
    """Get automation instance for any supported platform"""
    from automation import LinkedInAutomation, InstagramAutomation, TwitterAutomation

    platforms = {
        "linkedin": lambda: LinkedInAutomation(headless),
        "instagram": lambda: InstagramAutomation(headless),
        "twitter": lambda: TwitterAutomation(headless),
        "x": lambda: TwitterAutomation(headless),
        "facebook": lambda: _get_facebook(headless),
        "reddit": lambda: RedditAutomation(headless),
        "medium": lambda: MediumAutomation(headless),
        "substack": lambda: SubstackAutomation(headless, kwargs.get("publication_url")),
        "discord": lambda: DiscordAutomation(kwargs.get("webhook_url")),
        "heygen": lambda: _get_heygen(headless),
        "beehiiv": lambda: EmailAutomation(headless, "beehiiv"),
        "mailerlite": lambda: EmailAutomation(headless, "mailerlite"),
        "brevo": lambda: EmailAutomation(headless, "brevo"),
        "zalo": lambda: _get_zalo_oa(),
        "tiktok": lambda: _get_tiktok(headless),
    }

    factory = platforms.get(platform.lower())
    if not factory:
        raise ValueError(f"Unsupported platform: {platform}. "
                        f"Supported: {', '.join(platforms.keys())}")
    return factory()


def _get_facebook(headless: bool):
    """Graph API when a Page is configured, browser automation otherwise.

    The Graph API is the reliable path (one request, no UI, no session to
    expire). Playwright stays as the fallback so nothing breaks before the Page
    token is set up.
    """
    try:
        from facebook_graph import FacebookGraphAutomation
        graph = FacebookGraphAutomation()
        if graph.is_configured():
            print("[Facebook] using Graph API (Page)", flush=True)
            return graph
    except Exception as e:
        print(f"[Facebook] Graph API unavailable ({e}); falling back to browser", flush=True)
    return FacebookAutomation(headless)


def _get_heygen(headless: bool):
    """Import and return the enhanced HeyGen adapter"""
    try:
        from heygen_adapter import HeyGenVideoAdapter
        return HeyGenVideoAdapter(headless)
    except ImportError:
        return HeyGenAutomation(headless)


def _get_zalo_oa():
    """Import and return the Zalo OA automation (API-based, no browser)"""
    try:
        from importlib import import_module
        mod = import_module("zalo-oa-automation")
        return mod.ZaloOAAutomation()
    except ImportError:
        raise ValueError(
            "Zalo OA module not found. "
            "Ensure backend/zalo-oa-automation.py exists."
        )


def _get_tiktok(headless: bool):
    """Import and return the TikTok automation"""
    try:
        from importlib import import_module
        mod = import_module("tiktok-automation")
        return mod.TikTokAutomation(headless)
    except ImportError:
        raise ValueError(
            "TikTok module not found. "
            "Ensure backend/tiktok-automation.py exists."
        )
