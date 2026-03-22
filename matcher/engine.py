import json
import google.generativeai as genai
from config import Config
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def build_match_prompt(candidate: dict, post_text: str) -> str:
    """Build the prompt for matching a candidate to a job post"""
    
    skills_str = ", ".join(candidate.get("skills", []))
    experience_str = ""
    for exp in candidate.get("experience", [])[:3]:
        experience_str += f"- {exp.get('title', '')} ב-{exp.get('company', '')} ({exp.get('duration', '')})\n"
    
    return f"""
אתה מומחה גיוס. הנה פרופיל של מועמד ופוסט דרושים.
קבע האם יש התאמה ביניהם.

=== פרופיל המועמד ===
שם: {candidate.get('full_name', 'לא ידוע')}
תפקיד נוכחי: {candidate.get('current_title', 'לא ידוע')}
שנות ניסיון: {candidate.get('experience_years', 'לא ידוע')}
כישורים: {skills_str}
מיקום: {candidate.get('location', 'לא ידוע')}
השכלה: {candidate.get('education', 'לא ידוע')}
ניסיון:
{experience_str}
סיכום: {candidate.get('summary', '')}

=== העדפות המועמד ===
תחומים: {', '.join(candidate.get('_preferences', {}).get('job_fields', []))}
אזורים: {', '.join(candidate.get('_preferences', {}).get('locations', []))}
סוגי משרה: {', '.join(candidate.get('_preferences', {}).get('job_types', []))}

=== פוסט הדרושים ===
{post_text[:2000]}

=== הנחיות ===
החזר JSON בלבד, ללא markdown, ללא backticks:

{{
  "match_score": <מספר 0-100>,
  "match_reason": "הסבר קצר בעברית למה יש/אין התאמה (עד 2 משפטים)",
  "relevant_skills": ["כישור1", "כישור2"],
  "job_title_detected": "שם המשרה שזוהה מהפוסט",
  "company_detected": "שם החברה אם מוזכר, אחרת null"
}}

ציון 0-30: אין קשר כלל
ציון 31-50: קשר רופף
ציון 51-70: התאמה סבירה
ציון 71-85: התאמה טובה
ציון 86-100: התאמה מצוינת
"""


def match_candidate_to_post(candidate: dict, post_text: str) -> dict:
    """Use Gemini to score match between candidate and job post"""
    
    genai.configure(api_key=Config.GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-3-pro-preview")
    
    prompt = build_match_prompt(candidate, post_text)
    
    try:
        response = model.generate_content(prompt)
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        
        # Validate score
        score = result.get("match_score", 0)
        if not isinstance(score, (int, float)) or score < 0 or score > 100:
            result["match_score"] = 0
        
        return result
        
    except json.JSONDecodeError:
        logger.error(f"Gemini returned invalid JSON: {response.text[:200] if response else 'empty'}")
        return {"match_score": 0, "match_reason": "שגיאה בניתוח", "error": True}
    except Exception as e:
        logger.error(f"Matching error: {e}")
        return {"match_score": 0, "match_reason": str(e), "error": True}


def run_matching_for_all_candidates(db, posts: list[dict]):
    """Match all active candidates against all posts"""
    
    # Get all active candidates with preferences
    candidates = list(db.candidates.find({"is_active": True}))
    
    if not candidates:
        logger.info("No active candidates to match")
        return
    
    logger.info(f"Matching {len(candidates)} candidates against {len(posts)} posts")
    
    for candidate in candidates:
        user_id = candidate["user_id"]
        
        # Load preferences
        prefs = db.preferences.find_one({"user_id": user_id}) or {}
        candidate["_preferences"] = prefs
        min_score = prefs.get("min_match_score", Config.MIN_MATCH_SCORE)
        
        for post in posts:
            post_hash = post.get("hash", "")
            
            # Skip if already sent to this candidate
            already_sent = post.get("candidates_sent", [])
            if user_id in already_sent:
                continue
            
            # Also check matches collection
            existing_match = db.matches.find_one({
                "user_id": user_id,
                "post_hash": post_hash
            })
            if existing_match:
                continue
            
            # Run AI matching
            result = match_candidate_to_post(candidate, post["text"])
            
            if result.get("error"):
                continue
            
            score = result.get("match_score", 0)
            
            # Only proceed if above threshold
            if score >= min_score:
                # Determine send method
                send_method = "email" if post.get("email") else "fb_message"
                
                # Save match
                match_doc = {
                    "user_id": user_id,
                    "post_hash": post_hash,
                    "post_url": post.get("url", ""),
                    "post_text_preview": post["text"][:300],
                    "match_score": score,
                    "match_reason": result.get("match_reason", ""),
                    "job_title": result.get("job_title_detected", ""),
                    "company": result.get("company_detected"),
                    "relevant_skills": result.get("relevant_skills", []),
                    "send_method": send_method,
                    "send_status": "pending",
                    "target_email": post.get("email"),
                    "sent_at": None,
                    "created_at": datetime.utcnow()
                }
                
                try:
                    db.matches.insert_one(match_doc)
                    
                    # Update scanned_posts with candidate
                    db.scanned_posts.update_one(
                        {"hash": post_hash},
                        {"$addToSet": {"candidates_sent": user_id}}
                    )
                    
                    logger.info(f"Match found! User {user_id} → score {score} → {result.get('job_title_detected', '?')}")
                    
                except Exception as e:
                    # Duplicate match — skip
                    if "duplicate" in str(e).lower():
                        continue
                    logger.error(f"Error saving match: {e}")
