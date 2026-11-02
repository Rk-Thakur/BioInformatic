"""F1 genomic records: uploaded VCFs and the panel variants stored from them."""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import UPLOAD_STATUSES, one_of, utcnow


class VcfUpload(db.Model):
    __tablename__ = "vcf_upload"
    __table_args__ = (one_of("status", UPLOAD_STATUSES),)

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    uploaded_at: Mapped[datetime] = mapped_column(default=utcnow)
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(String(500))
    total_records: Mapped[int] = mapped_column(default=0)
    panel_records: Mapped[int] = mapped_column(default=0)


class PatientVariant(db.Model):
    """A patient's variant in a panel gene.

    The genome never changes; only its interpretation does.
    """

    __tablename__ = "patient_variant"
    __table_args__ = (
        UniqueConstraint("patient_id", "chrom", "pos", "ref", "alt", name="uq_patient_variant_key"),
        Index("ix_patient_variant_key", "chrom", "pos", "ref", "alt"),  # F5 joins on this
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    upload_id: Mapped[int | None] = mapped_column(ForeignKey("vcf_upload.id"))
    chrom: Mapped[str] = mapped_column(String(5))
    pos: Mapped[int]
    ref: Mapped[str] = mapped_column(String(1000))
    alt: Mapped[str] = mapped_column(String(1000))
    zygosity: Mapped[str] = mapped_column(String(15))  # heterozygous | homozygous | hemizygous
    gene: Mapped[str | None] = mapped_column(String(20))

    patient = relationship("Patient")

    @property
    def key(self):
        return (self.chrom, self.pos, self.ref, self.alt)
