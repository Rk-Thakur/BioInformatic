"""Read-only data for the patient page (paper Table II F1: "one patient view")."""

from dataclasses import dataclass

from sqlalchemy import select

from app.domain import ACTIONABLE
from app.models import Alert, Patient, PatientVariant, VcfUpload
from app.models.base import OPEN_STATUSES
from app.services.classify import classify_keys
from app.services.risk import current_risk_levels, person_level

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
KIND_ORDER = {"review_notice": 0, "alert": 1, "info_note": 2}
CLASS_ORDER = {
    "P": 0,
    "P/LP": 1,
    "LP": 2,
    "VUS": 3,
    "CONFLICT": 4,
    "LB": 5,
    "B/LB": 6,
    "B": 7,
    "OTHER": 8,
    "NC": 9,
}


@dataclass
class VariantRow:
    pv: PatientVariant
    classification: str
    source: str
    clinvar_id: str | None
    review_status: str | None

    @property
    def actionable(self):
        return self.classification in ACTIONABLE


def alert_sort_key(a):
    return (a.priority != "high", KIND_ORDER[a.kind], SEVERITY_ORDER[a.severity], -a.id)


def patient_variants(session, patient, release, *, include_unclassified=False):
    pvs = list(
        session.scalars(select(PatientVariant).where(PatientVariant.patient_id == patient.id))
    )
    classes = classify_keys(session, [pv.key for pv in pvs], release) if release else {}
    rows = []
    for pv in pvs:
        c = classes.get(pv.key)
        cls = c.classification if c else "NC"
        if cls == "NC" and not include_unclassified:
            continue
        rows.append(
            VariantRow(
                pv,
                cls,
                c.source if c else "none",
                c.clinvar_id if c else None,
                c.review_status if c else None,
            )
        )
    rows.sort(key=lambda r: (CLASS_ORDER.get(r.classification, 99), r.pv.gene or "", r.pv.pos))
    total = len(pvs)
    return rows, total


def patient_alerts(session, patient):
    """(open alerts sorted for display, past alerts newest first)."""
    alerts = list(session.scalars(select(Alert).where(Alert.patient_id == patient.id)))
    open_ = sorted((a for a in alerts if a.status in OPEN_STATUSES), key=alert_sort_key)
    past = sorted((a for a in alerts if a.status not in OPEN_STATUSES), key=lambda a: -a.id)
    return open_, past


def patient_uploads(session, patient):
    return list(
        session.scalars(
            select(VcfUpload)
            .where(VcfUpload.patient_id == patient.id)
            .order_by(VcfUpload.id.desc())
        )
    )


# --------------------------------------------------------------------------- Stage 7


@dataclass
class RiskSummary:
    """What the risk card shows for a person without diabetes (DD46)."""

    tested: bool
    level: str  # high | mild | uncertain | normal | untested
    label: str
    meaning: str
    findings: list  # open risk alerts / info notes, most serious first
    version: int


def risk_summary(session, patient, open_alerts):
    """RiskSummary for a person without diabetes, or None for a patient with diabetes."""
    if patient.has_diabetes:
        return None
    risk = current_risk_levels()
    tested = session.scalar(
        select(VcfUpload.id).where(
            VcfUpload.patient_id == patient.id, VcfUpload.status == "accepted"
        )
    )
    if not tested:
        return RiskSummary(False, "untested", "Not tested yet", "", [], risk.version)
    findings = [a for a in open_alerts if (a.rule_code or "").startswith("G")]
    level = person_level({a.rule_code for a in findings}, risk)
    return RiskSummary(True, level, risk.label(level), risk.meaning(level), findings, risk.version)


def risk_levels_for(session, patient_ids):
    """{patient id: level} for the people without diabetes among patient_ids.

    "untested" when they have no accepted upload yet.
    """
    if not patient_ids:
        return {}
    risk = current_risk_levels()
    healthy = set(
        session.scalars(
            select(Patient.id).where(Patient.id.in_(patient_ids), ~Patient.has_diabetes)
        )
    )
    if not healthy:
        return {}
    tested = set(
        session.scalars(
            select(VcfUpload.patient_id).where(
                VcfUpload.patient_id.in_(healthy), VcfUpload.status == "accepted"
            )
        )
    )
    codes = {}
    for pid, code in session.execute(
        select(Alert.patient_id, Alert.rule_code).where(
            Alert.patient_id.in_(healthy),
            Alert.status.in_(OPEN_STATUSES),
            Alert.rule_code.like("G%"),
        )
    ):
        codes.setdefault(pid, set()).add(code)
    return {
        pid: person_level(codes.get(pid, set()), risk) if pid in tested else "untested"
        for pid in healthy
    }


@dataclass
class FamilyMember:
    patient: Patient
    relation: str  # how they relate to the patient being viewed
    level: str | None  # risk level for people without diabetes, else None


def family(session, patient):
    """The people linked to this one: the patient whose result led to their test, and
    everyone tested because of the same patient (DD44). Returns [FamilyMember]."""
    members = []
    if patient.relative_of is not None:
        proband = patient.relative_of
        members.append(FamilyMember(proband, "tested because of this patient", None))
        for r in proband.relatives:
            if r.id != patient.id:
                members.append(FamilyMember(r, f"{r.relation} of {proband.name}", None))
    for r in patient.relatives:
        members.append(FamilyMember(r, r.relation, None))
    levels = risk_levels_for(session, [m.patient.id for m in members])
    for m in members:
        m.level = levels.get(m.patient.id)
    return members
