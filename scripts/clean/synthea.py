"""C12-C14: clean the Synthea diabetes export.

C12  The export is Apple Numbers. export_numbers() converts each .numbers file to a CSV
     next to it (format conversion only: no values change, integer-valued numbers such
     as RxNorm codes lose their ".0", dates and booleans become text). The CSVs are the
     input of every later step and are listed in data/raw/MANIFEST.csv.
C13  Medications: Synthea writes one row per refill. Rows of the same drug for the same
     patient are merged into therapy periods when the next refill starts within
     REFILL_GAP_DAYS of the previous one ending. Drug class comes from the RxNorm code
     (data/curated/medication_classes.tsv), with a keyword fallback. A deceased
     patient's therapy ends at death. "Current therapy" is evaluated on a fixed date.
C14  Patients: deceased patients stay but are marked inactive. Age at diagnosis is
     recomputed in months from the birth and diagnosis dates (rule D4 needs months).
     Conditions are categorized (diabetes / complication / pregnancy / other) from
     data/curated/condition_categories.tsv. Every cohort patient must exist in Synthea.

Outputs in data/clean/: patients.csv, diagnoses.csv, medications.csv, conditions.csv,
therapy.csv (current drug classes per patient on the reference date).
"""

import csv
import datetime as dt
import math
from collections import Counter, defaultdict
from pathlib import Path

from scripts.clean.common import CleaningError, read_tsv

REFILL_GAP_DAYS = 31
DAYS_PER_MONTH = 365.25 / 12
KEYWORDS = (  # (substring of the lower-case description, drug class)
    ("insulin", "insulin"),
    ("metformin", "metformin"),
    ("glipizide", "sulfonylurea"),
    ("glyburide", "sulfonylurea"),
    ("glibenclamide", "sulfonylurea"),
    ("glimepiride", "sulfonylurea"),
    ("gliclazide", "sulfonylurea"),
    ("tolbutamide", "sulfonylurea"),
    ("chlorpropamide", "sulfonylurea"),
)
TABLES = ("diabetes_patients", "diabetes_medications", "diabetes_conditions", "scenarios")


# ------------------------------------------------------------------------- C12 export


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return str(int(v))  # RxNorm / SNOMED codes are stored as numbers by Numbers
    if isinstance(v, float):
        return repr(round(v, 6))
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    return str(v)


