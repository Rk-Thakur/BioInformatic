"""Stage 1a pipeline: clean every raw source and write data/clean/ + QUALITY_REPORT.md.

    python -m scripts.clean.run_all            # from the project root

Raw files in data/raw/ are only read, never changed.
"""

import argparse
import csv
import datetime as dt
from collections import Counter
from pathlib import Path

from app.domain import ACTIONABLE, PANEL_GENES, change_category
from scripts.clean import clinvar, erepo, genes, synthea
from scripts.clean.common import Report, read_tsv, sha256, write_tsv

RAW_SOURCES = {
    "clinvar": "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/",
    "clingen": "https://erepo.clinicalgenome.org/evrepo/ (export: erepo.tabbed.txt)",
    "synthea": "Synthea diabetes module export (Apple Numbers; CSV export pending, C12)",
}


def write_manifest(raw_dir, section):
    """C1: checksum every raw file so later runs can prove they used the same inputs."""
    rows = []
    for p in sorted(raw_dir.rglob("*")):
        if not p.is_file() or p.name in {".gitkeep", "MANIFEST.csv", "README.md", ".DS_Store"}:
            continue
        rel = p.relative_to(raw_dir)
        rows.append(
            {
                "path": str(rel),
                "bytes": p.stat().st_size,
                "sha256": sha256(p),
                "source": RAW_SOURCES.get(rel.parts[0], ""),
            }
        )
    with open(raw_dir / "MANIFEST.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, ["path", "bytes", "sha256", "source"])
        w.writeheader()
        w.writerows(rows)
    section.table(
        "Raw files (data/raw/MANIFEST.csv)",
        ["File", "Size", "SHA-256 (first 12)"],
        [(r["path"], f"{r['bytes']:,}", r["sha256"][:12]) for r in rows],
    )
    empty = [r["path"] for r in rows if r["bytes"] == 0]
    if empty:
        section.note(f"**Empty files:** {', '.join(empty)}")
    return rows


def release_diff(old_rows, new_rows, section, label):
    """Category counts between two cleaned releases (the knowledge changes F5 must handle)."""
    old = {(r["chrom"], r["pos"], r["ref"], r["alt"]): r for r in old_rows}
    new = {(r["chrom"], r["pos"], r["ref"], r["alt"]): r for r in new_rows}
    both = old.keys() & new.keys()
    cats = Counter()
    per_gene = {g: Counter() for g in PANEL_GENES}
    for k in both:
        cat = change_category(old[k]["classification"], new[k]["classification"])
        cats[cat] += 1
        per_gene[new[k]["gene"]][cat] += 1
    added = new.keys() - old.keys()
    removed = old.keys() - new.keys()
    added_actionable = sum(1 for k in added if new[k]["classification"] in ACTIONABLE)
    removed_actionable = sum(1 for k in removed if old[k]["classification"] in ACTIONABLE)

    section.table(
        f"{label}: variants present in both releases ({len(both):,})",
        ["Change", "Variants"],
        [
            (c, f"{cats[c]:,}")
            for c in (
                "upgrade",
                "downgrade",
                "within_actionable",
                "within_nonactionable",
                "unchanged",
            )
        ],
    )
    section.table(
        f"{label}: upgrades and downgrades by gene",
        ["Gene", "Upgrades", "Downgrades", "Within actionable", "Within non-actionable"],
        [
            (
                g,
                per_gene[g]["upgrade"],
                per_gene[g]["downgrade"],
                per_gene[g]["within_actionable"],
                per_gene[g]["within_nonactionable"],
            )
            for g in PANEL_GENES
        ],
    )
    section.note(
        f"{label}: {len(added):,} variants are new ({added_actionable:,} of them P/LP, which a "
        f"patient carrying them would see as an upgrade from 'not classified'); "
        f"{len(removed):,} disappeared ({removed_actionable:,} of them P/LP)."
    )
    return cats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--curated", default="data/curated")
    ap.add_argument("--skip-manifest", action="store_true", help="skip checksums (faster)")
    ap.add_argument(
        "--cohort", default="ge_ehr_test_data_v2", help="cohort whose patients must be in Synthea"
    )
    ap.add_argument(
        "--as-of", default="2026-10-19", help="date for 'current therapy' (THERAPY_REFERENCE_DATE)"
    )
    args = ap.parse_args(argv)
    raw, clean, curated = Path(args.raw), Path(args.clean), Path(args.curated)
    clean.mkdir(parents=True, exist_ok=True)

    report = Report("Data quality report (Stage 1a)")
    report.preamble = [
        f"Generated {dt.date.today().isoformat()} by `python -m scripts.clean.run_all`. "
        "Raw inputs in `data/raw/` are never modified; "
        "every number below is recomputed on each run.",
    ]

    if any((raw / "synthea").glob("*.numbers")):
        synthea.export_numbers(raw / "synthea")  # C12 first, so the CSVs are in the manifest
    s = report.section("C1 · Raw inputs")
    if not args.skip_manifest:
        write_manifest(raw, s)
    else:
        s.note("Checksums skipped (--skip-manifest).")

    s = report.section("C11 · Gene coordinates")
    panel = genes.load_genes(curated / "panel_genes.tsv")
    genes.write_bed(panel, clean / "panel_genes.bed")
    s.table(
        "Panel genes (GRCh38, 1-based)",
        ["Gene", "Chrom", "Start", "End", "Strand"],
        [(g.gene, g.chrom, f"{g.start:,}", f"{g.end:,}", g.strand) for g in panel.values()],
    )
    s.note("Source: NCBI Gene (RS_2025_08). No two panel genes overlap, so position decides C6.")

    releases = {}
    for path in sorted((raw / "clinvar").glob("clinvar_*.vcf.gz")):
        s = report.section(f"C2–C8 · ClinVar {path.name}")
        date, rows = clinvar.clean_release(path, panel, s)
        out = clean / f"clinvar_{date.replace('-', '')}.panel.tsv"
        write_tsv(
            out, clinvar.COLUMNS, rows, comments=[f"ClinVar release {date}", f"source {path.name}"]
        )
        s.note(f"Release date {date} → `{out}`")
        releases[date] = rows

    dates = sorted(releases)
    if len(dates) >= 2:
        s = report.section("Release-to-release changes (what F5 must handle)")
        pairs = list(zip(dates, dates[1:], strict=False))
        if (dates[0], dates[-1]) not in pairs:
            pairs.append((dates[0], dates[-1]))
        for a, b in pairs:
            release_diff(releases[a], releases[b], s, f"{a} → {b}")

    s = report.section("C9–C10 · ClinGen eRepo")
    transcripts = {r["gene"]: r["accession"] for r in read_tsv(curated / "gene_transcripts.tsv")}
    erepo_rows = erepo.clean_erepo(raw / "clingen" / "erepo.tabbed.txt", transcripts, releases, s)
    write_tsv(clean / "erepo_classifications.tsv", erepo.COLUMNS, erepo_rows)

    s = report.section("C12–C14 · Synthea")
    synthea_dir = raw / "synthea"
    if any(synthea_dir.glob("*.numbers")):
        synthea.export_numbers(synthea_dir, s)  # again, only to list the files in the report
    cohort_ids = []
    if (Path(args.cohort) / "scenarios.csv").exists():
        with open(Path(args.cohort) / "scenarios.csv", newline="") as fh:
            cohort_ids = [r["patient_id"] for r in csv.DictReader(fh)]
    synthea.clean_synthea(
        synthea_dir, curated, clean, dt.date.fromisoformat(args.as_of), cohort_ids, s
    )

    report.write(clean / "QUALITY_REPORT.md")
    print(f"Wrote {clean}/ ({len(releases)} ClinVar releases, {len(erepo_rows)} eRepo rows)")
    print(f"Report: {clean / 'QUALITY_REPORT.md'}")


if __name__ == "__main__":
    main()
