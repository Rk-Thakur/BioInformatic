"""Stage 7 step 2: the healthy-relatives demo dataset (DD43)."""

import csv
from collections import Counter

import pytest

from app.services.vcf_service import parse_vcf
from scripts.build_healthy_relatives import build
from tests.factories import CURATED, ROOT

CLEAN = ROOT / "data" / "clean"
COHORT = ROOT / "ge_ehr_test_data_v2"
DATASET = ROOT / "ge_ehr_healthy_relatives"

pytestmark = pytest.mark.skipif(
    not (CLEAN / "clinvar_20261019.panel.tsv").exists(), reason="run the cleaning step first"
)


def rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("relatives")
    build(COHORT, CLEAN, CURATED / "risk_levels_v1.yaml", out)
    return out


def test_reproduces_committed_dataset(built):
    for name in ("relatives.csv", "expected_risk.csv", "manifest.json"):
        assert (built / name).read_text() == (DATASET / name).read_text(), name
    for p in rows(built / "relatives.csv"):
        rid = p["relative_id"]
        assert (built / "vcfs" / f"{rid}.vcf.gz").read_bytes() == (
            DATASET / "vcfs" / f"{rid}.vcf.gz"
        ).read_bytes(), rid


def test_people_are_healthy_relatives(built):
    people = rows(built / "relatives.csv")
    assert len(people) == 24
    assert {p["diabetes"] for p in people} == {"none"}
    assert {p["relation"] for p in people} == {"parent", "sibling", "child"}
    proband_ids = {r["patient_id"] for r in rows(COHORT / "scenarios.csv")}
    assert all(p["proband_id"] in proband_ids for p in people)
    assert all(p["background_from_proband"] != p["background_other_parent"] for p in people)


def test_family_variant_only_in_carriers(built):
    for p in rows(built / "relatives.csv"):
        chrom, pos, ref, alt = p["family_variant"].split(":")
        parsed = parse_vcf(built / "vcfs" / f"{p['relative_id']}.vcf.gz")
        keys = {c.key for c in parsed.calls}
        assert ((chrom, int(pos), ref, alt) in keys) == (p["carrier"] == "1"), p["name"]


def test_every_vcf_passes_the_upload_validator(built):
    for p in rows(built / "relatives.csv"):
        parsed = parse_vcf(built / "vcfs" / f"{p['relative_id']}.vcf.gz")
        assert parsed.sample == p["relative_id"]
        assert parsed.total_records == int(p["variants"])
        # Symbolic alleles from the 1000 Genomes backgrounds are skipped by design,
        # exactly as for the cohort patients; nothing else may be skipped.
        assert set(parsed.skipped) <= {"symbolic_alt"}, parsed.skipped


def test_expected_levels_cover_every_level_and_change(built):
    exp = rows(built / "expected_risk.csv")
    newest = Counter(e["level"] for e in exp if e["release"] == "2026-10-19")
    assert set(newest) == {"high", "mild", "uncertain", "normal"}
    by = {}
    for e in exp:
        by.setdefault(e["relative_id"], {})[e["release"]] = e["level"]
    moves = {(d["2023-01-07"], d["2026-10-19"]) for d in by.values()}
    assert {("uncertain", "high"), ("mild", "uncertain"), ("uncertain", "normal")} <= moves
    # Non-carriers never get "high" or "mild" from the family variant.
    people = {p["relative_id"]: p for p in rows(built / "relatives.csv")}
    for e in exp:
        if people[e["relative_id"]]["carrier"] == "0":
            assert e["deciding_variant"] != people[e["relative_id"]]["family_variant"]
