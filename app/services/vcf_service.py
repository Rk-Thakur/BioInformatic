"""F1: parse, validate and store a patient's VCF (paper §4.1).

parse_vcf() is pure (file in, variants out). store_vcf() writes the panel variants
for one patient. A malformed file is rejected with a readable reason (scenario S10)
and nothing is stored for it.
"""

import gzip
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import cyvcf2
from sqlalchemy import select

from app.genes import gene_at
from app.models import PatientVariant, VcfUpload, audit

_BASES = re.compile(r"^[ACGTN]+$")
_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}
_REQUIRED_COLUMNS = ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"]


class VcfValidationError(ValueError):
    pass


@dataclass(frozen=True)
class VariantCall:
    chrom: str
    pos: int
    ref: str
    alt: str
    zygosity: str  # heterozygous | homozygous | hemizygous

    @property
    def key(self):
        return (self.chrom, self.pos, self.ref, self.alt)


@dataclass
class ParsedVcf:
    sample: str
    calls: list = field(default_factory=list)
    total_records: int = 0
    skipped: dict = field(default_factory=dict)  # reason -> count


def normalize_chrom(chrom):
    c = chrom[3:] if chrom.lower().startswith("chr") else chrom
    return "MT" if c in ("M", "MT") else c


def _open_text(path):
    with open(path, "rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", errors="replace")
    return open(path, errors="replace")


def check_structure(path):
    """Fast line-level checks with readable messages, before htslib sees the file."""
    try:
        with _open_text(path) as fh:
            first = fh.readline()
            if not first.startswith("##fileformat=VCF"):
                raise VcfValidationError("not a VCF file: first line must be ##fileformat=VCFv4.x")
            header = None
            for n, line in enumerate(fh, start=2):
                line = line.rstrip("\n")
                if line.startswith("##"):
                    continue
                if line.startswith("#CHROM"):
                    header = line.split("\t")
                    if header[:9] != _REQUIRED_COLUMNS:
                        raise VcfValidationError("the #CHROM header line has the wrong columns")
                    if len(header) != 10:
                        raise VcfValidationError(
                            f"expected exactly one sample column, found {len(header) - 9}"
                        )
                    continue
                if not line:
                    continue
                if header is None:
                    raise VcfValidationError(f"line {n}: data before the #CHROM header line")
                cols = line.split("\t")
                if len(cols) != 10:
                    raise VcfValidationError(f"line {n}: expected 10 columns, found {len(cols)}")
                if not cols[1].isdigit() or int(cols[1]) < 1:
                    raise VcfValidationError(f"line {n}: position {cols[1]!r} is not a number")
                if not _BASES.match(cols[3].upper()):
                    raise VcfValidationError(f"line {n}: invalid REF {cols[3]!r}")
            if header is None:
                raise VcfValidationError("missing #CHROM header line")
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise VcfValidationError(f"cannot read file: {exc}") from exc


def _zygosity(alleles, alt_index):
    """alleles: genotype allele indices (phasing flag removed); alt_index is 1-based."""
    called = [a for a in alleles if a >= 0]
    copies = sum(1 for a in called if a == alt_index)
    if copies == 0:
        return None
    if len(called) == 1:
        return "hemizygous"
    return "homozygous" if copies == len(called) else "heterozygous"


def parse_vcf(path):
    """Parse a single-sample VCF (4.x, GRCh38). Raises VcfValidationError if malformed."""
    check_structure(path)
    try:
        vcf = cyvcf2.VCF(str(path))
    except OSError as exc:
        raise VcfValidationError(f"not a valid VCF: {exc}") from exc
    if len(vcf.samples) != 1:
        raise VcfValidationError(f"expected one sample, found {len(vcf.samples)}")

    parsed = ParsedVcf(sample=vcf.samples[0])
    skipped = parsed.skipped
    try:
        for rec in vcf:
            parsed.total_records += 1
            chrom = normalize_chrom(rec.CHROM)
            if chrom not in _CHROMS:
                skipped["unknown_chromosome"] = skipped.get("unknown_chromosome", 0) + 1
                continue
            alleles = rec.genotypes[0][:-1]  # last element is the phased flag
            for i, alt in enumerate(rec.ALT, start=1):
                if not _BASES.match(alt.upper()):  # symbolic (<DEL>), '*' or '.'
                    skipped["symbolic_alt"] = skipped.get("symbolic_alt", 0) + 1
                    continue
                zyg = _zygosity(alleles, i)
                if zyg is None:
                    skipped["not_carried"] = skipped.get("not_carried", 0) + 1  # 0/0 or ./.
                    continue
                parsed.calls.append(VariantCall(chrom, rec.POS, rec.REF.upper(), alt.upper(), zyg))
    except Exception as exc:  # htslib reports malformed records as a bare Exception
        raise VcfValidationError(
            f"malformed record after line {parsed.total_records}: {exc}"
        ) from exc
    finally:
        vcf.close()
    return parsed


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def store_vcf(session, patient, path, genes, *, filename=None, uploaded_by_id=None):
    """Validate a VCF and store the patient's panel variants.

    genes: {gene: GeneSpan} (app.genes.load_genes). Returns the VcfUpload row; its
    status is "rejected" (with `error`) for a malformed file, and then nothing else
    is stored. Re-uploading the same variants is harmless: existing keys are kept.
    """
    upload = VcfUpload(
        patient_id=patient.id,
        filename=filename or Path(path).name,
        sha256=_sha256(path),
        uploaded_by_id=uploaded_by_id,
        status="accepted",
    )
    session.add(upload)
    try:
        parsed = parse_vcf(path)
    except VcfValidationError as exc:
        upload.status, upload.error = "rejected", str(exc)[:500]
        session.flush()
        audit(
            session,
            "vcf_rejected",
            "vcf_upload",
            upload.id,
            user_id=uploaded_by_id,
            patient=patient.external_id,
            error=upload.error,
        )
        return upload

    session.flush()
    rows = session.execute(
        select(
            PatientVariant.chrom, PatientVariant.pos, PatientVariant.ref, PatientVariant.alt
        ).where(PatientVariant.patient_id == patient.id)
    )
    existing = {tuple(r) for r in rows}
    panel = 0
    for call in parsed.calls:
        gene = gene_at(call.chrom, call.pos, genes)
        if gene is None:
            continue
        panel += 1
        if call.key in existing:
            continue
        existing.add(call.key)
        session.add(
            PatientVariant(
                patient_id=patient.id,
                upload_id=upload.id,
                chrom=call.chrom,
                pos=call.pos,
                ref=call.ref,
                alt=call.alt,
                zygosity=call.zygosity,
                gene=gene,
            )
        )
    upload.total_records = parsed.total_records
    upload.panel_records = panel
    session.flush()
    audit(
        session,
        "vcf_accepted",
        "vcf_upload",
        upload.id,
        user_id=uploaded_by_id,
        patient=patient.external_id,
        records=parsed.total_records,
        panel=panel,
    )
    return upload
