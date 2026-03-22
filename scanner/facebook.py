from playwright.async_api import async_playwright
from scanner.session_manager import SessionManager
from datetime import datetime, timedelta
import hashlib
import asyncio
import gc
import random
import re
import logging
import os

logger = logging.getLogger(__name__)

NAVIGATION_TIMEOUT = 30000  # 30 seconds for cloud environments
DEBUG_SCREENSHOTS = os.getenv("DEBUG_SCREENSHOTS", "").lower() in ("1", "true", "yes")

# Resource types to block — saves ~60% memory
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

# Smart scroll settings
MAX_SCROLLS = 8
KNOWN_THRESHOLD = 3  # consecutive known posts before stopping


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


def _stable_text_for_hash(text: str) -> str:
    """Normalize text so the same post gets the same hash across scans.
    Removes dynamic engagement counters, invisible chars, and URLs with tracking params."""
    text = text.lower()
    # Remove URLs (tracking params change between scans)
    text = re.sub(r'https?://\S+', '', text)
    # Remove invisible/bidi/PUA chars
    text = re.sub(r'[\u200e\u200f\u200b\u200c\u200d\u2060\ufeff]', '', text)
    text = re.sub(r'[\uE000-\uF8FF\U000F0000-\U0010FFFD]', '', text)
    lines = text.split('\n')
    stable = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # Skip standalone numbers (like counts, engagement)
        if re.match(r'^\d[\d,. ]*$', s):
            continue
        # Skip engagement patterns: "5 תגובות", "3 שיתופים"
        if re.match(r'^\d+\s+\S+$', s):
            continue
        stable.append(s)
    return ' '.join(stable).strip()


def _legacy_post_hash_v1(post_text: str, post_url: str) -> str:
    """Original hash algorithm (v1) — raw text[:200] + full URL."""
    content = f"{post_url}:{post_text[:200]}"
    return hashlib.sha256(content.encode()).hexdigest()


def _legacy_post_hash_v2(post_text: str, post_url: str) -> str:
    """Second hash algorithm (v2) — normalized text[:150] + base URL."""
    normalized = _stable_text_for_hash(post_text)
    base_url = post_url.split('?')[0] if post_url else ''
    core = f"{base_url}:{normalized[:150]}"
    return hashlib.sha256(core.encode()).hexdigest()


def create_post_hash(post_text: str, post_url: str) -> str:
    """Create stable hash for a post — uses first 12 words of normalized text.
    12 words captures the core content (title + first sentence) without
    including dynamic tails (comments, engagement) that change between scans.
    Word-based cutoff is more stable than char-based for short posts."""
    normalized = _stable_text_for_hash(post_text)
    # Use URL base (without query params) + first 12 words
    base_url = post_url.split('?')[0] if post_url else ''
    words = normalized.split()[:12]
    core = f"{base_url}:{' '.join(words)}"
    return hashlib.sha256(core.encode()).hexdigest()


def find_existing_post(db, post_text: str, post_url: str) -> dict | None:
    """Find an existing post by current hash OR any legacy hash — prevents
    treating already-seen posts as new after hash algorithm changes.
    Migration chain: v1 → v2 → v3 (current, 12-word)."""
    current_hash = create_post_hash(post_text, post_url)
    existing = db.scanned_posts.find_one({"hash": current_hash})
    if existing:
        return existing

    # Check legacy hashes (v2: 150-char, v1: 200-char raw)
    for legacy_fn in (_legacy_post_hash_v2, _legacy_post_hash_v1):
        legacy = legacy_fn(post_text, post_url)
        existing = db.scanned_posts.find_one({"hash": legacy})
        if existing:
            # Migrate to current hash for fast future lookups
            db.scanned_posts.update_one(
                {"_id": existing["_id"]},
                {"$set": {"hash": current_hash}}
            )
            logger.info(f"Migrated post hash for {post_url}")
            return existing
    return None


def extract_email_from_text(text: str) -> str | None:
    """Try to find an email address in post text"""
    match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    return match.group(0) if match else None


