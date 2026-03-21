import jwt
import bcrypt
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify, current_app


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: str, role: str = "user") -> str:
    payload = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=current_app.config["JWT_EXPIRY_HOURS"])
    }
    return jwt.encode(payload, current_app.config["JWT_SECRET"], algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"])


def login_required(f):
    """Decorator — requires valid JWT in Authorization header"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization", "")
        
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        
        if not token:
            return jsonify({"error": "נדרשת התחברות"}), 401
        
        try:
            data = decode_token(token)
            request.user_id = data["user_id"]
            request.user_role = data.get("role", "user")
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "הטוקן פג תוקף, התחבר מחדש"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "טוקן לא תקין"}), 401
        
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator — requires admin role"""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if request.user_role != "admin":
            return jsonify({"error": "גישה מוגבלת למנהלים"}), 403
        return f(*args, **kwargs)
    return decorated
