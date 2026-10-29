"""C2-C8: clean one ClinVar GRCh38 VCF release down to the 7-gene panel.

Output columns (data/clean/clinvar_<YYYYMMDD>.panel.tsv):
  chrom pos ref alt gene gene_method genes_listed clinvar_id clnsig_raw classification
  review_status stars expert_reviewed variant_type hgvs_g disease long_indel outside_gene
"""

import gzip
import re
from collections import Counter

from app.domain import PANEL_GENES, normalize_clnsig, review_stars
from scripts.clean.common import CleaningError, chrom_rank
from scripts.clean.genes import FLANK, assign_gene

COLUMNS = [
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
LONG_INDEL = 50
_GENE_BYTES = tuple(f"{g}:".encode() for g in PANEL_GENES)


def _info(info):
    out = {}
    for item in info.split(";"):
        k, _, v = item.partition("=")
        out[k] = v
    return out


def read_header(path):
    """Return (release_date, reference) and check the file is a GRCh38 ClinVar VCF (C8)."""
    date = reference = None
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if not line.startswith("##"):
                break
            if line.startswith("##fileDate="):
                date = line.strip().split("=", 1)[1]
            elif line.startswith("##reference="):
                reference = line.strip().split("=", 1)[1]
    if not date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise CleaningError(f"{path}: missing or invalid ##fileDate")
    if reference != "GRCh38":
        raise CleaningError(f"{path}: reference is {reference!r}, expected GRCh38")
    return date, reference


def clean_release(path, genes, section=None):
    """Return (release_date, rows) for panel-gene records, counting every rule in `section`."""
    date, _ = read_header(path)
    rows, seen = [], set()
    c = Counter()
    raw_terms = Counter()

    with gzip.open(path, "rb") as fh:
        for raw in fh:
            if raw.startswith(b"#") or not any(g in raw for g in _GENE_BYTES):
                continue
            cols = raw.decode().rstrip("\n").split("\t")
            chrom, pos, vid, ref, alt, info = cols[0], cols[1], cols[2], cols[3], cols[4], cols[7]
            f = _info(info)
            listed = [g.split(":")[0] for g in f.get("GENEINFO", "").split("|") if g]
            gene, method = assign_gene(chrom, int(pos), listed, genes)
            if gene is None:
                continue  # byte prefilter matched another field, e.g. a disease name
            c["panel_records"] += 1

            # C8: structural assumptions the rest of the system depends on
            if chrom.startswith("chr"):
                raise CleaningError(f"{path}: unexpected 'chr' prefix at {chrom}:{pos}")
            key = (chrom, pos, ref, alt)
            if key in seen:
                raise CleaningError(f"{path}: duplicate variant key {':'.join(key)}")
            seen.add(key)

            # C5: unusable ALT
            if alt in ("", ".") or "," in alt:
                c["drop_no_alt"] += 1
                continue
            # C4: no germline classification
            clnsig = f.get("CLNSIG", "")
            if not clnsig:
                c["drop_no_clnsig"] += 1
                if "ONC" in f or "SCI" in f:
                    c["drop_no_clnsig_somatic"] += 1
                if "CLNSIGINCL" in f:
                    c["drop_no_clnsig_haplotype"] += 1
                continue

            cls = normalize_clnsig(clnsig)
            primary = re.split(r"[|,]", clnsig)[0]
            if "risk_allele" in primary and "/" in primary and cls != "OTHER":
                c["c2_risk_allele_combo"] += 1  # e.g. Pathogenic/Likely_risk_allele -> P
            if cls == "OTHER":
                c["c2_other"] += 1
                raw_terms[primary] += 1
            if primary.startswith("Conflicting_interpretations"):
                c["c3_old_conflicting_term"] += 1
            if method != "single":
                c[f"c6_{method}"] += 1
            long_indel = max(len(ref), len(alt)) > LONG_INDEL
            c["c7_long_indel"] += long_indel
            outside = not genes[gene].contains(chrom, int(pos), FLANK)
            c["outside_gene"] += outside

            status = f.get("CLNREVSTAT", "")
            stars = review_stars(status)
            rows.append(
                {
                    "chrom": chrom,
                    "pos": pos,
                    "ref": ref,
                    "alt": alt,
                    "gene": gene,
                    "gene_method": method,
                    "genes_listed": "|".join(listed),
                    "clinvar_id": vid,
                    "clnsig_raw": clnsig,
                    "classification": cls,
                    "review_status": status,
                    "stars": stars,
                    "expert_reviewed": int(stars >= 3),
                    "variant_type": f.get("CLNVC", ""),
                    "hgvs_g": f.get("CLNHGVS", ""),
                    "disease": f.get("CLNDN", ""),
                    "long_indel": int(long_indel),
                    "outside_gene": int(outside),
                }
            )

    rows.sort(key=lambda r: (chrom_rank(r["chrom"]), int(r["pos"]), r["ref"], r["alt"]))
    if section is not None:
        s = section
        s.add("in", "records listing a panel gene", c["panel_records"])
        s.add("C5", "dropped: ALT missing or multi-allelic", c["drop_no_alt"])
        s.add("C4", "dropped: no germline CLNSIG", c["drop_no_clnsig"])
        s.add("C4", "  of which oncogenicity/somatic only", c["drop_no_clnsig_somatic"])
        s.add(
            "C4", "  of which haplotype/genotype only (CLNSIGINCL)", c["drop_no_clnsig_haplotype"]
        )
        s.add("C2", "kept: risk-allele combination mapped to its tier", c["c2_risk_allele_combo"])
        s.add("C2", "kept as OTHER (no ACMG/AMP tier, non-actionable)", c["c2_other"])
        s.add(
            "C3",
            "old 'Conflicting interpretations' term mapped to CONFLICT",
            c["c3_old_conflicting_term"],
        )
        s.add("C6", "several panel genes listed, resolved by position", c["c6_by_position"])
        s.add("C6", "several panel genes listed, nearest gene chosen (flagged)", c["c6_nearest"])
        s.add("C7", f"kept, flagged: indel longer than {LONG_INDEL} bp", c["c7_long_indel"])
        s.add(
            "C11",
            f"kept, flagged: more than {FLANK:,} bp outside the assigned gene",
            c["outside_gene"],
        )
        s.add("out", "clean records", len(rows))
        if raw_terms:
            s.note("OTHER terms: " + ", ".join(f"{t} ({n})" for t, n in raw_terms.most_common()))
        by_gene = Counter(r["gene"] for r in rows)
        by_cls = Counter(r["classification"] for r in rows)
        s.table(
            "Clean records by gene",
            ["Gene", "Records"],
            [(g, f"{by_gene[g]:,}") for g in PANEL_GENES],
        )
        s.table(
            "Clean records by classification",
            ["Class", "Records"],
            [(k, f"{v:,}") for k, v in by_cls.most_common()],
        )
    return date, rows
