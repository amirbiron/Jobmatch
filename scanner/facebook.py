from playwright.async_api import async_playwright
from scanner.session_manager import SessionManager
from datetime import datetime, timedelta
import hashlib
import asyncio
import re
import logging

logger = logging.getLogger(__name__)


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
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        
        # Try loading existing session
        storage_state = self.session_manager.load_session()
        
        context_options = {
            "viewport": {"width": 1280, "height": 800},
            "locale": "he-IL",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            )
        }
        
        if storage_state:
            context_options["storage_state"] = storage_state
        
        context = await browser.new_context(**context_options)
        return browser, context
    
    async def _check_session_valid(self, page) -> bool:
        """Check if Facebook session is still active"""
        try:
            await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)
            
            # If we see login form — session is expired
            login_btn = await page.query_selector("[name='login']")
            if login_btn:
                return False
            
            # If we see the main feed — we're logged in
            return True
        except Exception as e:
            logger.error(f"Session check failed: {e}")
            return False
    
    async def _login(self, page, email: str, password: str) -> bool:
        """Login to Facebook and save session"""
        try:
            await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=15000)
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
    
    async def scan_group(self, page, group_url: str) -> list[dict]:
        """Scan a single Facebook group for job posts"""
        posts = []
        
        try:
            await page.goto(group_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(4000)
            
            # Scroll to load more posts
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(2000)
            
            # Extract posts — Facebook's DOM changes often, so we use multiple selectors
            post_elements = await page.query_selector_all(
                "[data-ad-comet-preview='message'], "
                "[data-ad-preview='message'], "
                "div[dir='auto'][style*='text-align']"
            )
            
            # Fallback: get all significant text blocks
            if not post_elements:
                post_elements = await page.query_selector_all("div[dir='auto']")
            
            seen_texts = set()
            
            for el in post_elements:
                try:
                    text = await el.inner_text()
                    text = text.strip()
                    
                    # Filter: at least 50 chars, not a duplicate
                    if len(text) < 50:
                        continue
                    
                    text_key = text[:100]
                    if text_key in seen_texts:
                        continue
                    seen_texts.add(text_key)
                    
                    # Try to get the post URL (permalink)
                    post_url = group_url  # fallback
                    try:
                        # Look for timestamp link which usually contains permalink
                        parent = await el.evaluate_handle("el => el.closest('[role=\"article\"]')")
                        link = await parent.query_selector("a[href*='/posts/'], a[href*='permalink']")
                        if link:
                            post_url = await link.get_attribute("href")
                    except:
                        pass
                    
                    email = extract_email_from_text(text)
                    
                    posts.append({
                        "text": text,
                        "url": post_url,
                        "email": email,
                        "group_url": group_url,
                        "scraped_at": datetime.utcnow()
                    })
                    
                except Exception:
                    continue
            
            logger.info(f"Scraped {len(posts)} posts from {group_url}")
            
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
