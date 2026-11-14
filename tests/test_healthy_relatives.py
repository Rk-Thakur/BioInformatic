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

    def alerts(*where):
        return session.scalar(select(func.count()).select_from(Alert).join(Patient).where(*where))

    # Their tests created risk findings (step 4) but never a treatment alert (D1-D6),
    # and left everyone else's alerts alone.
    treatment = (~Patient.has_diabetes, Alert.rule_code.like("D%"))
    assert alerts(*treatment) == 0
    assert alerts(Patient.has_diabetes) == alerts_before
    reevaluate_release(session, "2026-10-19", AS_OF)
    assert alerts(*treatment) == 0


# ------------------------------------------------------------------ step 4: risk


def risk_levels():
    from app.services.risk import load_risk_levels

    return load_risk_levels(str(CURATED / "risk_levels_v1.yaml"))


def test_risk_levels_file_is_complete_and_strict():
    import yaml

    from app.services.risk import RiskLevelsError, parse_risk_levels

    risk = risk_levels()
    assert risk.version == 1 and risk.precedence[-1] == "normal"
    assert risk.lookup("HNF1A", "P").level == "high"
    assert risk.lookup("GCK", "LP").level == "mild"
    assert risk.lookup("ABCC8", "VUS").level == "uncertain"
    assert risk.lookup("KCNJ11", "CONFLICT").level == "normal"
    assert risk.changes_level("GCK", "LP", "VUS") and not risk.changes_level("GCK", "B", "LB")

    data = yaml.safe_load((CURATED / "risk_levels_v1.yaml").read_text())
    gap = {**data, "assignments": data["assignments"][:-1]}
    with pytest.raises(RiskLevelsError, match="no level for"):
        parse_risk_levels(gap)
    twice = {**data, "assignments": data["assignments"] + [data["assignments"][0]]}
    with pytest.raises(RiskLevelsError, match="duplicate code"):
        parse_risk_levels(twice)
    bad_note = [dict(data["assignments"][0], pregnancy_note={"GCK": "x"})]
    with pytest.raises(RiskLevelsError, match="pregnancy_note"):
        parse_risk_levels({**data, "assignments": bad_note + data["assignments"][1:]})


def test_risk_findings_pure():
    from app.services.risk import person_level, risk_findings
    from app.services.rule_engine import PatientFacts, VariantFacts, evaluate

    risk = risk_levels()
    variants = [
        VariantFacts(1, "HNF4A", "LP", label="HNF4A v1"),
        VariantFacts(2, "ABCC8", "VUS", label="ABCC8 v2"),
        VariantFacts(3, "GCK", "B", label="GCK v3"),
    ]
    woman = risk_findings("F", variants, risk)
    assert [(v.patient_variant_id, a.code, kind, sev) for v, a, kind, sev, _ in woman] == [
        (1, "G1", "alert", "high"),
        (2, "G5", "info_note", "info"),
    ]
    assert "obstetric" in woman[0][4] and "High genetic risk" in woman[0][4]
    assert "obstetric" not in risk_findings("M", variants, risk)[0][4]
    assert person_level({"G1", "G5"}, risk) == "high"
    assert person_level({"G5"}, risk) == "uncertain"
    assert person_level(set(), risk) == "normal"

    healthy = PatientFacts(frozenset(), has_diabetes=False, sex="F")
    outs = evaluate(healthy, variants, [], risk)
    assert [(o.rule_code, o.rule_id, o.kind) for o in outs] == [
        ("G1", None, "alert"),
        ("G5", None, "info_note"),
    ]
    dead = PatientFacts(frozenset(), active=False, has_diabetes=False)
    assert evaluate(dead, variants, [], risk) == []


def levels_in_db(session):
    """{external id: level} for every person without diabetes, from their open findings."""
    from sqlalchemy import select

    from app.models import Alert, Patient
    from app.models.base import OPEN_STATUSES
    from app.services.risk import person_level

    risk = risk_levels()
    out = {}
    for p in session.scalars(select(Patient).where(~Patient.has_diabetes)):
        codes = set(
            session.scalars(
                select(Alert.rule_code).where(
                    Alert.patient_id == p.id,
                    Alert.status.in_(OPEN_STATUSES),
                    Alert.kind.in_(("alert", "info_note")),
                )
            )
        )
        out[p.external_id] = person_level(codes, risk)
    return out


def expected_levels(release):
    return {
        e["relative_id"]: e["level"]
        for e in rows(DATASET / "expected_risk.csv")
        if e["release"] == release
    }


@pytest.mark.slow
def test_risk_levels_through_every_release(tmp_path):
    """Every person gets the expected level under each release, whether they were
    re-evaluated from 2023 (change-driven) or loaded fresh under 2026."""
    from sqlalchemy import select

    from app.genes import load_genes
    from app.models import Alert, Patient
    from app.services.reevaluate import active_alert_state, reevaluate_release
    from app.services.seed import seed_relatives
    from tests.test_cohort_reevaluation import in_fresh_db
    from tests.test_synthea_seed import load

    genes = load_genes(CURATED / "panel_genes.tsv")

    def reevaluated(session):
        load(session, "2023-01-07")
        seed_relatives(session, DATASET, genes, AS_OF)
        assert levels_in_db(session) == expected_levels("2023-01-07")
        reevaluate_release(session, "2023-12-30", AS_OF)
        assert levels_in_db(session) == expected_levels("2023-12-30")
        reevaluate_release(session, "2026-10-19", AS_OF)
        assert levels_in_db(session) == expected_levels("2026-10-19")
        # RS2 carriers went from mild to uncertain: a review notice, worded for risk.
        notices = session.scalars(
            select(Alert).join(Patient).where(~Patient.has_diabetes, Alert.kind == "review_notice")
        ).all()
        assert len(notices) == 2 and all("genetic risk finding (G4)" in n.message for n in notices)
        # FAMILY1 carriers: new high-risk findings are marked reclassified, high priority.
        fam = session.scalars(
            select(Alert)
            .join(Patient)
            .where(~Patient.has_diabetes, Alert.rule_code == "G1", Alert.reclassified)
        ).all()
        assert {a.priority for a in fam} == {"high"} and len(fam) == 4
        return active_alert_state(session)

    def fresh(session):
        load(session, "2026-10-19")
        seed_relatives(session, DATASET, genes, AS_OF)
        assert levels_in_db(session) == expected_levels("2026-10-19")
        return active_alert_state(session)

    a, b = in_fresh_db(tmp_path, "re", reevaluated), in_fresh_db(tmp_path, "fresh", fresh)
    assert a == b, {"only_after": a - b, "only_fresh": b - a}


@pytest.mark.slow
def test_rule_revision_leaves_relatives_alone(session):
    from sqlalchemy import select

    from app.genes import load_genes
    from app.models import Alert, Patient
    from app.services.knowledge_import import import_rules
    from app.services.reevaluate import reevaluate_rule
    from app.services.seed import seed_relatives
    from tests.test_synthea_seed import load

    load(session, "2026-10-19")
    seed_relatives(session, DATASET, load_genes(CURATED / "panel_genes.tsv"), AS_OF)

    def healthy_state():
        return set(
            session.execute(
                select(Alert.id, Alert.status).join(Patient).where(~Patient.has_diabetes)
            )
        )

    before = healthy_state()
    import_rules(session, CURATED / "rules_v2.yaml")
    reevaluate_rule(session, "D4", 2, AS_OF)
    assert before and healthy_state() == before  # risk findings untouched by a D-rule change
