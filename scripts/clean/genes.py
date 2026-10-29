"""C11: gene coordinates for the 7-gene panel, and gene assignment for ClinVar
records that list several genes (C6)."""

from dataclasses import dataclass

from app.domain import PANEL_GENES
from scripts.clean.common import CleaningError, chrom_rank, read_tsv

# Variants up to this far outside a gene (promoter, UTR flanks) still count as "in" it.
FLANK = 5_000


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
    rows = read_tsv(path)
    genes = {
        r["gene"]: GeneSpan(r["gene"], r["chrom"], int(r["start"]), int(r["end"]), r["strand"])
        for r in rows
    }
    missing = set(PANEL_GENES) - set(genes)
    if missing:
        raise CleaningError(f"panel_genes.tsv is missing {sorted(missing)}")
    for g in genes.values():
        if g.start >= g.end:
            raise CleaningError(f"{g.gene}: start must be < end")
    spans = sorted(genes.values(), key=lambda g: (g.chrom, g.start))
    for a, b in zip(spans, spans[1:], strict=False):
        if a.chrom == b.chrom and b.start <= a.end:
            raise CleaningError(
                f"{a.gene} and {b.gene} overlap; gene assignment would be ambiguous"
            )
    return genes


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


def write_bed(genes, path):
    """BED: no header, 0-based half-open, columns chrom start end name score strand."""
    with open(path, "w") as fh:
        for g in sorted(genes.values(), key=lambda g: (chrom_rank(g.chrom), g.start)):
            fh.write(f"{g.chrom}\t{g.start - 1}\t{g.end}\t{g.gene}\t0\t{g.strand}\n")
