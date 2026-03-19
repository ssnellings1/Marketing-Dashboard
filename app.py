import os
from flask import Flask, redirect, url_for
from flask_login import LoginManager
from dotenv import load_dotenv

from models import db, User, MarketingChannel, AppSetting

load_dotenv()


def create_app():
    app = Flask(__name__)

    # ── Configuration ──────────────────────────────────────────────────────────
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

    db_url = os.environ.get("DATABASE_URL", "sqlite:///dashboard.db")
    # Railway Postgres gives postgres:// but SQLAlchemy needs postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["WTF_CSRF_ENABLED"] = True

    # ── Extensions ─────────────────────────────────────────────────────────────
    db.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access the dashboard."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ── Blueprints ─────────────────────────────────────────────────────────────
    from auth import auth_bp
    from dashboard import dashboard_bp
    from settings_routes import settings_bp
    from api_routes import api_bp
    from integrations_routes import integrations_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(integrations_bp)

    # ── Health check (Railway) ─────────────────────────────────────────────────
    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    @app.route("/")
    def index():
        return redirect(url_for("dashboard.home"))

    # ── DB init & seed ─────────────────────────────────────────────────────────
    with app.app_context():
        db.create_all()
        _seed_defaults()
        _start_scheduler(app)

    return app


def _seed_defaults():
    """Create admin user and default channels on first run."""
    from sqlalchemy.exc import IntegrityError

    # Admin user
    admin_username = os.environ.get("ADMIN_USERNAME", "admin")
    admin_password = os.environ.get("ADMIN_PASSWORD", "changeme123")
    try:
        if not User.query.filter_by(username=admin_username).first():
            user = User(username=admin_username)
            user.set_password(admin_password)
            db.session.add(user)
            db.session.commit()
    except IntegrityError:
        db.session.rollback()

    # Default marketing channels
    default_channels = [
        ("Google Ads",        "digital_paid",    "#4285F4"),
        ("Facebook Ads",      "digital_paid",    "#1877F2"),
        ("Instagram Ads",     "digital_paid",    "#E1306C"),
        ("YouTube Ads",       "digital_paid",    "#FF0000"),
        ("Referral",          "referral",        "#10B981"),
        ("Community Events",  "offline",         "#F59E0B"),
        ("Newsletter",        "digital_organic", "#8B5CF6"),
        ("TV / Radio",        "offline",         "#EF4444"),
        ("Billboard",         "offline",         "#6366F1"),
        ("SEO / Organic",     "digital_organic", "#14B8A6"),
        ("Other",             "offline",         "#94A3B8"),
    ]
    try:
        for name, ctype, color in default_channels:
            if not MarketingChannel.query.filter_by(name=name).first():
                db.session.add(MarketingChannel(name=name, channel_type=ctype, color=color))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()


def _start_scheduler(app):
    """Start background sync jobs."""
    try:
        from scheduler import start_scheduler
        start_scheduler(app)
    except Exception as e:
        app.logger.warning(f"Scheduler could not start: {e}")


app = create_app()

if __name__ == "__main__":
    app.run(debug=False)
