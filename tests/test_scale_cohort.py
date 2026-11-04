"""Stage 5: the derived scale cohort (generator + bulk loader)."""

import json

import pytest
from sqlalchemy import func, select

from app.genes import load_genes
from app.models import Alert, Patient, PatientVariant
from app.services.knowledge_import import import_release, import_rules
from app.services.reevaluate import active_alert_state, reevaluate_release
from app.services.seed import seed_scale
from app.services.workflow import evaluate_and_apply
from scripts.make_scale_cohort import make
from tests.factories import AS_OF, COHORT, CURATED, ROOT, read_csv

CLEAN = ROOT / "data" / "clean"
pytestmark = pytest.mark.skipif(
    not (CLEAN / "patients.csv").exists(), reason="run the cleaning step first"
)


@pytest.fixture(scope="module")
def cohort(tmp_path_factory):
    out = tmp_path_factory.mktemp("scale")
    manifest = make(
        60,
        out,
        clean=CLEAN,
        cohort=COHORT,
        start=CLEAN / "clinvar_20230107.panel.tsv",
        newest=CLEAN / "clinvar_20261019.panel.tsv",
        seed=3,
    )
    return out, manifest


def test_generator(cohort, tmp_path):
    out, m = cohort
    assert m["n_patients"] == 60 and m["backgrounds"] == 30 and m["profiles"] == 87
    assert m["carriers"] == round(60 * 0.02)
    pats = read_csv(out / "patients.csv")
    assert len({p["patient_id"] for p in pats}) == 60
    assert all(p["profile_source"] and p["background_sample"] for p in pats)
    again = make(
        60,
        tmp_path,
        clean=CLEAN,
        cohort=COHORT,
        start=CLEAN / "clinvar_20230107.panel.tsv",
        newest=CLEAN / "clinvar_20261019.panel.tsv",
        seed=3,
    )
    assert (tmp_path / "patients.csv").read_text() == (
        out / "patients.csv"
    ).read_text()  # deterministic
    assert json.loads((out / "manifest.json").read_text())["note"].startswith("Derived")
    assert again["carriers"] == m["carriers"]


def test_bulk_loader_matches_reevaluation(session, cohort):
    out, m = cohort
    import_rules(session, CURATED / "rules_v1.yaml")
    for v in ("20230107", "20261019"):
        import_release(session, CLEAN / f"clinvar_{v}.panel.tsv")
    n = seed_scale(session, out, load_genes(CURATED / "panel_genes.tsv"), AS_OF)
    assert n == 60
    assert session.scalar(select(func.count()).select_from(PatientVariant)) > 60 * 300
    carriers = read_csv(out / "carriers.csv")
    for c in carriers:  # the inserted variant is stored for every carrier
        p = session.scalar(select(Patient).where(Patient.external_id == c["patient_id"]))
        assert session.scalar(
            select(PatientVariant).where(
                PatientVariant.patient_id == p.id,
                PatientVariant.pos == int(c["pos"]),
                PatientVariant.alt == c["alt"],
            )
        )
    inactive = session.scalar(select(func.count()).select_from(Patient).where(~Patient.is_active))
    with_alerts = session.scalar(select(func.count(Alert.patient_id.distinct())))
    assert with_alerts <= 60 - inactive

    with pytest.raises(ValueError, match="empty"):
        seed_scale(session, out, load_genes(CURATED / "panel_genes.tsv"), AS_OF)

    # After change-driven re-evaluation, evaluating everyone from scratch changes nothing.
    reevaluate_release(session, "2026-10-19", AS_OF)
    before = active_alert_state(session)
    for p in session.scalars(select(Patient).where(Patient.is_active)).all():
        res = evaluate_and_apply(session, p, AS_OF, trigger="medication")
        assert res.created == [] and res.superseded == [], p.external_id
    assert active_alert_state(session) == before
