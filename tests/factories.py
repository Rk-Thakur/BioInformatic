"""Small builders shared by the tests."""

import csv
import datetime as dt
from pathlib import Path

from app.models import Diagnosis, Medication, Patient

ROOT = Path(__file__).resolve().parent.parent
CURATED = ROOT / "data" / "curated"
COHORT = ROOT / "ge_ehr_test_data_v2"
AS_OF = dt.date(2026, 10, 19)

RELEASE_COLUMNS = [
    "chrom",
    "pos",
    "ref",
    "alt",
    "gene",
    "gene_method",
    "genes_listed",
    "clinvar_id",
    "clnsig_raw",
    "classification",
    "review_status",
    "stars",
    "expert_reviewed",
    "variant_type",
    "hgvs_g",
    "disease",
    "long_indel",
    "outside_gene",
]


def write_release(path, date, records):
    """records: (chrom, pos, ref, alt, gene, classification[, clinvar_id]) tuples."""
    with open(path, "w", newline="") as fh:
        fh.write(f"# ClinVar release {date}\n")
        w = csv.DictWriter(fh, RELEASE_COLUMNS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for rec in records:
            chrom, pos, ref, alt, gene, cls = rec[:6]
            vid = rec[6] if len(rec) > 6 else f"{chrom}{pos}"
            w.writerow(
                {
                    "chrom": chrom,
                    "pos": pos,
                    "ref": ref,
                    "alt": alt,
                    "gene": gene,
                    "gene_method": "single",
                    "genes_listed": gene,
                    "clinvar_id": vid,
                    "clnsig_raw": cls,
                    "classification": cls,
                    "review_status": "",
                    "stars": 0,
                    "expert_reviewed": 0,
                    "variant_type": "single_nucleotide_variant",
                    "hgvs_g": f"NC:g.{pos}{ref}>{alt}",
                    "disease": "",
                    "long_indel": 0,
                    "outside_gene": 0,
                }
            )
    return path


def make_patient(session, ext="p1", *, therapy=(), age_months=None, active=True):
    """A patient with the given current drug classes and age at diagnosis (months)."""
    p = Patient(external_id=ext, name=f"Patient {ext}", is_active=active)
    for cls in therapy:
        p.medications.append(Medication(description=cls, drug_class=cls, start=dt.date(2020, 1, 1)))
    if age_months is not None:
        p.diagnoses.append(Diagnosis(diabetes_type="unknown", age_at_diagnosis_months=age_months))
    session.add(p)
    session.flush()
    return p


def write_vcf(path, records, sample="S"):
    """records: (chrom, pos, ref, alt, gt) tuples; writes a plain-text VCF."""
    lines = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=12>",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "\t".join(
            ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", sample]
        ),
    ]
    for chrom, pos, ref, alt, gt in records:
        lines.append("\t".join([str(chrom), str(pos), ".", ref, alt, ".", "PASS", ".", "GT", gt]))
    Path(path).write_text("\n".join(lines) + "\n")
    return path
