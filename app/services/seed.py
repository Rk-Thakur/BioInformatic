"""Load test patients into the database.

seed_synthea()  the cleaned Synthea patients (data/clean/, Stage 1a C12-C14) with their
                real therapy, diagnoses and deceased status, plus the cohort VCFs
                (ge_ehr_test_data_v2). This is the normal way to load patients.
seed_cohort()   only the 33 cohort patients, with therapy and age at diagnosis taken
                from the cohort's scenarios.csv. Kept for tests and for the replicated
                benchmark cohort.

The two edits the cohort notes ask for are applied as overrides in both: S2 also takes
a sulfonylurea (Synthea prescribes none); S6 was diagnosed at 3 months (rule D4 needs
infant onset).
"""

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.models import Condition, Diagnosis, Medication, Patient
from app.services.workflow import process_upload

THERAPY_START = dt.date(2020, 1, 1)
OVERRIDES = {
    "S2": {"add_therapy": ["sulfonylurea"]},
    "S6": {"age_years": 0.25},
}


def read_scenarios(cohort_dir):
    with open(Path(cohort_dir) / "scenarios.csv", newline="") as fh:
        return list(csv.DictReader(fh))


def seed_cohort(session, cohort_dir, genes, as_of, *, replicate=1):
    """Create one patient per scenarios.csv row and upload their VCF.

    replicate: load each cohort patient this many times (copies get "-r<n>" ids and
    their own family group), to make a larger cohort for timing until the Synthea
    scale cohort exists. Copies are labelled so they are never mistaken for Synthea.
    Patients that already exist (same external id) are skipped.
    Returns (created, skipped, rejected uploads).
    """
    cohort_dir = Path(cohort_dir)
    created = skipped = rejected = 0
    rows = read_scenarios(cohort_dir)
    for copy in range(replicate):
        for row in rows:
            created_one, was_rejected = _seed_one(session, cohort_dir, genes, as_of, row, copy)
            if created_one is None:
                skipped += 1
                continue
            created += 1
            rejected += was_rejected
    return created, skipped, rejected


def _seed_one(session, cohort_dir, genes, as_of, row, copy):
    """Create and upload one patient. Returns (patient or None if it exists, rejected?)."""
    base = row["patient_id"]
    pid = base if copy == 0 else f"{base}-r{copy}"
    if session.scalar(select(Patient).where(Patient.external_id == pid)):
        return None, False
    override = OVERRIDES.get(row["scenario"], {})
    family = row["share_group"] or None
    if family and copy:
        family = f"{family}-r{copy}"
    label = f"Cohort {row['scenario']} {base[:8]}" + (f" (copy {copy})" if copy else "")
    p = Patient(external_id=pid, name=label, family_id=family)
    therapy = [] if row["current_therapy"] == "none" else row["current_therapy"].split(" + ")
    for cls in therapy + override.get("add_therapy", []):
        p.medications.append(Medication(description=cls, drug_class=cls, start=THERAPY_START))
    years = override.get("age_years", float(row["age_at_diagnosis"]))
    p.diagnoses.append(Diagnosis(diabetes_type="unknown", age_at_diagnosis_months=years * 12))
    session.add(p)
    session.flush()
    upload, _ = process_upload(session, p, cohort_dir / "patients" / f"{base}.vcf.gz", genes, as_of)
    return p, upload.status != "accepted"


# --------------------------------------------------------------------------- Synthea


def _date(text):
    return dt.date.fromisoformat(text) if text else None


def _read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


@dataclass
class SyntheaSeedResult:
    patients_created: int = 0
    patients_skipped: int = 0
    inactive: int = 0
    cohort_uploads: int = 0
    rejected_uploads: int = 0
    cohort_not_in_synthea: int = 0


