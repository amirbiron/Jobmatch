from flask import Blueprint, request, jsonify, current_app
from auth.middleware import login_required
from bson import ObjectId
from datetime import datetime

notif_bp = Blueprint("notifications", __name__)


@notif_bp.route("/", methods=["GET"])
@login_required
def get_notifications():
    db = current_app.db
    
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 20))
    skip = (page - 1) * per_page
    
    notifs = list(db.notifications.find(
        {"user_id": request.user_id},
        {"_id": 1, "type": 1, "message": 1, "read": 1, "created_at": 1}
    ).sort("created_at", -1).skip(skip).limit(per_page))
    
    for n in notifs:
        n["_id"] = str(n["_id"])
        if n.get("created_at"):
            n["created_at"] = n["created_at"].isoformat()
    
    unread = db.notifications.count_documents({"user_id": request.user_id, "read": False})
    total = db.notifications.count_documents({"user_id": request.user_id})
    
    return jsonify({
        "notifications": notifs,
        "unread": unread,
        "total": total
    })


@notif_bp.route("/read", methods=["POST"])
@login_required
def mark_read():
    db = current_app.db
    data = request.get_json() or {}
    
    notif_id = data.get("id")
    
    if notif_id == "all":
        db.notifications.update_many(
            {"user_id": request.user_id, "read": False},
            {"$set": {"read": True}}
        )
    elif notif_id:
        db.notifications.update_one(
            {"_id": ObjectId(notif_id), "user_id": request.user_id},
            {"$set": {"read": True}}
        )
    
    return jsonify({"success": True})
