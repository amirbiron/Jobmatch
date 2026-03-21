# ⚡ JobMatch

**מערכת אוטומטית לשליחת קורות חיים למשרות רלוונטיות**

מעלים קו"ח → המערכת סורקת משרות בפייסבוק → AI מוצא התאמות → הקו"ח נשלח אוטומטית → המועמד מקבל עדכון.

---

## 🏗️ סטאק

| שכבה | טכנולוגיה |
|---|---|
| Backend | Python Flask |
| Frontend | HTML/CSS/JS (Vanilla) |
| Database | MongoDB Atlas |
| AI - Parsing & Matching | Gemini 2.0 Flash |
| AI - OCR (PDF תמונה) | Claude Vision |
| Scanner | Playwright (Chromium) |
| Scheduler | APScheduler |
| Email | SMTP (Gmail) |
| Hosting | Render |

---

## 📁 מבנה הפרויקט

```
jobmatch/
├── app.py                      # Flask ראשי
├── config.py                   # הגדרות
├── auth/
│   ├── middleware.py            # JWT + decorators
│   └── routes.py               # register / login / me
├── cv/
│   ├── parser.py               # PDF → טקסט (3 שיטות)
│   ├── ai_parser.py            # Gemini → JSON מובנה
│   └── routes.py               # upload / preview / confirm
├── scanner/
│   ├── facebook.py             # Playwright סריקת קבוצות
│   ├── session_manager.py      # sessions במונגו
│   └── scheduler.py            # APScheduler + recovery
├── matcher/
│   ├── engine.py               # Gemini matching 0-100
│   └── sender.py               # שליחת מייל + התראות
├── dashboard/
│   ├── routes.py               # matches / stats / prefs
│   └── notifications.py        # התראות למועמד
├── templates/                  # 8 דפי HTML
├── static/                     # CSS + JS
├── render.yaml                 # Render deploy config
├── build.sh                    # Build script
├── Procfile                    # Gunicorn
└── requirements.txt
```

---

## 🚀 התקנה מקומית

```bash
# 1. Clone
git clone <repo-url>
cd jobmatch

# 2. Virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install
pip install -r requirements.txt
playwright install chromium

# 4. Environment
cp .env.example .env
# ערוך את .env עם הפרטים שלך

# 5. Run
python app.py
```

פתח http://localhost:5000

---

## ⚙️ משתני סביבה

| משתנה | תיאור |
|---|---|
| `MONGO_URI` | MongoDB Atlas connection string |
| `GEMINI_API_KEY` | Google AI Studio API key |
| `ANTHROPIC_API_KEY` | Anthropic API key (ל-Vision OCR) |
| `FB_EMAIL` | חשבון פייסבוק לסריקה |
| `FB_PASSWORD` | סיסמת פייסבוק |
| `FB_GROUPS` | URLs של קבוצות (מופרד בפסיקים) |
| `SMTP_EMAIL` | Gmail לשליחת מיילים |
| `SMTP_PASSWORD` | App Password של Gmail |
| `SECRET_KEY` | Flask secret |
| `JWT_SECRET` | JWT signing secret |

---

## 🔄 הזרימה

1. **מועמד נרשם** → מעלה PDF → AI מחלץ פרופיל → מגדיר העדפות
2. **כל 4 שעות** → Playwright סורק קבוצות דרושים → deduplication
3. **AI Matching** → Gemini משווה כל פוסט לכל מועמד → ציון 0-100
4. **שליחה** → יש מייל בפוסט? מייל אוטומטי עם PDF. אין מייל? מועמד מקבל קישור לשלוח בעצמו
5. **עדכון** → המועמד רואה בדשבורד לאן נשלח הקו"ח שלו

---

## 👑 אדמין

כדי להפוך משתמש לאדמין, עדכן במונגו:
```javascript
db.users.updateOne({email: "your@email.com"}, {$set: {role: "admin"}})
```

פאנל אדמין: `/admin`
