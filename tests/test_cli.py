"""The `flask ...` commands, end to end on small inputs (plan §4: CLI jobs)."""

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import KbRelease, Rule, User
from tests.factories import COHORT, CURATED, write_release

PANEL_RELEASE = [("12", 120978486, "A", "C", "HNF1A", "P")]


@pytest.fixture()
def clean_dir(tmp_path, app):
    """A data/clean folder with two tiny releases, an eRepo file; app config points at it."""
    clean = tmp_path / "clean"
    clean.mkdir()
    write_release(
        clean / "clinvar_20230107.panel.tsv", "2023-01-07", [(*PANEL_RELEASE[0][:5], "VUS")]
    )
    write_release(clean / "clinvar_20261019.panel.tsv", "2026-10-19", PANEL_RELEASE)
    (clean / "erepo_classifications.tsv").write_text(
        "erepo_uuid\tgene\tclinvar_id\tcaid\thgvs_g\ttranscript_hgvs\tassertion\tclassification\t"
        "approval_date\tpublished_date\texpert_panel\tmet_codes\tlink_method\n"
    )
    app.config["DATA_CLEAN_DIR"] = str(clean)
    app.config["DATA_CURATED_DIR"] = str(CURATED)
    return clean


def ok(result):
    assert result.exit_code == 0, result.output
    return result.output


def test_show_config_and_init_db(runner):
    assert "THERAPY_REFERENCE_DATE" in ok(runner.invoke(args=["show-config"]))
    assert "Database ready" in ok(runner.invoke(args=["init-db"]))


def test_create_user(runner, session):
    out = ok(
        runner.invoke(args=["create-user", "dr", "--role", "clinician", "--password", "pw-123456"])
    )
    assert "Created clinician 'dr'" in out
    assert session.scalar(select(User).where(User.username == "dr")).check_password("pw-123456")
    again = runner.invoke(args=["create-user", "dr", "--role", "clinician", "--password", "x"])
    assert again.exit_code != 0 and "already exists" in again.output


def test_import_commands(runner, session, clean_dir):
    out = ok(runner.invoke(args=["import-release", str(clean_dir / "clinvar_20230107.panel.tsv")]))
    assert "Imported ClinVar 2023-01-07: 1 records (current)" in out
    dup = runner.invoke(args=["import-release", str(clean_dir / "clinvar_20230107.panel.tsv")])
    assert dup.exit_code != 0 and "already imported" in dup.output
    assert "0 added" in ok(
        runner.invoke(args=["import-expert", str(clean_dir / "erepo_classifications.tsv")])
    )
    assert "Rules: 6 added" in ok(
        runner.invoke(args=["import-rules", str(CURATED / "rules_v1.yaml")])
    )
    assert "Curated flags added: 54" in ok(
        runner.invoke(args=["import-flags", str(CURATED / "activating_variants.tsv")])
    )

    bad = clean_dir / "bad.yaml"
    bad.write_text("- {code: D9, version: 1}\n")
    res = runner.invoke(args=["import-rules", str(bad)])
    assert res.exit_code != 0 and "missing" in res.output


def test_load_knowledge_list_and_diff(runner, session, clean_dir):
    out = ok(runner.invoke(args=["load-knowledge"]))
    assert "ClinVar 2023-01-07: 1 records (current)" in out and "rules v1: 6 added" in out
    assert "skip clinvar_20230107.panel.tsv" in ok(runner.invoke(args=["load-knowledge"]))
    listing = ok(runner.invoke(args=["list-knowledge"]))
    assert "2023-01-07" in listing and "CURRENT" in listing and "D6 v1" in listing
    diff = ok(runner.invoke(args=["kb-diff", "2023-01-07", "2026-10-19"]))
    assert "upgrade" in diff and "1 variants in both releases" in diff
    missing = runner.invoke(args=["kb-diff", "2023-01-07", "2099-01-01"])
    assert missing.exit_code != 0 and "not imported" in missing.output


def test_reevaluate_apply_rule_and_runs(runner, session, clean_dir):
    ok(runner.invoke(args=["load-knowledge"]))
    out = ok(runner.invoke(args=["reevaluate", "2026-10-19", "--mode", "full"]))
    assert "(release, full): 2023-01-07 -> 2026-10-19" in out
    assert session.scalar(select(KbRelease).where(KbRelease.is_current)).version == "2026-10-19"
    bad = runner.invoke(args=["reevaluate", "2099-01-01"])
    assert bad.exit_code != 0 and "not imported" in bad.output

    ok(runner.invoke(args=["import-rules", str(CURATED / "rules_v2.yaml")]))
    assert "(rule, change_driven): D4 v1 -> D4 v2" in ok(
        runner.invoke(args=["apply-rule", "D4", "2"])
    )
    assert session.scalar(select(Rule).where(Rule.code == "D4", Rule.is_current)).version == 2
    again = runner.invoke(args=["apply-rule", "D4", "2"])
    assert again.exit_code != 0 and "already current" in again.output
    assert "Run " in ok(runner.invoke(args=["runs", "--limit", "5"]))


def test_seed_commands_need_knowledge(runner, app, tmp_path):
    app.config["DATA_CURATED_DIR"] = str(CURATED)
    res = runner.invoke(args=["seed-cohort", "--cohort", str(COHORT)])
    assert res.exit_code != 0 and "no current ClinVar release" in res.output
    app.config["DATA_CLEAN_DIR"] = str(tmp_path)
    res = runner.invoke(args=["seed-synthea", "--cohort", str(COHORT)])
    assert res.exit_code != 0 and "patients.csv missing" in res.output


def test_seed_cohort_command(runner, session, clean_dir):
    ok(runner.invoke(args=["load-knowledge"]))
    out = ok(runner.invoke(args=["seed-cohort", "--cohort", str(COHORT)]))
    assert "Patients: 33 created" in out and "0 VCFs rejected" in out
    assert "33 already present" in ok(runner.invoke(args=["seed-cohort", "--cohort", str(COHORT)]))
    assert db.session.scalar(select(func.count()).select_from(User)) == 0
