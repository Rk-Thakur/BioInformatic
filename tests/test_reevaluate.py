"""F5: Algorithm 2 (release changes) and Algorithm 3 (rule revisions) on a small,
hand-built situation that covers every row of paper Table IV."""

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.genes import load_genes
from app.models import Alert, AlertProvenance, CuratedFlag, KbRelease, Rule
from app.services.knowledge_import import current_release, import_release, import_rules
from app.services.reevaluate import (
    ReevaluationError,
    active_alert_state,
    reevaluate_release,
    reevaluate_rule,
)
from app.services.workflow import evaluate_and_apply, process_upload
from tests.factories import AS_OF, CURATED, make_patient, write_release, write_vcf

GENES = load_genes(CURATED / "panel_genes.tsv")

A = ("12", 120978600, "C", "T")  # HNF1A VUS -> LP   upgrade (family of three)
B = ("7", 44150000, "A", "G")  # GCK   LP  -> VUS  downgrade
C = ("20", 44356000, "G", "A")  # HNF4A LP  -> P    within actionable
D = ("12", 120978700, "C", "T")  # HNF1A VUS -> LB   within non-actionable
E = ("12", 120978800, "C", "T")  # HNF1A VUS -> P    carried by nobody (RS7)
F = ("12", 120978900, "C", "T")  # HNF1A B   -> B    unchanged (bystander)
R = ("11", 17393127, "T", "C")  # ABCC8 LP  -> LP   activating, for D4 v2 (RS5)

OLD = [
    (*A, "HNF1A", "VUS"),
    (*B, "GCK", "LP"),
    (*C, "HNF4A", "LP"),
    (*D, "HNF1A", "VUS"),
    (*E, "HNF1A", "VUS"),
    (*F, "HNF1A", "B"),
    (*R, "ABCC8", "LP"),
]
NEW = [
    (*A, "HNF1A", "LP"),
    (*B, "GCK", "VUS"),
    (*C, "HNF4A", "P"),
    (*D, "HNF1A", "LB"),
    (*E, "HNF1A", "P"),
    (*F, "HNF1A", "B"),
    (*R, "ABCC8", "LP"),
]


def build(session, tmp_path):
    """Two releases (old current), rules v1 current + v2 pending, seven patients uploaded."""
    import_rules(session, CURATED / "rules_v1.yaml")
    import_rules(session, CURATED / "rules_v2.yaml")
    import_release(
        session, write_release(tmp_path / "clinvar_20230107.panel.tsv", "2023-01-07", OLD)
    )
    import_release(
        session, write_release(tmp_path / "clinvar_20261019.panel.tsv", "2026-10-19", NEW)
    )
    session.add(
        CuratedFlag(chrom=R[0], pos=R[1], ref=R[2], alt=R[3], flag="activating", source="test")
    )
    people = {
        "fam1": (["insulin"], None, [A]),
        "fam2": (["insulin", "metformin"], None, [A]),
        "fam3": (["insulin"], None, [A]),
        "gck": (["metformin"], None, [B]),
        "hnf4a": (["metformin"], None, [C]),
        "lb": (["insulin"], None, [D]),
        "bystander": (["insulin"], None, [F]),
        "abcc8": (["insulin"], 3, [R]),
    }
    patients = {}
    for name, (therapy, age, variants) in people.items():
        p = make_patient(session, name, therapy=therapy, age_months=age)
        vcf = write_vcf(tmp_path / f"{name}.vcf", [(*v, "0/1") for v in variants])
        process_upload(session, p, vcf, GENES, AS_OF)
        patients[name] = p
    session.flush()
    return patients


def alerts(session, patient, status="active"):
    return list(
        session.scalars(select(Alert).where(Alert.patient_id == patient.id, Alert.status == status))
    )


def codes(session, patient, status="active"):
    return {(a.rule_code, a.kind) for a in alerts(session, patient, status)}


@pytest.fixture()
def world(session, tmp_path):
    return build(session, tmp_path)


def test_before_reevaluation(session, world):
    assert codes(session, world["fam1"]) == {("D6", "alert")}  # VUS: monitored
    assert codes(session, world["gck"]) == {("D1", "alert"), ("D5", "alert")}


def test_upgrade_reaches_every_family_member(session, world):
    """RS1 + RS6."""
    run = reevaluate_release(session, "2026-10-19", AS_OF)
    for name in ("fam1", "fam2", "fam3"):
        active = {a.rule_code: a for a in alerts(session, world[name])}
        assert set(active) == {"D2", "D5"}
        d2 = active["D2"]
        assert d2.reclassified and d2.priority == "high" and d2.release.version == "2026-10-19"
        assert codes(session, world[name], "superseded") == {("D6", "alert")}  # DD3
        assert not [a for a in alerts(session, world[name]) if a.kind == "review_notice"]
    assert run.variants_changed == 4  # A, B, C, D (E is carried by nobody)


