"""
Meta Ads integration (Facebook + Instagram).
Pulls ad spend from the Meta Marketing API.
"""
import os
import logging
from datetime import date
from dateutil.relativedelta import relativedelta

import requests

from models import db, MarketingSpend, MarketingChannel, AppSetting, SyncLog

logger = logging.getLogger(__name__)

META_API_BASE = "https://graph.facebook.com/v20.0"


def _get_credentials():
    return {
        "access_token":  AppSetting.get("meta_access_token")   or os.environ.get("META_ACCESS_TOKEN", ""),
        "ad_account_id": AppSetting.get("meta_ad_account_id")  or os.environ.get("META_AD_ACCOUNT_ID", ""),
    }


def sync_meta_ads(months_back: int = 3) -> dict:
    creds = _get_credentials()
    if not all([creds["access_token"], creds["ad_account_id"]]):
        msg = "Meta Ads credentials not configured. Add them in Settings."
        _log(msg, 0, "error")
        return {"status": "error", "message": msg}

    start = (date.today() - relativedelta(months=months_back)).replace(day=1)
    end   = date.today()

    account_id = creds["ad_account_id"]
    if not account_id.startswith("act_"):
        account_id = f"act_{account_id}"

    url = f"{META_API_BASE}/{account_id}/insights"
    params = {
        "access_token":   creds["access_token"],
        "level":          "campaign",
        "fields":         "campaign_id,campaign_name,spend,date_start,publisher_platform",
        "time_increment": 1,   # daily breakdown
        "time_range":     f'{{"since":"{start}","until":"{end}"}}',
        "limit":          500,
    }

    saved = 0
    try:
        while True:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            for row in data.get("data", []):
                spend_val = float(row.get("spend", 0))
                if spend_val <= 0:
                    continue

                spend_date    = date.fromisoformat(row.get("date_start", str(date.today())))
                campaign_id   = row.get("campaign_id", "")
                campaign_name = row.get("campaign_name", "")
                platform      = row.get("publisher_platform", "facebook")

                ref_id = f"meta_{campaign_id}_{spend_date}_{platform}"
                if MarketingSpend.query.filter_by(source_reference_id=ref_id).first():
                    continue

                channel_id = _map_channel(platform, campaign_name)
                spend = MarketingSpend(
                    channel_id=channel_id,
                    amount=round(spend_val, 2),
                    spend_date=spend_date,
                    source="meta_ads",
                    source_reference_id=ref_id,
                    notes=f"Meta Ads ({platform}): {campaign_name}",
                )
                db.session.add(spend)
                saved += 1

            # Pagination
            next_url = data.get("paging", {}).get("next")
            if not next_url:
                break
            url = next_url
            params = {}   # next_url already has params encoded

    except Exception as e:
        db.session.rollback()
        msg = f"Meta Ads query failed: {e}"
        _log(msg, 0, "error")
        return {"status": "error", "message": msg}

    db.session.commit()
    msg = f"Meta Ads: imported {saved} spend records."
    _log(msg, saved, "ok")
    return {"status": "ok", "message": msg, "records": saved}


def _map_channel(platform: str, campaign_name: str) -> int:
    p = platform.lower()
    name = campaign_name.lower()
    if "instagram" in p or "instagram" in name:
        return _ch("Instagram Ads") or _ch("Facebook Ads") or 1
    return _ch("Facebook Ads") or 1


def _ch(name):
    ch = MarketingChannel.query.filter_by(name=name).first()
    return ch.id if ch else None


def _log(message, records, status):
    try:
        db.session.add(SyncLog(source="meta_ads", status=status,
                               records_synced=records, message=message))
        db.session.commit()
    except Exception:
        pass
