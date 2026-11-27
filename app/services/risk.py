"""Stage 7: genetic risk for people without diabetes (DD42, DD45, DD47).

The levels are data (`data/curated/risk_levels_vN.yaml`), validated here as strictly as
the treatment rules: every panel gene x classification must map to exactly one level.
They are versioned in the database (RiskLevelSet): one version is current, later ones
wait as pending until `flask apply-risk-levels` re-evaluates the people they affect.

Optional frequency filter (v2): a variant whose population allele frequency is at or
above `max_allele_frequency` is common, so it cannot raise anyone's risk (ACMG/AMP BA1);
it applies to the levels listed in `applies_to` (normally only "uncertain").

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
    meanings: tuple  # ((level, one-sentence meaning), ...)
    precedence: tuple
    assignments: tuple
    table: tuple  # (((gene, class), assignment), ...) for fast lookup
    max_allele_frequency: float | None = None  # frequency filter (None = off)
    frequency_applies_to: tuple = ()
    frequency_source: str = ""

    def is_common(self, assignment, allele_frequency):
        """True when the frequency filter turns this finding off (BA1)."""
        return (
            self.max_allele_frequency is not None
            and allele_frequency is not None
            and allele_frequency >= self.max_allele_frequency
            and assignment.level in self.frequency_applies_to
        )

    def finding(self, gene, classification, allele_frequency=None):
        """The assignment a variant produces a finding from, or None (normal)."""
        a = self.lookup(gene, classification)
        if a is None or a.level == "normal" or self.is_common(a, allele_frequency):
            return None
        return a

    def label(self, level):
        return dict(self.labels)[level]

    def meaning(self, level):
        return dict(self.meanings)[level]

    def assignment(self, code):
        return next((a for a in self.assignments if a.code == code), None)

    def lookup(self, gene, classification):
        return dict(self.table).get((gene, classification))

    def changes_level(self, gene, old, new, allele_frequency=None):
        """Can a change old -> new in `gene` change a finding? (DD23 for risk)"""
        a = self.finding(gene, old, allele_frequency)
        b = self.finding(gene, new, allele_frequency)
        return (a.code if a else None) != (b.code if b else None)


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
    for lv in LEVELS:
        if not isinstance(levels[lv], dict) or not str(levels[lv].get("label", "")).strip():
            _fail(f"level {lv} needs a label")

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
    freq = data.get("frequency_filter")
    threshold, applies, fsource = None, (), ""
    if freq is not None:
        if not isinstance(freq, dict) or set(freq) - {
            "max_allele_frequency",
            "applies_to",
            "source",
        }:
            _fail("frequency_filter may only contain max_allele_frequency, applies_to, source")
        threshold = freq.get("max_allele_frequency")
        if not isinstance(threshold, int | float) or isinstance(threshold, bool):
            _fail("frequency_filter.max_allele_frequency must be a number")
        if not 0 < threshold <= 1:
            _fail("frequency_filter.max_allele_frequency must be in (0, 1]")
        applies = freq.get("applies_to")
        if not isinstance(applies, list) or not applies or set(applies) - set(LEVELS[:-1]):
            _fail(f"frequency_filter.applies_to must list levels from {LEVELS[:-1]}")
        fsource = str(freq.get("source", "")).strip()
        if not fsource:
            _fail("frequency_filter.source is required")
        threshold, applies = float(threshold), tuple(applies)
    eff = data.get("effective_from")
    return RiskLevels(
        version=data["version"],
        effective_from=eff if isinstance(eff, dt.date) else None,
        labels=tuple((lv, levels[lv]["label"]) for lv in LEVELS),
        meanings=tuple((lv, " ".join(str(levels[lv].get("meaning", "")).split())) for lv in LEVELS),
        precedence=tuple(precedence),
        assignments=tuple(assignments),
        table=tuple(table.items()),
        max_allele_frequency=threshold,
        frequency_applies_to=applies,
        frequency_source=fsource,
    )


@lru_cache(maxsize=16)
def parse_risk_levels_text(text):
    return parse_risk_levels(yaml.safe_load(text))


def load_risk_levels(path):
    return parse_risk_levels_text(Path(path).read_text())


def current_risk_levels():
    """The risk levels in force: the current RiskLevelSet in the database, or, when
    none has been imported yet (e.g. a bare test database), the file named by config
    RISK_LEVELS_FILE in DATA_CURATED_DIR."""
    from flask import current_app
    from sqlalchemy import select

    from app.extensions import db
    from app.models import RiskLevelSet

    content = db.session.scalar(select(RiskLevelSet.content).where(RiskLevelSet.is_current))
    if content is not None:
        return parse_risk_levels_text(content)
    cfg = current_app.config
    return load_risk_levels(str(Path(cfg["DATA_CURATED_DIR"]) / cfg["RISK_LEVELS_FILE"]))


def risk_findings(sex, variants, risk):
    """[(variant, assignment, kind, severity, message)] for one person without diabetes.

    variants: VariantFacts (gene, classification, frequency, label). Pure function.
    """
    out = []
    for v in variants:
        a = risk.finding(v.gene, v.classification, getattr(v, "frequency", None))
        if a is None:
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
