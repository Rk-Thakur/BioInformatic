"""Shared helpers: load a ClinVar VCF restricted to the 7 GE-EHR genes and
compare classifications between releases.

Standard library only, so it runs before cyvcf2 is installed.
"""

import gzip
import re

PANEL_GENES = ("GCK", "HNF1A", "HNF4A", "HNF1B", "KCNJ11", "ABCC8", "INS")
_GENE_BYTES = tuple(f"{g}:".encode() for g in PANEL_GENES)

ACTIONABLE = {"P", "LP", "P/LP"}
NOT_CLASSIFIED = "NC"  # variant key absent from the release

_CLNSIG_MAP = {
    "pathogenic": "P",
    "likely_pathogenic": "LP",
    "pathogenic/likely_pathogenic": "P/LP",
    "uncertain_significance": "VUS",
    "likely_benign": "LB",
    "benign": "B",
    "benign/likely_benign": "B/LB",
    # ClinVar renamed this term in 2024; both mean the same thing.
    "conflicting_interpretations_of_pathogenicity": "CONFLICT",
    "conflicting_classifications_of_pathogenicity": "CONFLICT",
}


def normalize_clnsig(raw):
    """Map a raw CLNSIG value to P / LP / P/LP / VUS / LB / B / B/LB / CONFLICT / OTHER."""
    if not raw:
        return "OTHER"
    # "Pathogenic|risk_factor" or "Likely_pathogenic,_low_penetrance" -> primary term
    primary = re.split(r"[|,]", raw)[0].strip("_").lower()
    return _CLNSIG_MAP.get(primary, "OTHER")


def band(cls):
    return "actionable" if cls in ACTIONABLE else "non-actionable"


def change_category(old_cls, new_cls):
    """Table IV categories of the paper."""
    if old_cls == new_cls:
        return "unchanged"
    old_b, new_b = band(old_cls), band(new_cls)
    if old_b != new_b:
        return "upgrade" if new_b == "actionable" else "downgrade"
    return "within_actionable" if old_b == "actionable" else "within_nonactionable"


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
