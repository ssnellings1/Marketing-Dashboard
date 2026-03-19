"""OAuth connection routes for QuickBooks and other integrations."""
import os
import secrets
import requests
from flask import Blueprint, redirect, request, url_for, flash, session
from flask_login import login_required
from models import AppSetting

integrations_bp = Blueprint("integrations", __name__, url_prefix="/integrations")

QB_AUTH_URL  = "https://appcenter.intuit.com/connect/oauth2"
QB_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_SCOPE     = "com.intuit.quickbooks.accounting"


def _qb_client():
    return (
        AppSetting.get("qb_client_id")     or os.environ.get("QB_CLIENT_ID", ""),
        AppSetting.get("qb_client_secret") or os.environ.get("QB_CLIENT_SECRET", ""),
        os.environ.get("QB_REDIRECT_URI", ""),
    )


@integrations_bp.route("/quickbooks/connect")
@login_required
def qb_connect():
    client_id, _, redirect_uri = _qb_client()
    if not client_id:
        flash("QuickBooks Client ID not configured. Add QB_CLIENT_ID to environment variables.", "danger")
        return redirect(url_for("settings.settings"))

    state = secrets.token_urlsafe(16)
    session["qb_oauth_state"] = state

    params = (
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={QB_SCOPE}"
        f"&state={state}"
    )
    return redirect(QB_AUTH_URL + params)


@integrations_bp.route("/quickbooks/callback")
@login_required
def qb_callback():
    error = request.args.get("error")
    if error:
        flash(f"QuickBooks authorization failed: {error}", "danger")
        return redirect(url_for("settings.settings"))

    state    = request.args.get("state", "")
    code     = request.args.get("code", "")
    realm_id = request.args.get("realmId", "")

    if state != session.pop("qb_oauth_state", None):
        flash("Invalid OAuth state. Please try connecting again.", "danger")
        return redirect(url_for("settings.settings"))

    client_id, client_secret, redirect_uri = _qb_client()

    try:
        resp = requests.post(QB_TOKEN_URL, data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": redirect_uri,
        }, auth=(client_id, client_secret), timeout=30)
        resp.raise_for_status()
        tokens = resp.json()
    except Exception as e:
        flash(f"QuickBooks token exchange failed: {e}", "danger")
        return redirect(url_for("settings.settings"))

    AppSetting.set("qb_refresh_token", tokens.get("refresh_token", ""))
    AppSetting.set("qb_realm_id",      realm_id)
    flash("QuickBooks connected successfully! Your expenses will sync automatically.", "success")
    return redirect(url_for("settings.settings"))


@integrations_bp.route("/quickbooks/disconnect")
@login_required
def qb_disconnect():
    AppSetting.set("qb_refresh_token", "")
    AppSetting.set("qb_realm_id", "")
    flash("QuickBooks disconnected.", "info")
    return redirect(url_for("settings.settings"))
