"""F2: classify variant keys against one ClinVar release (paper §4.2).

Exact key match (chrom, pos, ref, alt). A ClinGen expert-panel classification takes
precedence, but only if it was approved on or before the release date (DD6), so an
old release is never "improved" with knowledge from the future. A key that is not in
the release is "not classified" (NC), never VUS.
"""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select

from app.domain import NOT_CLASSIFIED, band, expert_as_of
from app.models import ExpertClassification, KnownVariant


@dataclass(frozen=True)
class Classification:
    classification: str  # P, LP, P/LP, VUS, LB, B, B/LB, CONFLICT, OTHER, NC
    source: str  # "expert_panel" | "clinvar" | "none"
    clinvar_classification: str  # ClinVar's own aggregate class (NC if absent)
    known_variant_id: int | None = None
    expert_id: int | None = None
    clinvar_id: str | None = None
    review_status: str | None = None

    @property
    def band(self):
        return band(self.classification)

    @property
    def actionable(self):
        return self.band == "actionable"


UNCLASSIFIED = Classification(NOT_CLASSIFIED, "none", NOT_CLASSIFIED)


def classify_keys(session, keys, release):
    """Classify many keys at once. Returns {key: Classification} for every key given."""
    keys = {normalize_key(k) for k in keys}
    if not keys:
        return {}
    by_chrom = defaultdict(set)
    for c, p, _, _ in keys:
        by_chrom[c].add(p)

    known = {}
    for chrom, positions in by_chrom.items():
        positions = sorted(positions)
        for i in range(0, len(positions), 500):  # stay under SQLite's parameter limit
            rows = session.scalars(
                select(KnownVariant).where(
                    KnownVariant.release_id == release.id,
                    KnownVariant.chrom == chrom,
                    KnownVariant.pos.in_(positions[i : i + 500]),
                )
            )
            for kv in rows:
                if kv.key in keys:
                    known[kv.key] = kv

    experts = defaultdict(list)
    ids = sorted({kv.clinvar_id for kv in known.values()})
    for i in range(0, len(ids), 500):
        for ec in session.scalars(
            select(ExpertClassification).where(
                ExpertClassification.clinvar_id.in_(ids[i : i + 500])
            )
        ):
            experts[ec.clinvar_id].append(ec)

    result = {}
    for key in keys:
        kv = known.get(key)
        if kv is None:
            result[key] = UNCLASSIFIED
            continue
        expert = expert_as_of(experts.get(kv.clinvar_id, []), release.release_date)
        if expert is not None:
            result[key] = Classification(
                expert.classification,
                "expert_panel",
                kv.classification,
                kv.id,
                expert.id,
                kv.clinvar_id,
                kv.review_status,
            )
        else:
            result[key] = Classification(
                kv.classification,
                "clinvar",
                kv.classification,
                kv.id,
                None,
                kv.clinvar_id,
                kv.review_status,
            )
    return result


def normalize_key(key):
    chrom, pos, ref, alt = key
    return (str(chrom), int(pos), ref, alt)


def classify(session, key, release):
    key = normalize_key(key)
    return classify_keys(session, [key], release)[key]
