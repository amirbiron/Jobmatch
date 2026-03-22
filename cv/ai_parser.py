import json
import logging
import time
import google.generativeai as genai
from config import Config

logger = logging.getLogger(__name__)


def parse_cv_with_ai(raw_text: str) -> dict:
    """Send extracted CV text to Gemini and get structured JSON back"""

    if not Config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not set!")
        return {"error": "missing_api_key"}

    genai.configure(api_key=Config.GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-3.1-pro-preview")
    
    prompt = f"""
אתה מומחה לניתוח קורות חיים בעברית ואנגלית.

להלן טקסט גולמי שחולץ מ-PDF של קורות חיים.
הטקסט עלול להיות מבולגן, עם שורות שבורות, או בסדר לא נכון — זה נורמלי.

טקסט קורות החיים:
---
{raw_text[:5000]}
---

חלץ את המידע הבא והחזר JSON בלבד, ללא טקסט נוסף, ללא markdown, ללא backticks.
אם מידע מסוים לא קיים — החזר null.

{{
  "full_name": "שם מלא",
  "email": "כתובת מייל",
  "phone": "טלפון",
  "location": "עיר/אזור מגורים",
  "current_title": "התפקיד הנוכחי או האחרון",
  "experience_years": null,
  "skills": ["כישור1", "כישור2"],
  "languages": ["עברית", "אנגלית"],
  "education": "תואר + מוסד + שנה אם קיים",
  "experience": [
    {{
      "title": "שם התפקיד",
      "company": "שם החברה",
      "duration": "2021-2024",
      "description": "תיאור קצר"
    }}
  ],
  "summary": "2-3 משפטים שמסכמים את הפרופיל המקצועי"
}}
"""
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            raw_json = response.text.strip()
            logger.info(f"Gemini response length: {len(raw_json)}")

            # Clean potential markdown wrapping
            raw_json = raw_json.replace("```json", "").replace("```", "").strip()

            return json.loads(raw_json)
        except json.JSONDecodeError:
            logger.error(f"JSON parse failed. Raw response: {response.text[:500] if response else 'no response'}")
            return {"error": "parse_failed", "raw": response.text[:500] if response else ""}
        except Exception as e:
            logger.error(f"Gemini API error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}: {e}")
            if "429" in str(e) or "quota" in str(e).lower() or "resource" in str(e).lower():
                if attempt < max_retries - 1:
                    wait_time = 2 ** (attempt + 1)
                    logger.info(f"Rate limited, waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                    continue
            return {"error": str(e)}
