from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from app.extensions import db
from app.models import User, audit
from app.web.forms import EmptyForm, LoginForm
from app.web.security import safe_next

bp = Blueprint("auth", __name__)


def demo_accounts():
    """Active demo_* accounts offered on the login page, or [] when demo mode is off."""
    if not current_app.config.get("DEMO_LOGIN"):
        return []
    prefix = current_app.config["DEMO_PREFIX"]
    users = db.session.scalars(
        select(User).where(User.username.startswith(prefix), User.active).order_by(User.role)
    ).all()
    order = {"clinician": 0, "lab_tech": 1, "admin": 2}
    return sorted(users, key=lambda u: order.get(u.role, 9))


def render_login(form, status=200):
    return render_template(
        "auth/login.html", form=form, demo_users=demo_accounts(), demo_form=EmptyForm()
    ), status


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(select(User).where(User.username == form.username.data))
        if user is None or not user.active or not user.check_password(form.password.data):
            audit(db.session, "login_failed", "user", None, username=form.username.data[:80])
            db.session.commit()
            flash("Invalid username or password.", "error")  # same message either way
            return render_login(form, 401)
        login_user(user)
        audit(db.session, "login", "user", user.id, user_id=user.id)
        db.session.commit()
        return redirect(safe_next(request.args.get("next")) or url_for("main.index"))
    return render_login(form)


@bp.post("/login/demo")
def demo_login():
    """Demo mode only: log in as one of the demo_* accounts without typing a password."""
    if not current_app.config.get("DEMO_LOGIN"):
        abort(404)
    if not EmptyForm().validate_on_submit():
        abort(400)
    username = request.form.get("username", "")
    user = next((u for u in demo_accounts() if u.username == username), None)
    if user is None:
        abort(403)  # only demo_* accounts, never a real one
    login_user(user)
    audit(db.session, "demo_login", "user", user.id, user_id=user.id)
    db.session.commit()
    return redirect(url_for("main.index"))


@bp.post("/logout")
@login_required
def logout():
    if EmptyForm().validate_on_submit():
        audit(db.session, "logout", "user", current_user.id, user_id=current_user.id)
        db.session.commit()
        logout_user()
    return redirect(url_for("auth.login"))
