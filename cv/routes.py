from flask import Blueprint, request, jsonify, current_app
from auth.middleware import login_required
from datetime import datetime
import os
import uuid

cv_bp = Blueprint("cv", __name__)


@cv_bp.route("/upload", methods=["POST"])
@login_required
def upload_cv():
    if "file" not in request.files:
        return jsonify({"error": "לא נבחר קובץ"}), 400
    
    file = request.files["file"]
    
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "רק קבצי PDF מתקבלים"}), 400
    
    # Check file size
    file.seek(0, 2)
    size_mb = file.tell() / (1024 * 1024)
    file.seek(0)
    
    if size_mb > current_app.config["MAX_PDF_SIZE_MB"]:
        return jsonify({"error": f"הקובץ גדול מדי (מקסימום {current_app.config['MAX_PDF_SIZE_MB']}MB)"}), 400
    
    # Save file
    user_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], request.user_id)
    os.makedirs(user_dir, exist_ok=True)
    
    filename = f"cv_{uuid.uuid4().hex[:8]}.pdf"
    filepath = os.path.join(user_dir, filename)
    file.save(filepath)
    
    # Parse CV with AI (imported here to avoid circular imports)
    from cv.parser import smart_extract
    from cv.ai_parser import parse_cv_with_ai
    
    # Step 1: Extract text
    raw_text = smart_extract(filepath)
    
    if len(raw_text.strip()) < 50:
        return jsonify({"error": "לא הצלחנו לקרוא את ה-PDF. נסה להעלות גרסת טקסט."}), 400
    
    # Step 2: AI parsing
    parsed = parse_cv_with_ai(raw_text)
    
    if "error" in parsed:
        return jsonify({"error": "שגיאה בניתוח קורות החיים, נסה שוב"}), 500
    
    # Save temp parsed data for preview
    db = current_app.db
    db.cv_previews.update_one(
        {"user_id": request.user_id},
        {"$set": {
            "user_id": request.user_id,
            "parsed": parsed,
            "pdf_path": filepath,
            "raw_text_length": len(raw_text),
            "created_at": datetime.utcnow()
        }},
        upsert=True
    )
    
    return jsonify({
        "success": True,
        "preview": parsed,
        "message": "קורות החיים נקראו בהצלחה — בדוק את הפרטים ואשר"
    })


@cv_bp.route("/preview", methods=["GET"])
@login_required
def get_preview():
    db = current_app.db
    preview = db.cv_previews.find_one({"user_id": request.user_id})
    
    if not preview:
        return jsonify({"error": "לא נמצאה תצוגה מקדימה — העלה קו\"ח קודם"}), 404
    
    return jsonify({
        "parsed": preview["parsed"],
        "created_at": preview["created_at"].isoformat()
    })


@cv_bp.route("/confirm", methods=["POST"])
@login_required
def confirm_cv():
    db = current_app.db
    preview = db.cv_previews.find_one({"user_id": request.user_id})
    
    if not preview:
        return jsonify({"error": "אין תצוגה מקדימה לאשר"}), 404
    
    parsed = preview["parsed"]
    
    # Allow user to override fields
    overrides = request.get_json() or {}
    for key in ["full_name", "email", "phone", "location", "current_title"]:
        if key in overrides:
            parsed[key] = overrides[key]
    
    # Save candidate profile
    profile = {
        "user_id": request.user_id,
        "full_name": parsed.get("full_name"),
        "email": parsed.get("email"),
        "phone": parsed.get("phone"),
        "location": parsed.get("location"),
        "current_title": parsed.get("current_title"),
        "experience_years": parsed.get("experience_years"),
        "skills": parsed.get("skills", []),
        "languages": parsed.get("languages", []),
        "education": parsed.get("education"),
        "experience": parsed.get("experience", []),
        "summary": parsed.get("summary"),
        "pdf_path": preview["pdf_path"],
        "is_active": True,
        "updated_at": datetime.utcnow()
    }
    
    db.candidates.update_one(
        {"user_id": request.user_id},
        {"$set": profile, "$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True
    )
    
    # Cleanup preview
    db.cv_previews.delete_one({"user_id": request.user_id})
    
    return jsonify({
        "success": True,
        "message": "הפרופיל נשמר בהצלחה ✅"
    })


@cv_bp.route("/replace", methods=["PUT"])
@login_required
def replace_cv():
    """Same as upload — overwrites existing"""
    return upload_cv()
