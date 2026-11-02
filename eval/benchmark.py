"""Evaluation harness: alert-once vs full recomputation vs change-driven (paper §5).

    python -m eval.benchmark                       # all scenarios, 33-patient cohort
    python -m eval.benchmark --scenario real --replicate 10 --repeats 5

For each scenario the cohort is loaded once under the starting knowledge and saved as
a SQLite file. Every strategy and every timing repeat then starts from its own copy of
that file, so all strategies see exactly the same patients and alerts. An oracle (the
same cohort evaluated from scratch under the target knowledge) gives the right answer.

Results go to eval/results/<scenario>/ (runs.csv, metrics.csv, table_vi.md) plus
eval/results/SUMMARY.md and environment.json.
"""

import argparse
import csv
import datetime as dt
import json
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.genes import load_genes
from app.models import KbRelease
from app.services.knowledge_import import (
    import_curated_flags,
    import_expert_classifications,
    import_release,
    import_rules,
)
from app.services.reevaluate import (
    active_alert_state,
    reevaluate_release,
    reevaluate_rule,
    release_changes,
)
from app.services.seed import seed_cohort
from eval import metrics

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "data" / "clean"
CURATED = ROOT / "data" / "curated"
COHORT = ROOT / "ge_ehr_test_data_v2"
CONTROLLED = ROOT / "eval" / "controlled"
AS_OF = dt.date(2026, 10, 19)
STRATEGIES = ("alert_once", "full", "change_driven")

# A step is ("release", version) or ("rule", code, version).
SCENARIOS = {
    "real": {
        "about": "Real ClinVar releases 2023-01-07 -> 2026-10-19",
        "start": "2023-01-07",
        "steps": [("release", "2026-10-19")],
    },
    "stepwise": {
        "about": "Two real releases in a row: 2023-01-07 -> 2023-12-30 -> 2026-10-19",
        "start": "2023-01-07",
        "steps": [("release", "2023-12-30"), ("release", "2026-10-19")],
    },
    "controlled": {
        "about": "Controlled release: 2023-01-07 with 23 injected changes (eval/controlled/)",
        "start": "2023-01-07",
        "steps": [("release", "2023-06-30")],
        "extra_releases": [CONTROLLED / "clinvar_20230630.panel.tsv"],
    },
    "rule_revision": {
        "about": "Rule revision D4 v1 -> v2 (adds ABCC8) under ClinVar 2026-10-19",
        "start": "2026-10-19",
        "steps": [("rule", "D4", 2)],
    },
}


# --------------------------------------------------------------------------- databases


class Db:
    """App context bound to one SQLite file."""

    def __init__(self, path):
        self.app = create_app("testing", overrides={"SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}"})
        self.ctx = self.app.app_context()

    def __enter__(self):
        self.ctx.push()
        return db.session

    def __exit__(self, *exc):
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()


def load_knowledge(session, current, extra_releases=(), rules_current=()):
    files = sorted(CLEAN.glob("clinvar_*.panel.tsv")) + [Path(p) for p in extra_releases]
    for f in files:
        version = f.stem.split("_")[1].split(".")[0]
        version = f"{version[:4]}-{version[4:6]}-{version[6:]}"
        import_release(session, f, make_current=version == current)
    import_expert_classifications(session, CLEAN / "erepo_classifications.tsv")
    import_rules(session, CURATED / "rules_v1.yaml")
    import_rules(session, CURATED / "rules_v2.yaml", make_current=("D4", 2) in rules_current)
    import_curated_flags(session, CURATED / "activating_variants.tsv")


def build(path, scenario, current, replicate, rules_current=()):
    """Create a database with knowledge (`current` release) and the seeded cohort."""
    with Db(path) as s:
        db.create_all()
        load_knowledge(s, current, scenario.get("extra_releases", ()), rules_current)
        genes = load_genes(CURATED / "panel_genes.tsv")
        created, _, rejected = seed_cohort(s, COHORT, genes, AS_OF, replicate=replicate)
        assert rejected == 0
        s.commit()
        return created


