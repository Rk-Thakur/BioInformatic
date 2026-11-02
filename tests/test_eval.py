"""Stage 5: the evaluation metrics and the controlled-release generator."""

import csv
import gzip

import pytest
from sqlalchemy import select

from app.models import KbRelease
from app.services.reevaluate import active_alert_state, reevaluate_release, release_changes
from eval import metrics
from scripts.make_controlled_release import make
from tests.factories import AS_OF, write_release
from tests.test_reevaluate import build


def run_and_measure(session, tmp_path, mode):
    build(session, tmp_path)
    before = active_alert_state(session)
    old, new = (
        session.scalar(select(KbRelease).where(KbRelease.version == v))
        for v in ("2023-01-07", "2026-10-19")
    )
    changes, _ = release_changes(session, old, new)
    run = reevaluate_release(session, "2026-10-19", AS_OF, mode=mode)
    return run, before, {c.key: c.category for c in changes}


def oracle(session, tmp_path):
    """Same world, evaluated fresh under 2026."""
    from app.extensions import db

    db.drop_all()
    db.create_all()
    patients = build(session, tmp_path)
    reevaluate_release(session, "2026-10-19", AS_OF, mode="full")
    state = active_alert_state(session)
    db.session.rollback()
    db.drop_all()
    db.create_all()
    return state, patients


def test_change_driven_scores_perfectly(session, tmp_path):
    truth, _ = oracle(session, tmp_path)
    run, before, changes = run_and_measure(session, tmp_path, "change_driven")
    m = metrics.compute(
        session, before=before, oracle=truth, run_ids=[run.id], changes=changes, as_of=AS_OF
    )
    assert (
        m["realert_sensitivity"] == 1.0 and m["expected_realerts"] == 6
    )  # 3 relatives x (D2 + D5)
    assert m["stale_alerts"] == 0 and m["missing_alerts"] == 0 and m["state_equals_oracle"]
    assert m["notification_precision"] == 1.0
    assert m["notifications"] == 7  # 6 new treatment alerts + 1 review notice
    assert m["point_in_time_correct"] == 1.0


def test_alert_once_is_stale(session, tmp_path):
    truth, _ = oracle(session, tmp_path)
    run, before, changes = run_and_measure(session, tmp_path, "alert_once")
    m = metrics.compute(
        session, before=before, oracle=truth, run_ids=[run.id], changes=changes, as_of=AS_OF
    )
    assert m["realert_sensitivity"] == 0.0
    assert m["stale_alerts"] == 2  # the GCK patient's D1 and D5 after the downgrade
    assert m["missing_alerts"] == 6
    assert m["notification_precision"] is None and not m["state_equals_oracle"]
    assert m["point_in_time_correct"] == 1.0  # old alerts are still explained by old knowledge


def test_ratio():
    assert metrics.ratio(1, 0) is None and metrics.ratio(1, 4) == 0.25


# ------------------------------------------------------------------ controlled release


def test_controlled_release_generator(tmp_path):
    cohort = tmp_path / "cohort"
    (cohort / "patients").mkdir(parents=True)
    keys = [("12", 120978600 + i, "C", "T") for i in range(8)]
    for p in range(2):
        with gzip.open(cohort / "patients" / f"p{p}.vcf.gz", "wt") as fh:
            fh.write(
                "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
            )
            for k in keys:
                fh.write(f"{k[0]}\t{k[1]}\t.\t{k[2]}\t{k[3]}\t.\tPASS\t.\tGT\t0/1\n")
    classes = ["VUS", "VUS", "LP", "P", "B", "VUS", "LP", "VUS"]
    base = write_release(
        tmp_path / "base.tsv",
        "2023-01-07",
        [(*k, "HNF1A", c, str(i)) for i, (k, c) in enumerate(zip(keys, classes, strict=True))],
    )
    erepo = tmp_path / "erepo.tsv"
    erepo.write_text(
        "clinvar_id\tapproval_date\n7\t2022-01-01\n"
    )  # variant 7 is expert-reviewed: never chosen
    plan = {"upgrade": 2, "downgrade": 1, "within_actionable": 1, "within_nonactionable": 1}
    release, truth, injected = make(base, "2023-06-30", cohort, erepo, tmp_path / "out", plan=plan)

    assert release.name == "clinvar_20230630.panel.tsv"
    assert {r["category"] for r in injected} == set(plan)
    assert "7" not in {r["clinvar_id"] for r in injected}
    rows = {
        r["clinvar_id"]: r
        for r in csv.DictReader(
            (ln for ln in open(release) if not ln.startswith("#")), delimiter="\t"
        )
    }
    for r in injected:
        assert rows[r["clinvar_id"]]["classification"] == r["new_class"]
    again = make(base, "2023-06-30", cohort, erepo, tmp_path / "out2", plan=plan)[2]
    assert again == injected  # deterministic

    with pytest.raises(SystemExit, match="candidates"):
        make(base, "2023-06-30", cohort, erepo, tmp_path / "out3", plan={"upgrade": 50})
