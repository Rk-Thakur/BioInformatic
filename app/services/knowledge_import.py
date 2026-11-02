"""Load the cleaned knowledge (Stage 1a output) into the database.

Inputs are the files written by `python -m scripts.clean.run_all` (data/clean/) and the
hand-curated files in data/curated/. Imports are append-only: a release or rule
version that already exists is never overwritten.
"""

import csv
import datetime as dt
import hashlib
import re
from collections import Counter
from pathlib import Path

from sqlalchemy import func, insert, select
from sqlalchemy.orm import aliased

from app.domain import CLASSIFICATIONS, PANEL_GENES, change_category
from app.models import (
    CuratedFlag,
    ExpertClassification,
    GeneTranscript,
    KbRelease,
    KnownVariant,
    Rule,
    audit,
)
from app.services.rules import load_rules_file

BATCH = 2_000


class KnowledgeImportError(Exception):
    """An input file cannot be imported as-is."""


def _read_tsv(path):
    """Return (comment lines, rows) for a TSV with optional leading '# ...' comments."""
    comments, body = [], []
    with open(path, newline="") as fh:
        for line in fh:
            (comments if line.startswith("#") else body).append(line)
    rows = list(csv.DictReader(body, delimiter="\t"))
    return [c[1:].strip() for c in comments], rows


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _bool(v):
    return str(v).strip() in ("1", "true", "True")


# --------------------------------------------------------------------------- releases


def import_release(session, path, *, make_current=None):
    """Import one cleaned ClinVar release (data/clean/clinvar_YYYYMMDD.panel.tsv).

    make_current: True / False, or None = only if no release is current yet. Later
    releases normally become current through re-evaluation (Stage 3), not here.
    """
    path = Path(path)
    comments, rows = _read_tsv(path)
    version = next(
        (m.group(1) for c in comments if (m := re.fullmatch(r"ClinVar release (\S+)", c))), None
    )
    if version is None:
        m = re.search(r"clinvar_(\d{4})(\d{2})(\d{2})", path.name)
        if not m:
            raise KnowledgeImportError(f"{path}: cannot tell the release date")
        version = "-".join(m.groups())
    release_date = dt.date.fromisoformat(version)

    if session.scalar(select(KbRelease).where(KbRelease.version == version)):
        raise KnowledgeImportError(f"release {version} is already imported")
    if not rows:
        raise KnowledgeImportError(f"{path}: no records")

    bad = Counter()
    for r in rows:
        if r["gene"] not in PANEL_GENES:
            bad["gene"] += 1
        if r["classification"] not in CLASSIFICATIONS:
            bad["classification"] += 1
    if bad:
        raise KnowledgeImportError(f"{path}: invalid values {dict(bad)}; re-run the cleaning step")

    if make_current is None:
        make_current = session.scalar(select(KbRelease).where(KbRelease.is_current)) is None
    if make_current:
        for current in session.scalars(select(KbRelease).where(KbRelease.is_current)):
            current.is_current = False
        session.flush()

    release = KbRelease(
        version=version,
        release_date=release_date,
        source_file=path.name,
        source_sha256=_sha256(path),
        record_count=len(rows),
        is_current=make_current,
    )
    session.add(release)
    session.flush()

    batch = []
    for r in rows:
        batch.append(
            {
                "release_id": release.id,
                "chrom": r["chrom"],
                "pos": int(r["pos"]),
                "ref": r["ref"],
                "alt": r["alt"],
                "gene": r["gene"],
                "gene_method": r["gene_method"],
                "clinvar_id": r["clinvar_id"],
                "clnsig_raw": r["clnsig_raw"],
                "classification": r["classification"],
                "review_status": r["review_status"],
                "stars": int(r["stars"]),
                "expert_reviewed": _bool(r["expert_reviewed"]),
                "variant_type": r["variant_type"],
                "hgvs_g": r["hgvs_g"],
                "disease": r["disease"],
                "long_indel": _bool(r["long_indel"]),
                "outside_gene": _bool(r["outside_gene"]),
            }
        )
        if len(batch) >= BATCH:
            session.execute(insert(KnownVariant), batch)
            batch = []
    if batch:
        session.execute(insert(KnownVariant), batch)

    audit(
        session,
        "import_release",
        "kb_release",
        release.id,
        version=version,
        records=len(rows),
        current=make_current,
        source=path.name,
    )
    return release


def current_release(session):
    return session.scalar(select(KbRelease).where(KbRelease.is_current))


# --------------------------------------------------------------------------- expert panel


