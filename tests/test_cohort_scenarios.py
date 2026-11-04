"""Paper Table V (S1-S10) end to end: real cohort VCFs, real cleaned ClinVar, real rules.

Patients' therapy and age at diagnosis come from ge_ehr_test_data_v2/scenarios.csv
until the Synthea tables are cleaned (C12-C14). The two hand edits the cohort notes
ask for are applied here: S2 also takes a sulfonylurea, S6 was diagnosed at 3 months.
"""

import pytest
from sqlalchemy import select

from app.genes import load_genes
from app.models import Alert, Patient, PatientVariant
from app.services.knowledge_import import (
    import_curated_flags,
    import_expert_classifications,
    import_release,
    import_rules,
)
from app.services.seed import seed_cohort
from tests.factories import AS_OF, COHORT, CURATED, ROOT, read_csv

CLEAN = ROOT / "data" / "clean"
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (CLEAN / "clinvar_20261019.panel.tsv").exists(), reason="run the cleaning step first"
    ),
]

SCENARIOS = read_csv(COHORT / "scenarios.csv")
INSERTED = {r["patient_id"]: r for r in read_csv(COHORT / "inserted_variants.csv")}


def load_cohort(session, current):
    """Import knowledge with `current` as the current release, then seed all 33 patients."""
    for f in sorted(CLEAN.glob("clinvar_*.panel.tsv")):
        import_release(
            session, f, make_current=f.name == f"clinvar_{current.replace('-', '')}.panel.tsv"
        )
    import_expert_classifications(session, CLEAN / "erepo_classifications.tsv")
    import_rules(session, CURATED / "rules_v1.yaml")
    import_curated_flags(session, CURATED / "activating_variants.tsv")
    created, _, rejected = seed_cohort(
        session, COHORT, load_genes(CURATED / "panel_genes.tsv"), AS_OF
    )
    assert (created, rejected) == (33, 0)
    session.flush()


def alerts_on_inserted_variant(session):
    """{patient_id: {(rule, kind, severity)}} for the scenario variant of each patient."""
    out = {}
    for pid, ins in INSERTED.items():
        pv = session.scalar(
            select(PatientVariant)
            .join(Patient)
            .where(
                Patient.external_id == pid,
                PatientVariant.pos == int(ins["pos"]),
                PatientVariant.ref == ins["ref"],
                PatientVariant.alt == ins["alt"],
            )
        )
        assert pv is not None, f"{pid}: scenario variant not stored"
        out[pid] = {
            (a.rule_code, a.kind, a.severity)
            for a in session.scalars(
                select(Alert).where(Alert.patient_variant_id == pv.id, Alert.status == "active")
            )
        }
    return out


D5 = ("D5", "alert", "low")

# Expected under the 2026-10-19 release (S1-S7, S9 and the extra D5 / RS patients).
EXPECTED_2026 = {
    "S1": {("D2", "alert", "high"), D5},
    "S2": {("D2", "info_note", "info"), D5},  # DD1: D5 fires as well
    "S3": {("D1", "alert", "medium"), D5},
    "S4": {("D1", "info_note", "info"), D5},
    "S5": {("D3", "alert", "medium"), D5},
    "S9": set(),
    "D5-HNF1B": {D5},
    "D5-ABCC8": {D5},  # ABCC8 is not in D4 v1, and the patient is an adult on metformin
    "D5-INS": {D5},
    "S8/RS1/RS6": {("D2", "alert", "high"), D5},  # family variant is LP by 2026
    "RS2": {("D6", "alert", "info")},  # GCK variant is VUS by 2026
    "RS3": {("D3", "alert", "medium"), D5},
    "RS4": set(),  # LB
    "RS5": {D5},  # D4 v1 does not cover ABCC8 (v2 does: Stage 3)
}


def test_table_v_under_2026_release(session):
    load_cohort(session, current="2026-10-19")
    got = alerts_on_inserted_variant(session)
    for row in SCENARIOS:
        pid, scen = row["patient_id"], row["scenario"]
        if scen in EXPECTED_2026:
            assert got[pid] == EXPECTED_2026[scen], f"{scen} {pid[:8]}"

    # S6: D4 needs an *activating* variant (DD8). One S6 variant is on the curated list;
    # the other has no disease in ClinVar, so it gets an explanatory info note instead.
    s6 = {
        INSERTED[r["patient_id"]]["pos"]: got[r["patient_id"]]
        for r in SCENARIOS
        if r["scenario"] == "S6"
    }
    assert s6["17387128"] == {("D4", "alert", "high"), D5}
    assert s6["17387094"] == {("D4", "info_note", "info"), D5}


def test_s8_family_vus_under_2023_release(session):
    """S8: the shared HNF1A variant is VUS in 2023 -> D6 info for every family member."""
    load_cohort(session, current="2023-01-07")
    got = alerts_on_inserted_variant(session)
    family = [r["patient_id"] for r in SCENARIOS if r["share_group"] == "FAMILY1"]
    assert len(family) == 4
    for pid in family:
        assert got[pid] == {("D6", "alert", "info")}


def test_background_variants_raise_no_treatment_alerts(session):
    """Benign background variants must never produce D1-D5 alerts."""
    load_cohort(session, current="2026-10-19")
    inserted_ids = set()
    for pid, ins in INSERTED.items():
        inserted_ids |= set(
            session.scalars(
                select(PatientVariant.id)
                .join(Patient)
                .where(
                    Patient.external_id == pid,
                    PatientVariant.pos == int(ins["pos"]),
                    PatientVariant.alt == ins["alt"],
                )
            )
        )
    stray = session.scalars(
        select(Alert).where(Alert.patient_variant_id.not_in(inserted_ids), Alert.status == "active")
    ).all()
    assert {a.rule_code for a in stray} <= {"D6"}, [(a.rule_code, a.message[:60]) for a in stray]
