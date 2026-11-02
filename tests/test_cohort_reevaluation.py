"""Stage 3 exit criteria on real data: the 33-patient cohort is uploaded under ClinVar
2023-01-07 and re-evaluated to 2026-10-19.

1. RS1-RS7 behave as paper Table V says.
2. The changed variants F5 finds are exactly the carried rows of expected_changes.csv.
3. Change-driven re-evaluation ends in the same alerts as (a) full recomputation and
   (b) uploading everyone fresh under 2026.
"""

import csv

import pytest
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import Alert, Patient, PatientVariant
from app.services.knowledge_import import current_release, import_rules
from app.services.reevaluate import active_alert_state, reevaluate_release, reevaluate_rule
from tests.factories import AS_OF, COHORT, CURATED
from tests.test_cohort_scenarios import CLEAN, INSERTED, SCENARIOS, load_cohort

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (CLEAN / "clinvar_20261019.panel.tsv").exists(), reason="run the cleaning step first"
    ),
]


def in_fresh_db(tmp_path, name, fn):
    """Run fn(session) against its own SQLite file and return its result."""
    app = create_app(
        "testing", overrides={"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / name}.db"}
    )
    with app.app_context():
        db.create_all()
        try:
            return fn(db.session)
        finally:
            db.session.remove()
            db.engine.dispose()


def by_scenario(session):
    """{scenario: [(patient, inserted PatientVariant)]}"""
    out = {}
    for row in SCENARIOS:
        ins = INSERTED[row["patient_id"]]
        p = session.scalar(select(Patient).where(Patient.external_id == row["patient_id"]))
        pv = session.scalar(
            select(PatientVariant).where(
                PatientVariant.patient_id == p.id,
                PatientVariant.pos == int(ins["pos"]),
                PatientVariant.ref == ins["ref"],
                PatientVariant.alt == ins["alt"],
            )
        )
        out.setdefault(row["scenario"], []).append((p, pv))
    return out


def on_variant(session, pv, status="active"):
    return list(
        session.scalars(
            select(Alert).where(Alert.patient_variant_id == pv.id, Alert.status == status)
        )
    )


def test_rs_scenarios_on_real_data(session):
    load_cohort(session, current="2023-01-07")
    run = reevaluate_release(session, "2026-10-19", AS_OF)
    assert current_release(session).version == "2026-10-19"
    s = by_scenario(session)

    # RS1 + RS6: the shared HNF1A VUS -> LP reaches all four relatives
    family = s["S8/RS1/RS6"]
    assert len(family) == 4
    for _, pv in family:
        d2 = [a for a in on_variant(session, pv) if a.rule_code == "D2"]
        assert len(d2) == 1 and d2[0].reclassified and d2[0].priority == "high"
        assert {a.rule_code for a in on_variant(session, pv, "superseded")} == {"D6"}

    # RS2: GCK LP -> VUS: superseded, review notice, nothing deleted
    for _, pv in s["RS2"]:
        kinds = {(a.rule_code, a.kind) for a in on_variant(session, pv)}
        assert kinds == {("D6", "alert"), (None, "review_notice")}
        assert {a.rule_code for a in on_variant(session, pv, "superseded")} == {"D1", "D5"}

    # RS3: HNF4A LP -> P: same alerts, provenance now says 2026
    ((_, pv),) = s["RS3"]
    alerts = on_variant(session, pv)
    assert {a.rule_code for a in alerts} == {"D3", "D5"}
    assert all(a.provenance[-1].release.version == "2026-10-19" for a in alerts)
    assert not any(a.reclassified for a in alerts)

    # RS4: HNF1A VUS -> LB: D6 retired, no notification
    ((_, pv),) = s["RS4"]
    assert on_variant(session, pv) == []
    assert [a.kind for a in on_variant(session, pv, "superseded")] == ["alert"]

    # Change-driven touched only carriers of changed variants
    assert 0 < run.patients_touched < len(SCENARIOS)

    # RS5: D4 v2 adds ABCC8 -> the infant-onset ABCC8 patient on insulin gets D4
    import_rules(session, CURATED / "rules_v2.yaml")
    reevaluate_rule(session, "D4", 2, AS_OF)
    rs5_patient, pv = s["RS5"][0]
    s5_rows = [r for r in SCENARIOS if r["scenario"] == "RS5"]
    assert s5_rows and float(s5_rows[0]["age_at_diagnosis"]) < 0.5
    assert {(a.rule_code, a.kind, a.severity) for a in on_variant(session, pv)} >= {
        ("D4", "alert", "high")
    }


def test_changes_match_expected_changes_csv(session):
    """F5's Delta over stored keys == the carried rows of the ground-truth file."""
    from app.models import KbRelease
    from app.services.reevaluate import release_changes

    load_cohort(session, current="2023-01-07")
    old, new = (
        session.scalar(select(KbRelease).where(KbRelease.version == v))
        for v in ("2023-01-07", "2026-10-19")
    )
    changes, _ = release_changes(session, old, new)
    got = {
        (c.key[0], str(c.key[1]), c.key[2], c.key[3]): (c.old, c.new, c.category) for c in changes
    }

    expected = {}
    with open(COHORT / "expected_changes.csv") as fh:
        for r in csv.DictReader(fh):
            if int(r["n_carriers"]) > 0:
                expected[(r["chrom"], r["pos"], r["ref"], r["alt"])] = (
                    r["old_class"],
                    r["new_class"],
                    r["category"],
                )

    differ = {
        k: (got.get(k), expected.get(k))
        for k in got.keys() | expected.keys()
        if got.get(k) != expected.get(k)
    }
    assert not differ, differ


@pytest.mark.parametrize("mode", ["change_driven", "full"])
def test_reevaluation_equals_fresh_upload(tmp_path, mode):
    """The core correctness property of the paper (plan §9)."""

    def reevaluated(session):
        load_cohort(session, current="2023-01-07")
        reevaluate_release(session, "2026-10-19", AS_OF, mode=mode)
        return active_alert_state(session)

    def fresh(session):
        load_cohort(session, current="2026-10-19")
        return active_alert_state(session)

    a = in_fresh_db(tmp_path, f"re_{mode}", reevaluated)
    b = in_fresh_db(tmp_path, f"fresh_{mode}", fresh)
    assert a == b, {"only_after_reevaluation": a - b, "only_fresh": b - a}