def export_numbers(synthea_dir, section=None):
    """Convert every .numbers file in `synthea_dir` to CSV (first table of first sheet)."""
    from numbers_parser import Document

    synthea_dir = Path(synthea_dir)
    written = []
    for src in sorted(synthea_dir.glob("*.numbers")):
        rows = Document(src).sheets[0].tables[0].rows(values_only=True)
        out = src.with_suffix(".csv")
        with open(out, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow([_cell(h) for h in rows[0]])
            for r in rows[1:]:
                w.writerow([_cell(v) for v in r])
        written.append((src.name, out.name, len(rows) - 1))
    if section is not None:
        section.table(
            "C12 · Numbers → CSV",
            ["Source", "CSV", "Rows"],
            [(a, b, f"{n:,}") for a, b, n in written],
        )
    return written


# ------------------------------------------------------------------------- helpers


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def parse_date(text):
    """'2025-09-23' or '2025-09-23T07:16:26Z' -> date; '' / 'None' -> None."""
    text = (text or "").strip()
    if not text or text == "None":
        return None
    return dt.date.fromisoformat(text[:10])


def code_of(text):
    text = (text or "").strip()
    return text[:-2] if text.endswith(".0") else text


def drug_class(code, description, by_code):
    if code in by_code:
        return by_code[code], "rxnorm"
    d = description.lower()
    for word, cls in KEYWORDS:
        if word in d:
            return cls, "keyword"
    return "other", "default"


# ------------------------------------------------------------------------- C13 medications


def merge_refills(rows, gap_days=REFILL_GAP_DAYS):
    """rows: dicts with start/stop dates, sorted by start. Returns merged periods."""
    periods = []
    for r in rows:
        if periods:
            last = periods[-1]
            open_ended = last["stop"] is None
            if open_ended or r["start"] <= last["stop"] + dt.timedelta(days=gap_days):
                if not open_ended:
                    last["stop"] = None if r["stop"] is None else max(last["stop"], r["stop"])
                last["refills"] += 1
                continue
        periods.append({**r, "refills": 1})
    return periods


def active_on(period, day):
    return period["start"] <= day and (period["stop"] is None or period["stop"] > day)


# ------------------------------------------------------------------------- main


def clean_synthea(synthea_dir, curated_dir, clean_dir, as_of, cohort_ids=(), section=None):
    """Clean the Synthea CSVs (after export_numbers) into data/clean/. Returns a dict of rows."""
    synthea_dir, curated_dir, clean_dir = Path(synthea_dir), Path(curated_dir), Path(clean_dir)
    for t in TABLES[:3]:
        if not (synthea_dir / f"{t}.csv").exists():
            raise CleaningError(
                f"{synthea_dir / t}.csv missing: export the .numbers files first (C12)"
            )
    raw_p = read_csv(synthea_dir / "diabetes_patients.csv")
    raw_m = read_csv(synthea_dir / "diabetes_medications.csv")
    raw_c = read_csv(synthea_dir / "diabetes_conditions.csv")
    by_code = {
        r["rxnorm_code"]: r["drug_class"] for r in read_tsv(curated_dir / "medication_classes.tsv")
    }
    categories = {r["code"]: r for r in read_tsv(curated_dir / "condition_categories.tsv")}
    c = Counter()

    # ---- C14 patients ----------------------------------------------------------------
    patients, diagnoses, deaths = [], [], {}
    age_diffs = []
    ids = set()
    for r in raw_p:
        pid = r["PATIENT_ID"].strip()
        if not pid or pid in ids:
            raise CleaningError(f"patient id missing or duplicated: {pid!r}")
        ids.add(pid)
        birth, death = parse_date(r["BIRTHDATE"]), parse_date(r["DEATHDATE"])
        deceased = r["IS_DECEASED"].strip().lower() == "true" or death is not None
        if deceased and death is None:
            c["deceased_without_date"] += 1
        if death:
            deaths[pid] = death
        c["deceased"] += deceased
        patients.append(
            {
                "patient_id": pid,
                "sex": r["GENDER"].strip() or "",
                "birth_date": birth or "",
                "death_date": death or "",
                "is_active": int(not deceased),
                "race": r["RACE"],
                "ethnicity": r["ETHNICITY"],
                "city": r["CITY"],
                "state": r["STATE"],
            }
        )
        dx = parse_date(r["DIABETES_DIAGNOSIS_DATE"])
        months = None
        if dx and birth:
            months = round((dx - birth).days / DAYS_PER_MONTH, 2)
            if r["AGE_AT_DIAGNOSIS"]:
                age_diffs.append(abs(months - float(r["AGE_AT_DIAGNOSIS"]) * 12))
        else:
            c["no_diagnosis_date"] += 1
        diagnoses.append(
            {
                "patient_id": pid,
                "diabetes_type": "unknown",
                "diagnosed_on": dx or "",
                "age_at_diagnosis_months": "" if months is None else months,
            }
        )

    # ---- C14 conditions ----------------------------------------------------------------
    conditions, unknown_codes = [], Counter()
    dx_type = {}
    for r in raw_c:
        pid = r["PATIENT"].strip()
        if pid not in ids:
            c["condition_unknown_patient"] += 1
            continue
        code = code_of(r["CODE"])
        cat = categories.get(code)
        if cat is None:
            unknown_codes[f"{code} {r['DESCRIPTION'][:50]}"] += 1
        category = cat["category"] if cat else "other"
        if category == "diabetes" and cat["diabetes_type"]:
            dx_type.setdefault(pid, cat["diabetes_type"])
        conditions.append(
            {
                "patient_id": pid,
                "code": code,
                "description": r["DESCRIPTION"],
                "category": category,
                "start": parse_date(r["START"]) or "",
                "stop": parse_date(r["STOP"]) or "",
            }
        )
        c[f"cond_{category}"] += 1
    for d in diagnoses:
        d["diabetes_type"] = dx_type.get(d["patient_id"], "unknown")
        c[f"type_{d['diabetes_type']}"] += 1

    # ---- C13 medications ---------------------------------------------------------------
    grouped = defaultdict(list)
    class_source = Counter()
    for r in raw_m:
        pid = r["PATIENT"].strip()
        if pid not in ids:
            c["med_unknown_patient"] += 1
            continue
        code = code_of(r["CODE"])
        cls, how = drug_class(code, r["DESCRIPTION"], by_code)
        class_source[how] += 1
        given = (r.get("DRUG_CLASS") or "").strip()
        if given and given != cls:
            c["class_disagrees_with_export"] += 1
        start, stop = parse_date(r["START"]), parse_date(r["STOP"])
        if start is None:
            c["med_no_start"] += 1
            continue
        if stop is not None and stop < start:
            c["med_stop_before_start"] += 1
            stop = start
        grouped[(pid, code)].append(
            {
                "patient_id": pid,
                "rxnorm_code": code,
                "description": r["DESCRIPTION"],
                "drug_class": cls,
                "start": start,
                "stop": stop,
            }
        )
    medications = []
    for (pid, _), rows in sorted(grouped.items()):
        rows.sort(key=lambda x: x["start"])
        for period in merge_refills(rows):
            death = deaths.get(pid)
            if death and (period["stop"] is None or period["stop"] > death):
                period["stop"] = death
                c["period_capped_at_death"] += 1
            medications.append(period)

    # ---- current therapy on the reference date -------------------------------------------
    therapy = defaultdict(set)
    for m in medications:
        if active_on(m, as_of):
            therapy[m["patient_id"]].add(m["drug_class"])
    export_therapy = {r["PATIENT_ID"]: r["CURRENT_THERAPY"].strip() for r in raw_p}
    mismatch = []
    therapy_rows = []
    for p in patients:
        mine = " + ".join(sorted(therapy.get(p["patient_id"], ()))) or "none"
        theirs = " + ".join(sorted(export_therapy.get(p["patient_id"], "none").split(" + ")))
        if mine != theirs:
            mismatch.append((p["patient_id"][:8], theirs, mine))
        therapy_rows.append({"patient_id": p["patient_id"], "as_of": as_of, "therapy": mine})

    # ---- cohort check ----------------------------------------------------------------------
    missing_cohort = sorted(set(cohort_ids) - ids)

    # ---- write -----------------------------------------------------------------------------
    clean_dir.mkdir(parents=True, exist_ok=True)
    _write(clean_dir / "patients.csv", patients)
    _write(clean_dir / "diagnoses.csv", diagnoses)
    _write(clean_dir / "conditions.csv", conditions)
    _write(clean_dir / "medications.csv", [{**m, "stop": m["stop"] or ""} for m in medications])
    _write(clean_dir / "therapy.csv", therapy_rows)

    if section is not None:
        s = section
        s.add("in", "patients", len(raw_p))
        s.add("C14", "deceased: kept, marked inactive (no new alerts)", c["deceased"])
        s.add("C14", "deceased flag set but no death date", c["deceased_without_date"])
        s.add(
            "C14", "no diabetes diagnosis date (age at diagnosis unknown)", c["no_diagnosis_date"]
        )
        s.add("in", "medication rows (one per refill)", len(raw_m))
        s.add("C13", "drug class from RxNorm code", class_source["rxnorm"])
        s.add("C13", "drug class from description keyword", class_source["keyword"])
        s.add("C13", "drug class defaulted to 'other'", class_source["default"])
        s.add(
            "C13",
            "drug class differs from the export's DRUG_CLASS column",
            c["class_disagrees_with_export"],
        )
        s.add("C13", "dropped: no start date", c["med_no_start"])
        s.add("C13", "stop before start (set to start)", c["med_stop_before_start"])
        s.add(
            "C13",
            f"therapy periods after merging refills (gap ≤ {REFILL_GAP_DAYS} days)",
            len(medications),
        )
        s.add("C13", "periods ended at the patient's death", c["period_capped_at_death"])
        s.add("in", "condition rows", len(raw_c))
        for cat in ("diabetes", "complication", "pregnancy", "other"):
            s.add("C14", f"conditions categorized '{cat}'", c[f"cond_{cat}"])
        s.add("out", "patients written (data/clean/patients.csv)", len(patients))
        s.table(
            "Diabetes type (from condition codes)",
            ["Type", "Patients"],
            sorted((k[5:], v) for k, v in c.items() if k.startswith("type_")),
        )
        current = Counter(r["therapy"] for r in therapy_rows)
        s.table(f"Current therapy on {as_of}", ["Therapy", "Patients"], current.most_common())
        if age_diffs:
            s.note(
                f"Age at diagnosis recomputed in months from dates; largest difference from the "
                f"export's AGE_AT_DIAGNOSIS (years x 12): {max(age_diffs):.1f} months."
            )
        if mismatch:
            dead = {p["patient_id"][:8] for p in patients if not p["is_active"]}
            n_dead = sum(1 for a, _, _ in mismatch if a in dead)
            s.note(
                f"{n_dead} of these {len(mismatch)} are deceased patients: the export still lists "
                "their last medication, the cleaned data ends it at death (C13)."
            )
            s.note(
                f"{len(mismatch)} patient(s) whose current therapy differs from the export's "
                f"CURRENT_THERAPY column (export → cleaned): "
                + "; ".join(f"{a} {b} → {m}" for a, b, m in mismatch[:10])
                + (" …" if len(mismatch) > 10 else "")
            )
        else:
            s.note(
                "Current therapy agrees with the export's CURRENT_THERAPY column for every patient."
            )
        if unknown_codes:
            s.note(
                "Condition codes not in condition_categories.tsv (treated as 'other'): "
                + ", ".join(f"{k} ({n})" for k, n in unknown_codes.most_common())
            )
        if cohort_ids:
            s.note(
                f"Cohort patients missing from Synthea: {len(missing_cohort)} "
                f"of {len(set(cohort_ids))}"
                + (f" ({', '.join(m[:8] for m in missing_cohort)})" if missing_cohort else "")
                + ". Patients created by scripts/build_reclass_cohort.py (RS3-RS5) are not in "
                "Synthea by design."
            )
    return {
        "patients": patients,
        "diagnoses": diagnoses,
        "medications": medications,
        "conditions": conditions,
        "therapy": therapy_rows,
        "therapy_mismatch": mismatch,
        "missing_cohort": missing_cohort,
    }


def _write(path, rows):
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