def oracle_state(workdir, scenario, step_index, replicate, cache):
    """Fresh evaluation under the knowledge in force after `step_index` steps."""
    release, rules = scenario["start"], set()
    for step in scenario["steps"][: step_index + 1]:
        if step[0] == "release":
            release = step[1]
        else:
            rules.add((step[1], step[2]))
    key = (release, tuple(sorted(rules)))
    if key not in cache:
        path = workdir / f"oracle_{scenario['name']}_{len(cache)}.db"
        build(path, scenario, release, replicate, rules_current=rules)
        with Db(path) as s:
            cache[key] = active_alert_state(s)
    return cache[key]


# --------------------------------------------------------------------------- running


def run_step(session, step, mode):
    """Apply one knowledge change. Returns (run, changes {key: category}, seconds)."""
    if step[0] == "release":
        old = session.scalar(select(KbRelease).where(KbRelease.is_current))
        new = session.scalar(select(KbRelease).where(KbRelease.version == step[1]))
        changes, _ = release_changes(session, old, new)
        t0 = time.perf_counter()
        run = reevaluate_release(session, step[1], AS_OF, mode=mode)
        session.commit()
        return run, {c.key: c.category for c in changes}, time.perf_counter() - t0
    t0 = time.perf_counter()
    run = reevaluate_rule(session, step[1], step[2], AS_OF, mode=mode)
    session.commit()
    return run, {}, time.perf_counter() - t0


def run_scenario(name, scenario, *, replicate, repeats, out_dir, workdir):
    scenario = {**scenario, "name": name}
    base = workdir / f"{name}_base.db"
    n_patients = build(base, scenario, scenario["start"], replicate)
    oracles = {}
    runs_rows, metric_rows = [], []

    for strategy in STRATEGIES:
        for rep in range(repeats):
            path = workdir / f"{name}_{strategy}_{rep}.db"
            shutil.copy(base, path)
            with Db(path) as s:
                for i, step in enumerate(scenario["steps"]):
                    before = active_alert_state(s) if rep == 0 else None
                    run, changes, secs = run_step(s, step, strategy)
                    label = step[1] if step[0] == "release" else f"{step[1]} v{step[2]}"
                    runs_rows.append(
                        {
                            "scenario": name,
                            "strategy": strategy,
                            "repeat": rep,
                            "step": label,
                            "ms": round(secs * 1000, 2),
                            "variants_changed": run.variants_changed,
                            "patients_reevaluated": run.patients_touched,
                            "records_examined": run.records_evaluated,
                            "alerts_created": run.alerts_created,
                            "alerts_superseded": run.alerts_superseded,
                        }
                    )
                    if rep == 0:
                        m = metrics.compute(
                            s,
                            before=before,
                            oracle=oracle_state(workdir, scenario, i, replicate, oracles),
                            run_ids=[run.id],
                            changes=changes,
                            as_of=AS_OF,
                            revised_rule=step[1] if step[0] == "rule" else None,
                        )
                        m.update(
                            patients_reevaluated=run.patients_touched,
                            records_examined=run.records_evaluated,
                            variants_changed=run.variants_changed,
                        )
                        for k, v in m.items():
                            metric_rows.append(
                                {
                                    "scenario": name,
                                    "strategy": strategy,
                                    "step": label,
                                    "metric": k,
                                    "value": v,
                                }
                            )
            path.unlink()

    out = out_dir / name
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "runs.csv", runs_rows)
    write_csv(out / "metrics.csv", metric_rows)
    table = table_vi(name, scenario, metric_rows, runs_rows, n_patients, replicate)
    (out / "table_vi.md").write_text(table)
    return table


# --------------------------------------------------------------------------- reporting


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def fmt(v, kind):
    if v is None:
        return "n/a"
    if kind == "pct":
        return f"{100 * v:.0f}%"
    if kind == "bool":
        return "yes" if v else "**no**"
    if kind == "float":
        return f"{v:.1f}"
    return f"{v:,}"


ROWS = [
    ("Re-alert sensitivity", "realert_sensitivity", "pct", "mean"),
    ("Stale alerts after release", "stale_alerts", "int", "last"),
    ("Missing alerts after release", "missing_alerts", "int", "last"),
    ("Notification precision", "notification_precision", "pct", "mean"),
    ("Notifications", "notifications", "int", "sum"),
    ("Records examined", "records_examined", "int", "sum"),
    ("Patients re-evaluated", "patients_reevaluated", "int", "sum"),
    ("Point-in-time correctness", "point_in_time_correct", "pct", "last"),
    ("Final alerts identical to fresh evaluation", "state_equals_oracle", "bool", "last"),
    ("Notifications per 1,000 patients", "notifications_per_1000_patients", "float", "sum"),
    ("Changed variants with no notification", "silent_change_share", "pct", "mean"),
]


