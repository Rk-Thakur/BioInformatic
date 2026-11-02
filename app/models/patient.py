"""F1 patient records: demographics, diabetes diagnosis, conditions and medications."""

from datetime import date, datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import DRUG_CLASSES, one_of, utcnow


class Patient(db.Model):
    __tablename__ = "patient"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True)  # Synthea UUID
    name: Mapped[str] = mapped_column(String(200))
    sex: Mapped[str | None] = mapped_column(String(10))
    birth_date: Mapped[date | None]
    deceased_date: Mapped[date | None]
    family_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # C14: deceased patients stay in the database (family groups) but get no new alerts.
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    diagnoses: Mapped[list["Diagnosis"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )
    conditions: Mapped[list["Condition"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )
    medications: Mapped[list["Medication"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )

    def current_therapy(self, as_of):
        """Drug classes active on `as_of` (paper §3.2: insulin, metformin, sulfonylurea, other)."""
        return {m.drug_class for m in self.medications if m.is_active_on(as_of)}

    def diabetes_diagnosis(self):
        """The most recent diabetes diagnosis, or None."""
        return max(self.diagnoses, key=lambda d: d.diagnosed_on or date.min, default=None)

    def is_pregnant_on(self, as_of):
        """DD15: an active pregnancy condition adds a caveat to rule D1."""
        return any(c.category == "pregnancy" and c.is_active_on(as_of) for c in self.conditions)

    def __repr__(self):
        return f"<Patient {self.external_id}>"


class Diagnosis(db.Model):
    """Diabetes diagnosis; rule D4 needs the age at diagnosis in months."""

    __tablename__ = "diagnosis"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    diabetes_type: Mapped[str] = mapped_column(String(40))  # type1 | type2 | neonatal | unknown
    diagnosed_on: Mapped[date | None]
    age_at_diagnosis_months: Mapped[float | None]

    patient: Mapped[Patient] = relationship(back_populates="diagnoses")


class Condition(db.Model):
    """Other coded conditions that rules need (currently pregnancy, DD15)."""

    __tablename__ = "condition"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))  # SNOMED CT from Synthea
    description: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(20))  # pregnancy | diabetes | other
    start: Mapped[date]
    stop: Mapped[date | None]

    patient: Mapped[Patient] = relationship(back_populates="conditions")

    def is_active_on(self, as_of):
        return self.start <= as_of and (self.stop is None or self.stop > as_of)


class Medication(db.Model):
    """One therapy period per drug (C13 collapses Synthea's monthly refills)."""

    __tablename__ = "medication"
    __table_args__ = (one_of("drug_class", DRUG_CLASSES),)

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    rxnorm_code: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(300))
    drug_class: Mapped[str] = mapped_column(String(20))
    start: Mapped[date]
    stop: Mapped[date | None]

    patient: Mapped[Patient] = relationship(back_populates="medications")

    def is_active_on(self, as_of):
        return self.start <= as_of and (self.stop is None or self.stop > as_of)
