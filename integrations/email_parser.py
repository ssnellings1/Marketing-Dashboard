"""
Gmail IMAP email parser.
Polls the dashboard Gmail inbox for CSV/Excel attachments from
Filevine and Lead Docket, parses them, and saves the data.
"""
import imaplib
import email
import io
import os
import logging
from datetime import datetime, timezone, date
from email.header import decode_header

import pandas as pd

from models import db, Case, ProcessedEmail, MarketingChannel, AppSetting, SyncLog

logger = logging.getLogger(__name__)

# ── Column name aliases ──────────────────────────────────────────────────────
# Keys are what we call the field; values are possible column names in reports.
FILEVINE_DATE_COLS   = ["Date Signed", "Sign Date", "Retention Date", "Date Retained",
                        "Created Date", "Open Date", "Date Opened", "date_signed"]
FILEVINE_ID_COLS     = ["Case ID", "Project ID", "ProjectId", "CaseId", "ID", "id"]
FILEVINE_SOURCE_COLS = ["Lead Source", "Source", "Referral Source", "Marketing Source",
                        "How Did You Hear", "Channel"]

LEAD_DOCKET_DATE_COLS   = ["Sign Date", "Date Signed", "Retention Date", "Signed At",
                            "Date Converted", "Converted Date"]
LEAD_DOCKET_ID_COLS     = ["Lead ID", "LeadId", "ID", "id", "Lead #"]
LEAD_DOCKET_SOURCE_COLS = ["Lead Source", "Source", "Marketing Source", "Channel",
                            "How Did You Hear About Us"]
LEAD_DOCKET_STATUS_COLS = ["Status", "Lead Status", "Case Status", "Disposition"]
LEAD_DOCKET_SIGNED_VALS = ["signed", "retained", "case opened", "converted", "client"]


def _find_col(df, candidates):
    """Return the first column name from candidates that exists in df."""
    cols_lower = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c in df.columns:
            return c
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    return None


def _gmail_credentials():
    address = AppSetting.get("gmail_address") or os.environ.get("GMAIL_ADDRESS", "")
    password = AppSetting.get("gmail_app_password") or os.environ.get("GMAIL_APP_PASSWORD", "")
    return address, password


def _detect_report_type(sender: str, subject: str) -> str:
    s = (sender + " " + subject).lower()
    if "filevine" in s:
        return "filevine"
    if "lead docket" in s or "leaddocket" in s:
        return "lead_docket"
    return "unknown"


def _parse_filevine(df: pd.DataFrame) -> int:
    """Parse a Filevine cases export. Returns number of records saved."""
    date_col = _find_col(df, FILEVINE_DATE_COLS)
    id_col   = _find_col(df, FILEVINE_ID_COLS)
    src_col  = _find_col(df, FILEVINE_SOURCE_COLS)

    if not date_col:
        raise ValueError(f"Cannot find a date column. Available: {list(df.columns)}")

    saved = 0
    for _, row in df.iterrows():
        try:
            raw_date = row[date_col]
            if pd.isna(raw_date):
                continue
            signed = pd.to_datetime(raw_date).date()
            ext_id = str(row[id_col]) if id_col and not pd.isna(row[id_col]) else None
            source_raw = str(row[src_col]) if src_col and not pd.isna(row[src_col]) else None

            # Skip if already imported
            if ext_id and Case.query.filter_by(external_id=ext_id, source="filevine").first():
                continue

            channel_id = _match_channel(source_raw)
            case = Case(
                external_id=ext_id,
                date_signed=signed,
                channel_id=channel_id,
                source="filevine",
                lead_source_raw=source_raw,
            )
            db.session.add(case)
            saved += 1
        except Exception as e:
            logger.warning(f"Skipping Filevine row: {e}")

    db.session.commit()
    return saved


def _parse_lead_docket(df: pd.DataFrame) -> int:
    """Parse a Lead Docket leads export. Returns number of records saved."""
    date_col   = _find_col(df, LEAD_DOCKET_DATE_COLS)
    id_col     = _find_col(df, LEAD_DOCKET_ID_COLS)
    src_col    = _find_col(df, LEAD_DOCKET_SOURCE_COLS)
    status_col = _find_col(df, LEAD_DOCKET_STATUS_COLS)

    if not date_col:
        raise ValueError(f"Cannot find a date column. Available: {list(df.columns)}")

    saved = 0
    for _, row in df.iterrows():
        try:
            # If there's a status column, only import signed/retained leads
            if status_col and not pd.isna(row[status_col]):
                status_val = str(row[status_col]).lower().strip()
                if not any(v in status_val for v in LEAD_DOCKET_SIGNED_VALS):
                    continue

            raw_date = row[date_col]
            if pd.isna(raw_date):
                continue
            signed = pd.to_datetime(raw_date).date()
            ext_id = str(row[id_col]) if id_col and not pd.isna(row[id_col]) else None
            source_raw = str(row[src_col]) if src_col and not pd.isna(row[src_col]) else None

            if ext_id and Case.query.filter_by(external_id=ext_id, source="lead_docket").first():
                continue

            channel_id = _match_channel(source_raw)
            case = Case(
                external_id=ext_id,
                date_signed=signed,
                channel_id=channel_id,
                source="lead_docket",
                lead_source_raw=source_raw,
            )
            db.session.add(case)
            saved += 1
        except Exception as e:
            logger.warning(f"Skipping Lead Docket row: {e}")

    db.session.commit()
    return saved


