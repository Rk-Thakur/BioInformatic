"""Reclassification rates and directions per gene across the cleaned ClinVar releases
(paper §6.2 future work: "analysis of real ClinVar history for the seven genes").

    python -m eval.reclassification

Writes eval/results/reclassification.csv and reclassification.md.
"""

import csv
from collections import Counter
from pathlib import Path

from app.domain import ACTIONABLE, PANEL_GENES, change_category

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "data" / "clean"
OUT = ROOT / "eval" / "results"
BENIGN = {"LB", "B", "B/LB"}
COLUMNS = (
    "in_both",
    "upgrade",
    "downgrade",
    "within_actionable",
    "within_nonactionable",
    "unchanged",
    "vus_before",
    "vus_to_pathogenic",
    "vus_to_benign",
    "actionable_before",
)


def read(path):
    with open(path, newline="") as fh:
        rows = csv.DictReader((ln for ln in fh if not ln.startswith("#")), delimiter="\t")
        return {(r["chrom"], r["pos"], r["ref"], r["alt"]): r for r in rows}


def compare(old, new):
    """Per-gene counts for variants present in both releases."""
    out = {g: Counter() for g in PANEL_GENES}
    for key in old.keys() & new.keys():
        o, n = old[key]["classification"], new[key]["classification"]
        c = out[new[key]["gene"]]
        c["in_both"] += 1
        c[change_category(o, n)] += 1
        if o == "VUS":
            c["vus_before"] += 1
            if n in ACTIONABLE:
                c["vus_to_pathogenic"] += 1
            elif n in BENIGN:
                c["vus_to_benign"] += 1
        if o in ACTIONABLE:
            c["actionable_before"] += 1
    return out


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "–"


def main():
    files = sorted(CLEAN.glob("clinvar_*.panel.tsv"))
    if len(files) < 2:
        raise SystemExit("need at least two cleaned releases in data/clean")
    releases = [(f.stem.split("_")[1].split(".")[0], read(f)) for f in files]
    pairs = list(zip(releases, releases[1:], strict=False))
    if (releases[0], releases[-1]) not in pairs:
        pairs.append((releases[0], releases[-1]))

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    md = [
        "# ClinVar reclassification per gene",
        "",
        "Variants present in both releases. *Upgrade* = into P/LP; *downgrade* = out of P/LP. "
        "VUS resolution = share of the earlier release's VUS that became P/LP or B/LB.",
        "",
    ]
    for (da, a), (db, b) in pairs:
        label = f"{da[:4]}-{da[4:6]}-{da[6:]} → {db[:4]}-{db[4:6]}-{db[6:]}"
        per_gene = compare(a, b)
        md += [
            f"## {label}",
            "",
            "| Gene | In both | Upgrades | Downgrades | Upgrade rate | "
            "Downgrade rate (of P/LP) | VUS → P/LP | VUS → B/LB |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        total = Counter()
        for g in PANEL_GENES:
            c = per_gene[g]
            total.update(c)
            rows.append({"pair": label, "gene": g, **{k: c[k] for k in COLUMNS}})
            md.append(_line(g, c))
        md.append(_line("**All**", total))
        md.append("")
    with open(OUT / "reclassification.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    (OUT / "reclassification.md").write_text("\n".join(md))
    print(f"Wrote {OUT / 'reclassification.md'}")


def _line(name, c):
    return (
        f"| {name} | {c['in_both']:,} | {c['upgrade']} | {c['downgrade']} | "
        f"{pct(c['upgrade'], c['in_both'])} | {pct(c['downgrade'], c['actionable_before'])} | "
        f"{c['vus_to_pathogenic']} ({pct(c['vus_to_pathogenic'], c['vus_before'])}) | "
        f"{c['vus_to_benign']} ({pct(c['vus_to_benign'], c['vus_before'])}) |"
    )


if __name__ == "__main__":
    main()
