import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime
from config import Config
import google.generativeai as genai
import json
import logging
import os

logger = logging.getLogger(__name__)


def generate_email_body(candidate: dict, job_title: str, match_reason: str) -> dict:
    """Use Gemini to write a natural cover email in Hebrew"""
    
    genai.configure(api_key=Config.GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.0-flash-lite")
    
    prompt = f"""
כתוב מייל קצר ומקצועי בעברית שנשלח למגייס עם קורות חיים.
הטון: מקצועי אבל אנושי, לא רובוטי.

פרטי המועמד:
- שם: {candidate.get('full_name', '')}
- תפקיד נוכחי: {candidate.get('current_title', '')}
- ניסיון: {candidate.get('experience_years', '?')} שנים
- סיבת ההתאמה: {match_reason}

המשרה: {job_title}

החזר JSON בלבד:
{{
  "subject": "נושא המייל",
  "body": "גוף המייל (עם שורות חדשות \\n)"
}}
"""
    
    try:
        response = model.generate_content(prompt)
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Email generation error: {e}")
        # Fallback template
        name = candidate.get('full_name', 'מועמד')
        return {
            "subject": f"מועמד/ת מתאים/ה למשרת {job_title}",
            "body": (
                f"שלום רב,\n\n"
                f"שמי {name} ואני מעוניין/ת במשרת {job_title} שפרסמתם.\n"
                f"מצורפים קורות החיים שלי לעיונכם.\n\n"
                f"אשמח לשוחח,\n{name}"
            )
        }


def send_cv_email(
    to_email: str,
    candidate: dict,
    job_title: str,
    match_reason: str,
    smtp_email: str,
    smtp_password: str
) -> bool:
    """Send CV as email attachment to the recruiter"""
    
    # Generate email content
    email_content = generate_email_body(candidate, job_title, match_reason)
    
    try:
        msg = MIMEMultipart()
        msg["From"] = smtp_email
        msg["To"] = to_email
        msg["Subject"] = email_content["subject"]
        
        # Body
        body = MIMEText(email_content["body"], "plain", "utf-8")
        msg.attach(body)
        
        # Attach PDF
        pdf_path = candidate.get("pdf_path", "")
        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
                filename = f"CV_{candidate.get('full_name', 'candidate').replace(' ', '_')}.pdf"
                pdf_attachment.add_header("Content-Disposition", "attachment", filename=filename)
                msg.attach(pdf_attachment)
        else:
            logger.warning(f"PDF not found at {pdf_path}")
        
        # Send
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(smtp_email, smtp_password)
            smtp.send_message(msg)
        
        logger.info(f"Email sent to {to_email} for {candidate.get('full_name')}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


def process_pending_matches(db, smtp_email: str, smtp_password: str):
    """Process all pending matches — send emails where possible"""
    
    pending = list(db.matches.find({
        "send_status": "pending",
        "send_method": "email",
        "target_email": {"$ne": None}
    }))
    
    if not pending:
        logger.info("No pending email matches to process")
        return
    
    logger.info(f"Processing {len(pending)} pending email matches")
    
    for match in pending:
        user_id = match["user_id"]
        
        # Load candidate
        candidate = db.candidates.find_one({"user_id": user_id})
        if not candidate:
            db.matches.update_one(
                {"_id": match["_id"]},
                {"$set": {"send_status": "failed", "error": "candidate not found"}}
            )
            continue
        
        success = send_cv_email(
            to_email=match["target_email"],
            candidate=candidate,
            job_title=match.get("job_title", "משרה"),
            match_reason=match.get("match_reason", ""),
            smtp_email=smtp_email,
            smtp_password=smtp_password
        )
        
        # Update match status
        db.matches.update_one(
            {"_id": match["_id"]},
            {"$set": {
                "send_status": "sent" if success else "failed",
                "sent_at": datetime.utcnow() if success else None
            }}
        )
        
        # Notify the candidate
        if success:
            notify_candidate(db, user_id, match)


def notify_candidate(db, user_id: str, match: dict):
    """Save notification for the candidate that their CV was sent"""
    db.notifications.insert_one({
        "user_id": user_id,
        "type": "cv_sent",
        "message": f"הקו\"ח שלך נשלח למשרת {match.get('job_title', 'משרה')} ({match.get('company', 'חברה לא ידועה')})",
        "match_id": str(match["_id"]),
        "read": False,
        "created_at": datetime.utcnow()
    })