def aggregate(values, how):
    vals = [v for v in values if v is not None]
    if how == "last":
        return values[-1] if values else None
    if not vals:
        return None
    if how == "sum":
        return sum(vals)
    return sum(vals) / len(vals)


def cohort_label(replicate):
    if replicate == 1:
        return "33-patient test cohort"
    return f"33-patient test cohort replicated x{replicate}, not Synthea"


def table_vi(name, scenario, metric_rows, runs_rows, n_patients, replicate):
    by = {}
    for r in metric_rows:
        by.setdefault((r["strategy"], r["metric"]), []).append(r["value"])
    lines = [
        f"### {name}: {scenario['about']}",
        "",
        f"{n_patients:,} patients ({cohort_label(replicate)}); "
        f"{len(scenario['steps'])} knowledge change(s).",
        "",
        "| Metric | Alert-once | Full recompute | GE-EHR (change-driven) |",
        "|---|---|---|---|",
    ]
    for label, key, kind, how in ROWS:
        cells = [fmt(aggregate(by.get((s, key), []), how), kind) for s in STRATEGIES]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    times = {}
    for r in runs_rows:
        times.setdefault((r["strategy"], r["repeat"]), 0.0)
        times[(r["strategy"], r["repeat"])] += r["ms"]
    cells = []
    for s in STRATEGIES:
        per_rep = [v for (st, _), v in times.items() if st == s]
        cells.append(f"{statistics.median(per_rep):,.0f} ms" if per_rep else "n/a")
    lines.append("| Time (median of repeats) | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def environment(args):
    def git(*a):
        try:
            return subprocess.check_output(
                ["git", *a], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            return None

    cpu = platform.processor()
    if sys.platform == "darwin":
        try:
            cpu = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
        except Exception:
            pass
    return {
        "date": dt.datetime.now().isoformat(timespec="seconds"),
        "git_commit": git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain")),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "cpu": cpu,
        "replicate": args.replicate,
        "repeats": args.repeats,
        "therapy_as_of": AS_OF.isoformat(),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--scenario", choices=[*SCENARIOS, "all"], default="all")
    ap.add_argument("--replicate", type=int, default=1, help="copies of the 33-patient cohort")
    ap.add_argument("--repeats", type=int, default=3, help="timing repeats per strategy")
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = ap.parse_args(argv)

    if not (CLEAN / "clinvar_20261019.panel.tsv").exists():
        raise SystemExit("cleaned data missing: run `python -m scripts.clean.run_all` first")
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    if "controlled" in names and not (CONTROLLED / "clinvar_20230630.panel.tsv").exists():
        raise SystemExit(
            "controlled release missing: run `python -m scripts.make_controlled_release`"
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = []
    with tempfile.TemporaryDirectory(prefix="ge-ehr-bench-") as tmp:
        for name in names:
            t0 = time.perf_counter()
            tables.append(
                run_scenario(
                    name,
                    SCENARIOS[name],
                    replicate=args.replicate,
                    repeats=args.repeats,
                    out_dir=out_dir,
                    workdir=Path(tmp),
                )
            )
            print(f"{name}: done in {time.perf_counter() - t0:.0f} s")
    env = environment(args)
    (out_dir / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    summary = [
        "# Evaluation results (paper Table VI)",
        "",
        f"Generated {env['date']} at commit {env['git_commit']}"
        f"{' (uncommitted changes)' if env['git_dirty'] else ''}; "
        f"Python {env['python']}, SQLite {env['sqlite']}, {env['cpu']}.",
        "",
        "Re-run with `python -m eval.benchmark`. Metric definitions: `eval/metrics.py`.",
        "",
    ]
    (out_dir / "SUMMARY.md").write_text("\n".join(summary) + "\n" + "\n".join(tables))
    print(f"Wrote {out_dir / 'SUMMARY.md'}")


if __name__ == "__main__":
    main()
