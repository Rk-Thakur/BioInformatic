"""C11: gene coordinates for the 7-gene panel, and gene assignment for ClinVar
records that list several genes (C6). The logic lives in app/genes.py so VCF
upload (F1) uses exactly the same gene boundaries."""

from app import genes as _genes
from app.genes import FLANK, GeneSpan, assign_gene, gene_at  # noqa: F401  (re-exported)
from scripts.clean.common import CleaningError, chrom_rank


def load_genes(path):
    try:
        return _genes.load_genes(path)
    except _genes.GeneFileError as exc:
        raise CleaningError(str(exc)) from exc


def write_bed(genes, path):
    """BED: no header, 0-based half-open, columns chrom start end name score strand."""
    with open(path, "w") as fh:
        for g in sorted(genes.values(), key=lambda g: (chrom_rank(g.chrom), g.start)):
            fh.write(f"{g.chrom}\t{g.start - 1}\t{g.end}\t{g.gene}\t0\t{g.strand}\n")
