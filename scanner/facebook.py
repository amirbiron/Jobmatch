from playwright.async_api import async_playwright
from scanner.session_manager import SessionManager
from datetime import datetime, timedelta
import hashlib
import asyncio
import re
import logging
import os

logger = logging.getLogger(__name__)

NAVIGATION_TIMEOUT = 30000  # 30 seconds for cloud environments
DEBUG_SCREENSHOTS = os.getenv("DEBUG_SCREENSHOTS", "").lower() in ("1", "true", "yes")


async def _goto_with_retry(page, url: str, timeout: int = NAVIGATION_TIMEOUT, retries: int = 2):
    """Navigate to URL with retry logic for flaky cloud networks"""
    last_error = None
    max_attempts = 1 + retries
    for attempt in range(1, max_attempts + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return
        except Exception as e:
            last_error = e
            logger.warning(f"Navigation to {url} failed (attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                await page.wait_for_timeout(2000 * attempt)
    raise last_error


def create_post_hash(post_text: str, post_url: str) -> str:
    """Create unique hash for a post based on text + URL"""
    content = f"{post_url}:{post_text[:200]}"
    return hashlib.sha256(content.encode()).hexdigest()


def extract_email_from_text(text: str) -> str | None:
    """Try to find an email address in post text"""
    match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    return match.group(0) if match else None


class FacebookScanner:
    
    def __init__(self, db):
        self.db = db
        self.session_manager = SessionManager(db)
    
    async def _create_context(self, playwright):
        """Create browser context with saved session"""
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
            ]
        )
        
        # Try loading existing session
        storage_state = self.session_manager.load_session()
        
        context_options = {
            "viewport": {"width": 1280, "height": 800},
            "locale": "he-IL",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        }
        
        if storage_state:
            context_options["storage_state"] = storage_state
        
        context = await browser.new_context(**context_options)
        return browser, context
    
    async def _check_session_valid(self, page) -> bool:
        """Check if Facebook session is still active"""
        try:
            await _goto_with_retry(page, "https://www.facebook.com")
            await page.wait_for_timeout(3000)

            # If we see login form — session is expired
            login_btn = await page.query_selector("[name='login']")
            if login_btn:
                logger.warning("Session check: login button found — session expired")
                return False

            # Check for checkpoint/captcha pages
            page_url = page.url
            if "checkpoint" in page_url or "captcha" in page_url:
                logger.warning(f"Session check: checkpoint/captcha detected at {page_url}")
                return False

            # If we see the main feed — we're logged in
            logger.info("Session check: valid session detected")
            return True
        except Exception as e:
            logger.error(f"Session check failed: {e}")
            return False
    
    async def _login(self, page, email: str, password: str) -> bool:
        """Login to Facebook and save session"""
        try:
            await _goto_with_retry(page, "https://www.facebook.com")
            await page.wait_for_timeout(2000)
            
            await page.fill("#email", email)
            await page.fill("#pass", password)
            await page.click("[name='login']")
            await page.wait_for_timeout(5000)
            
            # Check if login succeeded
            login_btn = await page.query_selector("[name='login']")
            if login_btn:
                logger.error("Login failed — wrong credentials or captcha")
                return False
            
            # Save session to MongoDB
            storage_state = await page.context.storage_state()
            self.session_manager.save_session(storage_state)
            logger.info("Session saved to MongoDB")
            return True
            
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False
    
    async def _extract_post_from_element(self, el, group_url, seen_texts):
        """Extract a post dict from a DOM element, or return None if filtered out."""
        text = await el.inner_text()
        text = text.strip()

        if len(text) < 50:
            return None

        text_key = text[:100]
        if text_key in seen_texts:
            return None
        seen_texts.add(text_key)

        post_url = group_url
        try:
            # For articles, search directly; for other elements, find parent article first
            container = el
            role = await el.get_attribute("role")
            if role != "article":
                container = await el.evaluate_handle("el => el.closest('[role=\"article\"]')")

            link = await container.query_selector(
                "a[href*='/posts/'], a[href*='permalink'], "
                "a[href*='/p/'], a[href*='story_fbid']"
            )
            if link:
                post_url = await link.get_attribute("href")
        except Exception:
            pass

        return {
            "text": text,
            "url": post_url,
            "email": extract_email_from_text(text),
            "group_url": group_url,
            "scraped_at": datetime.utcnow()
        }

    async def scan_group(self, page, group_url: str) -> list[dict]:
        """Scan a single Facebook group for job posts"""
        posts = []

        try:
            await _goto_with_retry(page, group_url)
            await page.wait_for_timeout(4000)

            # Check if we were redirected away from the group (login/checkpoint)
            current_url = page.url
            if current_url != group_url and not current_url.startswith(group_url):
                from urllib.parse import urlparse
                parsed = urlparse(current_url)
                path = parsed.path.lower()
                if "/login" in path or "/checkpoint" in path or "/captcha" in path:
                    logger.warning(f"Redirected to {current_url} instead of group — session may be invalid")
                    return posts

            # Scroll to load more posts
            for scroll_i in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(2000)

            # Save debug screenshot if enabled
            if DEBUG_SCREENSHOTS:
                try:
                    group_name = group_url.rstrip("/").split("/")[-1]
                    screenshot_path = f"/tmp/fb_debug_{group_name}.png"
                    await page.screenshot(path=screenshot_path)
                    logger.info(f"Debug screenshot saved: {screenshot_path}")
                except Exception as e:
                    logger.warning(f"Failed to save debug screenshot: {e}")

            # Strategy 1: article-based extraction (most reliable)
            articles = await page.query_selector_all("[role='article']")
            logger.info(f"Found {len(articles)} article elements in {group_url}")

            seen_texts = set()

            for el in articles:
                try:
                    post = await self._extract_post_from_element(el, group_url, seen_texts)
                    if post:
                        posts.append(post)
                except Exception:
                    continue

            # Strategy 2+3: fallback selectors (only if articles didn't yield results)
            if not posts:
                post_elements = await page.query_selector_all(
                    "[data-ad-comet-preview='message'], "
                    "[data-ad-preview='message'], "
                    "div[dir='auto'][style*='text-align']"
                )

                if not post_elements:
                    post_elements = await page.query_selector_all("div[dir='auto']")
                    logger.info(f"Fallback: found {len(post_elements)} dir=auto elements")

                for el in post_elements:
                    try:
                        post = await self._extract_post_from_element(el, group_url, seen_texts)
                        if post:
                            posts.append(post)
                    except Exception:
                        continue

            logger.info(f"Scraped {len(posts)} posts from {group_url}")

            if not posts:
                try:
                    title = await page.title()
                    logger.warning(f"Zero posts from {group_url} — page title: '{title}'")
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error scanning group {group_url}: {e}")

        return posts
    
    async def scan_all_groups(self, group_urls: list[str], fb_email: str, fb_password: str) -> list[dict]:
        """Full scan cycle — login if needed, scan all groups, deduplicate"""
        all_posts = []
        
        async with async_playwright() as p:
            browser, context = await self._create_context(p)
            page = await context.new_page()
            
            try:
                # Check session
                if not await self._check_session_valid(page):
                    logger.info("Session invalid — logging in...")
                    self.session_manager.invalidate_session()
                    
                    if not await self._login(page, fb_email, fb_password):
                        logger.error("Could not login to Facebook")
                        await browser.close()
                        return []
                
                # Scan each group
                for group_url in group_urls:
                    group_url = group_url.strip()
                    if not group_url:
                        continue
                    
                    posts = await self.scan_group(page, group_url)
                    
                    # Deduplication — filter already-seen posts
                    for post in posts:
                        post_hash = create_post_hash(post["text"], post["url"])
                        
                        existing = self.db.scanned_posts.find_one({"hash": post_hash})
                        
                        if not existing:
                            # New post — save it
                            self.db.scanned_posts.insert_one({
                                "hash": post_hash,
                                "post_url": post["url"],
                                "post_text": post["text"],
                                "extracted_email": post["email"],
                                "group_url": post["group_url"],
                                "first_seen": datetime.utcnow(),
                                "expires_at": datetime.utcnow() + timedelta(days=30),
                                "candidates_sent": []
                            })
                            post["hash"] = post_hash
                            post["is_new"] = True
                            all_posts.append(post)
                        else:
                            # Existing post — still include for matching new candidates
                            post["hash"] = post_hash
                            post["is_new"] = False
                            post["candidates_sent"] = existing.get("candidates_sent", [])
                            all_posts.append(post)
                    
                    # Polite delay between groups
                    await page.wait_for_timeout(3000)
                
            finally:
                # Save updated session
                try:
                    storage_state = await context.storage_state()
                    self.session_manager.save_session(storage_state)
                except:
                    pass
                await browser.close()
        
        logger.info(f"Total posts collected: {len(all_posts)} ({sum(1 for p in all_posts if p.get('is_new'))} new)")
        return all_posts
