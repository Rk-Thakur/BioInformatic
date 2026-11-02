"""Load and validate treatment-rule YAML files (paper Table III).

Rules are data, not code: a typo in a rule file must fail loudly at import time,
never silently change which patients get alerts.
"""

import datetime as dt
import re

import yaml

from app.domain import CLASSIFICATIONS, PANEL_GENES
from app.models.base import DRUG_CLASSES, SEVERITIES

REQUIRED = {"code", "version", "genes", "genetic", "severity", "recommendation", "source"}
OPTIONAL = {"therapy", "extra", "effective_from"}
GENETIC_KEYS = {"classes", "flag"}
THERAPY_KEYS = {"any_of", "none_of"}
EXTRA_KEYS = {"age_at_diagnosis_months_lt"}
FLAGS = {"activating"}


class RuleValidationError(ValueError):
    pass


def _fail(rule, msg):
    label = (
        f"{rule.get('code', '?')} v{rule.get('version', '?')}" if isinstance(rule, dict) else "?"
    )
    raise RuleValidationError(f"rule {label}: {msg}")


def validate_rule(rule):
    """Return a normalized copy of one rule dict, or raise RuleValidationError."""
    if not isinstance(rule, dict):
        _fail(rule, "must be a mapping")
    missing = REQUIRED - rule.keys()
    if missing:
        _fail(rule, f"missing {sorted(missing)}")
    unknown = rule.keys() - REQUIRED - OPTIONAL
    if unknown:
        _fail(rule, f"unknown keys {sorted(unknown)}")

    if not re.fullmatch(r"D\d+", str(rule["code"])):
        _fail(rule, "code must look like D1, D2, ...")
    if not isinstance(rule["version"], int) or isinstance(rule["version"], bool):
        _fail(rule, "version must be an integer")
    if rule["version"] < 1:
        _fail(rule, "version must be >= 1")

    genes = rule["genes"]
    if not isinstance(genes, list) or not genes or set(genes) - set(PANEL_GENES):
        _fail(rule, f"genes must be a non-empty list of {PANEL_GENES}")

    genetic = rule["genetic"]
    if not isinstance(genetic, dict) or genetic.keys() - GENETIC_KEYS:
        _fail(rule, f"genetic may only contain {sorted(GENETIC_KEYS)}")
    classes = genetic.get("classes")
    if not isinstance(classes, list) or not classes or set(classes) - set(CLASSIFICATIONS):
        _fail(rule, f"genetic.classes must be a non-empty list of {CLASSIFICATIONS}")
    if "flag" in genetic and genetic["flag"] not in FLAGS:
        _fail(rule, f"genetic.flag must be one of {sorted(FLAGS)}")

    therapy = rule.get("therapy")
    if therapy is not None:
        if not isinstance(therapy, dict) or not therapy or therapy.keys() - THERAPY_KEYS:
            _fail(rule, f"therapy must be null or contain {sorted(THERAPY_KEYS)}")
        for key, values in therapy.items():
            if not isinstance(values, list) or not values or set(values) - set(DRUG_CLASSES):
                _fail(rule, f"therapy.{key} must be a non-empty list of {DRUG_CLASSES}")
        if set(therapy.get("any_of", [])) & set(therapy.get("none_of", [])):
            _fail(rule, "therapy.any_of and therapy.none_of overlap")

    extra = rule.get("extra")
    if extra is not None:
        if not isinstance(extra, dict) or not extra or extra.keys() - EXTRA_KEYS:
            _fail(rule, f"extra must be null or contain {sorted(EXTRA_KEYS)}")
        limit = extra.get("age_at_diagnosis_months_lt")
        if limit is not None and (not isinstance(limit, int | float) or limit <= 0):
            _fail(rule, "extra.age_at_diagnosis_months_lt must be a positive number")

    if rule["severity"] not in SEVERITIES:
        _fail(rule, f"severity must be one of {SEVERITIES}")
    for key in ("recommendation", "source"):
        if not isinstance(rule[key], str) or not rule[key].strip():
            _fail(rule, f"{key} must be non-empty text")

    effective = rule.get("effective_from", dt.date(2021, 1, 1))
    if isinstance(effective, str):
        try:
            effective = dt.date.fromisoformat(effective)
        except ValueError:
            _fail(rule, "effective_from must be YYYY-MM-DD")
    if not isinstance(effective, dt.date):
        _fail(rule, "effective_from must be a date")

    return {
        "code": rule["code"],
        "version": rule["version"],
        "genes": list(genes),
        "genetic": dict(genetic),
        "therapy": dict(therapy) if therapy else None,
        "extra": dict(extra) if extra else None,
        "severity": rule["severity"],
        "recommendation": " ".join(rule["recommendation"].split()),
        "source": rule["source"].strip(),
        "effective_from": effective,
    }


def load_rules_file(path):
    """Parse and validate a rules YAML file; returns a list of normalized rule dicts."""
    with open(path) as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, list) or not data:
        raise RuleValidationError(f"{path}: expected a non-empty list of rules")
    rules = [validate_rule(r) for r in data]
    seen = set()
    for r in rules:
        key = (r["code"], r["version"])
        if key in seen:
            raise RuleValidationError(f"{path}: {r['code']} v{r['version']} appears twice")
        seen.add(key)
    return rules
