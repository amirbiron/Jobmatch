import json
import logging
import anthropic
from config import Config

logger = logging.getLogger(__name__)


def parse_cv_with_ai(raw_text: str) -> dict:
    """Send extracted CV text to Claude and get structured JSON back"""

    if not Config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY is not set!")
        return {"error": "missing_api_key"}

    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

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

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw_json = response.content[0].text.strip()
        logger.info(f"Claude response length: {len(raw_json)}")

        # Clean potential markdown wrapping
        raw_json = raw_json.replace("```json", "").replace("```", "").strip()

        return json.loads(raw_json)
    except json.JSONDecodeError:
        logger.error(f"JSON parse failed. Raw response: {raw_json[:500]}")
        return {"error": "parse_failed", "raw": raw_json[:500]}
    except Exception as e:
        logger.error(f"Claude API error: {type(e).__name__}: {e}")
        return {"error": str(e)}
