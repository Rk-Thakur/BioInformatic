from flask import Blueprint, render_template
from sqlalchemy import func, select

from app.extensions import db
from app.models import ExpertClassification, KbRelease, ReevaluationRun, Rule
from app.web.security import CLINICAL, roles_required

bp = Blueprint("knowledge", __name__, url_prefix="/knowledge")


@bp.get("/")
@roles_required(*CLINICAL)
def index():
    """Versioned knowledge (contribution 1) and the F5 run log. Read-only: imports and
    re-evaluation run as CLI jobs (plan §4)."""
    releases = db.session.scalars(select(KbRelease).order_by(KbRelease.release_date.desc())).all()
    rules = db.session.scalars(select(Rule).order_by(Rule.code, Rule.version)).all()
    runs = db.session.scalars(
        select(ReevaluationRun)
        .where(ReevaluationRun.trigger.in_(("release", "rule")))
        .order_by(ReevaluationRun.id.desc())
        .limit(20)
    ).all()
    experts = db.session.scalar(select(func.count()).select_from(ExpertClassification))
    return render_template(
        "knowledge/index.html", releases=releases, rules=rules, runs=runs, experts=experts
    )
