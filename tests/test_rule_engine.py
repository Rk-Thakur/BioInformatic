"""F3: Algorithm 1. Pure rule evaluation (paper Table V, S1-S9) and alert persistence."""

import datetime as dt

import pytest
from sqlalchemy import select

from app.genes import load_genes
from app.models import Alert, Medication, ReevaluationRun
from app.services.knowledge_import import import_release, import_rules
from app.services.rule_engine import (
    PREGNANCY_CAVEAT,
    PatientFacts,
    RuleSpec,
    VariantFacts,
    apply_outcomes,
    evaluate,
    evaluate_patient,
)
from app.services.rules import load_rules_file
from app.services.workflow import NoCurrentReleaseError, evaluate_and_apply, process_upload
from tests.factories import AS_OF, COHORT, CURATED, make_patient, write_release, write_vcf

RULES_V1 = [
    RuleSpec(
        i,
        **{
            k: r[k]
            for k in (
                "code",
                "version",
                "genetic",
                "therapy",
                "extra",
                "severity",
                "recommendation",
            )
        },
        genes=tuple(r["genes"]),
    )
    for i, r in enumerate(load_rules_file(CURATED / "rules_v1.yaml"), start=1)
]
RULES_V2_D4 = [r for r in RULES_V1 if r.code != "D4"] + [
    RuleSpec(
        99,
        **{
            k: r[k]
            for k in (
                "code",
                "version",
                "genetic",
                "therapy",
                "extra",
                "severity",
                "recommendation",
            )
        },
        genes=tuple(r["genes"]),
    )
    for r in load_rules_file(CURATED / "rules_v2.yaml")
]


def run(gene, cls, therapy=(), age=None, flags=(), rules=RULES_V1, pregnant=False, active=True):
    facts = PatientFacts(frozenset(therapy), age, pregnant, active)
    variant = VariantFacts(1, gene, cls, frozenset(flags), f"{gene} test")
    return {(o.rule_code, o.kind, o.severity) for o in evaluate(facts, [variant], rules)}


# ------------------------------------------------------------ paper Table V (DD1: D5 always fires)


@pytest.mark.parametrize(
    "scenario, gene, cls, therapy, age, flags, expected",
    [
        (
            "S1",
            "HNF1A",
            "P",
            {"insulin"},
            None,
            (),
            {("D2", "alert", "high"), ("D5", "alert", "low")},
        ),
        (
            "S2",
            "HNF1A",
            "P",
            {"metformin", "sulfonylurea"},
            None,
            (),
            {("D2", "info_note", "info"), ("D5", "alert", "low")},
        ),
        (
            "S3",
            "GCK",
            "P",
            {"metformin"},
            None,
            (),
            {("D1", "alert", "medium"), ("D5", "alert", "low")},
        ),
        ("S4", "GCK", "P", set(), None, (), {("D1", "info_note", "info"), ("D5", "alert", "low")}),
        (
            "S5",
            "HNF4A",
            "LP",
            {"metformin"},
            None,
            (),
            {("D3", "alert", "medium"), ("D5", "alert", "low")},
        ),
        (
            "S6",
            "KCNJ11",
            "P",
            {"insulin"},
            3,
            {"activating"},
            {("D4", "alert", "high"), ("D5", "alert", "low")},
        ),
        ("S7", "INS", "P", {"metformin"}, None, (), {("D5", "alert", "low")}),
        ("S8", "HNF1A", "VUS", {"insulin"}, None, (), {("D6", "alert", "info")}),
        ("S9", "GCK", "B", {"metformin"}, None, (), set()),
    ],
)
def test_table_v_scenarios(scenario, gene, cls, therapy, age, flags, expected):
    assert run(gene, cls, therapy, age, flags) == expected, scenario


def test_d4_needs_infant_onset_and_activating_flag():
    assert ("D4", "info_note", "info") in run(
        "KCNJ11", "P", {"insulin"}, age=46 * 12, flags={"activating"}
    )
    assert ("D4", "info_note", "info") in run(
        "KCNJ11", "P", {"insulin"}, age=None, flags={"activating"}
    )
    assert ("D4", "info_note", "info") in run(
        "KCNJ11", "P", {"insulin"}, age=3
    )  # not flagged (DD8)
    assert ("D4", "alert", "high") not in run(
        "KCNJ11", "P", {"metformin"}, age=3, flags={"activating"}
    ) - {("D4", "info_note", "info")}


def test_d4_version_2_adds_abcc8():
    """RS5: same patient, rule v1 vs v2."""
    assert not any(
        code == "D4" for code, _, _ in run("ABCC8", "LP", {"insulin"}, 3, {"activating"})
    )
    assert ("D4", "alert", "high") in run(
        "ABCC8", "LP", {"insulin"}, 3, {"activating"}, rules=RULES_V2_D4
    )


def test_pregnancy_caveat_on_d1():
    facts = PatientFacts(frozenset({"insulin"}), None, pregnant=True)
    (d1,) = [
        o for o in evaluate(facts, [VariantFacts(1, "GCK", "P")], RULES_V1) if o.rule_code == "D1"
    ]
    assert d1.kind == "alert" and PREGNANCY_CAVEAT in d1.message


