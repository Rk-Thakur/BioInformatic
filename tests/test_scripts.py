"""Stage 5/6 helper scripts: reclassification analysis, activating list, cohort builder,
and a benchmark smoke run."""

import csv
import json

import pytest

from eval import reclassification
from scripts import curate_activating
from tests.factories import CURATED, ROOT, read_csv, write_release

CLEAN = ROOT / "data" / "clean"
RAW = ROOT / "data" / "raw" / "clinvar"


def test_reclassification_per_gene(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    write_release(
        clean / "clinvar_20230107.panel.tsv",
        "2023-01-07",
        [
            ("7", 1, "A", "G", "GCK", "VUS"),
            ("7", 2, "A", "G", "GCK", "VUS"),
            ("7", 3, "A", "G", "GCK", "LP"),
            ("12", 4, "A", "G", "HNF1A", "P"),
        ],
    )
    write_release(
        clean / "clinvar_20261019.panel.tsv",
        "2026-10-19",
        [
            ("7", 1, "A", "G", "GCK", "LP"),
            ("7", 2, "A", "G", "GCK", "LB"),
            ("7", 3, "A", "G", "GCK", "VUS"),
            ("12", 4, "A", "G", "HNF1A", "P"),
        ],
    )
    rows = reclassification.main(clean, tmp_path / "out")
    gck = next(r for r in rows if r["gene"] == "GCK")
    assert (gck["upgrade"], gck["downgrade"], gck["vus_to_pathogenic"], gck["vus_to_benign"]) == (
        1,
        1,
        1,
        1,
    )
    md = (tmp_path / "out" / "reclassification.md").read_text()
    assert "| GCK | 3 | 1 | 1 | 33.3% | 100.0% | 1 (50.0%) | 1 (50.0%) |" in md
    with pytest.raises(SystemExit):
        reclassification.main(tmp_path / "empty", tmp_path / "out")


def test_curate_activating(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    path = clean / "clinvar_20261019.panel.tsv"
    write_release(
        path,
        "2026-10-19",
        [
            ("11", 17390000, "A", "G", "KCNJ11", "P"),
            ("11", 17393000, "A", "G", "ABCC8", "LP"),
            ("11", 17394000, "A", "G", "ABCC8", "P"),
            ("11", 17395000, "A", "G", "ABCC8", "VUS"),
        ],
    )
    rows = read_csv(path, delimiter="\t")
    diseases = [
        "Permanent_neonatal_diabetes_mellitus",
        "Neonatal_diabetes|Hyperinsulinism",
        "not_provided",
        "Neonatal_diabetes",
    ]
    for r, d in zip(rows, diseases, strict=True):
        r["disease"] = d
    with open(path, "w", newline="") as fh:
        fh.write("# ClinVar release 2026-10-19\n")
        w = csv.DictWriter(fh, list(rows[0]), delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    out = tmp_path / "activating.tsv"
    curate_activating.main(["--clean", str(clean), "--out", str(out)])
    got = read_csv(out, delimiter="\t")
    assert [r["pos"] for r in got] == ["17390000"]  # hyperinsulinism, no disease and VUS excluded

    # hand-curated rows survive a re-run
    with open(out, "a") as fh:
        fh.write("11\t17393000\tA\tG\tactivating\tABCC8\t1\tPMID:12345 hand-curated\n")
    curate_activating.main(["--clean", str(clean), "--out", str(out)])
    assert {r["pos"] for r in read_csv(out, delimiter="\t")} == {"17390000", "17393000"}


@pytest.mark.slow
@pytest.mark.skipif(not (RAW / "clinvar_20230107.vcf.gz").exists(), reason="raw ClinVar absent")
def test_build_reclass_cohort_reproduces_v2(tmp_path):
    """The committed test cohort is exactly what the builder produces from the raw files."""
    import subprocess
    import sys

    out = tmp_path / "v2"
    res = subprocess.run(
        [
            sys.executable,
            "scripts/build_reclass_cohort.py",
            "--old",
            str(RAW / "clinvar_20230107.vcf.gz"),
            "--new",
            str(RAW / "clinvar_20261019.vcf.gz"),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if "No such file" in res.stderr and "archive" in res.stderr:
        pytest.skip("original cohort (data/archive) absent")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Self-check: PASSED" in res.stdout
    for name in ("scenarios.csv", "inserted_variants.csv", "expected_changes.csv"):
        assert (out / name).read_text() == (ROOT / "ge_ehr_test_data_v2" / name).read_text(), name


@pytest.mark.slow
@pytest.mark.skipif(
    not (CLEAN / "clinvar_20261019.panel.tsv").exists(), reason="run the cleaning step first"
)
def test_benchmark_smoke(tmp_path):
    """One scenario, one repeat: the harness runs and scores GE-EHR perfectly."""
    from eval import benchmark

    benchmark._BUILDS.clear()
    benchmark.main(["--scenario", "rule_revision", "--repeats", "1", "--out", str(tmp_path)])
    metrics = read_csv(tmp_path / "rule_revision" / "metrics.csv")
    got = {(m["strategy"], m["metric"]): m["value"] for m in metrics}
    assert got[("change_driven", "state_equals_oracle")] == "True"
    assert (
        got[("change_driven", "stale_alerts")] == "0"
        and got[("change_driven", "missing_alerts")] == "0"
    )
    assert got[("full", "state_equals_oracle")] == "True"
    assert got[("alert_once", "state_equals_oracle")] == "False"
    assert int(got[("change_driven", "patients_reevaluated")]) < int(
        got[("full", "patients_reevaluated")]
    )
    env = json.loads((tmp_path / "environment.json").read_text())
    assert env["python"] and "Table VI" in (tmp_path / "SUMMARY.md").read_text()
    assert (CURATED / "rules_v2.yaml").exists()
