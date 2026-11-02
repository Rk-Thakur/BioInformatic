import csv
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditEvent,
    ExpertClassification,
    GeneTranscript,
    KbRelease,
    KnownVariant,
    Rule,
)
from app.services.knowledge_import import (
    KnowledgeImportError,
    current_release,
    import_curated_flags,
    import_expert_classifications,
    import_release,
    import_rules,
    import_transcripts,
    release_diff,
)

ROOT = Path(__file__).resolve().parent.parent
CURATED = ROOT / "data" / "curated"
CLEAN = ROOT / "data" / "clean"

COLS = [
    "chrom",
    "pos",
    "ref",
    "alt",
    "gene",
    "gene_method",
    "genes_listed",
    "clinvar_id",
    "clnsig_raw",
    "classification",
    "review_status",
    "stars",
    "expert_reviewed",
    "variant_type",
    "hgvs_g",
    "disease",
    "long_indel",
    "outside_gene",
]


def write_release(path, date, records):
    """records: list of (pos, gene, classification)."""
    with open(path, "w", newline="") as fh:
        fh.write(f"# ClinVar release {date}\n")
        w = csv.DictWriter(fh, COLS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for pos, gene, cls in records:
            w.writerow(
                {
                    "chrom": "12",
                    "pos": pos,
                    "ref": "C",
                    "alt": "T",
                    "gene": gene,
                    "gene_method": "single",
                    "genes_listed": gene,
                    "clinvar_id": str(pos),
                    "clnsig_raw": "x",
                    "classification": cls,
                    "review_status": "",
                    "stars": 0,
                    "expert_reviewed": 0,
                    "variant_type": "single_nucleotide_variant",
                    "hgvs_g": f"NC_000012.12:g.{pos}C>T",
                    "disease": "",
                    "long_indel": 0,
                    "outside_gene": 0,
                }
            )
    return path


@pytest.fixture()
def two_releases(tmp_path):
    old = write_release(
        tmp_path / "clinvar_20230107.panel.tsv",
        "2023-01-07",
        [
            (1, "HNF1A", "VUS"),  # -> LP upgrade
            (2, "GCK", "LP"),  # -> VUS downgrade
            (3, "HNF4A", "LP"),  # -> P within actionable
            (4, "HNF1A", "VUS"),  # -> LB within non-actionable
            (5, "HNF1A", "B"),  # unchanged
            (6, "GCK", "P"),  # removed
        ],
    )
    new = write_release(
        tmp_path / "clinvar_20261019.panel.tsv",
        "2026-10-19",
        [
            (1, "HNF1A", "LP"),
            (2, "GCK", "VUS"),
            (3, "HNF4A", "P"),
            (4, "HNF1A", "LB"),
            (5, "HNF1A", "B"),
            (7, "INS", "P"),  # added
        ],
    )
    return old, new


def test_first_release_becomes_current_later_ones_wait(session, two_releases):
    old, new = two_releases
    r1 = import_release(session, old)
    r2 = import_release(session, new)
    assert r1.is_current and not r2.is_current
    assert current_release(session).version == "2023-01-07"
    assert session.scalar(select(func.count()).select_from(KnownVariant)) == 12
    assert r1.record_count == 6 and len(r1.source_sha256) == 64


def test_make_current_switches(session, two_releases):
    old, new = two_releases
    import_release(session, old)
    import_release(session, new, make_current=True)
    assert current_release(session).version == "2026-10-19"
    assert (
        session.scalar(select(func.count()).select_from(KbRelease).where(KbRelease.is_current)) == 1
    )


def test_release_imported_once(session, two_releases):
    import_release(session, two_releases[0])
    with pytest.raises(KnowledgeImportError, match="already imported"):
        import_release(session, two_releases[0])


def test_release_rejects_uncleaned_values(session, tmp_path):
    bad = write_release(
        tmp_path / "clinvar_20230107.panel.tsv", "2023-01-07", [(1, "BRCA1", "Pathogenic")]
    )
    with pytest.raises(KnowledgeImportError, match="cleaning"):
        import_release(session, bad)


def test_release_diff(session, two_releases):
    for f in two_releases:
        import_release(session, f)
    d = release_diff(session, "2023-01-07", "2026-10-19")
    assert d["both"] == 5 and d["added"] == 1 and d["removed"] == 1
    assert d["categories"] == {
        "upgrade": 1,
        "downgrade": 1,
        "within_actionable": 1,
        "within_nonactionable": 1,
        "unchanged": 1,
    }
    assert d["by_gene"]["HNF1A"]["upgrade"] == 1 and d["by_gene"]["GCK"]["downgrade"] == 1
    with pytest.raises(KnowledgeImportError, match="not imported"):
        release_diff(session, "2023-01-07", "2099-01-01")


def test_import_rules_versions(session):
    added, unchanged = import_rules(session, CURATED / "rules_v1.yaml")
    assert (added, unchanged) == (6, 0)
    assert import_rules(session, CURATED / "rules_v1.yaml") == (0, 6)  # idempotent
    import_rules(session, CURATED / "rules_v2.yaml")
    d4 = {r.version: r for r in session.scalars(select(Rule).where(Rule.code == "D4"))}
    assert d4[1].is_current and not d4[2].is_current  # v2 waits for F5 (Algorithm 3)
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == 7


def test_rule_versions_are_immutable(session, tmp_path):
    import_rules(session, CURATED / "rules_v1.yaml")
    changed = tmp_path / "r.yaml"
    changed.write_text(
        (CURATED / "rules_v1.yaml").read_text().replace("severity: high", "severity: low", 1)
    )
    with pytest.raises(KnowledgeImportError, match="immutable"):
        import_rules(session, changed)


def test_import_expert_and_transcripts(session, tmp_path):
    f = tmp_path / "e.tsv"
    cols = [
        "erepo_uuid",
        "gene",
        "clinvar_id",
        "caid",
        "hgvs_g",
        "transcript_hgvs",
        "assertion",
        "classification",
        "approval_date",
        "published_date",
        "expert_panel",
        "met_codes",
        "link_method",
    ]
    with open(f, "w", newline="") as fh:
        w = csv.DictWriter(fh, cols, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerow(
            {
                "erepo_uuid": "u1",
                "gene": "GCK",
                "clinvar_id": "36199",
                "caid": "",
                "hgvs_g": "",
                "transcript_hgvs": "",
                "assertion": "Pathogenic",
                "classification": "P",
                "approval_date": "2022-01-01",
                "published_date": "",
                "expert_panel": "Monogenic Diabetes VCEP",
                "met_codes": "",
                "link_method": "clinvar_id",
            }
        )
    assert import_expert_classifications(session, f) == (1, 0)
    assert import_expert_classifications(session, f) == (0, 1)
    ec = session.scalar(select(ExpertClassification))
    assert ec.caid is None and str(ec.approval_date) == "2022-01-01"

    assert import_transcripts(session, CURATED / "gene_transcripts.tsv") == 3
    assert session.get(GeneTranscript, "HNF1A").current_version == "NM_000545.8"


def test_import_curated_flags(session, tmp_path):
    f = tmp_path / "flags.tsv"
    f.write_text("chrom\tpos\tref\talt\tflag\tsource\n11\t17388084\tG\tC\tactivating\tPMID:1\n")
    assert import_curated_flags(session, f) == 1
    assert import_curated_flags(session, f) == 0


def test_cli_load_knowledge_requires_cleaned_data(runner, app, tmp_path):
    app.config["DATA_CLEAN_DIR"] = str(tmp_path)
    result = runner.invoke(args=["load-knowledge"])
    assert result.exit_code != 0 and "cleaning step" in result.output


# ------------------------------------------------------------ Stage 1b exit criterion


@pytest.mark.slow
@pytest.mark.skipif(
    not (CLEAN / "clinvar_20261019.panel.tsv").exists(), reason="run the cleaning step first"
)
def test_real_releases_reproduce_quality_report(runner, session):
    """All three releases load, and the database diff matches data/clean/QUALITY_REPORT.md."""
    result = runner.invoke(args=["load-knowledge"])
    assert result.exit_code == 0, result.output
    versions = list(session.scalars(select(KbRelease.version).order_by(KbRelease.version)))
    assert versions == ["2023-01-07", "2023-12-30", "2026-10-19"]
    assert current_release(session).version == "2023-01-07"

    d = release_diff(session, "2023-01-07", "2026-10-19")
    assert d["both"] == 4378
    assert d["categories"]["upgrade"] == 184
    assert d["categories"]["downgrade"] == 71
    assert d["by_gene"]["GCK"]["upgrade"] == 107
    assert session.scalar(select(func.count()).select_from(ExpertClassification)) == 1167
    assert session.scalar(select(func.count()).select_from(Rule).where(Rule.is_current)) == 6
