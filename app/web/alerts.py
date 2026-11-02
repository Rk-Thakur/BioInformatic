from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import Alert, audit
from app.models.base import utcnow
from app.services.patient_view import alert_sort_key
from app.web.forms import EmptyForm
from app.web.security import CLINICAL, roles_required

bp = Blueprint("alerts", __name__, url_prefix="/alerts")


@bp.get("/")
@roles_required(*CLINICAL)
def index():
    """Worklist: unreviewed alerts and review notices across all patients (info notes hidden)."""
    show_info = request.args.get("info") == "1"
    stmt = select(Alert).where(Alert.status == "active", Alert.kind.in_(("alert", "review_notice")))
    if not show_info:
        stmt = stmt.where(Alert.severity != "info")
    alerts = sorted(db.session.scalars(stmt).all(), key=alert_sort_key)
    return render_template(
        "alerts/index.html", alerts=alerts, show_info=show_info, action_form=EmptyForm()
    )


@bp.post("/<int:alert_id>/review")
@roles_required(*CLINICAL)
def review(alert_id):
    """Mark an alert or review notice as reviewed. A reviewed alert stays in force
    (OPEN_STATUSES); re-evaluation can still supersede it later."""
    alert = db.session.get(Alert, alert_id) or abort(404)
    back = request.form.get("back") or url_for("patients.detail", patient_id=alert.patient_id)
    if not back.startswith("/") or back.startswith("//"):
        back = url_for("alerts.index")
    if not EmptyForm().validate_on_submit():
        abort(400)
    if alert.status != "active":
        flash("This alert was already reviewed or no longer applies.", "error")
        return redirect(back)
    alert.status = "reviewed"
    alert.reviewed_by_id = current_user.id
    alert.reviewed_at = alert.status_changed_at = utcnow()
    audit(db.session, "alert_reviewed", "alert", alert.id, user_id=current_user.id)
    db.session.commit()
    flash("Marked as reviewed.", "success")
    return redirect(back)
