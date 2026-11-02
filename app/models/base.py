"""Shared column helpers and enumerations for the models."""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint

ROLES = ("clinician", "lab_tech", "admin")
DRUG_CLASSES = ("insulin", "metformin", "sulfonylurea", "other")
SEVERITIES = ("high", "medium", "low", "info")
PRIORITIES = ("high", "normal")  # DD14: separate from severity
ALERT_KINDS = ("alert", "info_note", "review_notice")
ALERT_STATUSES = ("active", "superseded", "reviewed")
RUN_TRIGGERS = ("initial", "release", "rule", "medication", "upload")
RUN_MODES = ("change_driven", "full", "alert_once")
UPLOAD_STATUSES = ("accepted", "rejected")


def utcnow():
    return datetime.now(UTC)


def one_of(column, values, name=None):
    """CHECK constraint restricting a string column to a fixed set of values."""
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name or f"{column}_valid")