def import_expert_classifications(session, path):
    """Import ClinGen eRepo classifications (data/clean/erepo_classifications.tsv).

    Rows already present (same eRepo UUID) are skipped, so re-running is safe.
    Returns (added, skipped).
    """
    _, rows = _read_tsv(path)
    existing = set(session.scalars(select(ExpertClassification.erepo_uuid)))
    new = []
    for r in rows:
        if r["erepo_uuid"] in existing:
            continue
        if r["classification"] not in CLASSIFICATIONS:
            raise KnowledgeImportError(f"{path}: invalid classification {r['classification']!r}")
        new.append(
            {
                "erepo_uuid": r["erepo_uuid"],
                "gene": r["gene"],
                "clinvar_id": r["clinvar_id"] or None,
                "caid": r["caid"] or None,
                "hgvs_g": r["hgvs_g"] or None,
                "transcript_hgvs": r["transcript_hgvs"] or None,
                "assertion": r["assertion"],
                "classification": r["classification"],
                "approval_date": dt.date.fromisoformat(r["approval_date"]),
                "published_date": dt.date.fromisoformat(r["published_date"])
                if r["published_date"]
                else None,
                "expert_panel": r["expert_panel"],
                "met_codes": r["met_codes"] or None,
                "link_method": r["link_method"],
            }
        )
    for i in range(0, len(new), BATCH):
        session.execute(insert(ExpertClassification), new[i : i + BATCH])
    audit(
        session,
        "import_expert",
        "expert_classification",
        None,
        added=len(new),
        skipped=len(rows) - len(new),
        source=Path(path).name,
    )
    return len(new), len(rows) - len(new)


# --------------------------------------------------------------------------- curated files


def import_transcripts(session, path):
    """Upsert reference transcripts (data/curated/gene_transcripts.tsv)."""
    _, rows = _read_tsv(path)
    for r in rows:
        tx = session.get(GeneTranscript, r["gene"]) or GeneTranscript(gene=r["gene"])
        tx.accession = r["accession"]
        tx.current_version = r["current_version"]
        tx.paper_version = r.get("paper_version") or None
        session.add(tx)
    return len(rows)


def import_curated_flags(session, path):
    """Import curated variant flags, e.g. activating KCNJ11/ABCC8 variants (DD8).

    TSV columns: chrom pos ref alt flag source. Existing flags are kept.
    """
    _, rows = _read_tsv(path)
    added = 0
    for r in rows:
        exists = session.scalar(
            select(CuratedFlag).filter_by(
                chrom=r["chrom"], pos=int(r["pos"]), ref=r["ref"], alt=r["alt"], flag=r["flag"]
            )
        )
        if not exists:
            session.add(
                CuratedFlag(
                    chrom=r["chrom"],
                    pos=int(r["pos"]),
                    ref=r["ref"],
                    alt=r["alt"],
                    flag=r["flag"],
                    source=r["source"],
                )
            )
            added += 1
    return added


# --------------------------------------------------------------------------- rules


def import_rules(session, path, *, make_current=None):
    """Import rule versions from a YAML file.

    A rule version that already exists must be identical (versions are immutable).
    make_current: None = a version becomes current only if its code has no current
    version yet (first import); later versions stay pending until F5 applies them.
    Returns (added, unchanged).
    """
    added = unchanged = 0
    for spec in load_rules_file(path):
        existing = session.scalar(
            select(Rule).where(Rule.code == spec["code"], Rule.version == spec["version"])
        )
        if existing:
            same = all(getattr(existing, k) == v for k, v in spec.items())
            if not same:
                raise KnowledgeImportError(
                    f"{spec['code']} v{spec['version']} already exists with different content; "
                    "rule versions are immutable, add a new version instead"
                )
            unchanged += 1
            continue

        current = session.scalar(select(Rule).where(Rule.code == spec["code"], Rule.is_current))
        activate = (current is None) if make_current is None else make_current
        if activate and current is not None:
            current.is_current = False
            session.flush()
        rule = Rule(**spec, source_file=Path(path).name, is_current=activate)
        session.add(rule)
        session.flush()
        audit(
            session,
            "import_rule",
            "rule",
            rule.id,
            code=rule.code,
            version=rule.version,
            current=activate,
        )
        added += 1
    return added, unchanged


# --------------------------------------------------------------------------- comparison


def release_diff(session, old_version, new_version):
    """Compare two imported releases over the variants present in both.

    Returns {"categories": Counter, "by_gene": {gene: Counter}, "added": n, "removed": n}.
    This is the change set Algorithm 2 works from, before it is narrowed to the
    variants that patients actually carry.
    """
    releases = {
        r.version: r
        for r in session.scalars(
            select(KbRelease).where(KbRelease.version.in_([old_version, new_version]))
        )
    }
    for v in (old_version, new_version):
        if v not in releases:
            raise KnowledgeImportError(f"release {v} is not imported")
    old_id, new_id = releases[old_version].id, releases[new_version].id

    o, n = aliased(KnownVariant), aliased(KnownVariant)
    pairs = session.execute(
        select(o.classification, n.classification, n.gene)
        .join(n, (o.chrom == n.chrom) & (o.pos == n.pos) & (o.ref == n.ref) & (o.alt == n.alt))
        .where(o.release_id == old_id, n.release_id == new_id)
    ).all()
    categories, by_gene = Counter(), {g: Counter() for g in PANEL_GENES}
    for old_cls, new_cls, gene in pairs:
        cat = change_category(old_cls, new_cls)
        categories[cat] += 1
        by_gene[gene][cat] += 1

    def count(release_id):
        return session.scalar(
            select(func.count())
            .select_from(KnownVariant)
            .where(KnownVariant.release_id == release_id)
        )

    both = len(pairs)
    return {
        "categories": categories,
        "by_gene": by_gene,
        "both": both,
        "added": count(new_id) - both,
        "removed": count(old_id) - both,
    }
