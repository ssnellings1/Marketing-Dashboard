"""
QuickBooks Online integration.
Pulls marketing expenses from QBO and stores them as MarketingSpend records.
Uses the OAuth2 refresh token flow (no interactive login needed after setup).
"""
import os
import logging
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

import requests

from models import db, MarketingSpend, MarketingChannel, AppSetting, SyncLog

logger = logging.getLogger(__name__)

QB_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_BASE_URL  = "https://quickbooks.api.intuit.com"


def _get_credentials():
    return {
        "client_id":          AppSetting.get("qb_client_id")      or os.environ.get("QB_CLIENT_ID", ""),
        "client_secret":      AppSetting.get("qb_client_secret")   or os.environ.get("QB_CLIENT_SECRET", ""),
        "realm_id":           AppSetting.get("qb_realm_id")        or os.environ.get("QB_REALM_ID", ""),
        "refresh_token":      AppSetting.get("qb_refresh_token")   or os.environ.get("QB_REFRESH_TOKEN", ""),
        "marketing_accounts": (AppSetting.get("qb_marketing_accounts") or
                               os.environ.get("QB_MARKETING_ACCOUNTS", "Advertising,Marketing,Promotions")),
    }


def _refresh_access_token(client_id, client_secret, refresh_token) -> str:
    resp = requests.post(QB_TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    }, auth=(client_id, client_secret), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # Save new refresh token if rotated
    if "refresh_token" in data:
        AppSetting.set("qb_refresh_token", data["refresh_token"])
    return data["access_token"]


def _query_qbo(access_token, realm_id, sql) -> list:
    url = f"{QB_BASE_URL}/v3/company/{realm_id}/query"
    resp = requests.get(url, params={"query": sql, "minorversion": 70},
                        headers={"Authorization": f"Bearer {access_token}",
                                 "Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("QueryResponse", {}).get("Purchase", []) or \
           data.get("QueryResponse", {}).get("JournalEntry", []) or []


def sync_quickbooks(months_back: int = 3) -> dict:
    creds = _get_credentials()
    if not all([creds["client_id"], creds["client_secret"], creds["realm_id"], creds["refresh_token"]]):
        msg = "QuickBooks credentials not configured. Add them in Settings."
        _log(msg, 0, "error")
        return {"status": "error", "message": msg}

    try:
        access_token = _refresh_access_token(
            creds["client_id"], creds["client_secret"], creds["refresh_token"]
        )
    except Exception as e:
        msg = f"QuickBooks token refresh failed: {e}"
        _log(msg, 0, "error")
        return {"status": "error", "message": msg}

    start = (date.today() - relativedelta(months=months_back)).replace(day=1)
    end   = date.today()
    acct_names = [a.strip() for a in creds["marketing_accounts"].split(",") if a.strip()]

    # Build account name filter for SQL
    quoted = ", ".join(f"'{a}'" for a in acct_names)
    sql = (
        f"SELECT * FROM Purchase WHERE TxnDate >= '{start}' AND TxnDate <= '{end}' "
        f"MAXRESULTS 1000"
    )

    saved = 0
    try:
        purchases = _query_qbo(access_token, creds["realm_id"], sql)
    except Exception as e:
        msg = f"QuickBooks query failed: {e}"
        _log(msg, 0, "error")
        return {"status": "error", "message": msg}

    for purchase in purchases:
        txn_date_str = purchase.get("TxnDate")
        if not txn_date_str:
            continue

        txn_date = date.fromisoformat(txn_date_str)
        txn_id   = purchase.get("Id", "")

        # Check each line item for marketing account names
        lines = purchase.get("Line", [])
        for line in lines:
            acct_ref = (line.get("AccountBasedExpenseLineDetail") or {}).get("AccountRef", {})
            acct_name = acct_ref.get("name", "")
            if not any(a.lower() in acct_name.lower() for a in acct_names):
                continue

            amount = float(line.get("Amount", 0))
            if amount <= 0:
                continue

            ref_id = f"qb_{txn_id}_{line.get('Id', '')}"
            if MarketingSpend.query.filter_by(source_reference_id=ref_id).first():
                continue

            # Map account name to channel
            channel_id = _account_to_channel(acct_name)

            spend = MarketingSpend(
                channel_id=channel_id or _default_channel_id(),
                amount=amount,
                spend_date=txn_date,
                source="quickbooks",
                source_reference_id=ref_id,
                notes=f"QB: {acct_name} — {purchase.get('PrivateNote', '')}",
            )
            db.session.add(spend)
            saved += 1

    db.session.commit()
    msg = f"QuickBooks: imported {saved} expense records."
    _log(msg, saved, "ok")
    return {"status": "ok", "message": msg, "records": saved}


def _account_to_channel(acct_name: str) -> int | None:
    name = acct_name.lower()
    if "google" in name:
        return _ch("Google Ads")
    if "facebook" in name or "meta" in name or "instagram" in name:
        return _ch("Facebook Ads")
    if "youtube" in name:
        return _ch("YouTube Ads")
    if "billboard" in name or "outdoor" in name:
        return _ch("Billboard")
    if "tv" in name or "radio" in name or "broadcast" in name:
        return _ch("TV / Radio")
    if "event" in name or "community" in name:
        return _ch("Community Events")
    if "newsletter" in name or "email" in name:
        return _ch("Newsletter")
    if "seo" in name or "organic" in name:
        return _ch("SEO / Organic")
    return None


def _ch(name):
    ch = MarketingChannel.query.filter_by(name=name).first()
    return ch.id if ch else None


def _default_channel_id():
    ch = MarketingChannel.query.filter_by(name="Other").first()
    return ch.id if ch else 1


def _log(message, records, status):
    try:
        db.session.add(SyncLog(source="quickbooks", status=status,
                               records_synced=records, message=message))
        db.session.commit()
    except Exception:
        pass
