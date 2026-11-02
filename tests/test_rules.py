import copy
from pathlib import Path

import pytest

from app.services.rules import RuleValidationError, load_rules_file, validate_rule

CURATED = Path(__file__).resolve().parent.parent / "data" / "curated"

GOOD = {
    "code": "D2",
    "version": 1,
    "genes": ["HNF1A"],
    "genetic": {"classes": ["P", "LP", "P/LP"]},
    "therapy": {"any_of": ["insulin", "metformin"], "none_of": ["sulfonylurea"]},
    "severity": "high",
    "recommendation": "Consider   low-dose\n sulfonylurea.",
    "source": "[1, 7]",
    "effective_from": "2021-01-01",
}


def with_(**changes):
    r = copy.deepcopy(GOOD)
    for k, v in changes.items():
        if v is ...:
            r.pop(k)
        else:
            r[k] = v
    return r


def test_rules_v1_match_paper_table_iii():
    rules = {r["code"]: r for r in load_rules_file(CURATED / "rules_v1.yaml")}
    assert sorted(rules) == ["D1", "D2", "D3", "D4", "D5", "D6"]
    assert {c: r["severity"] for c, r in rules.items()} == {
        "D1": "medium",
        "D2": "high",
        "D3": "medium",
        "D4": "high",
        "D5": "low",
        "D6": "info",
    }
    assert rules["D4"]["genes"] == ["KCNJ11"]  # v1 covers KCNJ11 only (paper §5.1)
    assert rules["D4"]["genetic"]["flag"] == "activating"
    assert rules["D4"]["extra"] == {"age_at_diagnosis_months_lt": 6}  # DD7
    assert len(rules["D5"]["genes"]) == 7
    assert rules["D6"]["genetic"]["classes"] == ["VUS"]
    assert rules["D2"]["therapy"]["none_of"] == ["sulfonylurea"]


def test_rules_v2_extends_d4_to_abcc8():
    (d4,) = load_rules_file(CURATED / "rules_v2.yaml")
    assert (d4["code"], d4["version"]) == ("D4", 2)
    assert d4["genes"] == ["KCNJ11", "ABCC8"]


def test_validate_normalizes():
    r = validate_rule(GOOD)
    assert r["recommendation"] == "Consider low-dose sulfonylurea."
    assert str(r["effective_from"]) == "2021-01-01"


@pytest.mark.parametrize(
    "bad, message",
    [
        (with_(code="X1"), "code"),
        (with_(version="1"), "integer"),
        (with_(version=0), ">= 1"),
        (with_(genes=["BRCA1"]), "genes"),
        (with_(genes=[]), "genes"),
        (with_(genetic={"classes": ["Pathogenic"]}), "classes"),
        (with_(genetic={"classes": ["P"], "flag": "gain"}), "flag"),
        (with_(therapy={"any_of": ["aspirin"]}), "therapy"),
        (with_(therapy={"any_of": ["insulin"], "none_of": ["insulin"]}), "overlap"),
        (with_(extra={"age_lt": 6}), "extra"),
        (with_(severity="urgent"), "severity"),
        (with_(recommendation=" "), "recommendation"),
        (with_(effective_from="01/01/2021"), "effective_from"),
        (with_(severity=...), "missing"),
        (with_(colour="red"), "unknown"),
    ],
)
def test_validation_errors(bad, message):
    with pytest.raises(RuleValidationError, match=message):
        validate_rule(bad)


def test_duplicate_version_in_file(tmp_path):
    f = tmp_path / "r.yaml"
    f.write_text(
        "- {code: D6, version: 1, genes: [GCK], genetic: {classes: [VUS]}, severity: info, recommendation: a, source: b}\n"
        "- {code: D6, version: 1, genes: [GCK], genetic: {classes: [VUS]}, severity: info, recommendation: a, source: b}\n"
    )
    with pytest.raises(RuleValidationError, match="twice"):
        load_rules_file(f)
