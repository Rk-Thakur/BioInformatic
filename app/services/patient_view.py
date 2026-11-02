"""Read-only data for the patient page (paper Table II F1: "one patient view")."""

from dataclasses import dataclass

from sqlalchemy import select

from app.domain import ACTIONABLE
from app.models import Alert, PatientVariant, VcfUpload
from app.models.base import OPEN_STATUSES
from app.services.classify import classify_keys

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
