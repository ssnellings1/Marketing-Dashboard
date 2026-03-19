"""JSON API endpoints used by the frontend and for triggering manual syncs."""
from flask import Blueprint, jsonify, request
from flask_login import login_required
from datetime import date
from models import db, MarketingSpend, Case, MarketingChannel, SyncLog

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/sync/<source>", methods=["POST"])
@login_required
def trigger_sync(source):
    """Manually trigger a sync for a given source."""
    allowed = {"quickbooks", "google_ads", "meta_ads", "gmail"}
    if source not in allowed:
        return jsonify({"error": "Unknown source"}), 400

    try:
        if source == "quickbooks":
            from integrations.quickbooks import sync_quickbooks
            result = sync_quickbooks()
        elif source == "google_ads":
            from integrations.google_ads import sync_google_ads
            result = sync_google_ads()
        elif source == "meta_ads":
            from integrations.meta_ads import sync_meta_ads
            result = sync_meta_ads()
        elif source == "gmail":
            from integrations.email_parser import check_gmail
            result = check_gmail()
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route("/spend", methods=["POST"])
@login_required
def add_spend():
    """Add a manual marketing spend entry."""
    data = request.json
    try:
        channel = MarketingChannel.query.get(int(data["channel_id"]))
        if not channel:
            return jsonify({"error": "Channel not found"}), 404
        spend = MarketingSpend(
            channel_id=channel.id,
            amount=float(data["amount"]),
            spend_date=date.fromisoformat(data["spend_date"]),
            source="manual",
            notes=data.get("notes", ""),
        )
        db.session.add(spend)
        db.session.commit()
        return jsonify({"status": "ok", "id": spend.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400


@api_bp.route("/spend/<int:spend_id>", methods=["DELETE"])
@login_required
def delete_spend(spend_id):
    spend = MarketingSpend.query.get_or_404(spend_id)
    db.session.delete(spend)
    db.session.commit()
    return jsonify({"status": "ok"})


@api_bp.route("/case", methods=["POST"])
@login_required
def add_case():
    """Add a manual case entry."""
    data = request.json
    try:
        case = Case(
            date_signed=date.fromisoformat(data["date_signed"]),
            channel_id=int(data["channel_id"]) if data.get("channel_id") else None,
            source="manual",
            notes=data.get("notes", ""),
        )
        db.session.add(case)
        db.session.commit()
        return jsonify({"status": "ok", "id": case.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400


@api_bp.route("/case/<int:case_id>", methods=["DELETE"])
@login_required
def delete_case(case_id):
    case = Case.query.get_or_404(case_id)
    db.session.delete(case)
    db.session.commit()
    return jsonify({"status": "ok"})


@api_bp.route("/channels", methods=["GET"])
@login_required
def get_channels():
    channels = MarketingChannel.query.filter_by(is_active=True).order_by(MarketingChannel.name).all()
    return jsonify([{"id": c.id, "name": c.name, "color": c.color} for c in channels])


@api_bp.route("/sync-logs", methods=["GET"])
@login_required
def get_sync_logs():
    logs = SyncLog.query.order_by(SyncLog.ran_at.desc()).limit(20).all()
    return jsonify([{
        "source": l.source,
        "status": l.status,
        "records": l.records_synced,
        "message": l.message,
        "ran_at": l.ran_at.isoformat(),
    } for l in logs])
