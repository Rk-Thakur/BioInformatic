"""Stage 7: genetic risk for people without diabetes (DD42, DD45).

The levels are data (`data/curated/risk_levels_v1.yaml`), validated here as strictly as
the treatment rules: every panel gene x classification must map to exactly one level.

Each variant of a person without diabetes that maps to a level other than "normal"
becomes one finding, which the rule engine returns as an outcome with rule code G1..G5.
So risk findings are stored, shown, re-evaluated, prioritized and superseded exactly
like treatment alerts:

    high      -> alert, severity high
    mild      -> alert, severity low
    uncertain -> info note
    normal    -> no finding

A person's level is the most serious open finding; no finding means "normal".
"""

import datetime as dt
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.domain import CLASSIFICATIONS, PANEL_GENES

LEVELS = ("high", "mild", "uncertain", "normal")
OUTCOME = {  # level -> (kind, severity); "normal" makes no outcome
    "high": ("alert", "high"),
    "mild": ("alert", "low"),
    "uncertain": ("info_note", "info"),
}
CODE = re.compile(r"G\d+")


class RiskLevelsError(ValueError):
    pass


@dataclass(frozen=True)
class RiskAssignment:
    code: str
    genes: tuple
    classes: tuple
    level: str
    advice: str
    source: str
    pregnancy_note: tuple = ()  # ((gene, note), ...)

    def note_for(self, gene):
        return dict(self.pregnancy_note).get(gene)


@dataclass(frozen=True)
class RiskLevels:
    version: int
    effective_from: dt.date | None
    labels: tuple  # ((level, label), ...)
    precedence: tuple
    assignments: tuple
    table: tuple  # (((gene, class), assignment), ...) for fast lookup

    def label(self, level):
        return dict(self.labels)[level]

    def lookup(self, gene, classification):
        return dict(self.table).get((gene, classification))

    def changes_level(self, gene, old, new):
        """Can a change old -> new in `gene` change a finding? (DD23 for risk)"""
        a, b = self.lookup(gene, old), self.lookup(gene, new)
        return (a.level if a else "normal") != (b.level if b else "normal")


def _fail(msg):
    raise RiskLevelsError(f"risk levels: {msg}")


def parse_risk_levels(data):
    """Validate the parsed YAML and return RiskLevels, or raise RiskLevelsError."""
    if not isinstance(data, dict):
        _fail("must be a mapping")
    if not isinstance(data.get("version"), int) or isinstance(data["version"], bool):
        _fail("version must be an integer")
    levels = data.get("levels")
    if not isinstance(levels, dict) or set(levels) != set(LEVELS):
        _fail(f"levels must define exactly {LEVELS}")
    precedence = data.get("precedence")
    if not isinstance(precedence, list) or sorted(precedence) != sorted(LEVELS):
        _fail(f"precedence must list each of {LEVELS} once")
    if precedence[-1] != "normal":
        _fail("normal must be the least serious level")

    assignments, table = [], {}
    for a in data.get("assignments") or []:
        code = str(a.get("code", "?"))
        if not CODE.fullmatch(code):
            _fail(f"{code}: code must look like G1, G2, ...")
        if code in {x.code for x in assignments}:
            _fail(f"{code}: duplicate code")
        genes, classes = a.get("genes"), a.get("classes")
        if not isinstance(genes, list) or not genes or set(genes) - set(PANEL_GENES):
            _fail(f"{code}: genes must be a non-empty list of {PANEL_GENES}")
        if not isinstance(classes, list) or not classes or set(classes) - set(CLASSIFICATIONS):
            _fail(f"{code}: classes must be a non-empty list of {CLASSIFICATIONS}")
        if a.get("level") not in LEVELS:
            _fail(f"{code}: level must be one of {LEVELS}")
        for key in ("advice", "source"):
            if not isinstance(a.get(key), str) or not a[key].strip():
                _fail(f"{code}: {key} is required")
        notes = a.get("pregnancy_note") or {}
        if not isinstance(notes, dict) or set(notes) - set(genes):
            _fail(f"{code}: pregnancy_note must map genes of this assignment to text")
        item = RiskAssignment(
            code,
            tuple(genes),
            tuple(classes),
            a["level"],
            " ".join(a["advice"].split()),
            a["source"],
            tuple((g, " ".join(n.split())) for g, n in sorted(notes.items())),
        )
        assignments.append(item)
        for g in genes:
            for c in classes:
                if (g, c) in table:
                    _fail(f"{g} {c} is assigned twice ({table[(g, c)].code}, {code})")
                table[(g, c)] = item
    missing = [(g, c) for g in PANEL_GENES for c in CLASSIFICATIONS if (g, c) not in table]
    if missing:
        _fail(f"no level for {missing[:5]}{' ...' if len(missing) > 5 else ''}")
    eff = data.get("effective_from")
    return RiskLevels(
        version=data["version"],
        effective_from=eff if isinstance(eff, dt.date) else None,
        labels=tuple((lv, levels[lv]["label"]) for lv in LEVELS),
        precedence=tuple(precedence),
        assignments=tuple(assignments),
        table=tuple(table.items()),
    )


@lru_cache(maxsize=8)
def load_risk_levels(path):
    return parse_risk_levels(yaml.safe_load(Path(path).read_text()))


def current_risk_levels():
    """The risk levels in force (config RISK_LEVELS_FILE inside DATA_CURATED_DIR)."""
    from flask import current_app

    cfg = current_app.config
    return load_risk_levels(str(Path(cfg["DATA_CURATED_DIR"]) / cfg["RISK_LEVELS_FILE"]))


def risk_findings(sex, variants, risk):
    """[(variant, assignment, kind, severity, message)] for one person without diabetes.

    variants: VariantFacts (gene, classification, label). Pure function.
    """
    out = []
    for v in variants:
        a = risk.lookup(v.gene, v.classification)
        if a is None or a.level == "normal":
            continue
        kind, severity = OUTCOME[a.level]
        message = f"{risk.label(a.level)}: {v.label} is {v.classification}. {a.advice}"
        note = a.note_for(v.gene)
        if note and sex == "F":
            message = f"{message} {note}"
        out.append((v, a, kind, severity, message))
    return out


def person_level(codes, risk):
    """Overall level from the codes of a person's open findings ('normal' if none)."""
    by_code = {a.code: a.level for a in risk.assignments}
    levels = {by_code[c] for c in codes if c in by_code}
    return next((lv for lv in risk.precedence if lv in levels), "normal")
