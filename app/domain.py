"""Pure domain rules shared by the cleaning scripts and the application.

No Flask, no database: plain functions with unit tests (tests/test_domain.py).
"""

import re

PANEL_GENES = ("GCK", "HNF1A", "HNF4A", "HNF1B", "KCNJ11", "ABCC8", "INS")

# Normalized classification codes.
P, LP, P_LP = "P", "LP", "P/LP"
VUS = "VUS"
LB, B, B_LB = "LB", "B", "B/LB"
CONFLICT = "CONFLICT"
OTHER = "OTHER"  # risk factor, drug response, not provided, ...
NOT_CLASSIFIED = "NC"  # variant key absent from the release (paper §4.2: not "VUS")

ACTIONABLE = frozenset({P, LP, P_LP})
CLASSIFICATIONS = (P, LP, P_LP, VUS, LB, B, B_LB, CONFLICT, OTHER, NOT_CLASSIFIED)

_TIER_TERMS = {
    "pathogenic": P,
    "likely_pathogenic": LP,
    "uncertain_significance": VUS,
    "likely_benign": LB,
    "benign": B,
}
# Combinations ClinVar writes as "A/B" when submitters disagree within one band.
_TIER_COMBOS = {
    frozenset({P}): P,
    frozenset({LP}): LP,
    frozenset({P, LP}): P_LP,
    frozenset({VUS}): VUS,
    frozenset({LB}): LB,
    frozenset({B}): B,
    frozenset({B, LB}): B_LB,
}


def normalize_clnsig(raw):
    """Map a raw ClinVar CLNSIG value to one normalized code (plan §2a, C2/C3).

    - Secondary values after "|" or "," are ignored ("Pathogenic|risk_factor" -> P,
      "Likely_pathogenic,_low_penetrance" -> LP).
    - Risk-allele qualifiers are dropped from "/" combinations
      ("Pathogenic/Likely_risk_allele" -> P, "Uncertain_significance/Uncertain_risk_allele" -> VUS).
    - Both spellings of the conflicting term map to CONFLICT (ClinVar renamed it in 2024).
    - Anything without an ACMG/AMP tier (risk_factor, drug_response, not_provided, ...) -> OTHER.
    """
    if not raw:
        return OTHER
    primary = re.split(r"[|,]", raw)[0].strip().strip("_").lower()
    if primary.startswith("conflicting_"):
        return CONFLICT
    tiers = frozenset(_TIER_TERMS[t] for t in primary.split("/") if t in _TIER_TERMS)
    return _TIER_COMBOS.get(tiers, OTHER)


def band(classification):
    return "actionable" if classification in ACTIONABLE else "non-actionable"


def change_category(old, new):
    """Paper Table IV: upgrade, downgrade, within_actionable, within_nonactionable, unchanged."""
    if old == new:
        return "unchanged"
    old_band, new_band = band(old), band(new)
    if old_band != new_band:
        return "upgrade" if new_band == "actionable" else "downgrade"
    return "within_actionable" if old_band == "actionable" else "within_nonactionable"


_STARS = {
    "practice_guideline": 4,
    "reviewed_by_expert_panel": 3,
    "criteria_provided,_multiple_submitters,_no_conflicts": 2,
    "criteria_provided,_conflicting_classifications": 1,
    "criteria_provided,_conflicting_interpretations": 1,
    "criteria_provided,_single_submitter": 1,
}


def review_stars(review_status):
    """ClinVar gold stars (0-4) for a CLNREVSTAT value."""
    return _STARS.get((review_status or "").strip(), 0)


def is_expert_reviewed(review_status):
    return review_stars(review_status) >= 3


_ASSERTIONS = {
    "pathogenic": P,
    "likely pathogenic": LP,
    "uncertain significance": VUS,
    "likely benign": LB,
    "benign": B,
}


def normalize_assertion(text):
    """Map a ClinGen eRepo assertion ("Likely Pathogenic") to a code (C10)."""
    try:
        return _ASSERTIONS[text.strip().lower()]
    except (AttributeError, KeyError):
        raise ValueError(f"unknown ClinGen assertion: {text!r}") from None


def expert_as_of(versions, as_of):
    """Latest expert-panel version approved on or before `as_of` (DD6, C9).

    versions: iterable of objects/dicts with an ISO `approval_date`.
    Returns None when no version had been approved by that date, so a 2026
    expert opinion never leaks into a 2023 ClinVar release.
    """

    def date_of(v):
        return v["approval_date"] if isinstance(v, dict) else v.approval_date

    eligible = [v for v in versions if date_of(v) <= as_of]
    return max(eligible, key=date_of) if eligible else None
