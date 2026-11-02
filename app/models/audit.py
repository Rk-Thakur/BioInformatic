"""Run log (paper Table II F5: "log every run") and audit trail."""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import RUN_MODES, RUN_TRIGGERS, one_of, utcnow


class ReevaluationRun(db.Model):
    __tablename__ = "reevaluation_run"
    __table_args__ = (one_of("trigger", RUN_TRIGGERS), one_of("mode", RUN_MODES))

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20), default="change_driven")
    old_ref: Mapped[str | None] = mapped_column(String(40))  # release version or "D4 v1"
    new_ref: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    variants_examined: Mapped[int] = mapped_column(default=0)
    variants_changed: Mapped[int] = mapped_column(default=0)
    patients_touched: Mapped[int] = mapped_column(default=0)
    alerts_created: Mapped[int] = mapped_column(default=0)
    alerts_superseded: Mapped[int] = mapped_column(default=0)
    provenance_updates: Mapped[int] = mapped_column(default=0)
    notes: Mapped[str | None] = mapped_column(String(2000))


class AuditEvent(db.Model):
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("reevaluation_run.id"))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    details: Mapped[dict | None] = mapped_column(JSON)


def audit(session, action, entity, entity_id=None, *, user_id=None, run_id=None, **details):
    """Record one audit event in the current transaction."""
    event = AuditEvent(
        action=action,
        entity=entity,
        entity_id=None if entity_id is None else str(entity_id),
        user_id=user_id,
        run_id=run_id,
        details=details or None,
    )
    session.add(event)
    return event
