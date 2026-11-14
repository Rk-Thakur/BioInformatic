"""Read-only numbers for the clinician overview page."""

import re
from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.models import Alert, Patient, PatientVariant, ReevaluationRun
from app.services.knowledge_import import current_release

# Treatment advice only (rules D1-D6). Genetic risk findings of people without diabetes
# (codes G1-G5, Stage 7) are alerts too and still count as "waiting for review".
TREATMENT = (Alert.kind == "alert") & (Alert.severity != "info") & Alert.rule_code.like("D%")


@dataclass
class Overview:
    patients: int = 0
    active_patients: int = 0
    tested_patients: int = 0
    open_treatment_alerts: int = 0
    unreviewed: int = 0
    high_priority: int = 0
    review_notices: int = 0
    release: object = None
    last_run: object = None
    run_notes: dict = field(default_factory=dict)
    by_rule: list = field(default_factory=list)  # [(rule code, open treatment alerts)]
    recent: list = field(default_factory=list)  # newest unreviewed high-priority items


def unreviewed_count(session):
    """Badge on the Alerts tab: unreviewed treatment alerts and review notices."""
    return session.scalar(
        select(func.count())
        .select_from(Alert)
        .where(
            Alert.status == "active",
            ((Alert.kind == "alert") & (Alert.severity != "info"))
            | (Alert.kind == "review_notice"),
        )
    )


def overview(session):
    o = Overview()
    o.patients = session.scalar(select(func.count()).select_from(Patient))
    o.active_patients = session.scalar(
        select(func.count()).select_from(Patient).where(Patient.is_active)
    )
    o.tested_patients = session.scalar(select(func.count(PatientVariant.patient_id.distinct())))
    o.open_treatment_alerts = session.scalar(
        select(func.count())
        .select_from(Alert)
        .where(Alert.status.in_(("active", "reviewed")), TREATMENT)
    )
    o.unreviewed = unreviewed_count(session)
    o.high_priority = session.scalar(
        select(func.count())
        .select_from(Alert)
        .where(Alert.status == "active", Alert.priority == "high")
    )
    o.review_notices = session.scalar(
        select(func.count())
        .select_from(Alert)
        .where(Alert.status == "active", Alert.kind == "review_notice")
    )
    o.release = current_release(session)
    o.last_run = session.scalar(
        select(ReevaluationRun)
        .where(ReevaluationRun.trigger.in_(("release", "rule")))
        .order_by(ReevaluationRun.id.desc())
        .limit(1)
    )
    if o.last_run and o.last_run.notes:
        o.run_notes = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", o.last_run.notes)}
    o.by_rule = [
        tuple(r)
        for r in session.execute(
            select(Alert.rule_code, func.count())
            .where(Alert.status.in_(("active", "reviewed")), TREATMENT)
            .group_by(Alert.rule_code)
            .order_by(Alert.rule_code)
        )
    ]
    o.recent = list(
        session.scalars(
            select(Alert)
            .where(Alert.status == "active", Alert.priority == "high")
            .order_by(Alert.id.desc())
            .limit(4)
        )
    )
    return o
