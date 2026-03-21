from datetime import datetime
import json


class SessionManager:
    """Manage Playwright Facebook sessions in MongoDB instead of local files"""
    
    def __init__(self, db):
        self.collection = db.sessions
    
    def save_session(self, storage_state: dict, platform: str = "facebook"):
        """Save or update browser session in MongoDB"""
        self.collection.update_one(
            {"platform": platform},
            {"$set": {
                "platform": platform,
                "storage_state": storage_state,
                "is_valid": True,
                "updated_at": datetime.utcnow()
            }},
            upsert=True
        )
    
    def load_session(self, platform: str = "facebook") -> dict | None:
        """Load existing session from MongoDB"""
        doc = self.collection.find_one({"platform": platform, "is_valid": True})
        if doc:
            return doc["storage_state"]
        return None
    
    def invalidate_session(self, platform: str = "facebook"):
        """Mark session as invalid (needs re-login)"""
        self.collection.update_one(
            {"platform": platform},
            {"$set": {"is_valid": False, "updated_at": datetime.utcnow()}}
        )
    
    def get_session_age_hours(self, platform: str = "facebook") -> float:
        """How old is the current session in hours"""
        doc = self.collection.find_one({"platform": platform})
        if not doc:
            return 999
        delta = datetime.utcnow() - doc["updated_at"]
        return delta.total_seconds() / 3600
