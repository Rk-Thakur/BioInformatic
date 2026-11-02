"""F2: classification against one release, with expert-panel precedence (DD6)."""

import datetime as dt

from app.models import ExpertClassification
from app.services.classify import classify, classify_keys
from app.services.knowledge_import import import_release
from tests.factories import write_release


def setup_releases(session, tmp_path):
    old = import_release(
        session,
        write_release(
            tmp_path / "clinvar_20230107.panel.tsv",
            "2023-01-07",
            [
                ("12", 100, "A", "G", "HNF1A", "VUS", "1001"),
                ("12", 200, "C", "T", "HNF1A", "CONFLICT", "1002"),
            ],
        ),
    )
    new = import_release(
        session,
        write_release(
            tmp_path / "clinvar_20261019.panel.tsv",
            "2026-10-19",
            [
                ("12", 100, "A", "G", "HNF1A", "VUS", "1001"),
                ("12", 200, "C", "T", "HNF1A", "CONFLICT", "1002"),
                ("12", 300, "G", "A", "HNF1A", "P", "1003"),
            ],
        ),
    )
    # Expert panel says 1001 is LP, approved in 2024: after the old release, before the new one.
    session.add(
        ExpertClassification(
            erepo_uuid="u1",
            gene="HNF1A",
            clinvar_id="1001",
            assertion="Likely Pathogenic",
            classification="LP",
            approval_date=dt.date(2024, 5, 1),
            expert_panel="Monogenic Diabetes VCEP",
            link_method="clinvar_id",
        )
    )
    session.flush()
    return old, new


def test_clinvar_match_and_not_classified(session, tmp_path):
    _, new = setup_releases(session, tmp_path)
    c = classify(session, ("12", "300", "G", "A"), new)
    assert (c.classification, c.source, c.actionable) == ("P", "clinvar", True)
    assert c.clinvar_id == "1003"
    missing = classify(session, ("12", 999, "G", "A"), new)
    assert (missing.classification, missing.source, missing.actionable) == ("NC", "none", False)


def test_exact_key_only(session, tmp_path):
    _, new = setup_releases(session, tmp_path)
    assert classify(session, ("12", 300, "G", "T"), new).classification == "NC"  # other ALT


def test_expert_panel_only_from_its_approval_date(session, tmp_path):
    old, new = setup_releases(session, tmp_path)
    before = classify(session, ("12", 100, "A", "G"), old)
    after = classify(session, ("12", 100, "A", "G"), new)
    assert (before.classification, before.source) == ("VUS", "clinvar")  # no leak into 2023
    assert (after.classification, after.source) == ("LP", "expert_panel")
    assert after.clinvar_classification == "VUS" and after.actionable


def test_classify_many(session, tmp_path):
    _, new = setup_releases(session, tmp_path)
    keys = [("12", 100, "A", "G"), ("12", 200, "C", "T"), ("12", 300, "G", "A"), ("7", 1, "A", "C")]
    got = {k: c.classification for k, c in classify_keys(session, keys, new).items()}
    assert got == {keys[0]: "LP", keys[1]: "CONFLICT", keys[2]: "P", keys[3]: "NC"}
