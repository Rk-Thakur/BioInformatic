"""Stage 2: loading the cleaned Synthea patients (seed_synthea) and their VCFs."""

import csv
import datetime as dt

import pytest
from sqlalchemy import func, select

from app.genes import load_genes
from app.models import Alert, Medication, Patient, PatientVariant
from app.services.knowledge_import import (
    import_curated_flags,
    import_expert_classifications,
    import_release,
    import_rules,
)
from app.services.reevaluate import active_alert_state, reevaluate_release
from app.services.seed import seed_synthea
from tests.factories import AS_OF, COHORT, CURATED, ROOT, read_csv, write_release
from tests.test_cohort_reevaluation import in_fresh_db
from tests.test_cohort_scenarios import EXPECTED_2026, INSERTED, SCENARIOS

CLEAN = ROOT / "data" / "clean"
GENES = load_genes(CURATED / "panel_genes.tsv")
S1 = "0661fbba-0fa1-1375-bc15-30a567e0ce32"  # in Synthea, on insulin
RS3 = next(r["patient_id"] for r in SCENARIOS if r["scenario"] == "RS3")  # not in Synthea


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, list(rows[0]))
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ small, hand-built


def test_seed_synthea_small(session, tmp_path):
    clean, cohort = tmp_path / "clean", tmp_path / "cohort"
    clean.mkdir()
    (cohort / "patients").mkdir(parents=True)
    for pid in (S1, RS3):
        (cohort / "patients" / f"{pid}.vcf.gz").write_bytes(
            (COHORT / "patients" / f"{pid}.vcf.gz").read_bytes()
        )
    write_csv(cohort / "scenarios.csv", [r for r in SCENARIOS if r["patient_id"] in (S1, RS3)])

    pat = dict(
        sex="F",
        birth_date="1974-07-16",
        death_date="",
        is_active="1",
        race="",
        ethnicity="",
        city="",
        state="",
    )
    write_csv(
        clean / "patients.csv",
        [
            {"patient_id": S1, **pat},
            {"patient_id": "dead-1", **pat, "death_date": "2020-01-01", "is_active": "0"},
            {"patient_id": "plain-1", **pat},
        ],
    )
    write_csv(
        clean / "diagnoses.csv",
        [
            {
                "patient_id": p,
                "diabetes_type": "type2",
                "diagnosed_on": "2025-09-23",
                "age_at_diagnosis_months": "614.2",
            }
            for p in (S1, "dead-1", "plain-1")
        ],
    )
    write_csv(
        clean / "medications.csv",
        [
            {
                "patient_id": S1,
                "rxnorm_code": "106892",
                "description": "Humulin",
                "drug_class": "insulin",
                "start": "2025-09-23",
                "stop": "",
                "refills": "3",
            },
            {
                "patient_id": "dead-1",
                "rxnorm_code": "860975",
                "description": "Metformin",
                "drug_class": "metformin",
                "start": "2015-01-01",
                "stop": "2020-01-01",
                "refills": "9",
            },
        ],
    )
    write_csv(
        clean / "conditions.csv",
        [
            {
                "patient_id": S1,
                "code": "44054006",
                "description": "Diabetes mellitus type 2 (disorder)",
                "category": "diabetes",
                "start": "2025-09-23",
                "stop": "",
            },
        ],
    )

    ins = {r["patient_id"]: r for r in read_csv(COHORT / "inserted_variants.csv")}
    import_rules(session, CURATED / "rules_v1.yaml")
    import_release(
        session,
        write_release(
            tmp_path / "clinvar_20261019.panel.tsv",
            "2026-10-19",
            [
                (ins[S1]["chrom"], ins[S1]["pos"], ins[S1]["ref"], ins[S1]["alt"], "HNF1A", "P"),
                (
                    ins[RS3]["chrom"],
                    ins[RS3]["pos"],
                    ins[RS3]["ref"],
                    ins[RS3]["alt"],
                    "HNF4A",
                    "P",
                ),
            ],
        ),
    )

    r = seed_synthea(session, clean, cohort, GENES, AS_OF)
    assert (r.patients_created, r.inactive, r.cohort_uploads, r.rejected_uploads) == (4, 1, 2, 0)
    assert r.cohort_not_in_synthea == 1

    s1 = session.scalar(select(Patient).where(Patient.external_id == S1))
    assert s1.current_therapy(AS_OF) == {"insulin"}  # real Synthea therapy, not scenarios.csv
    assert (
        s1.diabetes_diagnosis().diabetes_type == "type2" and s1.conditions[0].category == "diabetes"
    )
    assert {
        a.rule_code for a in session.scalars(select(Alert).where(Alert.patient_id == s1.id))
    } == {"D2", "D5"}
    dead = session.scalar(select(Patient).where(Patient.external_id == "dead-1"))
    assert not dead.is_active and dead.deceased_date == dt.date(2020, 1, 1)
    plain = session.scalar(select(Patient).where(Patient.external_id == "plain-1"))
    assert (
        session.scalar(
            select(func.count())
            .select_from(PatientVariant)
            .where(PatientVariant.patient_id == plain.id)
        )
        == 0
    )

    again = seed_synthea(session, clean, cohort, GENES, AS_OF)  # idempotent
    assert again.patients_created == 0 and again.patients_skipped == 4


