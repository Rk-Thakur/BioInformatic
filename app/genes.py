"""Panel gene coordinates (data/curated/panel_genes.tsv, GRCh38, from NCBI Gene).

Used by the cleaning step (C6/C11) and by VCF upload (F1) to keep only variants
that fall in one of the 7 genes.
"""

import csv
from dataclasses import dataclass
from functools import lru_cache

from app.domain import PANEL_GENES

# Variants up to this far outside a gene (promoter, UTR flanks) still count as "in" it.
FLANK = 5_000


class GeneFileError(ValueError):
    pass


@dataclass(frozen=True)
class GeneSpan:
    gene: str
    chrom: str
    start: int  # 1-based inclusive
    end: int
    strand: str

    def contains(self, chrom, pos, flank=0):
        return chrom == self.chrom and self.start - flank <= pos <= self.end + flank

    def distance(self, chrom, pos):
        if chrom != self.chrom:
            return float("inf")
        if pos < self.start:
            return self.start - pos
        return max(0, pos - self.end)


def load_genes(path):
    """Read and check the gene table; returns {gene: GeneSpan}."""
    with open(path, newline="") as fh:
        rows = list(csv.DictReader((ln for ln in fh if not ln.startswith("#")), delimiter="\t"))
    genes = {
        r["gene"]: GeneSpan(r["gene"], r["chrom"], int(r["start"]), int(r["end"]), r["strand"])
        for r in rows
    }
    missing = set(PANEL_GENES) - set(genes)
    if missing:
        raise GeneFileError(f"panel_genes.tsv is missing {sorted(missing)}")
    for g in genes.values():
        if g.start >= g.end:
            raise GeneFileError(f"{g.gene}: start must be < end")
    spans = sorted(genes.values(), key=lambda g: (g.chrom, g.start))
    for a, b in zip(spans, spans[1:], strict=False):
        if a.chrom == b.chrom and b.start <= a.end:
            raise GeneFileError(
                f"{a.gene} and {b.gene} overlap; gene assignment would be ambiguous"
            )
    return genes


@lru_cache(maxsize=4)
def load_genes_cached(path):
    return load_genes(path)


def assign_gene(chrom, pos, listed, genes):
    """Pick the panel gene for a ClinVar record.

    listed: gene symbols from GENEINFO, in ClinVar's order.
    Returns (gene, method) where method is
      "single"      - exactly one panel gene listed
      "by_position" - several panel genes listed, the variant lies in exactly one of them
      "nearest"     - several listed, none or both contain it: nearest gene wins (flagged)
    or (None, "no_panel_gene").
    """
    candidates = [g for g in listed if g in genes]
    if not candidates:
        return None, "no_panel_gene"
    if len(candidates) == 1:
        return candidates[0], "single"
    inside = [g for g in candidates if genes[g].contains(chrom, pos)]
    if len(inside) == 1:
        return inside[0], "by_position"
    return min(candidates, key=lambda g: genes[g].distance(chrom, pos)), "nearest"


def gene_at(chrom, pos, genes, flank=FLANK):
    """The panel gene containing chrom:pos (within `flank` bp), or None."""
    hits = [g for g in genes.values() if g.contains(chrom, pos, flank)]
    if not hits:
        return None
    return min(hits, key=lambda g: g.distance(chrom, pos)).gene
