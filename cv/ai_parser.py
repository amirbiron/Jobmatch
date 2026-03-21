import json
import google.generativeai as genai
from config import Config


def parse_cv_with_ai(raw_text: str) -> dict:
    """Send extracted CV text to Gemini and get structured JSON back"""
    
    genai.configure(api_key=Config.GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.0-flash")
    
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
        response = model.generate_content(prompt)
        raw_json = response.text.strip()
        
        # Clean potential markdown wrapping
        raw_json = raw_json.replace("```json", "").replace("```", "").strip()
        
        return json.loads(raw_json)
    except json.JSONDecodeError:
        return {"error": "parse_failed", "raw": response.text[:500] if response else ""}
    except Exception as e:
        return {"error": str(e)}