# ------------------------------------------------------------------ real cleaned Synthea

real = pytest.mark.skipif(
    not (CLEAN / "patients.csv").exists() or not (CLEAN / "clinvar_20261019.panel.tsv").exists(),
    reason="run the cleaning step first",
)


def load(session, current):
    for f in sorted(CLEAN.glob("clinvar_*.panel.tsv")):
        import_release(
            session, f, make_current=f.name == f"clinvar_{current.replace('-', '')}.panel.tsv"
        )
    import_expert_classifications(session, CLEAN / "erepo_classifications.tsv")
    import_rules(session, CURATED / "rules_v1.yaml")
    import_curated_flags(session, CURATED / "activating_variants.tsv")
    r = seed_synthea(session, CLEAN, COHORT, GENES, AS_OF)
    assert (r.patients_created, r.cohort_uploads, r.rejected_uploads, r.cohort_not_in_synthea) == (
        90,
        33,
        0,
        3,
    )
    session.flush()
    return r


def scenario_alerts(session):
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
        out[pid] = {
            (a.rule_code, a.kind, a.severity)
            for a in session.scalars(
                select(Alert).where(Alert.patient_variant_id == pv.id, Alert.status == "active")
            )
        }
    return out


@pytest.mark.slow
@real
def test_table_v_on_real_synthea_patients(session):
    """S1-S9 with each patient's real Synthea therapy, diagnoses and deceased status."""
    r = load(session, "2026-10-19")
    assert r.inactive == 23
    got = scenario_alerts(session)
    for row in SCENARIOS:
        if row["scenario"] in EXPECTED_2026:
            assert got[row["patient_id"]] == EXPECTED_2026[row["scenario"]], row["scenario"]
    s2 = [r["patient_id"] for r in SCENARIOS if r["scenario"] == "S2"]
    for pid in s2:  # S2 override: Synthea has no sulfonylurea
        p = session.scalar(select(Patient).where(Patient.external_id == pid))
        assert "sulfonylurea" in p.current_therapy(AS_OF)
    no_dna = session.scalar(
        select(func.count())
        .select_from(Patient)
        .where(~Patient.id.in_(select(PatientVariant.patient_id).distinct()))
    )
    assert no_dna == 57  # Synthea patients without a test result
    assert session.scalar(select(func.count()).select_from(Medication)) > 79  # periods + overrides


@pytest.mark.slow
@real
def test_reevaluation_equals_fresh_on_synthea(tmp_path):
    def reevaluated(session):
        load(session, "2023-01-07")
        reevaluate_release(session, "2026-10-19", AS_OF)
        return active_alert_state(session)

    def fresh(session):
        load(session, "2026-10-19")
        return active_alert_state(session)

    a, b = in_fresh_db(tmp_path, "re", reevaluated), in_fresh_db(tmp_path, "fresh", fresh)
    assert a == b, {"only_after": a - b, "only_fresh": b - a}