def _match_channel(source_raw: str | None) -> int | None:
    """Best-effort match a raw source string to a MarketingChannel id."""
    if not source_raw or source_raw.lower() in ("nan", "none", ""):
        return None
    s = source_raw.lower()
    channels = MarketingChannel.query.filter_by(is_active=True).all()
    for ch in channels:
        if ch.name.lower() in s or s in ch.name.lower():
            return ch.id
    # Fallback fuzzy checks
    if any(k in s for k in ["google", "search", "ppc"]):
        return _channel_id_by_name("Google Ads")
    if any(k in s for k in ["facebook", "fb", "instagram", "ig", "meta", "social"]):
        return _channel_id_by_name("Facebook Ads")
    if "referr" in s:
        return _channel_id_by_name("Referral")
    if any(k in s for k in ["event", "community", "seminar"]):
        return _channel_id_by_name("Community Events")
    if "newsletter" in s or "email" in s:
        return _channel_id_by_name("Newsletter")
    if any(k in s for k in ["tv", "radio", "broadcast"]):
        return _channel_id_by_name("TV / Radio")
    if "billboard" in s or "outdoor" in s:
        return _channel_id_by_name("Billboard")
    if any(k in s for k in ["organic", "seo", "website"]):
        return _channel_id_by_name("SEO / Organic")
    return None


def _channel_id_by_name(name: str) -> int | None:
    ch = MarketingChannel.query.filter_by(name=name).first()
    return ch.id if ch else None


def _read_attachment(filename: str, data: bytes) -> pd.DataFrame:
    fn_lower = filename.lower()
    if fn_lower.endswith(".csv"):
        return pd.read_csv(io.BytesIO(data))
    if fn_lower.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(data))
    raise ValueError(f"Unsupported file type: {filename}")


def check_gmail() -> dict:
    """Connect to Gmail via IMAP and process any new report emails."""
    address, password = _gmail_credentials()
    if not address or not password:
        return {"status": "error", "message": "Gmail credentials not configured. Add them in Settings."}

    total_imported = 0
    errors = []

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(address, password)
        mail.select("inbox")

        _, msg_ids = mail.search(None, "UNSEEN")
        ids = msg_ids[0].split()

        for msg_id in ids:
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            # Decode subject
            subj_raw, enc = decode_header(msg["Subject"] or "")[0]
            subject = subj_raw.decode(enc or "utf-8") if isinstance(subj_raw, bytes) else subj_raw
            sender = msg.get("From", "")
            message_id = msg.get("Message-ID", str(msg_id))
            received_str = msg.get("Date", "")

            try:
                received_at = email.utils.parsedate_to_datetime(received_str)
            except Exception:
                received_at = datetime.now(timezone.utc)

            # Skip already processed
            if ProcessedEmail.query.filter_by(message_id=message_id).first():
                continue

            report_type = _detect_report_type(sender, subject)
            log = ProcessedEmail(
                message_id=message_id,
                sender=sender,
                subject=subject,
                received_at=received_at,
                report_type=report_type,
            )

            imported = 0
            try:
                for part in msg.walk():
                    if part.get_content_disposition() != "attachment":
                        continue
                    filename = part.get_filename() or ""
                    if not filename.lower().endswith((".csv", ".xlsx", ".xls")):
                        continue

                    data = part.get_payload(decode=True)
                    df = _read_attachment(filename, data)

                    if report_type == "filevine":
                        imported += _parse_filevine(df)
                    elif report_type == "lead_docket":
                        imported += _parse_lead_docket(df)
                    else:
                        # Try both parsers
                        try:
                            imported += _parse_filevine(df)
                        except Exception:
                            imported += _parse_lead_docket(df)

                log.records_imported = imported
                log.status = "ok"
                total_imported += imported
                # Mark email as read
                mail.store(msg_id, "+FLAGS", "\\Seen")
            except Exception as e:
                log.status = "error"
                log.error_message = str(e)
                errors.append(str(e))
                logger.error(f"Error processing email '{subject}': {e}")

            db.session.add(log)
            db.session.commit()

        mail.logout()

    except imaplib.IMAP4.error as e:
        msg = f"Gmail login failed: {e}. Make sure you're using an App Password, not your regular Gmail password."
        _log_sync("gmail", "error", 0, msg)
        return {"status": "error", "message": msg}
    except Exception as e:
        _log_sync("gmail", "error", 0, str(e))
        return {"status": "error", "message": str(e)}

    status = "error" if errors and total_imported == 0 else "ok"
    message = f"Imported {total_imported} records from {len(ids)} emails."
    if errors:
        message += f" Errors: {'; '.join(errors[:3])}"
    _log_sync("gmail", status, total_imported, message)
    return {"status": status, "message": message, "records": total_imported}


def _log_sync(source, status, records, message):
    try:
        log = SyncLog(source=source, status=status, records_synced=records, message=message)
        db.session.add(log)
        db.session.commit()
    except Exception:
        pass
