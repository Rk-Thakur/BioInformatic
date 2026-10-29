"""Shared helpers: load a ClinVar VCF restricted to the 7 GE-EHR genes and
compare classifications between releases.

Used by build_reclass_cohort.py; needs the project installed (pip install -e .).
"""

import gzip
import re

# Classification rules live in app/domain.py (one source of truth for the
# cleaning scripts, this cohort builder and the application).
from app.domain import (  # noqa: F401  (re-exported for build_reclass_cohort.py)
    ACTIONABLE,
    NOT_CLASSIFIED,
    PANEL_GENES,
    band,
    change_category,
    normalize_clnsig,
)

_GENE_BYTES = tuple(f"{g}:".encode() for g in PANEL_GENES)


def _info_field(info, key):
    m = re.search(rf"(?:^|;){key}=([^;]*)", info)
    return m.group(1) if m else ""


def load_release(path):
    """Return {(chrom, pos, ref, alt): record dict} for panel-gene records."""
    records = {}
    release_date = ""
    with gzip.open(path, "rb") as fh:
        for raw in fh:
            if raw.startswith(b"#"):
                if raw.startswith(b"##fileDate="):
                    release_date = raw.decode().strip().split("=", 1)[1]
                continue
            if not any(g in raw for g in _GENE_BYTES):
                continue
            cols = raw.decode().rstrip("\n").split("\t")
            info = cols[7]
            genes = [g.split(":")[0] for g in _info_field(info, "GENEINFO").split("|") if g]
            gene = next((g for g in genes if g in PANEL_GENES), None)
            if gene is None:
                continue
            raw_sig = _info_field(info, "CLNSIG")
            records[(cols[0], cols[1], cols[3], cols[4])] = {
                "clinvar_id": cols[2],
                "gene": gene,
                "clnsig": raw_sig,
                "cls": normalize_clnsig(raw_sig),
                "review_status": _info_field(info, "CLNREVSTAT"),
                "disease": _info_field(info, "CLNDN"),
            }
    return release_date, records


def classify(release, key):
    rec = release.get(key)
    return rec["cls"] if rec else NOT_CLASSIFIED
