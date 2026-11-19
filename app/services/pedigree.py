"""Family tree (pedigree) for the patient page (Stage 7, DD48).

Built around the patient whose result led to the relatives' tests (the proband):
parents above, the proband and siblings in the middle, children below, drawn with the
usual pedigree symbols (square = male, circle = female, filled = diabetes, slash =
deceased, arrow = proband). People nobody tested but who must exist to connect the
tree (the parents of the sibship, the other parent of the children) are drawn as
dashed placeholders.

For every ClinVar release up to the current one, each person gets
  - level:   genetic risk level (people without diabetes; "untested" without a test),
  - carrier: whether they carry a P/LP variant that the proband also carries,
so the page can replay how the family's picture changed as ClinVar changed.
Levels are computed with the risk levels in force, from the stored variants; they are
what GE-EHR would say under that release, not a record of past alerts.

Pure layout (`layout()`) is separate from the database part (`build_pedigree()`).
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select

from app.domain import ACTIONABLE, NOT_CLASSIFIED
from app.models import KbRelease, Patient, PatientVariant, VcfUpload
from app.services.classify import classify_keys
from app.services.risk import current_risk_levels, person_level, risk_findings
from app.services.rule_engine import VariantFacts

SPACING = 132  # horizontal distance between people
SHORT = {  # level labels short enough to sit under a symbol
    "high": "High risk",
    "mild": "Mild",
    "uncertain": "Uncertain",
    "normal": "Normal",
    "untested": "Not tested",
}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
ROW = 150  # vertical distance between generations
R = 22  # symbol half-size
MARGIN_X = 70
TOP = 56


@dataclass
class Person:
    key: str  # unique within the tree ("p12", "ph-father", ...)
    gen: int  # -1 parents, 0 proband and siblings, 1 children
    role: str  # proband | sibling | parent | child | partner
    sex: str | None = None
    patient: Patient | None = None  # None for placeholders
    x: float = 0.0
    y: float = 0.0
    order: int = 0  # animation order
    states: dict = field(default_factory=dict)  # release version -> state dict

    @property
    def placeholder(self):
        return self.patient is None

    @property
    def shape(self):
        return {"M": "square", "F": "circle"}.get(self.sex or "", "diamond")


@dataclass
class Pedigree:
    people: list
    lines: list  # [{"d": svg path, "gen": n}]
    width: float
    height: float
    releases: list  # versions, oldest first
    release_labels: dict  # version -> "Oct 2026"
    current: str | None
    summary: dict  # version -> one-sentence change summary
    proband: Patient
    unlinked: list  # same family group, relation not recorded


# --------------------------------------------------------------------------- layout


def layout(proband, siblings, parents, children, *, partner_sex=None):
    """Place people; returns (people, lines, width, height). Pure function.

    proband/siblings/parents/children are Person objects (gen/role set by the caller).
    Missing connecting people are added as placeholders.
    """
    people, lines = [], []
    gen0 = list(siblings) + [proband]
    partner = None
    if children:
        partner = Person(
            "ph-partner",
            0,
            "partner",
            sex=partner_sex or {"M": "F", "F": "M"}.get(proband.sex or "", None),
        )
        gen0.append(partner)
    for i, p in enumerate(gen0):
        p.x = i * SPACING
    sib_xs = [p.x for p in gen0 if p is not partner]

    parent_row = list(parents)
    if siblings or parents:
        # A sibship hangs from a couple: fill in untested parents.
        have = {p.sex for p in parent_row}
        for sex, key in (("M", "ph-father"), ("F", "ph-mother")):
            if len(parent_row) >= 2:
                break
            if sex not in have:
                parent_row.append(Person(key, -1, "parent", sex=sex))
        parent_row.sort(key=lambda p: 0 if p.sex == "M" else 1)
        mid = sum(sib_xs) / len(sib_xs)
        parent_row[0].x, parent_row[1].x = mid - SPACING / 2, mid + SPACING / 2

    if children:
        mid = (proband.x + partner.x) / 2
        n = len(children)
        for i, c in enumerate(children):
            c.x = mid + (i - (n - 1) / 2) * SPACING

    rows = [r for r in (parent_row, gen0, list(children)) if r]
    everyone = parent_row + gen0 + list(children)
    shift = MARGIN_X - min(p.x for p in everyone)
    for p in everyone:
        p.x += shift
    y = {-1: TOP + R, 0: TOP + R + (ROW if parent_row else 0)}
    y[1] = y[0] + ROW
    for p in everyone:
        p.y = y[p.gen]

    def d(*pts):
        return "M" + " L".join(f"{px:.1f} {py:.1f}" for px, py in pts)

    if parent_row:
        a, b = parent_row
        lines.append({"d": d((a.x + R, a.y), (b.x - R, b.y)), "gen": 0})
        mid = (a.x + b.x) / 2
        bar = y[0] - R - 34
        sibs = [p for p in gen0 if p is not partner]
        lines.append({"d": d((mid, a.y), (mid, bar)), "gen": 1})
        lines.append(
            {"d": d((min(p.x for p in sibs), bar), (max(p.x for p in sibs), bar)), "gen": 1}
        )
        for p in sibs:
            lines.append({"d": d((p.x, bar), (p.x, p.y - R - 8)), "gen": 2})
    if children:
        lines.append({"d": d((proband.x + R, proband.y), (partner.x - R, partner.y)), "gen": 2})
        mid = (proband.x + partner.x) / 2
        bar = y[1] - R - 34
        lines.append({"d": d((mid, proband.y), (mid, bar)), "gen": 3})
        lines.append(
            {"d": d((min(c.x for c in children), bar), (max(c.x for c in children), bar)), "gen": 3}
        )
        for c in children:
            lines.append({"d": d((c.x, bar), (c.x, c.y - R - 8)), "gen": 4})

    order = sorted(everyone, key=lambda p: (p.gen, p.x))
    for i, p in enumerate(order):
        p.order = i
    width = max(p.x for p in everyone) + MARGIN_X
    height = max(p.y for p in everyone) + R + 64
    people.extend(order)
    return people, lines, width, height, len(rows)


# --------------------------------------------------------------------------- database


def _releases(session):
    current = session.scalar(select(KbRelease).where(KbRelease.is_current))
    if current is None:
        return [], None
    rel = session.scalars(
        select(KbRelease)
        .where(KbRelease.release_date <= current.release_date)
        .order_by(KbRelease.release_date)
    ).all()
    return rel, current


def build_pedigree(session, patient):
    """Pedigree for the family `patient` belongs to, or None when nobody is linked."""
    proband = patient.relative_of or patient
    relatives = list(proband.relatives)
    if not relatives:
        return None

    def person(p, gen, role):
        return Person(f"p{p.id}", gen, role, sex=p.sex, patient=p)

    by_relation = {"parent": [], "sibling": [], "child": []}
    for r in sorted(
        relatives, key=lambda r: (r.birth_date is None, r.birth_date or dt.date.min, r.id)
    ):
        by_relation.get(r.relation, by_relation["sibling"]).append(r)
    me = person(proband, 0, "proband")
    people, lines, width, height, _ = layout(
        me,
        [person(p, 0, "sibling") for p in by_relation["sibling"]],
        [person(p, -1, "parent") for p in by_relation["parent"]],
        [person(p, 1, "child") for p in by_relation["child"]],
    )

    releases, current = _releases(session)
    real = [p for p in people if not p.placeholder]
    ids = [p.patient.id for p in real]
    rows = session.execute(
        select(
            PatientVariant.id,
            PatientVariant.patient_id,
            PatientVariant.gene,
            PatientVariant.chrom,
            PatientVariant.pos,
            PatientVariant.ref,
            PatientVariant.alt,
        ).where(PatientVariant.patient_id.in_(ids))
    ).all()
    tested = set(
        session.scalars(
            select(VcfUpload.patient_id).where(
                VcfUpload.patient_id.in_(ids), VcfUpload.status == "accepted"
            )
        )
    )
    variants = {}
    for pv_id, pid, gene, *key in rows:
        variants.setdefault(pid, []).append((pv_id, gene, tuple(key)))
    keys = {k for vs in variants.values() for _, _, k in vs}
    risk = current_risk_levels()

    summary, previous, previous_date = {}, None, None
    for rel in releases:
        classes = classify_keys(session, keys, rel) if keys else {}
        family_pathogenic = {
            k for _, _, k in variants.get(proband.id, []) if classes[k].classification in ACTIONABLE
        }
        for p in real:
            pid = p.patient.id
            mine = variants.get(pid, [])
            carrier = any(k in family_pathogenic for _, _, k in mine)
            if p.patient.has_diabetes:
                level = None
            elif pid not in tested:
                level = "untested"
            else:
                facts = [
                    VariantFacts(
                        pv_id,
                        gene,
                        classes[k].classification,
                        label=f"{gene} {k[0]}:{k[1]} {k[2]}>{k[3]}",
                        frequency=classes[k].allele_frequency,
                    )
                    for pv_id, gene, k in mine
                    if classes[k].classification != NOT_CLASSIFIED
                ]
                codes = {a.code for _, a, *_ in risk_findings(p.patient.sex, facts, risk)}
                level = person_level(codes, risk)
            p.states[rel.version] = {
                "level": level,
                "label": SHORT.get(level, "Diabetes"),
                "carrier": carrier,
            }
        if previous is not None:
            moved = [
                p for p in real if p.states[rel.version]["level"] != p.states[previous]["level"]
            ]
            up = sum(
                _rank(p.states[rel.version]["level"]) < _rank(p.states[previous]["level"])
                for p in moved
            )
            summary[rel.version] = (
                f"{len(moved)} risk level{'s' if len(moved) != 1 else ''} changed since "
                f"ClinVar {_label(previous_date)}"
                + (f" ({up} up, {len(moved) - up} down)" if moved else "")
                + "."
            )
        else:
            summary[rel.version] = "The family as ClinVar saw it then."
        previous, previous_date = rel.version, rel.release_date

    unlinked = []
    if proband.family_id:
        linked = set(ids)
        unlinked = session.scalars(
            select(Patient).where(Patient.family_id == proband.family_id, Patient.id.not_in(linked))
        ).all()
    return Pedigree(
        people=people,
        lines=lines,
        width=width,
        height=height,
        releases=[r.version for r in releases],
        release_labels={r.version: _label(r.release_date) for r in releases},
        current=current.version if current else None,
        summary=summary,
        proband=proband,
        unlinked=list(unlinked),
    )


def _label(day):
    return f"{MONTHS[day.month - 1]} {day.year}"


def _rank(level):
    return {"high": 0, "mild": 1, "uncertain": 2, "normal": 3}.get(level, 4)
