from apscheduler.schedulers.background import BackgroundScheduler
from scanner.facebook import FacebookScanner
from matcher.engine import run_matching_for_all_candidates
from matcher.sender import process_pending_matches
from config import Config
from datetime import datetime
import asyncio
import logging
import os

logger = logging.getLogger(__name__)

# Track scan state
_scan_state = {
    "last_run": None,
    "last_status": "idle",
    "last_error": None,
    "posts_found": 0,
    "matches_found": 0
}


def get_scan_state() -> dict:
    return _scan_state.copy()


def _run_full_cycle(db):
    """Full scan → match → send cycle"""
    
    global _scan_state
    _scan_state["last_run"] = datetime.utcnow()
    _scan_state["last_status"] = "running"
    
    fb_email = os.getenv("FB_EMAIL", "")
    fb_password = os.getenv("FB_PASSWORD", "")
    smtp_email = os.getenv("SMTP_EMAIL", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    
    if not fb_email or not fb_password:
        _scan_state["last_status"] = "error"
        _scan_state["last_error"] = "Missing FB_EMAIL or FB_PASSWORD"
        logger.error("Missing Facebook credentials")
        return
    
    try:
        # Step 1: Scan Facebook groups
        logger.info("=== Starting scan cycle ===")
        scanner = FacebookScanner(db)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        posts = loop.run_until_complete(
            scanner.scan_all_groups(
                group_urls=Config.FB_GROUPS,
                fb_email=fb_email,
                fb_password=fb_password
            )
        )
        loop.close()
        
        _scan_state["posts_found"] = len(posts)
        logger.info(f"Scan complete: {len(posts)} posts")
        
        if not posts:
            _scan_state["last_status"] = "done_empty"
            return
        
        # Step 2: Match candidates to posts
        logger.info("Running matching...")
        run_matching_for_all_candidates(db, posts)
        
        # Count new matches
        new_matches = db.matches.count_documents({
            "send_status": "pending",
            "created_at": {"$gte": _scan_state["last_run"]}
        })
        _scan_state["matches_found"] = new_matches
        logger.info(f"Matching complete: {new_matches} new matches")
        
        # Step 3: Send emails
        if smtp_email and smtp_password:
            logger.info("Sending pending emails...")
            process_pending_matches(db, smtp_email, smtp_password)
        else:
            logger.warning("SMTP not configured — skipping email sending")
        
        _scan_state["last_status"] = "done"
        _scan_state["last_error"] = None
        logger.info("=== Scan cycle complete ===")
        
    except Exception as e:
        _scan_state["last_status"] = "error"
        _scan_state["last_error"] = str(e)
        logger.error(f"Scan cycle failed: {e}", exc_info=True)
        
        # Alert admin
        _alert_admin(db, str(e))


def _alert_admin(db, error_msg: str):
    """Save alert for admin when scan fails"""
    db.admin_alerts.insert_one({
        "type": "scan_failure",
        "message": error_msg,
        "created_at": datetime.utcnow(),
        "acknowledged": False
    })


_scheduler_started = False


def start_scheduler(db):
    """Start the background scheduler (ensures only one instance across workers)"""
    global _scheduler_started
    if _scheduler_started:
        logger.info("Scheduler already running — skipping duplicate start")
        return None
    _scheduler_started = True

    scheduler = BackgroundScheduler()

    scheduler.add_job(
        func=lambda: _run_full_cycle(db),
        trigger="interval",
        hours=Config.SCAN_INTERVAL_HOURS,
        id="facebook_scan",
        max_instances=1,
        misfire_grace_time=600,
        next_run_time=None  # Don't run immediately on startup
    )

    scheduler.start()
    logger.info(f"Scheduler started — scanning every {Config.SCAN_INTERVAL_HOURS} hours")

    return scheduler


def trigger_manual_scan(db):
    """Trigger a scan manually (from admin panel)"""
    import threading
    thread = threading.Thread(target=_run_full_cycle, args=(db,))
    thread.daemon = True
    thread.start()
    return {"status": "started", "message": "סריקה ידנית הופעלה"}
