from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class MarketingChannel(db.Model):
    __tablename__ = "marketing_channels"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    channel_type = db.Column(db.String(50))   # digital_paid, digital_organic, offline, referral
    color = db.Column(db.String(20), default="#6366f1")
    is_active = db.Column(db.Boolean, default=True)

    spends = db.relationship("MarketingSpend", backref="channel", lazy=True)
    cases = db.relationship("Case", backref="channel", lazy=True)


class MarketingSpend(db.Model):
    __tablename__ = "marketing_spend"
    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey("marketing_channels.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    spend_date = db.Column(db.Date, nullable=False)
    source = db.Column(db.String(50), default="manual")   # manual, quickbooks, google_ads, meta_ads
    source_reference_id = db.Column(db.String(200))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Case(db.Model):
    __tablename__ = "cases"
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(200))          # ID from Filevine or Lead Docket
    date_signed = db.Column(db.Date, nullable=False)
    channel_id = db.Column(db.Integer, db.ForeignKey("marketing_channels.id"), nullable=True)
    source = db.Column(db.String(50), default="manual")  # manual, filevine, lead_docket
    lead_source_raw = db.Column(db.String(200))      # raw source string from report
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class ProcessedEmail(db.Model):
    __tablename__ = "processed_emails"
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(500), unique=True, nullable=False)
    sender = db.Column(db.String(200))
    subject = db.Column(db.String(500))
    received_at = db.Column(db.DateTime)
    report_type = db.Column(db.String(50))   # filevine, lead_docket
    records_imported = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default="ok")  # ok, error, skipped
    error_message = db.Column(db.Text)
    processed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class SyncLog(db.Model):
    __tablename__ = "sync_logs"
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(50))           # quickbooks, google_ads, meta_ads, gmail
    status = db.Column(db.String(50))           # ok, error
    records_synced = db.Column(db.Integer, default=0)
    message = db.Column(db.Text)
    ran_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class AppSetting(db.Model):
    __tablename__ = "app_settings"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    @classmethod
    def get(cls, key, default=None):
        row = cls.query.filter_by(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        row = cls.query.filter_by(key=key).first()
        if row:
            row.value = value
            row.updated_at = datetime.now(timezone.utc)
        else:
            row = cls(key=key, value=value)
            db.session.add(row)
        db.session.commit()
