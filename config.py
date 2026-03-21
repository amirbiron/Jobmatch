import os
from datetime import timedelta

class Config:
    # Flask
    SECRET_KEY = os.getenv("SECRET_KEY", "jobmatch-dev-secret-change-in-prod")
    
    # MongoDB
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/jobmatch")
    
    # JWT
    JWT_SECRET = os.getenv("JWT_SECRET", "jwt-secret-change-in-prod")
    JWT_EXPIRY_HOURS = 72
    
    # AI
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    
    # Scanner
    SCAN_INTERVAL_HOURS = 4
    MIN_MATCH_SCORE = 65
    
    # Upload
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
    MAX_PDF_SIZE_MB = 10
    
    # Facebook Groups to scan
    FB_GROUPS = os.getenv("FB_GROUPS", "").split(",")
    
    # Admin
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
