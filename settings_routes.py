from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required
from models import db, MarketingChannel, MarketingSpend, Case, AppSetting, SyncLog, ProcessedEmail
from datetime import date

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "save_gmail":
            AppSetting.set("gmail_address", request.form.get("gmail_address", "").strip())
            AppSetting.set("gmail_app_password", request.form.get("gmail_app_password", "").strip())
            flash("Gmail settings saved.", "success")

        elif action == "save_qb_creds":
            AppSetting.set("qb_client_id",     request.form.get("qb_client_id", "").strip())
            AppSetting.set("qb_client_secret",  request.form.get("qb_client_secret", "").strip())
            AppSetting.set("qb_redirect_uri",
                           "https://web-production-75c4d.up.railway.app/integrations/quickbooks/callback")
            flash("QuickBooks credentials saved. Now click Connect QuickBooks.", "success")

        elif action == "save_qb":
            AppSetting.set("qb_marketing_accounts", request.form.get("qb_marketing_accounts", "").strip())
            flash("QuickBooks settings saved.", "success")

        elif action == "save_google":
            AppSetting.set("google_developer_token", request.form.get("google_developer_token", "").strip())
            AppSetting.set("google_client_id", request.form.get("google_client_id", "").strip())
            AppSetting.set("google_client_secret", request.form.get("google_client_secret", "").strip())
            AppSetting.set("google_refresh_token", request.form.get("google_refresh_token", "").strip())
            AppSetting.set("google_customer_id", request.form.get("google_customer_id", "").strip())
            flash("Google Ads settings saved.", "success")

        elif action == "save_meta":
            AppSetting.set("meta_app_id", request.form.get("meta_app_id", "").strip())
            AppSetting.set("meta_app_secret", request.form.get("meta_app_secret", "").strip())
            AppSetting.set("meta_access_token", request.form.get("meta_access_token", "").strip())
            AppSetting.set("meta_ad_account_id", request.form.get("meta_ad_account_id", "").strip())
            flash("Meta Ads settings saved.", "success")

        elif action == "add_channel":
            name = request.form.get("channel_name", "").strip()
            if name:
                if not MarketingChannel.query.filter_by(name=name).first():
                    ch = MarketingChannel(
                        name=name,
                        channel_type=request.form.get("channel_type", "offline"),
                        color=request.form.get("channel_color", "#6366f1"),
                    )
                    db.session.add(ch)
                    db.session.commit()
                    flash(f"Channel '{name}' added.", "success")
                else:
                    flash(f"Channel '{name}' already exists.", "warning")

        return redirect(url_for("settings.settings"))

    channels = MarketingChannel.query.order_by(MarketingChannel.name).all()
    email_logs = ProcessedEmail.query.order_by(ProcessedEmail.processed_at.desc()).limit(20).all()

    return render_template("settings.html",
                           channels=channels,
                           email_logs=email_logs,
                           setting=AppSetting)


@settings_bp.route("/data-entry")
@login_required
def data_entry():
    channels = MarketingChannel.query.filter_by(is_active=True).order_by(MarketingChannel.name).all()

    # Recent manual entries
    recent_spend = (MarketingSpend.query
                    .filter_by(source="manual")
                    .order_by(MarketingSpend.spend_date.desc())
                    .limit(50).all())
    recent_cases = (Case.query
                    .filter_by(source="manual")
                    .order_by(Case.date_signed.desc())
                    .limit(50).all())

    return render_template("data_entry.html",
                           channels=channels,
                           recent_spend=recent_spend,
                           recent_cases=recent_cases,
                           today=date.today().isoformat())
