"""
Google Ads integration.
Pulls campaign spend from the Google Ads API and maps it to marketing channels.
"""
import os
import logging
from datetime import date
from dateutil.relativedelta import relativedelta

import requests

from models import db, MarketingSpend, MarketingChannel, AppSetting, SyncLog

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ADS_API   = "https://googleads.googleapis.com/v17"


def _get_credentials():
    return {
        "developer_token":  AppSetting.get("google_developer_token")  or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
        "client_id":        AppSetting.get("google_client_id")         or os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
        "client_secret":    AppSetting.get("google_client_secret")     or os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
        "refresh_token":    AppSetting.get("google_refresh_token")     or os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
        "customer_id":      (AppSetting.get("google_customer_id")      or os.environ.get("GOOGLE_ADS_CUSTOMER_ID", "")).replace("-", ""),
    }


def _get_access_token(creds) -> str:
    resp = requests.post(GOOGLE_TOKEN_URL, data={
        "client_id":     creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type":    "refresh_token",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def sync_google_ads(months_back: int = 3) -> dict:
    creds = _get_credentials()
    if not all([creds["developer_token"], creds["client_id"],
                creds["client_secret"], creds["refresh_token"], creds["customer_id"]]):
        msg = "Google Ads credentials not configured. Add them in Settings."
        _log(msg, 0, "error")
        return {"status": "error", "message": msg}

    try:
        access_token = _get_access_token(creds)
    except Exception as e:
        msg = f"Google Ads token refresh failed: {e}"
        _log(msg, 0, "error")
        return {"status": "error", "message": msg}

    start = (date.today() - relativedelta(months=months_back)).replace(day=1)
    end   = date.today()

    # Google Ads Query Language (GAQL)
    gaql = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.advertising_channel_type,
            segments.date,
            metrics.cost_micros
        FROM campaign
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND metrics.cost_micros > 0
        ORDER BY segments.date DESC
    """

    url = f"{GOOGLE_ADS_API}/customers/{creds['customer_id']}/googleAds:search"
    headers = {
        "Authorization":       f"Bearer {access_token}",
        "developer-token":     creds["developer_token"],
        "Content-Type":        "application/json",
    }

    saved = 0
    next_page = None
    try:
        while True:
            payload = {"query": gaql, "pageSize": 10000}
            if next_page:
                payload["pageToken"] = next_page

            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            for result in data.get("results", []):
                campaign      = result.get("campaign", {})
                segments      = result.get("segments", {})
                metrics       = result.get("metrics", {})
                channel_type  = campaign.get("advertisingChannelType", "")
                campaign_name = campaign.get("name", "")
                spend_date    = date.fromisoformat(segments.get("date", str(date.today())))
                cost          = int(metrics.get("costMicros", 0)) / 1_000_000

                if cost <= 0:
                    continue

                ref_id = f"gads_{campaign.get('id', '')}_{spend_date}"
                if MarketingSpend.query.filter_by(source_reference_id=ref_id).first():
                    continue

                channel_id = _map_channel(channel_type, campaign_name)
                spend = MarketingSpend(
                    channel_id=channel_id,
                    amount=round(cost, 2),
                    spend_date=spend_date,
                    source="google_ads",
                    source_reference_id=ref_id,
                    notes=f"Google Ads: {campaign_name}",
                )
                db.session.add(spend)
                saved += 1

            next_page = data.get("nextPageToken")
            if not next_page:
                break

    except Exception as e:
        db.session.rollback()
        msg = f"Google Ads query failed: {e}"
        _log(msg, 0, "error")
        return {"status": "error", "message": msg}

    db.session.commit()
    msg = f"Google Ads: imported {saved} spend records."
    _log(msg, saved, "ok")
    return {"status": "ok", "message": msg, "records": saved}


def _map_channel(channel_type: str, campaign_name: str) -> int:
    name_lower = campaign_name.lower()
    if "youtube" in name_lower or channel_type == "VIDEO":
        return _ch("YouTube Ads") or _ch("Google Ads") or 1
    return _ch("Google Ads") or 1


def _ch(name):
    ch = MarketingChannel.query.filter_by(name=name).first()
    return ch.id if ch else None


def _log(message, records, status):
    try:
        db.session.add(SyncLog(source="google_ads", status=status,
                               records_synced=records, message=message))
        db.session.commit()
    except Exception:
        pass
