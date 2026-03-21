from flask import Blueprint, request, jsonify, current_app
from auth.middleware import login_required
from bson import ObjectId

dash_bp = Blueprint("dashboard", __name__)


@dash_bp.route("/matches", methods=["GET"])
@login_required
def get_matches():
    db = current_app.db
    
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 20))
    skip = (page - 1) * per_page
    
    matches = list(db.matches.find(
        {"user_id": request.user_id},
        {"_id": 0}
    ).sort("created_at", -1).skip(skip).limit(per_page))
    
    total = db.matches.count_documents({"user_id": request.user_id})
    
    # Convert datetimes to strings
    for m in matches:
        for key in ["created_at", "sent_at"]:
            if key in m and m[key]:
                m[key] = m[key].isoformat()
    
    return jsonify({
        "matches": matches,
        "total": total,
        "page": page,
        "pages": (total + per_page - 1) // per_page
    })


@dash_bp.route("/stats", methods=["GET"])
@login_required
def get_stats():
    db = current_app.db
    
    total_sent = db.matches.count_documents({
        "user_id": request.user_id,
        "send_status": "sent"
    })
    
    total_failed = db.matches.count_documents({
        "user_id": request.user_id,
        "send_status": "failed"
    })
    
    total_pending = db.matches.count_documents({
        "user_id": request.user_id,
        "send_status": "pending"
    })
    
    total_manual = db.matches.count_documents({
        "user_id": request.user_id,
        "send_method": "fb_message"
    })
    
    # Average match score
    pipeline = [
        {"$match": {"user_id": request.user_id}},
        {"$group": {"_id": None, "avg_score": {"$avg": "$match_score"}}}
    ]
    avg_result = list(db.matches.aggregate(pipeline))
    avg_score = round(avg_result[0]["avg_score"], 1) if avg_result else 0
    
    return jsonify({
        "total_sent": total_sent,
        "total_failed": total_failed,
        "total_pending": total_pending,
        "total_manual": total_manual,
        "avg_match_score": avg_score
    })


@dash_bp.route("/preferences", methods=["GET"])
@login_required
def get_preferences():
    db = current_app.db
    prefs = db.preferences.find_one({"user_id": request.user_id}, {"_id": 0})
    
    if not prefs:
        return jsonify({
            "job_fields": [],
            "locations": [],
            "job_types": [],
            "keywords": [],
            "min_match_score": 65
        })
    
    return jsonify(prefs)


@dash_bp.route("/preferences", methods=["PUT"])
@login_required
def update_preferences():
    data = request.get_json()
    db = current_app.db
    
    prefs = {
        "user_id": request.user_id,
        "job_fields": data.get("job_fields", []),
        "locations": data.get("locations", []),
        "job_types": data.get("job_types", []),
        "keywords": data.get("keywords", []),
        "min_match_score": data.get("min_match_score", 65)
    }
    
    db.preferences.update_one(
        {"user_id": request.user_id},
        {"$set": prefs},
        upsert=True
    )
    
    return jsonify({"success": True, "message": "ההעדפות עודכנו ✅"})
