"""Build a controlled ClinVar release for the evaluation (paper §5.1).

Takes a cleaned base release and changes the classification of chosen variants that
cohort patients carry, so every change and its correct outcome is known in advance:

  upgrade                non-actionable (VUS, CONFLICT, LB, B) -> LP
  downgrade              P / LP / P/LP                        -> VUS
  within_actionable      LP <-> P
  within_nonactionable   VUS -> LB  (or B -> LB)

Variants with a ClinGen expert classification approved by the controlled release date
are never chosen: the expert opinion would override the injected class (DD6) and make
the ground truth wrong. Selection is deterministic (fixed seed).

    python -m scripts.make_controlled_release \\
        --base data/clean/clinvar_20230107.panel.tsv --date 2023-06-30 --out eval/controlled
writes eval/controlled/clinvar_20230630.panel.tsv and injected_changes.csv.
"""

import argparse
import csv
import gzip
import random
from collections import Counter
from pathlib import Path

from app.domain import ACTIONABLE, change_category

PLAN = {"upgrade": 10, "downgrade": 5, "within_actionable": 3, "within_nonactionable": 5}
TREATMENT_GENES = ("GCK", "HNF1A", "HNF4A")  # genes with a therapy rule (D1-D3) and D6
RARE_CARRIERS = 4  # a family can share a variant; a common background variant is not "rare"


def read_release(path):
    comments, body = [], []
    with open(path, newline="") as fh:
        for line in fh:
            (comments if line.startswith("#") else body).append(line)
    reader = csv.DictReader(body, delimiter="\t")
    return comments, reader.fieldnames, list(reader)


def carried_keys(cohort_dir):
    """{key: number of cohort patients carrying it} from the cohort VCFs."""
    counts = Counter()
    for vcf in sorted(Path(cohort_dir, "patients").glob("*.vcf.gz")):
        seen = set()
        with gzip.open(vcf, "rt") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                c = line.split("\t", 5)
                seen.add((c[0], c[1], c[3], c[4]))
        counts.update(seen)
    return counts


def expert_ids(erepo_path, by_date):
    if not erepo_path or not Path(erepo_path).exists():
        return set()
    with open(erepo_path, newline="") as fh:
        return {
            r["clinvar_id"]
            for r in csv.DictReader(fh, delimiter="\t")
            if r["clinvar_id"] and r["approval_date"] <= by_date
        }


def target_class(category, old):
    if category == "upgrade":
        return "LP"
    if category == "downgrade":
        return "VUS"
    if category == "within_actionable":
        return "P" if old == "LP" else "LP"
    return "LB"  # within_nonactionable


def eligible(category, row):
    cls, gene = row["classification"], row["gene"]
    if category == "upgrade":
        return cls in ("VUS", "CONFLICT", "LB", "B", "B/LB") and gene in TREATMENT_GENES
    if category == "downgrade":
        return cls in ACTIONABLE and gene in TREATMENT_GENES
    if category == "within_actionable":
        return cls in ("P", "LP")
    return cls in ("VUS", "B") and gene in TREATMENT_GENES


def make(base, date, cohort, erepo, out_dir, plan=PLAN, seed=7):
    comments, fields, rows = read_release(base)
    carried = carried_keys(cohort)
    experts = expert_ids(erepo, date)
    rng = random.Random(seed)
    used, injected = set(), []
    index = {(r["chrom"], r["pos"], r["ref"], r["alt"]): r for r in rows}

    for category, n in plan.items():
        pool = sorted(
            k
            for k, r in index.items()
            if k in carried
            and k not in used
            and r["clinvar_id"] not in experts
            and eligible(category, r)
        )
        if len(pool) < n:
            raise SystemExit(f"only {len(pool)} candidates for {category}, need {n}")
        # Pathogenic variants are rare: prefer variants few patients carry (at most
        # RARE_CARRIERS), so one injected change does not touch the whole cohort.
        rare = [k for k in pool if carried[k] <= RARE_CARRIERS]
        if len(rare) >= n:
            pool = rare
        for key in rng.sample(pool, n):
            r = index[key]
            old, new = r["classification"], target_class(category, r["classification"])
            assert change_category(old, new) == category, (key, old, new)
            r["classification"] = new
            r["clnsig_raw"] = f"controlled:{old}->{new}"
            used.add(key)
            injected.append(
                {
                    "chrom": key[0],
                    "pos": key[1],
                    "ref": key[2],
                    "alt": key[3],
                    "gene": r["gene"],
                    "clinvar_id": r["clinvar_id"],
                    "old_class": old,
                    "new_class": new,
                    "category": category,
                    "n_carriers": carried[key],
                }
            )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    release = out_dir / f"clinvar_{date.replace('-', '')}.panel.tsv"
    with open(release, "w", newline="") as fh:
        fh.write(f"# ClinVar release {date}\n")
        fh.write(
            f"# controlled release: {Path(base).name} with {len(injected)} "
            f"injected changes (seed {seed})\n"
        )
        w = csv.DictWriter(fh, fields, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    truth = out_dir / "injected_changes.csv"
    with open(truth, "w", newline="") as fh:
        w = csv.DictWriter(fh, list(injected[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(injected, key=lambda r: (r["category"], r["gene"], int(r["pos"]))))
    return release, truth, injected


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base", default="data/clean/clinvar_20230107.panel.tsv")
    ap.add_argument("--date", default="2023-06-30", help="version/date of the controlled release")
    ap.add_argument("--cohort", default="ge_ehr_test_data_v2")
    ap.add_argument("--erepo", default="data/clean/erepo_classifications.tsv")
    ap.add_argument("--out", default="eval/controlled")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    release, truth, injected = make(
        args.base, args.date, args.cohort, args.erepo, args.out, seed=args.seed
    )
    by_cat = Counter(r["category"] for r in injected)
    print(f"Wrote {release} and {truth}: {dict(by_cat)}")


if __name__ == "__main__":
    main()