class FacebookScanner:
    
    def __init__(self, db):
        self.db = db
        self.session_manager = SessionManager(db)
    
    async def _create_context(self, playwright):
        """Create browser context with saved session — mobile viewport for lower memory"""
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
            "viewport": {"width": 360, "height": 640},  # mobile = lighter DOM
            "locale": "he-IL",
            "user_agent": (
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Mobile Safari/537.36"
            )
        }

        if storage_state:
            context_options["storage_state"] = storage_state

        context = await browser.new_context(**context_options)

        # Block heavy resources — saves ~60% memory
        async def _block_resources(route):
            if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", _block_resources)

        return browser, context
    
    async def _check_session_valid(self, page) -> bool:
        """Check if Facebook session is still active by visiting the main page"""
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

            # Check if redirected to login
            if "/login" in page_url.lower():
                logger.warning(f"Session check: redirected to login — session expired")
                return False

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
            if self._is_login_redirect(page.url, group_url):
                logger.warning(f"Redirected to {page.url} instead of group — session may be invalid")
                return posts

            # Save debug screenshot if enabled
            if DEBUG_SCREENSHOTS:
                try:
                    group_name = group_url.rstrip("/").split("/")[-1]
                    screenshot_path = f"/tmp/fb_debug_{group_name}.png"
                    await page.screenshot(path=screenshot_path)
                    logger.info(f"Debug screenshot saved: {screenshot_path}")
                except Exception as e:
                    logger.warning(f"Failed to save debug screenshot: {e}")

            # Smart scroll — track by content, stop after consecutive known posts
            seen_texts = set()
            consecutive_known = 0

            for scroll_num in range(MAX_SCROLLS):
                # Extract articles after each scroll
                articles = await page.query_selector_all("[role='article']")
                new_in_scroll = 0

                for el in articles:
                    try:
                        post = await self._extract_post_from_element(el, group_url, seen_texts)
                        if post:
                            # Check if we've already seen this post in DB (both hash algorithms)
                            existing = find_existing_post(self.db, post["text"], post["url"])
                            if existing:
                                consecutive_known += 1
                                # Cache for _dedup_posts to avoid redundant DB lookups
                                post["_existing"] = existing
                            else:
                                consecutive_known = 0
                                new_in_scroll += 1
                                post["_existing"] = None
                            posts.append(post)
                            if consecutive_known >= KNOWN_THRESHOLD:
                                logger.info(f"Hit {KNOWN_THRESHOLD} consecutive known posts — stopping scroll")
                                break
                    except Exception:
                        continue

                if consecutive_known >= KNOWN_THRESHOLD:
                    break

                if scroll_num > 0 and new_in_scroll == 0:
                    break  # scroll didn't yield new content

                # Scroll down
                await page.evaluate("window.scrollBy(0, 1500)")
                await asyncio.sleep(random.uniform(1.5, 3.0))

            # Fallback selectors if articles didn't yield results
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
    
    _UNCACHED = object()  # sentinel to distinguish "not cached" from "cached as None"

    def _dedup_posts(self, posts: list[dict]) -> list[dict]:
        """Dedup posts against DB — returns list with is_new/candidates_sent flags.
        Checks both new and legacy hash to avoid re-processing after hash migration.
        Uses cached _existing from scan_group when available to avoid redundant DB lookups."""
        result = []
        for post in posts:
            post_hash = create_post_hash(post["text"], post["url"])
            # Use cached lookup from scan_group if available, otherwise query DB
            cached = post.pop("_existing", self._UNCACHED)
            existing = cached if cached is not self._UNCACHED else find_existing_post(self.db, post["text"], post["url"])

            if not existing:
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
                result.append(post)
            else:
                post["hash"] = post_hash
                post["is_new"] = False
                post["candidates_sent"] = existing.get("candidates_sent", [])
                result.append(post)
        return result

    @staticmethod
    def _is_login_redirect(page_url: str, group_url: str) -> bool:
        """Check if page was redirected to a login/checkpoint page.
        Uses urlparse on the path only — avoids false positives on group URLs
        that contain 'login' as a substring (e.g. /groups/loginhelpers/)."""
        if page_url == group_url or page_url.startswith(group_url):
            return False  # still on the group page
        from urllib.parse import urlparse
        path = urlparse(page_url).path.lower()
        return "/login" in path or "/checkpoint" in path or "/captcha" in path

    async def scan_all_groups(self, group_urls: list[str], fb_email: str, fb_password: str) -> list[dict]:
        """Full scan cycle — login if needed, scan all groups, deduplicate"""
        all_posts = []

        async with async_playwright() as p:
            browser, context = await self._create_context(p)
            page = await context.new_page()

            try:
                # Ensure we have a valid session
                async def ensure_session(force=False):
                    if force:
                        # Clear browser cookies so _login sees the login form
                        await page.context.clear_cookies()
                    if force or not await self._check_session_valid(page):
                        logger.info("Session invalid — logging in...")
                        self.session_manager.invalidate_session()
                        if not await self._login(page, fb_email, fb_password):
                            logger.error("Could not login to Facebook")
                            return False
                    return True

                if not await ensure_session():
                    await browser.close()
                    return []

                async def retry_failed(groups: list[str], reason: str):
                    """Retry a list of failed group URLs, dedup results into all_posts."""
                    if not groups:
                        return
                    logger.info(f"Retrying {len(groups)} group(s) — {reason}")
                    for retry_url in groups:
                        retry_posts = await self.scan_group(page, retry_url)
                        all_posts.extend(self._dedup_posts(retry_posts))
                        await page.goto("about:blank")
                        await asyncio.sleep(random.uniform(2.0, 4.0))

                # Scan each group — track login failures for mid-scan re-login
                login_redirect_count = 0
                failed_groups = []
                for group_url in group_urls:
                    group_url = group_url.strip()
                    if not group_url:
                        continue

                    posts = await self.scan_group(page, group_url)

                    # Detect login redirects — collect failed groups, re-login after 2 consecutive
                    if not posts and self._is_login_redirect(page.url, group_url):
                        login_redirect_count += 1
                        failed_groups.append(group_url)
                        if login_redirect_count >= 2:
                            logger.warning("Multiple groups redirected to login — forcing re-login...")
                            if not await ensure_session(force=True):
                                logger.error("Re-login failed — aborting scan")
                                break
                            await retry_failed(failed_groups, "after mid-scan re-login")
                            failed_groups.clear()
                            login_redirect_count = 0
                        continue  # don't dedup empty posts from failed attempt
                    elif posts:
                        # Got actual posts — session is confirmed working.
                        # Retry any previously failed groups and reset counter.
                        await retry_failed(failed_groups, "session confirmed working")
                        failed_groups.clear()
                        login_redirect_count = 0
                    # else: empty scan without login redirect — ambiguous,
                    # don't reset counter or retry (group may be legitimately empty)

                    all_posts.extend(self._dedup_posts(posts))

                    # Free DOM memory and polite delay between groups
                    await page.goto("about:blank")
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                # After loop — retry any remaining failed groups (e.g. last group(s) failed)
                if failed_groups:
                    logger.warning(f"End of scan loop — {len(failed_groups)} failed group(s) remain")
                    if await ensure_session(force=True):
                        await retry_failed(failed_groups, "end-of-loop re-login")
                    else:
                        logger.error(f"Re-login failed — {len(failed_groups)} group(s) skipped this cycle")
                    failed_groups.clear()

            finally:
                # Save updated session
                try:
                    storage_state = await context.storage_state()
                    self.session_manager.save_session(storage_state)
                except:
                    pass
                await browser.close()

        # Free browser memory before classification/matching
        gc.collect()

        logger.info(f"Total posts collected: {len(all_posts)} ({sum(1 for p in all_posts if p.get('is_new'))} new)")
        return all_posts
