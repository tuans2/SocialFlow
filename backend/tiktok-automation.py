import asyncio
from typing import List
from automation import SocialAutomation

class TikTokAutomation(SocialAutomation):
    """TikTok browser automation for video uploads"""
    
    PLATFORM = "tiktok"
    LOGIN_URL = "https://www.tiktok.com/login"
    HOME_URL = "https://www.tiktok.com/"
    UPLOAD_URL = "https://www.tiktok.com/creator-center/upload"
    
    async def login(self, username: str, password: str) -> dict:
        """Login to TikTok using headless/headed browser"""
        await self.init_browser()
        await self.create_new_context()
        
        try:
            await self.page.goto(self.LOGIN_URL)
            await self.page.wait_for_load_state("networkidle")
            
            # TikTok has multiple login methods (Email/Username, Phone, QR, etc.)
            # Due to heavy bot protection, the best approach for the initial login
            # is to let the user login manually (scan QR or type password + solve captcha).
            # The system will wait until the login is successful.
            
            print("⏳ Please login to TikTok manually in the browser (Scan QR code is recommended).")
            print("⏳ Waiting for login success...")
            
            # Wait for url to change away from login, or for a specific element that indicates logged-in state
            for _ in range(120):  # Wait up to 2 minutes
                await self.page.wait_for_timeout(1000)
                if "login" not in self.page.url:
                    break
            
            await self.page.wait_for_timeout(3000)
            
            if "login" not in self.page.url:
                await self.save_session(self.PLATFORM)
                return {"success": True, "message": "Successfully logged in to TikTok."}
            else:
                return {"success": False, "message": "Login timeout or failed."}
                
        except Exception as e:
            return {"success": False, "message": f"Login process error: {str(e)}"}
            
    async def check_login(self) -> bool:
        """Check if current session is valid"""
        await self.init_browser()
        if await self.load_session(self.PLATFORM):
            try:
                await self.page.goto(self.HOME_URL)
                await self.page.wait_for_load_state("networkidle")
                return "login" not in self.page.url
            except Exception:
                pass
        return False
        
    async def post(self, content: str, media_paths: List[str] = None) -> dict:
        """Upload video to TikTok"""
        if not media_paths:
            return {"success": False, "message": "TikTok requires a video file."}
            
        await self.init_browser()
        
        if not await self.load_session(self.PLATFORM):
            return {"success": False, "message": "Not logged in to TikTok."}
            
        try:
            # Go to upload page
            await self.page.goto(self.UPLOAD_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(3000)
            
            # Handle iframe upload structure (TikTok sometimes uses an iframe for the creator center)
            iframe = self.page.frame(url=lambda u: "creator-center" in u)
            target = iframe if iframe else self.page
            
            print("Uploading video...")
            
            # 1. Start waiting for the file chooser BEFORE clicking
            async with target.expect_file_chooser() as fc_info:
                # 2. Click the upload button (Wait for the select video button)
                # Note: TikTok uses a lot of different selectors. We try the most common ones.
                upload_btn = await target.wait_for_selector(
                    'button[aria-label="Select file"], button:has-text("Select video"), div:has-text("Select video")',
                    timeout=15000
                )
                await upload_btn.click()
            
            # 3. Resolve the file chooser and set the video file
            file_chooser = await fc_info.value
            await file_chooser.set_files(media_paths[0])
            
            # Wait for upload to start processing
            await self.page.wait_for_timeout(5000)
            
            print("Writing caption...")
            
            # Fill the caption
            # The TikTok editor uses draft.js / contenteditable div
            editor = await target.wait_for_selector('div[contenteditable="true"]', timeout=15000)
            await editor.click()
            await self.page.wait_for_timeout(500)
            # Clear existing placeholder text
            await self.page.keyboard.press("Meta+a" if "mac" in self.page.evaluate("navigator.platform").lower() else "Control+a")
            await self.page.keyboard.press("Backspace")
            # Type caption
            await self.page.keyboard.type(content, delay=50)
            
            print("Waiting for video to process and Post button to enable...")
            
            # Wait for Post button to become enabled. 
            # TikTok disables the button while processing the video.
            # Selectors can change, so we look for buttons with "Post" text that aren't disabled.
            post_btn = await target.wait_for_selector(
                'button:not([disabled]):has-text("Post"), div[role="button"]:not([aria-disabled="true"]):has-text("Post")',
                timeout=60000  # Give it 60 seconds to process the video
            )
            
            # Add a small buffer after it becomes enabled
            await self.page.wait_for_timeout(2000)
            await post_btn.click()
            
            # Wait for successful upload confirmation
            await self.page.wait_for_timeout(5000)
            
            await self.save_session(self.PLATFORM)
            return {"success": True, "message": "Successfully posted video to TikTok."}
            
        except Exception as e:
            return {"success": False, "message": f"TikTok upload failed: {str(e)}"}
        finally:
            await self.close()
