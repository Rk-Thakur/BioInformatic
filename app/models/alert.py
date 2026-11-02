"""Alerts with full provenance (paper §3.2; DD9, DD12, DD13, DD14).

Alerts are never deleted. A downgrade marks an alert `superseded`; a clinician's
review marks it `reviewed`. Every knowledge version an alert has been confirmed
under is appended to AlertProvenance, so history is never overwritten.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import (
    ALERT_KINDS,
    ALERT_STATUSES,
    PRIORITIES,
    SEVERITIES,
    one_of,
    utcnow,
)


class Alert(db.Model):
    __tablename__ = "alert"
    __table_args__ = (
        one_of("kind", ALERT_KINDS),
        one_of("status", ALERT_STATUSES),
        one_of("severity", SEVERITIES),
        one_of("priority", PRIORITIES),
        # DD9: at most one open (active or reviewed) alert per patient, variant, rule and kind.
        Index(
            "uq_alert_open",
            "patient_id",
            "patient_variant_id",
            "rule_code",
            "kind",
            unique=True,
            sqlite_where=text("status IN ('active', 'reviewed')"),
            postgresql_where=text("status IN ('active', 'reviewed')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    patient_variant_id: Mapped[int] = mapped_column(ForeignKey("patient_variant.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("rule.id"))  # None for review notices
    rule_code: Mapped[str | None] = mapped_column(String(10))
    release_id: Mapped[int] = mapped_column(ForeignKey("kb_release.id"))  # release that CREATED it
    kind: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(10))
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    status: Mapped[str] = mapped_column(String(20), default="active")
    reclassified: Mapped[bool] = mapped_column(default=False)
    message: Mapped[str] = mapped_column(String(1000))
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("alert.id"))
    created_by_run_id: Mapped[int | None] = mapped_column(ForeignKey("reevaluation_run.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    status_changed_at: Mapped[datetime | None]
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    reviewed_at: Mapped[datetime | None]

    patient = relationship("Patient")
    patient_variant = relationship("PatientVariant")
    rule = relationship("Rule")
    release = relationship("KbRelease")
    provenance: Mapped[list["AlertProvenance"]] = relationship(
        back_populates="alert", order_by="AlertProvenance.id"
    )


class AlertProvenance(db.Model):
    """Append-only: each knowledge version under which an alert was produced or confirmed."""

    __tablename__ = "alert_provenance"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alert.id"), index=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("kb_release.id"))
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("rule.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("reevaluation_run.id"))
    classification: Mapped[str] = mapped_column(String(10))  # variant class under that release
    at: Mapped[datetime] = mapped_column(default=utcnow)

    alert: Mapped[Alert] = relationship(back_populates="provenance")
    release = relationship("KbRelease")
    rule = relationship("Rule")