def test_downgrade_supersedes_and_asks_for_review(session, world):
    """RS2: alerts are never deleted and therapy is never changed automatically."""
    reevaluate_release(session, "2026-10-19", AS_OF)
    g = world["gck"]
    assert codes(session, g, "superseded") == {("D1", "alert"), ("D5", "alert")}
    assert codes(session, g) == {("D6", "alert"), (None, "review_notice")}
    notice = next(a for a in alerts(session, g) if a.kind == "review_notice")
    assert notice.priority == "high" and notice.supersedes_id is not None
    assert session.get(Alert, notice.supersedes_id).rule_code == "D1"  # the most severe one
    assert "LP to VUS" in notice.message and "never changes therapy" in notice.message


def test_within_band_changes_only_update_provenance(session, world):
    """RS3 (LP -> P) and RS4 (VUS -> LB): no notification."""
    before = {a.id for a in alerts(session, world["hnf4a"])}
    reevaluate_release(session, "2026-10-19", AS_OF)
    after = alerts(session, world["hnf4a"])
    assert {a.id for a in after} == before  # same alerts, nothing new
    for a in after:
        last = a.provenance[-1]
        assert last.release.version == "2026-10-19" and last.classification == "P"
        assert a.release.version == "2023-01-07"  # creation provenance is kept (DD13)

    lb = world["lb"]
    assert codes(session, lb) == set()
    assert codes(session, lb, "superseded") == {("D6", "alert")}  # silently retired


def test_bystanders_are_not_touched(session, world):
    """Change-driven: patients without a changed variant are not re-evaluated."""

    def prov_count():
        return session.scalar(
            select(func.count())
            .select_from(AlertProvenance)
            .join(Alert)
            .where(Alert.patient_id == world["bystander"].id)
        )

    n = prov_count()
    run = reevaluate_release(session, "2026-10-19", AS_OF)
    assert prov_count() == n
    assert run.patients_touched == 6  # fam1-3, gck, hnf4a, lb


def test_release_switch_and_errors(session, world):
    reevaluate_release(session, "2026-10-19", AS_OF)
    assert current_release(session).version == "2026-10-19"
    assert (
        session.scalar(select(func.count()).select_from(KbRelease).where(KbRelease.is_current)) == 1
    )
    with pytest.raises(ReevaluationError, match="already current"):
        reevaluate_release(session, "2026-10-19", AS_OF)
    with pytest.raises(ReevaluationError, match="not imported"):
        reevaluate_release(session, "2099-01-01", AS_OF)


def test_result_is_a_fixed_point(session, world):
    """Re-evaluating everyone again after the run changes nothing (idempotent)."""
    reevaluate_release(session, "2026-10-19", AS_OF)
    for p in world.values():
        res = evaluate_and_apply(session, p, AS_OF, trigger="medication")
        assert res.created == [] and res.superseded == [], p.external_id


def test_change_driven_equals_full_recompute(app, tmp_path):
    """The paper's central claim: re-checking only changed variants gives the same alerts
    as recomputing every patient."""
    states, touched = {}, {}
    for mode in ("change_driven", "full"):
        db.drop_all()
        db.create_all()
        build(db.session, tmp_path / mode if (tmp_path / mode).mkdir() is None else tmp_path)
        run = reevaluate_release(db.session, "2026-10-19", AS_OF, mode=mode)
        states[mode], touched[mode] = active_alert_state(db.session), run.patients_touched
        db.session.rollback()
    assert states["change_driven"] == states["full"]
    assert touched["change_driven"] < touched["full"]


def test_alert_once_leaves_stale_alerts(session, world):
    """Baseline: the release switches but alerts are not re-evaluated."""
    before = active_alert_state(session)
    run = reevaluate_release(session, "2026-10-19", AS_OF, mode="alert_once")
    assert active_alert_state(session) == before and run.patients_touched == 0
    assert codes(session, world["gck"]) == {("D1", "alert"), ("D5", "alert")}  # stale: now VUS


def test_rule_revision_d4_v2(session, world):
    """RS5: D4 v2 adds ABCC8. Only the ABCC8 carrier's outcome changes."""
    assert "D4" not in {a.rule_code for a in alerts(session, world["abcc8"])}
    run = reevaluate_rule(session, "D4", 2, AS_OF)
    d4 = [a for a in alerts(session, world["abcc8"]) if a.rule_code == "D4"]
    assert len(d4) == 1 and d4[0].kind == "alert" and d4[0].severity == "high"
    assert d4[0].rule.version == 2
    assert run.alerts_created == 1 and run.patients_touched == 1  # only ABCC8/KCNJ11 carriers
    current = {
        r.version for r in session.scalars(select(Rule).where(Rule.code == "D4", Rule.is_current))
    }
    assert current == {2}
    with pytest.raises(ReevaluationError, match="already current"):
        reevaluate_rule(session, "D4", 2, AS_OF)


def test_cli_reevaluate(runner, app, tmp_path):
    build(db.session, tmp_path)
    db.session.commit()
    result = runner.invoke(args=["reevaluate", "2026-10-19"])
    assert result.exit_code == 0, result.output
    assert "changed 4" in result.output and "review_notices=1" in result.output
    again = runner.invoke(args=["reevaluate", "2026-10-19"])
    assert again.exit_code != 0 and "already current" in again.output
    assert (
        "(release, change_driven): 2023-01-07 -> 2026-10-19" in runner.invoke(args=["runs"]).output
    )
