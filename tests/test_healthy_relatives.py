"""Stage 7 step 2: the healthy-relatives demo dataset (DD43)."""

import csv
from collections import Counter

import pytest

from app.services.vcf_service import parse_vcf
from scripts.build_healthy_relatives import build
from tests.factories import AS_OF, CURATED, ROOT

CLEAN = ROOT / "data" / "clean"
COHORT = ROOT / "ge_ehr_test_data_v2"
DATASET = ROOT / "ge_ehr_healthy_relatives"

pytestmark = pytest.mark.skipif(
    not (CLEAN / "clinvar_20261019.panel.tsv").exists(), reason="run the cleaning step first"
)


def rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("relatives")
    build(COHORT, CLEAN, CURATED / "risk_levels_v1.yaml", out)
    return out


def test_reproduces_committed_dataset(built):
    for name in ("relatives.csv", "expected_risk.csv", "manifest.json"):
        assert (built / name).read_text() == (DATASET / name).read_text(), name
    for p in rows(built / "relatives.csv"):
        rid = p["relative_id"]
        assert (built / "vcfs" / f"{rid}.vcf.gz").read_bytes() == (
            DATASET / "vcfs" / f"{rid}.vcf.gz"
        ).read_bytes(), rid


def test_people_are_healthy_relatives(built):
    people = rows(built / "relatives.csv")
    assert len(people) == 24
    assert {p["diabetes"] for p in people} == {"none"}
    assert {p["relation"] for p in people} == {"parent", "sibling", "child"}
    proband_ids = {r["patient_id"] for r in rows(COHORT / "scenarios.csv")}
    assert all(p["proband_id"] in proband_ids for p in people)
    assert all(p["background_from_proband"] != p["background_other_parent"] for p in people)


def test_family_variant_only_in_carriers(built):
    for p in rows(built / "relatives.csv"):
        chrom, pos, ref, alt = p["family_variant"].split(":")
        parsed = parse_vcf(built / "vcfs" / f"{p['relative_id']}.vcf.gz")
        keys = {c.key for c in parsed.calls}
        assert ((chrom, int(pos), ref, alt) in keys) == (p["carrier"] == "1"), p["name"]


def test_every_vcf_passes_the_upload_validator(built):
    for p in rows(built / "relatives.csv"):
        parsed = parse_vcf(built / "vcfs" / f"{p['relative_id']}.vcf.gz")
        assert parsed.sample == p["relative_id"]
        assert parsed.total_records == int(p["variants"])
        # Symbolic alleles from the 1000 Genomes backgrounds are skipped by design,
        # exactly as for the cohort patients; nothing else may be skipped.
        assert set(parsed.skipped) <= {"symbolic_alt"}, parsed.skipped


def test_expected_levels_cover_every_level_and_change(built):
    exp = rows(built / "expected_risk.csv")
    newest = Counter(e["level"] for e in exp if e["release"] == "2026-10-19")
    assert set(newest) == {"high", "mild", "uncertain", "normal"}
    by = {}
    for e in exp:
        by.setdefault(e["relative_id"], {})[e["release"]] = e["level"]
    moves = {(d["2023-01-07"], d["2026-10-19"]) for d in by.values()}
    assert {("uncertain", "high"), ("mild", "uncertain"), ("uncertain", "normal")} <= moves
    # Non-carriers never get "high" or "mild" from the family variant.
    people = {p["relative_id"]: p for p in rows(built / "relatives.csv")}
    for e in exp:
        if people[e["relative_id"]]["carrier"] == "0":
            assert e["deciding_variant"] != people[e["relative_id"]]["family_variant"]


# ------------------------------------------------------------------ step 3: storage


def test_rule_engine_skips_people_without_diabetes():
    from app.services.rule_engine import PatientFacts, VariantFacts, evaluate
    from tests.test_rule_engine import RULES_V1

    hnf1a_p = [VariantFacts(1, "HNF1A", "P", label="HNF1A test")]
    with_dm = evaluate(PatientFacts(frozenset({"insulin"})), hnf1a_p, RULES_V1)
    assert {o.rule_code for o in with_dm} >= {"D2", "D5"}
    healthy = PatientFacts(frozenset({"insulin"}), has_diabetes=False)
    assert evaluate(healthy, hnf1a_p, RULES_V1) == []


def test_unsaved_patient_counts_as_diabetic():
    from app.models import Patient
    from app.services.rule_engine import patient_facts

    assert patient_facts(Patient(name="new"), AS_OF).has_diabetes is True


def test_relation_must_be_known(session):
    import sqlalchemy.exc

    from app.models import Patient

    session.add(Patient(external_id="x", name="x", relation="cousin"))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        session.flush()
    session.rollback()


def test_seed_relatives_needs_the_family_loaded(session):
    from app.genes import load_genes
    from app.services.seed import seed_relatives

    r = seed_relatives(session, DATASET, load_genes(CURATED / "panel_genes.tsv"), AS_OF)
    assert (r.created, len(r.missing_proband)) == (0, 24)


@pytest.mark.slow
def test_seed_relatives_on_real_data(session):
    from sqlalchemy import func, select

    from app.genes import load_genes
    from app.models import Alert, Patient, PatientVariant
    from app.services.reevaluate import reevaluate_release
    from app.services.seed import seed_relatives
    from tests.test_synthea_seed import load

    load(session, "2023-01-07")
    alerts_before = session.scalar(select(func.count()).select_from(Alert))
    genes = load_genes(CURATED / "panel_genes.tsv")
    r = seed_relatives(session, DATASET, genes, AS_OF)
    assert (r.created, r.skipped, r.rejected_uploads, r.missing_proband) == (24, 0, 0, [])
    assert seed_relatives(session, DATASET, genes, AS_OF).skipped == 24  # idempotent

    people = {p["relative_id"]: p for p in rows(DATASET / "relatives.csv")}
    healthy = session.scalars(select(Patient).where(~Patient.has_diabetes)).all()
    assert {p.external_id for p in healthy} == set(people)
    for p in healthy:
        row = people[p.external_id]
        assert p.relative_of.external_id == row["proband_id"]
        assert p.relation == row["relation"] and p in p.relative_of.relatives
        assert p.family_id == p.relative_of.family_id
        assert not p.diagnoses and not p.medications
        n = session.scalar(
            select(func.count())
            .select_from(PatientVariant)
            .where(PatientVariant.patient_id == p.id)
        )
        assert n > 0

    def healthy_alerts():
        return session.scalar(
            select(func.count()).select_from(Alert).join(Patient).where(~Patient.has_diabetes)
        )

    # Uploading their tests created no treatment alerts, and left everyone else's alone.
    assert healthy_alerts() == 0
    assert session.scalar(select(func.count()).select_from(Alert)) == alerts_before
    reevaluate_release(session, "2026-10-19", AS_OF)
    assert healthy_alerts() == 0
