"""Web layer: Flask blueprints and templates."""

from flask import Blueprint, jsonify, redirect, render_template, url_for
from flask_login import current_user

from app.extensions import db, login_manager
from app.web.forms import EmptyForm
from app.web.security import add_security_headers

main = Blueprint("main", __name__, template_folder="templates")


@main.get("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("patients.index"))
    return redirect(url_for("auth.login"))


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
    def inject_logout_form():
        return {"logout_form": EmptyForm()}

    for code, title in ((403, "Not allowed"), (404, "Not found"), (400, "Bad request")):
        app.register_error_handler(code, _error_page(code, title))


def _error_page(code, title):
    def handler(err):
        return render_template("error.html", code=code, title=title), code

    return handler
