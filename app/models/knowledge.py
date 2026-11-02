"""Versioned knowledge (paper §3.2, contribution 1): ClinVar releases, expert-panel
classifications, curated flags, reference transcripts and treatment rules.

Knowledge rows are never edited in place: a new ClinVar release or a new rule
version is a new row, and `is_current` says which one the system uses.
"""

from datetime import date, datetime

from sqlalchemy import JSON, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import SEVERITIES, one_of, utcnow


class KbRelease(db.Model):
    __tablename__ = "kb_release"
    __table_args__ = (
        # At most one current release.
        Index(
            "uq_kb_release_current",
            "is_current",
            unique=True,
            sqlite_where=text("is_current = 1"),
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(20), unique=True)  # "2023-01-07"
    release_date: Mapped[date]
    source_file: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    record_count: Mapped[int]
    is_current: Mapped[bool] = mapped_column(default=False)
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)

    variants: Mapped[list["KnownVariant"]] = relationship(back_populates="release")

    def __repr__(self):
        return f"<KbRelease {self.version}{' current' if self.is_current else ''}>"


class KnownVariant(db.Model):
    """One ClinVar record of one release (cleaned, see data/clean/QUALITY_REPORT.md)."""

    __tablename__ = "known_variant"
    __table_args__ = (
        UniqueConstraint("release_id", "chrom", "pos", "ref", "alt", name="uq_known_variant_key"),
        Index("ix_known_variant_key", "chrom", "pos", "ref", "alt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("kb_release.id"), index=True)
    chrom: Mapped[str] = mapped_column(String(5))
    pos: Mapped[int]
    ref: Mapped[str] = mapped_column(String(1000))
    alt: Mapped[str] = mapped_column(String(1000))
    gene: Mapped[str] = mapped_column(String(20), index=True)
    gene_method: Mapped[str] = mapped_column(String(20))
    clinvar_id: Mapped[str] = mapped_column(String(20), index=True)
    clnsig_raw: Mapped[str] = mapped_column(String(200))
    classification: Mapped[str] = mapped_column(String(10))
    review_status: Mapped[str] = mapped_column(String(100))
    stars: Mapped[int]
    expert_reviewed: Mapped[bool]
    variant_type: Mapped[str] = mapped_column(String(50))
    hgvs_g: Mapped[str] = mapped_column(String(1000))
    disease: Mapped[str] = mapped_column(String(2000))
    long_indel: Mapped[bool]
    outside_gene: Mapped[bool]

    release: Mapped[KbRelease] = relationship(back_populates="variants")

    @property
    def key(self):
        return (self.chrom, self.pos, self.ref, self.alt)


class ExpertClassification(db.Model):
    """ClinGen Monogenic Diabetes VCEP classification (eRepo). Overrides ClinVar only for
    releases dated on or after its approval date (DD6)."""

    __tablename__ = "expert_classification"

    id: Mapped[int] = mapped_column(primary_key=True)
    erepo_uuid: Mapped[str] = mapped_column(String(40), unique=True)
    gene: Mapped[str] = mapped_column(String(20))
    clinvar_id: Mapped[str | None] = mapped_column(String(20), index=True)
    caid: Mapped[str | None] = mapped_column(String(20))
    hgvs_g: Mapped[str | None] = mapped_column(String(1000))
    transcript_hgvs: Mapped[str | None] = mapped_column(String(1000))
    assertion: Mapped[str] = mapped_column(String(40))
    classification: Mapped[str] = mapped_column(String(10))
    approval_date: Mapped[date] = mapped_column(index=True)
    published_date: Mapped[date | None]
    expert_panel: Mapped[str] = mapped_column(String(200))
    met_codes: Mapped[str | None] = mapped_column(String(500))
    link_method: Mapped[str] = mapped_column(String(40))


class CuratedFlag(db.Model):
    """Hand-curated variant facts ClinVar does not record, e.g. 'activating' (DD8)."""

    __tablename__ = "curated_flag"
    __table_args__ = (
        UniqueConstraint("chrom", "pos", "ref", "alt", "flag", name="uq_curated_flag"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chrom: Mapped[str] = mapped_column(String(5))
    pos: Mapped[int]
    ref: Mapped[str] = mapped_column(String(1000))
    alt: Mapped[str] = mapped_column(String(1000))
    flag: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(500))


class GeneTranscript(db.Model):
    """Reference transcript per gene (DD16)."""

    __tablename__ = "gene_transcript"

    gene: Mapped[str] = mapped_column(String(20), primary_key=True)
    accession: Mapped[str] = mapped_column(String(20))
    current_version: Mapped[str] = mapped_column(String(20))
    paper_version: Mapped[str | None] = mapped_column(String(20))


class Rule(db.Model):
    """One version of a treatment rule (paper Table III). See app/services/rules.py."""

    __tablename__ = "rule"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_rule_code_version"),
        Index(
            "uq_rule_current",
            "code",
            unique=True,
            sqlite_where=text("is_current = 1"),
            postgresql_where=text("is_current"),
        ),
        one_of("severity", SEVERITIES),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10))
    version: Mapped[int]
    genes: Mapped[list] = mapped_column(JSON)
    genetic: Mapped[dict] = mapped_column(JSON)
    therapy: Mapped[dict | None] = mapped_column(JSON)
    extra: Mapped[dict | None] = mapped_column(JSON)
    severity: Mapped[str] = mapped_column(String(10))
    recommendation: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(200))
    effective_from: Mapped[date]
    source_file: Mapped[str] = mapped_column(String(255))
    is_current: Mapped[bool] = mapped_column(default=False)
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)

    def __repr__(self):
        return f"<Rule {self.code} v{self.version}{' current' if self.is_current else ''}>"
