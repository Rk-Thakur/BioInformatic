import pytest

from app.domain import (
    CONFLICT,
    NOT_CLASSIFIED,
    OTHER,
    band,
    change_category,
    expert_as_of,
    normalize_assertion,
    normalize_clnsig,
    review_stars,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Pathogenic", "P"),
        ("Likely_pathogenic", "LP"),
        ("Pathogenic/Likely_pathogenic", "P/LP"),
        ("Uncertain_significance", "VUS"),
        ("Likely_benign", "LB"),
        ("Benign", "B"),
        ("Benign/Likely_benign", "B/LB"),
        # C2: risk-allele qualifiers no longer hide the tier (the original bug)
        ("Pathogenic/Likely_risk_allele", "P"),
        ("Likely_pathogenic/Likely_risk_allele", "LP"),
        ("Pathogenic/Likely_pathogenic/Likely_risk_allele", "P/LP"),
        ("Uncertain_significance/Uncertain_risk_allele", "VUS"),
        # secondary values after | or , are ignored
        ("Pathogenic|risk_factor", "P"),
        ("Likely_pathogenic,_low_penetrance", "LP"),
        # C3: both spellings of the conflicting term
        ("Conflicting_interpretations_of_pathogenicity", CONFLICT),
        ("Conflicting_classifications_of_pathogenicity", CONFLICT),
        # no ACMG/AMP tier
        ("Likely_risk_allele", OTHER),
        ("Uncertain_risk_allele", OTHER),
        ("risk_factor", OTHER),
        ("drug_response", OTHER),
        ("not_provided", OTHER),
        ("association_not_found", OTHER),
        ("", OTHER),
        (None, OTHER),
    ],
)
def test_normalize_clnsig(raw, expected):
    assert normalize_clnsig(raw) == expected


def test_band():
    assert band("P") == band("LP") == band("P/LP") == "actionable"
    for cls in ("VUS", "LB", "B", "B/LB", CONFLICT, OTHER, NOT_CLASSIFIED):
        assert band(cls) == "non-actionable"


@pytest.mark.parametrize(
    "old, new, expected",
    [
        ("VUS", "LP", "upgrade"),
        (NOT_CLASSIFIED, "P", "upgrade"),  # DD4: absent -> P/LP is an upgrade
        (CONFLICT, "LP", "upgrade"),
        ("LP", "VUS", "downgrade"),
        ("P", NOT_CLASSIFIED, "downgrade"),
        ("LP", "P", "within_actionable"),
        ("VUS", "LB", "within_nonactionable"),
        ("P", "P", "unchanged"),
    ],
)
def test_change_category(old, new, expected):
    assert change_category(old, new) == expected


def test_review_stars():
    assert review_stars("practice_guideline") == 4
    assert review_stars("reviewed_by_expert_panel") == 3
    assert review_stars("criteria_provided,_multiple_submitters,_no_conflicts") == 2
    assert review_stars("criteria_provided,_single_submitter") == 1
    assert review_stars("no_assertion_criteria_provided") == 0
    assert review_stars(None) == 0


def test_normalize_assertion():
    assert normalize_assertion("Likely Pathogenic") == "LP"
    assert normalize_assertion(" uncertain significance ") == "VUS"
    with pytest.raises(ValueError):
        normalize_assertion("Pathogenic, low penetrance")


def test_expert_as_of_never_uses_future_approvals():
    versions = [
        {"approval_date": "2022-05-01", "classification": "VUS"},
        {"approval_date": "2025-03-01", "classification": "LP"},
    ]
    assert expert_as_of(versions, "2021-12-31") is None
    assert expert_as_of(versions, "2023-01-07")["classification"] == "VUS"
    assert expert_as_of(versions, "2026-10-19")["classification"] == "LP"
