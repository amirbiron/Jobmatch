from flask import Flask, request, jsonify, render_template, redirect, url_for
from pymongo import MongoClient
from config import Config
from auth.routes import auth_bp
from cv.routes import cv_bp
from dashboard.routes import dash_bp
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # MongoDB connection
    client = MongoClient(Config.MONGO_URI)
    # Use the database name from the URI, or fall back to "jobmatch"
    db = client.get_default_database(default="jobmatch")
    app.db = db
    
    # Ensure upload folder exists
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    
    # Create indexes
    _setup_indexes(db)
    
    # Register blueprints
    from dashboard.notifications import notif_bp
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(cv_bp, url_prefix="/api/cv")
    app.register_blueprint(dash_bp, url_prefix="/api/dashboard")
    app.register_blueprint(notif_bp, url_prefix="/api/notifications")
    
    # Start scanner scheduler
    from scanner.scheduler import start_scheduler, trigger_manual_scan, get_scan_state
    from auth.middleware import admin_required
    
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler(db)
    
    # --- Admin API routes ---
    
    @app.route("/api/admin/scan/status", methods=["GET"])
    @admin_required
    def scan_status():
        state = get_scan_state()
        if state.get("last_run"):
            state["last_run"] = state["last_run"].isoformat()
        return jsonify(state)
    
    @app.route("/api/admin/scan/trigger", methods=["POST"])
    @admin_required
    def scan_trigger():
        result = trigger_manual_scan(db)
        return jsonify(result)
    
    @app.route("/api/admin/users", methods=["GET"])
    @admin_required
    def admin_users():
        users = list(db.users.find({}, {"password_hash": 0}))
        for u in users:
            u["_id"] = str(u["_id"])
            u["created_at"] = u["created_at"].isoformat()
            u["has_cv"] = db.candidates.find_one({"user_id": str(u["_id"])}) is not None
        return jsonify({"users": users, "total": len(users)})
    
    @app.route("/api/admin/alerts", methods=["GET"])
    @admin_required
    def admin_alerts():
        alerts = list(db.admin_alerts.find().sort("created_at", -1).limit(20))
        for a in alerts:
            a["_id"] = str(a["_id"])
            a["created_at"] = a["created_at"].isoformat()
        return jsonify({"alerts": alerts})
    
    @app.route("/api/admin/stats", methods=["GET"])
    @admin_required
    def admin_stats():
        return jsonify({
            "total_users": db.users.count_documents({}),
            "total_candidates": db.candidates.count_documents({"is_active": True}),
            "total_posts_scanned": db.scanned_posts.count_documents({}),
            "total_matches": db.matches.count_documents({}),
            "matches_sent": db.matches.count_documents({"send_status": "sent"}),
            "matches_pending": db.matches.count_documents({"send_status": "pending"}),
            "matches_failed": db.matches.count_documents({"send_status": "failed"})
        })
    
    # --- Page routes ---
    
    @app.route("/")
    def index():
        return render_template("index.html")
    
    @app.route("/register")
    def register_page():
        return render_template("register.html")
    
    @app.route("/login")
    def login_page():
        return render_template("login.html")
    
    @app.route("/upload")
    def upload_page():
        return render_template("upload.html")
    
    @app.route("/preview")
    def preview_page():
        return render_template("preview.html")
    
    @app.route("/preferences")
    def preferences_page():
        return render_template("preferences.html")
    
    @app.route("/dashboard")
    def dashboard_page():
        return render_template("dashboard.html")
    
    @app.route("/admin")
    def admin_page():
        return render_template("admin.html")
    
    return app


def _setup_indexes(db):
    """Create MongoDB indexes on startup"""
    # Users — unique email
    db.users.create_index("email", unique=True)
    
    # Candidates — one per user
    db.candidates.create_index("user_id", unique=True)
    
    # Preferences — one per user
    db.preferences.create_index("user_id", unique=True)
    
    # Scanned posts — unique hash + TTL
    db.scanned_posts.create_index("hash", unique=True)
    db.scanned_posts.create_index("expires_at", expireAfterSeconds=0)
    
    # Matches — by user + by post
    db.matches.create_index("user_id")
    db.matches.create_index("post_hash")
    db.matches.create_index([("user_id", 1), ("post_hash", 1)], unique=True)


if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
