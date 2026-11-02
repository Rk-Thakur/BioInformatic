"""Database models (plan §6). Every model is imported here so Flask-Migrate sees its table."""

from app.models.alert import Alert, AlertProvenance
from app.models.audit import AuditEvent, ReevaluationRun, audit
from app.models.knowledge import (
    CuratedFlag,
    ExpertClassification,
    GeneTranscript,
    KbRelease,
    KnownVariant,
    Rule,
)
from app.models.patient import Condition, Diagnosis, Medication, Patient
from app.models.user import User
from app.models.variant import PatientVariant, VcfUpload

__all__ = [
    "Alert",
    "AlertProvenance",
    "AuditEvent",
    "Condition",
    "CuratedFlag",
    "Diagnosis",
    "ExpertClassification",
    "GeneTranscript",
    "KbRelease",
    "KnownVariant",
    "Medication",
    "Patient",
    "PatientVariant",
    "ReevaluationRun",
    "Rule",
    "User",
    "VcfUpload",
    "audit",
]
