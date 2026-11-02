"""Load the 33-patient test cohort (ge_ehr_test_data_v2) into the database.

Each patient's therapy and age at diagnosis come from the cohort's scenarios.csv
until the Synthea tables are cleaned (C12-C14). The two edits the cohort notes ask
for are applied as overrides: S2 also takes a sulfonylurea; S6 was diagnosed at
3 months (rule D4 needs infant onset).
"""

import csv
import datetime as dt
from pathlib import Path

from sqlalchemy import select

from app.models import Diagnosis, Medication, Patient
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
