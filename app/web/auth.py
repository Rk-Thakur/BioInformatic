from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from app.extensions import db
from app.models import User, audit
from app.web.forms import EmptyForm, LoginForm
from app.web.security import safe_next

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("patients.index"))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(select(User).where(User.username == form.username.data))
        if user is None or not user.active or not user.check_password(form.password.data):
            audit(db.session, "login_failed", "user", None, username=form.username.data[:80])
            db.session.commit()
            flash("Invalid username or password.", "error")  # same message either way
            return render_template("auth/login.html", form=form), 401
        login_user(user)
        audit(db.session, "login", "user", user.id, user_id=user.id)
        db.session.commit()
        return redirect(safe_next(request.args.get("next")) or url_for("patients.index"))
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    if EmptyForm().validate_on_submit():
        audit(db.session, "logout", "user", current_user.id, user_id=current_user.id)
        db.session.commit()
        logout_user()
    return redirect(url_for("auth.login"))
