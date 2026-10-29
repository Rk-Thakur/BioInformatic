"""C9-C10: clean the ClinGen Evidence Repository export for the panel genes.

Each eRepo row is one expert-panel classification with an approval date. Rows are
linked to ClinVar by Variation ID; rows without an ID are linked by their GRCh38
genomic HGVS, which ClinVar also carries (CLNHGVS).

Output columns (data/clean/erepo_classifications.tsv):
  erepo_uuid gene clinvar_id caid hgvs_g transcript_hgvs assertion classification
  approval_date published_date expert_panel met_codes link_method
"""

import csv
import re
from collections import Counter

from app.domain import PANEL_GENES, normalize_assertion
from scripts.clean.common import CleaningError

COLUMNS = [
    "erepo_uuid",
    "gene",
    "clinvar_id",
    "caid",
    "hgvs_g",
    "transcript_hgvs",
    "assertion",
    "classification",
    "approval_date",
    "published_date",
    "expert_panel",
    "met_codes",
    "link_method",
]

# GRCh38 RefSeq accessions of the panel chromosomes.
GRCH38_ACCESSIONS = {
    "NC_000007.14": "7",
    "NC_000011.10": "11",
    "NC_000012.12": "12",
    "NC_000017.11": "17",
    "NC_000020.11": "20",
}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def grch38_hgvs(expressions):
    """The GRCh38 genomic HGVS from eRepo's comma-separated 'HGVS Expressions'."""
    for e in (x.strip() for x in expressions.split(",")):
        acc = e.split(":", 1)[0]
        if acc in GRCH38_ACCESSIONS and ":g." in e:
            return e
    return ""


def transcript_hgvs(expressions, accession):
    """HGVS on the newest version of `accession` (e.g. NM_000545) present in the row."""
    found = []
    for e in (x.strip() for x in expressions.split(",")):
        m = re.match(rf"({accession}\.(\d+)):(c\..+)", e)
        if m:
            found.append((int(m.group(2)), f"{m.group(1)}:{m.group(3)}"))
    return max(found)[1] if found else ""


def clean_erepo(path, transcripts, clinvar_releases, section=None):
    """transcripts: {gene: accession}; clinvar_releases: {date: rows from clinvar.clean_release}.

    Returns the cleaned rows. clinvar_id is filled for rows linked by HGVS.
    """
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        required = {
            "ClinVar Variation Id",
            "HGNC Gene Symbol",
            "Assertion",
            "Approval Date",
            "HGVS Expressions",
            "Allele Registry Id",
            "Retracted",
            "Uuid",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise CleaningError(f"{path}: missing columns {sorted(missing)}")
        raw = list(reader)

    # Lookups across all ClinVar releases: ID -> True, HGVS -> ID
    ids, by_hgvs = set(), {}
    for rows in clinvar_releases.values():
        for r in rows:
            ids.add(r["clinvar_id"])
            if r["hgvs_g"]:
                by_hgvs.setdefault(r["hgvs_g"], r["clinvar_id"])

    c = Counter()
    out, seen_uuid = [], set()
    for r in raw:
        gene = r["HGNC Gene Symbol"]
        if gene not in PANEL_GENES:
            continue
        c["panel_rows"] += 1
        if r["Retracted"].strip().lower() == "true":
            c["drop_retracted"] += 1
            continue
        if r["Uuid"] in seen_uuid:
            c["drop_duplicate_uuid"] += 1
            continue
        seen_uuid.add(r["Uuid"])
        approval = r["Approval Date"].strip()
        if not _DATE.fullmatch(approval):
            c["drop_bad_date"] += 1
            continue
        try:
            cls = normalize_assertion(r["Assertion"])
        except ValueError:
            c["drop_unknown_assertion"] += 1
            continue

        hgvs_g = grch38_hgvs(r["HGVS Expressions"])
        vid = r["ClinVar Variation Id"].strip()
        if vid and vid != "-":
            method = "clinvar_id" if vid in ids else "clinvar_id_not_in_releases"
        elif hgvs_g in by_hgvs:
            vid, method = by_hgvs[hgvs_g], "hgvs_g"
        else:
            vid, method = "", "unlinked"
        c[f"link_{method}"] += 1

        out.append(
            {
                "erepo_uuid": r["Uuid"],
                "gene": gene,
                "clinvar_id": vid,
                "caid": r["Allele Registry Id"].strip(),
                "hgvs_g": hgvs_g,
                "transcript_hgvs": transcript_hgvs(
                    r["HGVS Expressions"], transcripts.get(gene, "-")
                ),
                "assertion": r["Assertion"].strip(),
                "classification": cls,
                "approval_date": approval,
                "published_date": r.get("Published Date", "").strip(),
                "expert_panel": r.get("Expert Panel", "").strip(),
                "met_codes": r.get("Applied Evidence Codes (Met)", "").strip(),
                "link_method": method,
            }
        )

    # One expert classification per ClinVar variant is expected; more would need DD6 ordering.
    per_id = Counter(r["clinvar_id"] for r in out if r["clinvar_id"])
    multi = sum(1 for n in per_id.values() if n > 1)
    out.sort(key=lambda r: (r["gene"], r["approval_date"], r["erepo_uuid"]))

    if section is not None:
        s = section
        s.add("in", "rows for panel genes", c["panel_rows"])
        s.add("C10", "dropped: retracted", c["drop_retracted"])
        s.add("C10", "dropped: duplicate eRepo UUID", c["drop_duplicate_uuid"])
        s.add("C10", "dropped: missing or invalid approval date", c["drop_bad_date"])
        s.add("C10", "dropped: unknown assertion text", c["drop_unknown_assertion"])
        s.add("C10", "linked to ClinVar by Variation ID", c["link_clinvar_id"])
        s.add(
            "C10",
            "has a Variation ID that is in none of the ClinVar releases",
            c["link_clinvar_id_not_in_releases"],
        )
        s.add("C10", "no Variation ID, linked by GRCh38 HGVS (CLNHGVS)", c["link_hgvs_g"])
        s.add("C10", "no Variation ID and no HGVS match: kept, unlinked", c["link_unlinked"])
        s.add("C9", "ClinVar variants with more than one expert classification", multi)
        s.add("out", "clean classifications", len(out))
        dates = sorted(r["approval_date"] for r in out)
        if dates:
            s.note(f"Approval dates range from {dates[0]} to {dates[-1]}.")
        for date in sorted(clinvar_releases):
            n = sum(1 for d in dates if d <= date)
            s.note(
                f"Approved on or before ClinVar release {date}: {n:,} of {len(out):,} "
                "(only these may override that release, DD6)."
            )
        no_tx = sum(1 for r in out if not r["transcript_hgvs"])
        s.note(f"Rows without HGVS on the reference transcript: {no_tx:,}.")
    return out