def test_inactive_patient_gets_nothing():
    assert run("HNF1A", "P", {"insulin"}, active=False) == set()


def test_info_note_explains_why():
    facts = PatientFacts(frozenset({"sulfonylurea"}))
    notes = [
        o
        for o in evaluate(facts, [VariantFacts(1, "HNF1A", "P", label="HNF1A x")], RULES_V1)
        if o.kind == "info_note"
    ]
    assert "already on sulfonylurea" in notes[0].message


# ------------------------------------------------------------ persistence


@pytest.fixture()
def kb(session, tmp_path):
    import_rules(session, CURATED / "rules_v1.yaml")
    old = import_release(
        session,
        write_release(
            tmp_path / "clinvar_20230107.panel.tsv",
            "2023-01-07",
            [
                ("12", 120978486, "A", "C", "HNF1A", "P"),
                ("7", 44150000, "A", "G", "GCK", "VUS"),
            ],
        ),
    )
    new = import_release(
        session,
        write_release(
            tmp_path / "clinvar_20261019.panel.tsv",
            "2026-10-19",
            [
                ("12", 120978486, "A", "C", "HNF1A", "P"),
                ("7", 44150000, "A", "G", "GCK", "LP"),
            ],
        ),
    )
    return old, new


def upload(session, patient, tmp_path, records):
    genes = load_genes(CURATED / "panel_genes.tsv")
    return process_upload(
        session, patient, write_vcf(tmp_path / f"{patient.external_id}.vcf", records), genes, AS_OF
    )


def active_alerts(session, patient):
    return {
        (a.rule_code, a.kind)
        for a in session.scalars(
            select(Alert).where(Alert.patient_id == patient.id, Alert.status == "active")
        )
    }


def test_upload_creates_alerts_with_provenance(session, tmp_path, kb):
    p = make_patient(session, therapy=["insulin"])
    up, result = upload(session, p, tmp_path, [("12", 120978486, "A", "C", "0/1")])
    assert up.status == "accepted"
    assert active_alerts(session, p) == {("D2", "alert"), ("D5", "alert")}
    d2 = next(a for a in result.created if a.rule_code == "D2")
    assert d2.release.version == "2023-01-07" and d2.rule.version == 1
    assert len(d2.provenance) == 1 and d2.provenance[0].classification == "P"
    assert session.scalar(select(ReevaluationRun)).trigger == "upload"


def test_rerun_is_idempotent(session, tmp_path, kb):
    """DD9: evaluating twice creates nothing new."""
    p = make_patient(session, therapy=["insulin"])
    upload(session, p, tmp_path, [("12", 120978486, "A", "C", "0/1")])
    again = evaluate_and_apply(session, p, AS_OF, trigger="medication")
    assert again.created == [] and again.superseded == [] and len(again.kept) == 2
    assert len(session.scalars(select(Alert)).all()) == 2


def test_medication_change_turns_alert_into_info_note(session, tmp_path, kb):
    p = make_patient(session, therapy=["insulin"])
    upload(session, p, tmp_path, [("12", 120978486, "A", "C", "0/1")])
    p.medications.append(
        Medication(description="Glipizide", drug_class="sulfonylurea", start=dt.date(2026, 1, 1))
    )
    session.flush()
    res = evaluate_and_apply(session, p, AS_OF, trigger="medication")
    assert {a.rule_code for a in res.superseded} == {"D2"}
    assert active_alerts(session, p) == {("D2", "info_note"), ("D5", "alert")}
    old = session.scalar(select(Alert).where(Alert.rule_code == "D2", Alert.status == "superseded"))
    assert old.status_changed_at is not None  # history kept, never deleted


def test_new_release_appends_provenance_and_switches_d6(session, tmp_path, kb):
    """apply_outcomes under a newer release: kept alerts gain provenance, D6 note is superseded (DD3)."""
    _, new = kb
    p = make_patient(session, therapy=["metformin"])
    upload(session, p, tmp_path, [("7", 44150000, "A", "G", "0/1")])
    assert active_alerts(session, p) == {("D6", "alert")}
    res = apply_outcomes(
        session,
        p,
        evaluate_patient(session, p, new, AS_OF),
        new,
        priority="high",
        reclassified=True,
    )
    assert {a.rule_code for a in res.superseded} == {"D6"}
    d1 = next(a for a in res.created if a.rule_code == "D1")
    assert d1.priority == "high" and d1.reclassified and d1.release.version == "2026-10-19"


def test_rejected_upload_creates_no_alerts(session, kb):
    p = make_patient(session)
    genes = load_genes(CURATED / "panel_genes.tsv")
    up, result = process_upload(
        session, p, COHORT / "malformed" / "S10_non_numeric_pos.vcf", genes, AS_OF
    )
    assert up.status == "rejected" and result is None
    assert session.scalars(select(Alert)).all() == []


def test_upload_needs_a_current_release(session, tmp_path):
    p = make_patient(session)
    with pytest.raises(NoCurrentReleaseError):
        upload(session, p, tmp_path, [("12", 120978486, "A", "C", "0/1")])