def seed_synthea(session, clean_dir, cohort_dir, genes, as_of):
    """Load the cleaned Synthea patients (Stage 1a C13-C14) and the cohort's VCFs.

    - every Synthea patient: demographics, deceased status, diabetes diagnosis (age in
      months), therapy periods and categorized conditions, from data/clean/;
    - cohort patients (scenarios.csv) that are in Synthea get their VCF uploaded;
      cohort patients that are not (RS3-RS5, created by build_reclass_cohort.py) are
      created from scenarios.csv as in seed_cohort();
    - OVERRIDES apply in both cases: S2 takes a sulfonylurea (Synthea prescribes none),
      S6 is set to infant onset (rule D4).
    Patients that already exist (same external id) are skipped. Returns SyntheaSeedResult.
    """
    clean_dir, cohort_dir = Path(clean_dir), Path(cohort_dir)
    result = SyntheaSeedResult()
    scenarios = {r["patient_id"]: r for r in read_scenarios(cohort_dir)}
    diagnoses = {r["patient_id"]: r for r in _read_csv(clean_dir / "diagnoses.csv")}
    meds, conds = {}, {}
    for r in _read_csv(clean_dir / "medications.csv"):
        meds.setdefault(r["patient_id"], []).append(r)
    for r in _read_csv(clean_dir / "conditions.csv"):
        conds.setdefault(r["patient_id"], []).append(r)

    to_upload = []
    in_synthea = set()
    for r in _read_csv(clean_dir / "patients.csv"):
        pid = r["patient_id"]
        in_synthea.add(pid)
        if session.scalar(select(Patient).where(Patient.external_id == pid)):
            result.patients_skipped += 1
            continue
        scen = scenarios.get(pid)
        label = f"Synthea {pid[:8]}" + (f" ({scen['scenario']})" if scen else "")
        p = Patient(
            external_id=pid,
            name=label,
            sex=r["sex"] or None,
            birth_date=_date(r["birth_date"]),
            deceased_date=_date(r["death_date"]),
            is_active=r["is_active"] == "1",
            family_id=(scen["share_group"] or None) if scen else None,
        )
        d = diagnoses.get(pid)
        if d:
            months = float(d["age_at_diagnosis_months"]) if d["age_at_diagnosis_months"] else None
            p.diagnoses.append(
                Diagnosis(
                    diabetes_type=d["diabetes_type"],
                    diagnosed_on=_date(d["diagnosed_on"]),
                    age_at_diagnosis_months=months,
                )
            )
        for m in meds.get(pid, []):
            p.medications.append(
                Medication(
                    rxnorm_code=m["rxnorm_code"] or None,
                    description=m["description"][:300],
                    drug_class=m["drug_class"],
                    start=_date(m["start"]),
                    stop=_date(m["stop"]),
                )
            )
        for c in conds.get(pid, []):
            p.conditions.append(
                Condition(
                    code=c["code"],
                    description=c["description"][:300],
                    category=c["category"],
                    start=_date(c["start"]),
                    stop=_date(c["stop"]),
                )
            )
        if scen:
            _apply_overrides(p, scen, as_of)
            to_upload.append((p, scen))
        session.add(p)
        result.patients_created += 1
        result.inactive += not p.is_active
    session.flush()

    # Cohort patients that Synthea does not have (RS3-RS5): created from scenarios.csv.
    for pid, scen in scenarios.items():
        if pid in in_synthea:
            continue
        result.cohort_not_in_synthea += 1
        p, rejected = _seed_one(session, cohort_dir, genes, as_of, scen, 0)
        if p is None:
            result.patients_skipped += 1
            continue
        result.patients_created += 1
        result.cohort_uploads += 1
        result.rejected_uploads += rejected

    for p, scen in to_upload:
        vcf = cohort_dir / "patients" / f"{scen['patient_id']}.vcf.gz"
        upload, _ = process_upload(session, p, vcf, genes, as_of)
        result.cohort_uploads += 1
        result.rejected_uploads += upload.status != "accepted"
    return result


def _apply_overrides(patient, scen, as_of):
    override = OVERRIDES.get(scen["scenario"], {})
    for cls in override.get("add_therapy", []):
        patient.medications.append(
            Medication(
                description=f"{cls} (test override, {scen['scenario']})",
                drug_class=cls,
                start=as_of - dt.timedelta(days=90),
            )
        )
    if "age_years" in override:
        months = override["age_years"] * 12
        if patient.diagnoses:
            patient.diagnoses[0].age_at_diagnosis_months = months
        else:
            patient.diagnoses.append(
                Diagnosis(diabetes_type="unknown", age_at_diagnosis_months=months)
            )
