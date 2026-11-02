"""Stage 1a, cleaning rules C12-C14 (Synthea)."""

import csv
import datetime as dt
from pathlib import Path

import pytest

from scripts.clean.common import CleaningError, Report
from scripts.clean.synthea import (
    _cell,
    active_on,
    clean_synthea,
    drug_class,
    export_numbers,
    merge_refills,
    parse_date,
)

ROOT = Path(__file__).resolve().parent.parent
CURATED = ROOT / "data" / "curated"
RAW_SYNTHEA = ROOT / "data" / "raw" / "synthea"
AS_OF = dt.date(2026, 10, 19)
D = dt.date


def write(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


PAT_H = [
    "PATIENT_ID",
    "BIRTHDATE",
    "DEATHDATE",
    "IS_DECEASED",
    "GENDER",
    "RACE",
    "ETHNICITY",
    "CITY",
    "STATE",
    "DIABETES_DIAGNOSIS_DATE",
    "AGE_AT_DIAGNOSIS",
    "CURRENT_INSULIN",
    "CURRENT_METFORMIN",
    "CURRENT_SULFONYLUREA",
    "CURRENT_THERAPY",
]
MED_H = ["PATIENT", "START", "STOP", "CODE", "DESCRIPTION", "DRUG_CLASS"]
CON_H = ["PATIENT", "START", "STOP", "CODE", "DESCRIPTION"]


@pytest.fixture()
def synthea(tmp_path):
    d = tmp_path / "synthea"
    d.mkdir()
    write(
        d / "diabetes_patients.csv",
        PAT_H,
        [
            [
                "alive",
                "1970-01-01",
                "",
                "false",
                "F",
                "white",
                "nonhispanic",
                "Boston",
                "MA",
                "2020-01-01",
                "50",
                "true",
                "true",
                "false",
                "insulin + metformin",
            ],
            [
                "dead",
                "1950-06-15",
                "2024-03-01",
                "true",
                "M",
                "white",
                "nonhispanic",
                "Lynn",
                "MA",
                "2010-06-15",
                "60",
                "true",
                "false",
                "false",
                "insulin",
            ],
            [
                "baby",
                "2025-12-01",
                "",
                "false",
                "M",
                "asian",
                "nonhispanic",
                "Salem",
                "MA",
                "2026-02-15",
                "0.2",
                "false",
                "false",
                "false",
                "none",
            ],
        ],
    )
    write(
        d / "diabetes_medications.csv",
        MED_H,
        [
            # three monthly refills -> one period; a later restart after a long gap -> second period
            [
                "alive",
                "2021-01-01T08:00:00Z",
                "2021-02-01T08:00:00Z",
                "106892",
                "Humulin",
                "insulin",
            ],
            [
                "alive",
                "2021-02-01T08:00:00Z",
                "2021-03-01T08:00:00Z",
                "106892",
                "Humulin",
                "insulin",
            ],
            [
                "alive",
                "2021-03-01T08:00:00Z",
                "2021-04-01T08:00:00Z",
                "106892",
                "Humulin",
                "insulin",
            ],
            ["alive", "2025-01-01T08:00:00Z", "", "106892", "Humulin", "insulin"],
            ["alive", "2020-01-01T08:00:00Z", "", "860975", "Metformin ER", "metformin"],
            [
                "alive",
                "2026-01-01T08:00:00Z",
                "2025-12-25T08:00:00Z",
                "999999",
                "Gliclazide 40 MG",
                "",
            ],  # stop < start; keyword
            ["dead", "2015-01-01T08:00:00Z", "", "106892", "Humulin", "insulin"],  # ends at death
        ],
    )
    write(
        d / "diabetes_conditions.csv",
        CON_H,
        [
            ["alive", "2020-01-01", "", "44054006", "Diabetes mellitus type 2 (disorder)"],
            ["alive", "2026-05-01", "", "72892002", "Normal pregnancy (finding)"],
            ["dead", "2010-06-15", "", "44054006", "Diabetes mellitus type 2 (disorder)"],
            ["baby", "2026-02-15", "", "123456", "Something unmapped (disorder)"],
        ],
    )
    return d


def run(synthea, tmp_path, cohort=()):
    section = Report("t").section("s")
    out = clean_synthea(synthea, CURATED, tmp_path / "clean", AS_OF, cohort, section)
    return out, {d: n for _, d, n in section.rows}, section


def test_helpers():
    assert parse_date("2025-09-23T07:16:26Z") == D(2025, 9, 23)
    assert parse_date("") is None and parse_date("None") is None
    assert (
        _cell(106892.0) == "106892"
        and _cell(51.2) == "51.2"
        and _cell(None) == ""
        and _cell(True) == "true"
    )
    by_code = {"860975": "metformin"}
    assert drug_class("860975", "x", by_code) == ("metformin", "rxnorm")
    assert drug_class("1", "Glipizide 5 MG Oral Tablet", by_code) == ("sulfonylurea", "keyword")
    assert drug_class("1", "Atorvastatin", by_code) == ("other", "default")


def test_merge_refills():
    rows = [
        {"start": D(2021, 1, 1), "stop": D(2021, 2, 1)},
        {"start": D(2021, 2, 20), "stop": D(2021, 3, 20)},
        {"start": D(2021, 6, 1), "stop": None},
        {"start": D(2022, 1, 1), "stop": D(2022, 2, 1)},
    ]
    periods = merge_refills(rows)
    assert [(p["start"], p["stop"], p["refills"]) for p in periods] == [
        (D(2021, 1, 1), D(2021, 3, 20), 2),  # 19-day gap merged
        (D(2021, 6, 1), None, 2),  # open-ended period absorbs later refills
    ]
    assert active_on(periods[1], AS_OF) and not active_on(periods[0], AS_OF)


def test_clean_synthea(synthea, tmp_path):
    out, counts, section = run(synthea, tmp_path, cohort=["alive", "dead", "ghost"])
    pats = {p["patient_id"]: p for p in out["patients"]}
    assert pats["dead"]["is_active"] == 0 and pats["alive"]["is_active"] == 1
    dx = {d["patient_id"]: d for d in out["diagnoses"]}
    assert dx["alive"]["diabetes_type"] == "type2" and dx["baby"]["diabetes_type"] == "unknown"
    assert dx["baby"]["age_at_diagnosis_months"] == pytest.approx(
        2.5, abs=0.1
    )  # months, from dates

    meds = [
        (m["patient_id"], m["drug_class"], m["start"], m["stop"], m["refills"])
        for m in out["medications"]
    ]
    assert ("alive", "insulin", D(2021, 1, 1), D(2021, 4, 1), 3) in meds
    assert ("alive", "insulin", D(2025, 1, 1), None, 1) in meds
    assert ("dead", "insulin", D(2015, 1, 1), D(2024, 3, 1), 1) in meds  # capped at death
    assert ("alive", "sulfonylurea", D(2026, 1, 1), D(2026, 1, 1), 1) in meds  # stop < start fixed
    therapy = {t["patient_id"]: t["therapy"] for t in out["therapy"]}
    assert therapy == {"alive": "insulin + metformin", "dead": "none", "baby": "none"}
    assert out["therapy_mismatch"] == [("dead", "insulin", "none")]

    cats = {(c["patient_id"], c["category"]) for c in out["conditions"]}
    assert ("alive", "pregnancy") in cats and ("baby", "other") in cats
    assert out["missing_cohort"] == ["ghost"]
    assert counts["deceased: kept, marked inactive (no new alerts)"] == 1
    assert counts["stop before start (set to start)"] == 1
    assert counts["drug class from description keyword"] == 1
    assert counts["periods ended at the patient's death"] == 1
    assert any("123456" in n for n in section.notes)  # unmapped condition code reported
    assert (tmp_path / "clean" / "therapy.csv").exists()


def test_missing_export_is_an_error(tmp_path):
    with pytest.raises(CleaningError, match="export"):
        clean_synthea(tmp_path, CURATED, tmp_path / "clean", AS_OF)


def test_duplicate_patient_rejected(synthea, tmp_path):
    with open(synthea / "diabetes_patients.csv", "a", newline="") as fh:
        csv.writer(fh).writerow(
            [
                "alive",
                "1970-01-01",
                "",
                "false",
                "F",
                "",
                "",
                "",
                "",
                "2020-01-01",
                "50",
                "",
                "",
                "",
                "none",
            ]
        )
    with pytest.raises(CleaningError, match="duplicated"):
        run(synthea, tmp_path)


@pytest.mark.slow
@pytest.mark.skipif(
    not (RAW_SYNTHEA / "diabetes_patients.numbers").exists(), reason="Synthea export absent"
)
def test_real_synthea_export(tmp_path):
    """C12 on the real files, then C13-C14."""
    work = tmp_path / "synthea"
    work.mkdir()
    for f in RAW_SYNTHEA.glob("*.numbers"):
        (work / f.name).write_bytes(f.read_bytes())
    written = {name: n for _, name, n in export_numbers(work)}
    assert written == {
        "diabetes_patients.csv": 87,
        "diabetes_medications.csv": 5149,
        "diabetes_conditions.csv": 222,
        "scenarios.csv": 30,
    }
    out, _, _ = run(work, tmp_path)
    assert len(out["patients"]) == 87 and sum(not p["is_active"] for p in out["patients"]) == 23
    dead = {p["patient_id"] for p in out["patients"] if not p["is_active"]}
    assert all(
        m[0][:8] in {d[:8] for d in dead} for m in out["therapy_mismatch"]
    )  # only deceased differ
