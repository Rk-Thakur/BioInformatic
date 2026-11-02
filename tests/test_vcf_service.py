"""F1: VCF parsing, validation (S10) and storage."""

import pytest
from sqlalchemy import func, select

from app.genes import load_genes
from app.models import PatientVariant
from app.services.vcf_service import VcfValidationError, parse_vcf, store_vcf
from tests.factories import COHORT, CURATED, make_patient, write_vcf

GENES = load_genes(CURATED / "panel_genes.tsv")
S1_PATIENT = COHORT / "patients" / "0661fbba-0fa1-1375-bc15-30a567e0ce32.vcf.gz"


def test_parse_cohort_vcf():
    parsed = parse_vcf(S1_PATIENT)
    assert parsed.sample == "0661fbba-0fa1-1375-bc15-30a567e0ce32"
    assert parsed.total_records == 472
    inserted = [c for c in parsed.calls if c.key == ("12", 120978486, "A", "C")]
    assert inserted and inserted[0].zygosity == "heterozygous"


def test_zygosity_and_skips(tmp_path):
    f = write_vcf(
        tmp_path / "z.vcf",
        [
            ("12", 100, "A", "G", "0/1"),
            ("12", 101, "A", "G", "1|1"),
            ("12", 102, "A", "G", "1"),  # haploid
            ("12", 103, "A", "G", "0/0"),  # reference only
            ("12", 104, "A", "G", "./."),  # missing
            ("12", 105, "A", "G,T", "1/2"),  # multi-allelic
            ("12", 106, "A", "<DEL>", "0/1"),  # symbolic
            ("chr12", 107, "a", "g", "0|1"),  # chr prefix, lower case
        ],
    )
    parsed = parse_vcf(f)
    got = {(c.pos, c.alt): c.zygosity for c in parsed.calls}
    assert got == {
        (100, "G"): "heterozygous",
        (101, "G"): "homozygous",
        (102, "G"): "hemizygous",
        (105, "G"): "heterozygous",
        (105, "T"): "heterozygous",
        (107, "G"): "heterozygous",
    }
    assert all(c.chrom == "12" for c in parsed.calls)
    assert parsed.skipped == {"not_carried": 2, "symbolic_alt": 1}


@pytest.mark.parametrize(
    "name, message",
    [
        ("S10_truncated_row.vcf", "expected 10 columns"),
        ("S10_non_numeric_pos.vcf", "not a number"),
        ("S10_missing_header.vcf", "not a VCF file"),
        ("S10_not_a_vcf.vcf.gz", "not a VCF file"),
    ],
)
def test_malformed_files_rejected(name, message):
    """Scenario S10."""
    with pytest.raises(VcfValidationError, match=message):
        parse_vcf(COHORT / "malformed" / name)


def test_more_validation(tmp_path):
    two = tmp_path / "two.vcf"
    two.write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tA\tB\n"
    )
    with pytest.raises(VcfValidationError, match="exactly one sample"):
        parse_vcf(two)
    bad_ref = write_vcf(tmp_path / "r.vcf", [("12", 1, "X", "G", "0/1")])
    with pytest.raises(VcfValidationError, match="invalid REF"):
        parse_vcf(bad_ref)
    empty = tmp_path / "e.vcf"
    empty.write_text("##fileformat=VCFv4.2\n")
    with pytest.raises(VcfValidationError, match="missing #CHROM"):
        parse_vcf(empty)


def test_store_keeps_panel_variants_only_and_is_idempotent(session, tmp_path):
    p = make_patient(session)
    f = write_vcf(
        tmp_path / "p.vcf",
        [
            ("12", 120978486, "A", "C", "0/1"),  # HNF1A
            ("1", 1000, "A", "G", "0/1"),  # outside the panel
            ("11", 17393127, "T", "C", "1/1"),  # ABCC8
        ],
    )
    upload = store_vcf(session, p, f, GENES)
    assert upload.status == "accepted" and upload.total_records == 3 and upload.panel_records == 2
    rows = session.scalars(select(PatientVariant).order_by(PatientVariant.pos)).all()
    assert [(r.gene, r.zygosity) for r in rows] == [
        ("ABCC8", "homozygous"),
        ("HNF1A", "heterozygous"),
    ]

    store_vcf(session, p, f, GENES)  # same file again
    assert session.scalar(select(func.count()).select_from(PatientVariant)) == 2


def test_store_rejected_file_stores_nothing(session):
    p = make_patient(session)
    upload = store_vcf(session, p, COHORT / "malformed" / "S10_truncated_row.vcf", GENES)
    assert upload.status == "rejected" and "expected 10 columns" in upload.error
    assert session.scalar(select(func.count()).select_from(PatientVariant)) == 0
