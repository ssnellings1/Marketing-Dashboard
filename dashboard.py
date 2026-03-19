from flask import Blueprint, render_template, request
from flask_login import login_required
from sqlalchemy import func, extract
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from models import db, MarketingSpend, Case, MarketingChannel, SyncLog

dashboard_bp = Blueprint("dashboard", __name__)


def _date_range(period: str):
    today = date.today()
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        last = today.replace(day=1) - timedelta(days=1)
        return first, last
    if period == "last_3":
        return today - relativedelta(months=3), today
    if period == "last_6":
        return today - relativedelta(months=6), today
    if period == "last_12":
        return today - relativedelta(months=12), today
    if period == "ytd":
        return today.replace(month=1, day=1), today
    if period == "custom":
        return None, None   # handled in route
    return today.replace(month=1, day=1), today   # default: YTD


@dashboard_bp.route("/dashboard")
@login_required
def home():
    period = request.args.get("period", "ytd")
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    if period == "custom" and start_str and end_str:
        try:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
        except ValueError:
            start, end = _date_range("ytd")
    else:
        start, end = _date_range(period)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    total_spend = db.session.query(
        func.coalesce(func.sum(MarketingSpend.amount), 0)
    ).filter(MarketingSpend.spend_date.between(start, end)).scalar() or 0

    total_cases = db.session.query(func.count(Case.id)).filter(
        Case.date_signed.between(start, end)
    ).scalar() or 0

    avg_cpa = float(total_spend) / total_cases if total_cases else 0

    # ── Monthly trend (last 13 months always shown for trend chart) ───────────
    trend_start = date.today().replace(day=1) - relativedelta(months=12)
    monthly_rows = db.session.query(
        extract("year", MarketingSpend.spend_date).label("yr"),
        extract("month", MarketingSpend.spend_date).label("mo"),
        func.sum(MarketingSpend.amount).label("spend"),
    ).filter(MarketingSpend.spend_date >= trend_start).group_by("yr", "mo").order_by("yr", "mo").all()

    monthly_cases_rows = db.session.query(
        extract("year", Case.date_signed).label("yr"),
        extract("month", Case.date_signed).label("mo"),
        func.count(Case.id).label("cases"),
    ).filter(Case.date_signed >= trend_start).group_by("yr", "mo").order_by("yr", "mo").all()

    spend_by_month = {(int(r.yr), int(r.mo)): float(r.spend) for r in monthly_rows}
    cases_by_month = {(int(r.yr), int(r.mo)): int(r.cases) for r in monthly_cases_rows}

    trend_labels, trend_spend, trend_cases, trend_cpa = [], [], [], []
    cursor = trend_start
    today = date.today()
    while cursor <= today:
        key = (cursor.year, cursor.month)
        label = cursor.strftime("%b %Y")
        sp = spend_by_month.get(key, 0)
        ca = cases_by_month.get(key, 0)
        cpa = sp / ca if ca else 0
        trend_labels.append(label)
        trend_spend.append(round(sp, 2))
        trend_cases.append(ca)
        trend_cpa.append(round(cpa, 2))
        cursor += relativedelta(months=1)

    # ── Spend by channel ──────────────────────────────────────────────────────
    channel_spend_rows = db.session.query(
        MarketingChannel.name,
        MarketingChannel.color,
        func.coalesce(func.sum(MarketingSpend.amount), 0).label("spend"),
    ).join(MarketingSpend, MarketingSpend.channel_id == MarketingChannel.id, isouter=True
    ).filter(
        db.or_(MarketingSpend.spend_date == None,
               MarketingSpend.spend_date.between(start, end))
    ).group_by(MarketingChannel.id).order_by(db.desc("spend")).all()

    # ── Channel breakdown table ───────────────────────────────────────────────
    cases_by_channel = dict(
        db.session.query(Case.channel_id, func.count(Case.id))
        .filter(Case.date_signed.between(start, end))
        .group_by(Case.channel_id).all()
    )

    channel_table = []
    for row in channel_spend_rows:
        ch = MarketingChannel.query.filter_by(name=row.name).first()
        ch_cases = cases_by_channel.get(ch.id, 0) if ch else 0
        ch_spend = float(row.spend)
        ch_cpa = ch_spend / ch_cases if ch_cases else None
        channel_table.append({
            "name": row.name,
            "color": row.color,
            "spend": ch_spend,
            "cases": ch_cases,
            "cpa": ch_cpa,
        })

    # ── Last sync logs ────────────────────────────────────────────────────────
    sync_logs = SyncLog.query.order_by(SyncLog.ran_at.desc()).limit(10).all()

    return render_template(
        "dashboard.html",
        period=period,
        start=start,
        end=end,
        total_spend=total_spend,
        total_cases=total_cases,
        avg_cpa=avg_cpa,
        trend_labels=trend_labels,
        trend_spend=trend_spend,
        trend_cases=trend_cases,
        trend_cpa=trend_cpa,
        channel_spend_rows=channel_spend_rows,
        channel_table=channel_table,
        sync_logs=sync_logs,
    )
