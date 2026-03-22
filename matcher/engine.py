import json
import re
import google.generativeai as genai
from config import Config
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Model priority list — auto-fallback when a model is deprecated/removed
MODEL_PRIORITY = ["gemini-2.5-flash", "gemini-2.0-flash"]
_active_model = MODEL_PRIORITY[0]


def _parse_json_response(raw: str) -> dict | list:
    """Parse JSON from API response — handles markdown code blocks.
    Models sometimes wrap JSON in ```json ... ``` despite instructions."""
    text = raw.strip()
    # Unwrap markdown code block
    md_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if md_match:
        text = md_match.group(1).strip()
    return json.loads(text)


def _is_model_deprecated(e: Exception) -> bool:
    """Check if API error indicates the model is deprecated/removed."""
    status = getattr(e, 'status_code', None) or getattr(e, 'code', None)
    if status in (404, 410):
        return True
    msg = str(e).lower()
    return any(h in msg for h in ["deprecated", "does not exist", "model not found", "not supported"])


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
    """Use Gemini to score match between candidate and job post.
    Auto-fallback to next model if current one is deprecated."""
    global _active_model

    genai.configure(api_key=Config.GEMINI_API_KEY)
    prompt = build_match_prompt(candidate, post_text)

    # Try active model, then fallback models
    models_to_try = [_active_model] + [m for m in MODEL_PRIORITY if m != _active_model]

    for model_name in models_to_try:
        response = None
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            result = _parse_json_response(response.text)

            # Validate score
            score = result.get("match_score", 0)
            if not isinstance(score, (int, float)) or score < 0 or score > 100:
                result["match_score"] = 0

            # Update active model if we fell back successfully
            if model_name != _active_model:
                logger.warning(f"Switched active model from {_active_model} to {model_name}")
                _active_model = model_name

            return result

        except (json.JSONDecodeError, ValueError, KeyError):
            raw = 'empty/no response'
            try:
                if response is not None:
                    raw = response.text[:200]
            except Exception:
                raw = 'response.text inaccessible (blocked/safety filter?)'
            logger.error(f"Gemini returned invalid JSON: {raw}")
            return {"match_score": 0, "match_reason": "שגיאה בניתוח", "error": True}
        except Exception as e:
            if _is_model_deprecated(e):
                logger.warning(f"Model {model_name} appears deprecated: {e}")
                continue  # try next model
            logger.error(f"Matching error with {model_name}: {e}")
            return {"match_score": 0, "match_reason": str(e), "error": True}

    logger.error("All models failed — no available model")
    return {"match_score": 0, "match_reason": "כל המודלים נכשלו", "error": True}


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
