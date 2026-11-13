"""Web layer: Flask blueprints and templates."""

from flask import Blueprint, jsonify, redirect, render_template, url_for
from flask_login import current_user

from app.extensions import db, login_manager
from app.web.forms import EmptyForm
from app.web.security import CLINICAL, add_security_headers, roles_required

main = Blueprint(
    "main", __name__, template_folder="templates", static_folder="static", static_url_path="/assets"
)


@main.get("/")
def index():
    """Public home page for visitors; signed-in users go straight to their work."""
    if not current_user.is_authenticated:
        return render_template("landing.html")
    if current_user.can_view_recommendations():
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("patients.index"))


@main.get("/overview")
@roles_required(*CLINICAL)
def dashboard():
    """Clinician overview: patients, open alerts and the latest re-evaluation."""
    from app.services.dashboard import overview

    return render_template("dashboard.html", o=overview(db.session))


@main.get("/health")
def health():
    db.session.execute(db.text("SELECT 1"))
    return jsonify(status="ok", database="ok")


def register_blueprints(app):
    from app.web import alerts, auth, knowledge, patients

    for bp in (main, auth.bp, patients.bp, alerts.bp, knowledge.bp):
        app.register_blueprint(bp)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in."
    login_manager.login_message_category = "error"
    app.after_request(add_security_headers)

    @app.context_processor
    def inject_chrome():
        ctx = {"logout_form": EmptyForm(), "nav_alert_count": 0}
        if current_user.is_authenticated and current_user.can_view_recommendations():
            from app.services.dashboard import unreviewed_count

            ctx["nav_alert_count"] = unreviewed_count(db.session)
        return ctx

    for code, title in ((403, "Not allowed"), (404, "Not found"), (400, "Bad request")):
        app.register_error_handler(code, _error_page(code, title))


def _error_page(code, title):
    def handler(err):
        return render_template("error.html", code=code, title=title), code

    return handler
