from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
from auth.middleware import hash_password, check_password, create_token, login_required
from config import Config
import logging
import re

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    name = data.get("name", "").strip()
    
    # Validation
    if not email or not password or not name:
        return jsonify({"error": "כל השדות חובה"}), 400
    
    if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
        return jsonify({"error": "כתובת מייל לא תקינה"}), 400
    
    if len(password) < 6:
        return jsonify({"error": "הסיסמה חייבת להכיל לפחות 6 תווים"}), 400
    
    db = current_app.db
    
    # Check if exists
    if db.users.find_one({"email": email}):
        return jsonify({"error": "כתובת המייל כבר רשומה במערכת"}), 409
    
    # Create user
    user = {
        "email": email,
        "password_hash": hash_password(password),
        "name": name,
        "role": "user",
        "is_active": True,
        "created_at": datetime.utcnow()
    }
    
    # Promote to admin if email matches ADMIN_EMAIL
    admin_email = (Config.ADMIN_EMAIL or "").strip().lower()
    if admin_email and email == admin_email:
        user["role"] = "admin"
        logger.info(f"New user {email} promoted to admin on registration")

    result = db.users.insert_one(user)
    user_id = str(result.inserted_id)

    token = create_token(user_id, user["role"])
    
    return jsonify({
        "token": token,
        "user": {
            "id": user_id,
            "email": email,
            "name": name
        }
    }), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    
    if not email or not password:
        return jsonify({"error": "מייל וסיסמה חובה"}), 400
    
    db = current_app.db
    user = db.users.find_one({"email": email})
    
    if not user or not check_password(password, user["password_hash"]):
        return jsonify({"error": "מייל או סיסמה שגויים"}), 401
    
    if not user.get("is_active", True):
        return jsonify({"error": "החשבון מושבת"}), 403

    # Promote to admin on login if email matches ADMIN_EMAIL
    admin_email = (Config.ADMIN_EMAIL or "").strip().lower()
    if admin_email and email == admin_email and user.get("role") != "admin":
        db.users.update_one({"_id": user["_id"]}, {"$set": {"role": "admin"}})
        user["role"] = "admin"
        logger.info(f"User {email} promoted to admin on login")

    user_id = str(user["_id"])
    token = create_token(user_id, user.get("role", "user"))
    
    return jsonify({
        "token": token,
        "user": {
            "id": user_id,
            "email": user["email"],
            "name": user["name"]
        }
    })


@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    db = current_app.db
    from bson import ObjectId
    
    user = db.users.find_one({"_id": ObjectId(request.user_id)})
    if not user:
        return jsonify({"error": "משתמש לא נמצא"}), 404
    
    # Check if candidate profile exists
    has_cv = db.candidates.find_one({"user_id": request.user_id}) is not None
    has_preferences = db.preferences.find_one({"user_id": request.user_id}) is not None
    
    return jsonify({
        "user": {
            "id": str(user["_id"]),
            "email": user["email"],
            "name": user["name"],
            "role": user.get("role", "user"),
            "created_at": user["created_at"].isoformat()
        },
        "has_cv": has_cv,
        "has_preferences": has_preferences
    })
