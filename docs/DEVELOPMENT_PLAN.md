# GE-EHR for Monogenic Diabetes — Development Plan

Version 1.2 · 2026-10-29 (Stage 1a ClinVar/ClinGen cleaning done; C9 corrected) · Source of truth for scope: the paper *"GE-EHR for Monogenic Diabetes: Reclassification-Aware Genomic Decision Support in the EHR"* (Thakur, Thakuri, Awasthi).

---

## 1. Readiness verdict

**Ready to start.** Data and environment were checked on 2026-10-29.

| Item | Status | Notes |
|---|---|---|
| ClinVar old release (2023-01-07) | ✅ | `clinvar_old (1).vcf.gz`; rename to `clinvar_20230107.vcf.gz` |
| ClinVar new release (2026-10-19) | ✅ | `clinvar.vcf.gz` |
| ClinVar middle release (2023-12-30) | ✅ | optional third release for a multi-step evaluation |
| 33-patient test cohort with real reclassifications | ✅ | `ge_ehr_test_data_v2/` + `expected_changes.csv` |
| ClinGen expert-panel classifications | ✅ | `clingen_diabetes.csv` (GCK, HNF1A, HNF4A) |
| Python, cyvcf2, Java | ✅ | Python 3.14 works: a cyvcf2 0.34 wheel exists for it. Java 17 is enough for Synthea. |
| Synthea tables as CSV | ⚠️ week 1 | They're in `.numbers` format; export them to CSV |
| Data cleaning | ⚠️ Stage 1 | 14 cleaning rules found by checking the real files (§2a), including one bug that would hide pathogenic variants |
| Activating KCNJ11/ABCC8 list | ⚠️ week 2 | Needed only for rule D4 |
| S2 sulfonylurea row, S6 infant onset | ⚠️ week 2 | Do these in the seed script, not by hand |
| 10,000-patient scale cohort | ⚠️ week 5 | Needed only for the timing evaluation |
| Version control | ❌ day 1 | The folder is not a git repo yet |

None of the open items blocks the first two weeks.

---

## 2. Scope

### In scope (MVP, which is what the paper evaluates)
- F1: patients, diagnosis, medications, VCF upload, one patient view
- F2: classify variants in 7 genes against the **current** ClinVar release
- F3: rules D1–D6, versioned and medication-aware, advisory only
- F4: FHIR R4 Bundle export, with `amended` status after reclassification
- F5: re-evaluation engine for ClinVar releases **and** rule revisions
- Role-based access (clinician / lab technician), hashed passwords, audit log
- Evaluation harness comparing alert-once, full recompute and change-driven re-evaluation

### Out of scope (listed as future work in the paper)
- Real patient data, production deployment, EHR vendor integration
- Variant normalization against a reference genome (exact matching only; documented as a limitation)
- MODY probability calculator, ingesting lab amended reports
- Polished UI. Server-rendered Jinja pages are enough.

---

## 2a. Data cleaning and preparation (Stage 1)

**Yes, cleaning is needed.** These checks were run on the real files on 2026-10-29; the findings are below. The amount of work is small, but some of the problems would silently produce **wrong alerts** if they were skipped.

### Principles
- **Never edit raw files.** Raw data stays in `data/raw/`. Cleaning scripts write to `data/clean/`, and anyone can re-run them.
- **Cleaning is code, not manual work.** Every rule is a function with a unit test.
- **Every dropped or changed row is counted.** `data/clean/QUALITY_REPORT.md` lists, for each source, rows in, rows changed or dropped per rule (with a reason) and rows out. This report also supplies the numbers for the paper's Data section.
- **Fixes to the cohort live in `data/curated/overrides.csv`** (the S2 sulfonylurea row, the S6 infant onset). The seed script applies them, so they're never typed into a spreadsheet by hand.

### What was found and what to do

| # | Source | Finding (measured) | Cleaning rule | Why it matters |
|---|---|---|---|---|
| C1 | Folder | `clinvar_old.vcf.gz` is 0 bytes; the real file is `clinvar_old (1).vcf.gz`. `ge_ehr_test_data_OLD_clinvar` is obsolete. The PDF has the wrong name. | Move to `data/raw/` with clear names: `clinvar_20230107.vcf.gz`, `clinvar_20231230.vcf.gz`, `clinvar_20261019.vcf.gz`. Archive the obsolete folder. Record SHA-256 checksums in `data/raw/MANIFEST.csv`. ✅ Done | Nobody loads the wrong release by mistake |
| C2 | ClinVar | Compound terms such as `Pathogenic/Likely_risk_allele` (5), `Likely_pathogenic/Likely_risk_allele` (12–18) and `Uncertain_significance/Uncertain_risk_allele` (~50). **The current `normalize_clnsig` maps these to OTHER, which is a bug.** | Remove the `/…risk_allele` part, then map the rest: `Pathogenic/Likely_risk_allele` → P, `Uncertain_significance/Uncertain_risk_allele` → VUS. Pure `risk_factor`, `drug_response`, `Likely_risk_allele`, `not_provided` and `association_not_found` → OTHER (non-actionable). ✅ Done | Otherwise real pathogenic variants would get **no alert** |
| C3 | ClinVar | The term "Conflicting interpretations" was renamed "Conflicting classifications" between releases (870 / 971 records) | Map both to CONFLICT (already done) ✅ Done | Prevents about 650 false "changes" |
| C4 | ClinVar | 18 records in the 2026 release have no germline `CLNSIG` (somatic or oncogenicity only) | Drop them and count them in the report ✅ Done | Not germline evidence |
| C5 | ClinVar | 11 records per release have no usable ALT (`.`) | Drop them ✅ Done | They can't be matched to a patient variant |
| C6 | ClinVar | 253 (2023) / 555 (2026) records list several genes. Measured: almost all pair a panel gene with a non-panel neighbour (e.g. INS with INS-IGF2). Only 7 / 7 / 1 records list **two panel genes** (ABCC8 + KCNJ11). | Keep only panel genes from GENEINFO. If two remain, decide by NCBI gene coordinates (C11); if a variant lies in neither, take the nearest gene and flag it. Keep the full GENEINFO list for display. ✅ Done | ABCC8 vs KCNJ11 decides whether D4 applies |
| C7 | ClinVar | 11–14 indels are longer than 50 bp | Keep them (exact matching still works), but flag them in the report ✅ Done | — |
| C8 | ClinVar | No duplicate keys, no `chr` prefix, GRCh38 in both | Check these on every import and fail loudly if a future release breaks them ✅ Done | Protects later imports |
| C9 | ClinGen eRepo | ~~Same variant approved several times~~ **Corrected on 2026-10-29:** every variant has exactly one classification. The 1,167 rows are 773 rows with a ClinVar ID plus 394 rows with **no** ClinVar ID (`-`), which had looked like duplicates. | Keep one row per classification with its approval date. For each ClinVar release, an expert classification may override it only if it was approved **on or before** the release date (DD6): 103 of 1,167 for 2023-01-07, 341 for 2023-12-30, 1,165 for 2026-10-19. ✅ Done | Otherwise 2026 expert opinion leaks into the 2023 release and **hides real reclassifications** |
| C10 | ClinGen eRepo | Assertions are words ("Likely Pathogenic"). 394 rows have no ClinVar ID. eRepo uses newer transcripts than the paper (HNF1A NM_000545.**8**, HNF4A NM_175914.**5**). | Map the words to codes. Link by Variation ID (772), otherwise by matching the GRCh38 genomic HGVS to ClinVar's `CLNHGVS` (381). 13 remain unlinked and are kept and flagged. Take the transcript HGVS on the newest version of the reference transcript. ✅ Done | Needed for precedence and display; the paper's §4.2 transcript versions need updating |
| C11 | Gene regions | No gene coordinate file yet | Build `data/clean/panel_genes.bed`: each gene's span from ClinVar positions plus ±5 kb, checked by hand against NCBI Gene ✅ Done | Needed for C6 and for keeping only panel variants from VCFs |
| C12 | Synthea | Tables are in `.numbers` format | Export them to CSV in `data/raw/synthea/`. The loader reads only CSV. | Python can't read Numbers files |
| C13 | Synthea medications | One row per **monthly refill** (hundreds per patient), with STOP dates in the future, and a free-text DESCRIPTION | Collapse the refills into one period per patient and drug. Define current therapy **as of a fixed reference date** (written in the config). Map drugs to classes by RxNorm code, with a keyword fallback (insulin / metformin / glipizide, glyburide, glimepiride, gliclazide → sulfonylurea / else other). List unmapped drugs in the report. | The rules depend entirely on current therapy |
| C14 | Synthea patients and conditions | Some patients are **deceased**. Age at diagnosis is in years. Several diabetes-related conditions exist (type 2, prediabetes). Pregnancy conditions are needed for DD15. | Keep deceased patients but mark them inactive: no new alerts, though they're still counted for family groups. Store age at diagnosis in **months** (needed for D4). Map condition codes to diabetes type and an active-pregnancy flag. Check that every `scenarios.csv` patient exists in the Synthea tables. | Rules D1 and D4, and correct cohort counts |

Patient VCFs (`ge_ehr_test_data_v2`) were checked and are **clean**: no duplicates, no multi-allelic records, no `chr` prefix. The only cleaning needed is to read genotypes consistently: `0|1`, `1|0` and `0/1` mean heterozygous; `1|1` and `1/1` mean homozygous. That's handled in the parser (F1), not as a separate cleaning step.

### Deliverables
- `scripts/clean/` with one module per source: `clinvar.py`, `erepo.py`, `synthea.py`, `genes.py`, plus `run_all.py`
- `data/clean/`: `clinvar_<date>.panel.tsv`, `erepo_versions.tsv`, `panel_genes.bed`, `patients.csv`, `therapy.csv`, `diagnoses.csv`, `QUALITY_REPORT.md`
- `tests/test_cleaning.py`: one test per rule C2–C14
- Re-run `scripts/build_reclass_cohort.py` after C2 and C6 are fixed, then compare its output with the current `ge_ehr_test_data_v2`. Expect no change to the chosen RS variants.

---

## 3. Design decisions that resolve gaps in the paper

The paper leaves these open. Decide them now and record them in the paper's Methods section.

| # | Question | Decision |
|---|---|---|
| DD1 | D5 fires for every actionable variant, so S2/S4 ("info only") would also get a D5 alert | D5 is a **separate family-cascade alert** that always fires for P/LP. Update the S2/S4 expectations to "info note + D5 low". |
| DD2 | Algorithm 2 only handles ClinVar changes | Add **Algorithm 3: rule revision**. Re-run the revised rule for carriers of its genes, diff old vs new outcomes, and notify only when the outcome changes. |
| DD3 | What happens to the D6 "monitored VUS" note after an upgrade | **Supersede** the D6 note and create the new treatment alert. After a downgrade to VUS, create a D6 note. |
| DD4 | Category of a variant absent from ClinVar ("not classified") | Non-actionable. NC → P/LP counts as an **upgrade**. |
| DD5 | "Conflicting classifications" | Non-actionable. Show the raw ClinVar label to the clinician. |
| DD6 | Expert-panel precedence | Use ClinVar's aggregate CLNSIG. If a ClinGen eRepo classification exists **and its approval date ≤ the release date**, it overrides. Without the date check, a single eRepo snapshot would leak 2026 knowledge into the 2023 release. |
| DD7 | "Diagnosed in infancy" (D4) | `age_at_diagnosis < 6 months` |
| DD8 | "Activating" variants (D4) | Use a curated CSV of activating KCNJ11/ABCC8 variants. If a variant isn't listed, D4 doesn't fire and the UI shows an info note: "neonatal-diabetes gene variant, activation status unknown". |
| DD9 | Duplicate alerts on re-runs | At most **one active alert per (patient, variant, rule code)**. Re-evaluation must be idempotent: running it twice changes nothing. |
| DD10 | Definition of "point-in-time correctness" | For every alert, re-running Algorithm 1 with its *stored* release and rule version reproduces the same alert. An "as of date T" query returns the alerts that were active at T. |
| DD11 | Medication classes | Map Synthea RxNorm codes to {insulin, metformin, sulfonylurea, other} with a lookup table. Unknown codes map to "other". |
| DD12 | Downgrades | Never delete an alert or touch therapy. Set status `superseded`, create a `review` notice, and mark the FHIR Observation `amended`. |
| DD13 | Algorithm 2 line 10 says "update provenance of p's alerts to K_new". Overwriting `release_id` would destroy the history the paper promises. | Keep `Alert.release_id` as the release that **created** the alert. Each provenance update appends an `AlertProvenance` row (alert, release, rule version, run, time). The "current provenance" is the latest row. |
| DD14 | Table IV: an upgrade alert is "marked reclassified, **high priority**", but D3 has severity medium | Make **priority** a separate field from **severity**. Severity comes from the rule (Table III). Priority is `high` for any alert created by an upgrade and `normal` otherwise. The UI sorts by priority, then severity. |
| DD15 | D1 says "medication usually not required **outside pregnancy**" (and §6.1 says pregnancy is out of scope) | If the patient has an active pregnancy condition (from Synthea), D1 still fires but adds the caveat "pregnancy: specialist guidance required". Treatment in pregnancy itself stays out of scope. |
| DD16 | §4.2 names reference transcripts (NM_000162.5 GCK, NM_000545.6 HNF1A, NM_175914.4 HNF4A) | Keep a `GeneTranscript` table. Show transcript-level HGVS from ClinGen eRepo where it exists, and otherwise the genomic HGVS (`CLNHGVS`). FHIR Observations carry the transcript. |

---

## 4. Architecture

```
Browser (Jinja pages) ──► Flask app (blueprints: auth, patients, alerts, admin, fhir)
                              │
                  ┌───────────┴───────────────────────────┐
                  │            Service layer               │
                  │  vcf_service  classify  rule_engine    │
                  │  fhir_export  reevaluate (F5)          │
                  └───────────┬───────────────────────────┘
                              │ SQLAlchemy 2.0 ORM
                          SQLite (dev) — schema portable to PostgreSQL
                              ▲
     CLI jobs (flask commands): import-release, import-rules, reevaluate, seed, benchmark
```

Principles:
- **The service layer has no Flask imports.** F2, F3 and F5 are plain functions that take a DB session, so they're unit-testable and the benchmark can call them directly.
- **Knowledge is data.** ClinVar releases and rules are rows with versions, never constants in code.
- **Append-only history.** Alerts, releases and rule versions are never updated destructively, only status changes. Every change writes an `AuditEvent`.
- **Re-evaluation runs as a CLI job**, not in a web request. That also makes it easy to schedule (cron).

### Tech stack

| Concern | Choice |
|---|---|
| Language | Python 3.12+ (the existing 3.14 venv works) |
| Web | Flask 3, Jinja2, Flask-Login, Flask-WTF (CSRF) |
| ORM / DB | SQLAlchemy 2.0, Alembic migrations, SQLite |
| VCF | cyvcf2 |
| FHIR | fhir.resources (R4B/R4 models) |
| Tests | pytest, pytest-cov, factory fixtures |
| Quality | ruff (lint + format), pre-commit, GitHub Actions CI |
| Data | Synthea (Java), ClinVar VCFs, ClinGen eRepo |

---

## 5. Repository layout

```
BioInformatic/
  app/
    __init__.py            # create_app(), config, extensions
    config.py
    models/                # one file per aggregate
      user.py patient.py variant.py knowledge.py alert.py audit.py
    services/
      vcf_service.py       # F1: parse + validate + store
      classify.py          # F2
      rule_engine.py       # F3 (Algorithm 1)
      reevaluate.py        # F5 (Algorithms 2 and 3)
      fhir_export.py       # F4
      clinvar_import.py    # release loader (wraps scripts/clinvar_panel.py)
    web/                   # Flask blueprints + templates
      auth.py patients.py alerts.py admin.py fhir.py
      templates/
    cli.py                 # flask import-release | import-rules | reevaluate | seed | benchmark
  data/
    clinvar/               # release VCFs (git-ignored, too large)
    curated/
      activating_variants.csv
      medication_classes.csv
      rules_v1.yaml rules_v2.yaml
    synthetic/             # Synthea CSVs + cohorts (v2 test cohort, 10k cohort)
  scripts/                 # existing: clinvar_panel.py, build_reclass_cohort.py
  eval/                    # benchmark.py, metrics.py, results/
  tests/
    unit/ integration/ scenarios/   # scenarios = S1–S12, RS1–RS7
  docs/
    DEVELOPMENT_PLAN.md  decisions.md
  migrations/
  pyproject.toml  .pre-commit-config.yaml  .github/workflows/ci.yml
```

---

## 6. Data model

```
User(id, username, password_hash, role[clinician|lab_tech|admin], active)

Patient(id, external_id[Synthea UUID], name, sex, birth_date, family_id NULL)
Diagnosis(id, patient_id, diabetes_type, diagnosed_on, age_at_diagnosis_months)
Medication(id, patient_id, rxnorm_code, description, drug_class, start, stop NULL)
   -- "current therapy" = rows with stop IS NULL

VcfUpload(id, patient_id, filename, sha256, uploaded_by, uploaded_at, status, error)
PatientVariant(id, patient_id, upload_id, chrom, pos, ref, alt, zygosity, gene NULL)
   INDEX (chrom, pos, ref, alt)

KbRelease(id, version["2023-01-07"], source_file, imported_at, is_current, record_count)
KnownVariant(id, release_id, chrom, pos, ref, alt, gene, clinvar_id,
             clnsig_raw, classification[P|LP|P/LP|VUS|LB|B|B/LB|CONFLICT|OTHER],
             review_status, expert_panel BOOL, disease)
   UNIQUE (release_id, chrom, pos, ref, alt)

CuratedFlag(chrom, pos, ref, alt, flag['activating'], source)

Rule(id, code[D1..D6], version, genes JSON, genetic_condition JSON,
     therapy_condition JSON NULL, extra_condition JSON NULL, severity,
     recommendation, source, effective_from, is_current)
   UNIQUE (code, version)

GeneTranscript(gene, transcript["NM_000545.6"], is_reference)          -- DD16

Alert(id, patient_id, patient_variant_id, rule_id, release_id,   -- release that CREATED it
      kind[alert|info_note|review_notice], severity, priority[high|normal],   -- DD14
      status[active|superseded|reviewed],
      reclassified BOOL, supersedes_id NULL, created_at, created_by_run_id NULL,
      reviewed_by NULL, reviewed_at NULL)
   PARTIAL UNIQUE (patient_id, patient_variant_id, rule code) WHERE status='active'

AlertProvenance(id, alert_id, release_id, rule_id, run_id NULL, at)    -- DD13, append-only

ReevaluationRun(id, trigger[release|rule], old_ref, new_ref, started_at, finished_at,
                variants_examined, variants_changed, patients_touched,
                alerts_created, alerts_superseded, provenance_updates)

AuditEvent(id, at, user_id NULL, run_id NULL, action, entity, entity_id, details JSON)
```

Rules are stored as data, for example in `rules_v1.yaml`:

```yaml
- code: D2
  version: 1
  genes: [HNF1A]
  genetic: {band: actionable}
  therapy: {any_of: [insulin, metformin], none_of: [sulfonylurea]}
  severity: high
  recommendation: Consider low-dose sulfonylurea as first-line treatment
  source: "[1,7]"
- code: D4
  version: 1                 # v2 adds ABCC8 (scenario RS5)
  genes: [KCNJ11]
  genetic: {band: actionable, flag: activating}
  therapy: {any_of: [insulin]}
  extra: {age_at_diagnosis_months_lt: 6}
  severity: high
```

---

## 7. Module specifications

### F1 — Records and VCF (`vcf_service.py`)
- `parse_vcf(path) -> list[VariantKey]`. It accepts `.vcf` and `.vcf.gz`, requires a `#CHROM` header and exactly one sample, strips any `chr` prefix, and reads zygosity from GT.
- It raises `VcfValidationError` with a readable message for truncated rows, non-numeric POS, missing header or non-gzip data (the 4 files in `malformed/`).
- It stores only variants inside the 7 gene regions. Background variants outside them are counted but not stored.
- Uploading a file triggers F2 and F3 for that patient. Changing a medication triggers F3 only.

### F2 — Classification (`classify.py`)
- `classify(session, variant, release) -> Classification(cls, band, known_variant_id | None)`, with exact-key lookup in `KnownVariant` and the DD6 precedence.
- No match returns `NC`.

### F3 — Rule engine (`rule_engine.py`, Algorithm 1)
- `evaluate_patient(session, patient, release, rules) -> list[Outcome]` is a **pure function**: it returns the outcomes and doesn't write anything.
- `apply_outcomes(session, patient, outcomes, run)` creates or keeps alerts, enforces DD9, and writes audit events.
- Keeping evaluation and persistence apart is what makes the benchmark and the point-in-time check (DD10) possible.

### F5 — Re-evaluation (`reevaluate.py`)
- `reevaluate_release(session, old, new) -> ReevaluationRun` (Algorithm 2):
  1. `stored = SELECT DISTINCT chrom,pos,ref,alt FROM patient_variant` (one indexed query).
  2. Classify each key under `old` and `new` in bulk (two joins). The changed ones form Δ.
  3. Apply the action for each category, following Table IV and DD3/DD12.
  4. Set `new.is_current = True` and log the run, all in **one transaction**.
- `reevaluate_rule(session, old_rule, new_rule) -> ReevaluationRun` (Algorithm 3 from DD2).
- `full_recompute(session, release)` and `alert_once` mode exist **only for the benchmark**.

### F4 — FHIR (`fhir_export.py`)
- `GET /patients/<id>/fhir` returns a `Bundle` (type `collection`) containing `Patient`, one variant `Observation` per stored panel variant, and one `DiagnosticReport`.
- Observation codes follow HL7 Genomics Reporting: LOINC 69548-6 for the variant, 48018-6 for the gene, 53037-8 for clinical significance. Status is `final`, or `amended` if the variant's class changed since the first alert.
- Validate the Bundle with fhir.resources in tests. Run the HL7 validator jar once manually.

### Web and security
- Pages: login, patient list, patient detail (demographics, therapy, variants with classification, active and past alerts with provenance), VCF upload, alert review (clinician marks "reviewed"), admin import.
- Role matrix: lab technicians can upload VCFs and see variants, but get 403 on recommendations and alerts (S12). Clinicians can see everything and review alerts. Admins run imports.
- Hash passwords with werkzeug (scrypt), turn on CSRF protection, cap upload size at 20 MB, and never log patient identifiers.

---

## 8. Milestones (6 weeks, 1-week sprints)

| Week | Milestone | Deliverables | Exit criteria |
|---|---|---|---|
| **0** (2 days) | Foundation | git repo, `pyproject.toml`, ruff + pre-commit, CI running pytest, app factory, config, Alembic | CI green on an empty test |
| **1a** (3 days) | **Stage 1: Data cleaning** (§2a) | Tasks C1–C14; `data/clean/`; `data/clean/QUALITY_REPORT.md`; `tests/test_cleaning.py` | Every cleaning rule has a test; the report shows rows in, rows dropped per rule and rows out for every source; the raw files are untouched |
| **1b** (2 days) | Data model and knowledge import | All models + migration; `flask import-release` and `flask import-expert` load the **cleaned** data; `flask import-rules rules_v1.yaml` | Both releases in the DB; a query gives about 192 upgrades and 75 downgrades (exact numbers recorded in the quality report) |
| **2** | F1 + F2 + F3 | VCF upload and validation; classification; rule engine D1–D6; `flask seed` loads the v2 cohort including the S2/S6 fixes; activating variants CSV | **S1–S10 pass** as automated tests |
| **3** | F5 (core) | `reevaluate_release`, `reevaluate_rule`, idempotency, audit log, run log | **RS1–RS7 pass**; running twice changes nothing; results match `expected_changes.csv` 100% |
| **4** | Web UI, security, F4 | Patient page, alert review, role checks, FHIR export | **S11, S12 pass**; clinician can walk through RS1 end to end in the browser |
| **5** | Evaluation | 10k Synthea cohort; `eval/benchmark.py` for the 3 strategies; metrics; middle release (2023-12-30) as an extra step | Table VI filled with measured numbers; results CSV and plots committed |
| **6** | Hardening and write-up | Bug fixes, coverage ≥ 85% on services, README and demo script, screenshots, paper Sections 5–6 updated with results and the DD decisions | Paper draft with real results; tagged `v1.0` |

**Critical path:** data cleaning → data model → F2 → F3 → F5 → evaluation. The UI (week 4) and FHIR can slip without blocking the paper's results.

### Suggested team split

The names come from the paper's author list; reassign as you like.

| Stream | Owner | Weeks |
|---|---|---|
| Knowledge and data: **data cleaning**, ClinVar import, eRepo precedence, Synthea, cohorts, activating list | Barsha | 1, 2, 5 |
| Core engine: F2, F3, F5, idempotency, audit | Ranjan | 2, 3 |
| App and interop: Flask, auth, UI, FHIR, CI | Bishal | 0, 4 |
| Evaluation and paper: benchmark, metrics, write-up | Everyone, led by Ranjan | 5, 6 |

The interfaces in §7 are the contract between streams. Agree on them on day 1 so people can work in parallel against stubs.

---

## 9. Testing strategy

| Layer | What | Tool |
|---|---|---|
| Unit | CLNSIG normalization, band and category logic, each rule's conditions, VCF validation errors | pytest |
| Integration | upload → classify → alerts; release import; re-evaluation transactions | pytest + temporary SQLite |
| Scenario | **S1–S12 and RS1–RS7 as one test each**, data-driven from `ge_ehr_test_data_v2/scenarios.csv` | pytest parametrize |
| Ground truth | F5 output vs `expected_changes.csv` (every changed variant and carrier) | pytest |
| Property | idempotency (re-run = no-op); change-driven result == full-recompute result | pytest |
| Security | role matrix (403s), CSRF, password hashing | Flask test client |
| FHIR | Bundle parses with fhir.resources; `amended` after RS1 | pytest |

The **property test "change-driven == full recompute"** is the most important single test. It proves the optimization is correct, which is the paper's central claim.

---

## 10. Evaluation plan (paper Section 5)

`eval/benchmark.py --cohort {30|10k} --old 2023-01-07 --new 2026-10-19 --strategy {alert_once|full|change_driven}`

| Metric | How it's computed |
|---|---|
| Re-alert sensitivity | carriers of upgraded variants with a new active alert ÷ all such carriers (from `expected_changes.csv`) |
| Stale alerts after release | active alerts whose variant is now non-actionable, or whose class no longer matches the current release |
| Notification precision | notifications that correspond to a real band change or outcome change ÷ all notifications |
| Records examined | variant keys classified during the run |
| Time per release | wall-clock median of 5 runs, 10k cohort, same machine |
| Point-in-time correctness | DD10 check over every alert |

| Alert burden (from Discussion §6) | notifications per 1,000 patients per release, and the share of changes that produced **no** notification |

**Two kinds of knowledge change, as §5.1 requires both:**
1. **Real release pair:** 2023-01-07 → 2026-10-19. Optionally 2023-12-30 as a middle step, which tests two re-evaluations in a row.
2. **Controlled change set:** `scripts/make_controlled_release.py` copies the new release and injects a known number of upgrades, downgrades and within-band changes into variants carried by the 10k cohort. Because every change is known, sensitivity and precision can be checked exactly at scale. Without it, the 10k run only measures time.

Plus the simulated rule revision **D4 v1 → v2** (adds ABCC8).

**Stretch (from §6.2 future work, cheap because the data is already here):** per-gene reclassification rates and directions across the three ClinVar releases. The earlier analysis already found GCK with 115 upgrades and 21 downgrades.

Report the environment (CPU, RAM, Python and SQLite versions). Commit raw results to `eval/results/`.

---

## 11. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| eRepo snapshot leaks future knowledge into the old release | Wrong RS results | DD6 approval-date filter |
| Activating-variant list incomplete | D4/RS5 weak | Curated CSV with sources; state it as a limitation |
| ClinVar term changes between releases (e.g. "Conflicting interpretations" renamed) | False "changes" | Normalization layer (already handled in `clinvar_panel.py`) |
| Exact matching misses some indels | Under-classification | Documented limitation; cohort built from ClinVar keys |
| SQLite locking during re-evaluation | Slow or failed runs | Single writer CLI job, WAL mode; schema portable to PostgreSQL |
| Scope creep in the UI | Delays results | UI is week 4 and off the critical path |
| Clinical over-claiming | Ethics/review risk | "Advisory only" banner; synthetic data only; specialist-review caveat in the paper |

---

## 12. Paper traceability matrix

Every claim the paper makes, and where this plan delivers it.

| Paper | Requirement | Plan |
|---|---|---|
| Contribution 1, §3.2 | Versioned knowledge; every alert records the knowledge version | `KbRelease`, `Rule.version`, `AlertProvenance` (DD13) |
| Contribution 2, §4.4, Alg. 2 | Change-driven re-check of stored variants only, lab-independent | `reevaluate_release` (§7 F5) |
| Contribution 3, Table III | Six medication-aware rules + cascade prompt | `rules_v1.yaml`, rule engine, DD1, DD7, DD8, DD15 |
| Contribution 4, §5 | Three-strategy evaluation | §10, `eval/benchmark.py` |
| Table I "re-applies revised rules" | Rule revision handling | Algorithm 3 (DD2) |
| Table II F1 | Diagnosis + age at diagnosis, medications, VCF upload, one patient view | F1, patient detail page |
| Table II F2, §4.2 | 7 genes, ACMG/AMP labels, expert-panel precedence, "not classified" ≠ VUS | F2, DD4, DD6, DD16 |
| Table II F3, §4.3 | Advisory only, info note when therapy condition fails | Alg. 1 in `rule_engine.py`, "advisory only" banner |
| Table II F4, §4.5 | FHIR R4 Bundle, `amended` after reclassification | `fhir_export.py` |
| Table II F5 | Import releases/rules, re-alert / supersede / update provenance, log every run | F5, `ReevaluationRun`, `AuditEvent` |
| §3 non-functional | Roles, hashed passwords, synthetic data only, full provenance | §7 Web and security, DD13 |
| §3.1 | F1–F3 run on upload **or medication change** | Medication edit triggers F3 |
| §4.1 | VCF 4.2, cyvcf2, GRCh38, zygosity, reject malformed | `vcf_service.py`, `malformed/` tests |
| Table IV | Five change categories and actions, high priority on upgrade | F5, DD3, DD12, DD14 |
| §4.5 | Scheduled release-import job | `flask import-release` + `flask reevaluate` run from cron |
| §5.1 | 30 + 10,000 Synthea patients, families, real pair + controlled set, D4 v1→v2 | §10 |
| Table V | S1–S12, RS1–RS7 | §9 scenario tests from `ge_ehr_test_data_v2` |
| Table VI, §5.4 | Six metrics | §10 |
| §6 | Low alert burden | Alert-burden metric (§10) |
| §6.1 | Limitations (synthetic, exact match, SQLite, simplified rules) | §2 out of scope, §11 risks |

### Fixes to make in the paper itself

- **Filename:** the PDF is called "Machine_Learning_Based_Identification_of_Disease_Associated_Genes…", which doesn't match the paper's content.
- **Table V:** S2 and S4 expect "info note only", but D5 also fires for them (DD1).
- **Algorithm 2:** covers only ClinVar changes, yet Table IV and RS5 need rule revisions. Add Algorithm 3 (DD2).
- **Section 5.3:** has a heading but no text. Add a sentence pointing to Table V.
- **"Point-in-time correctness":** used as a metric but never defined. Use DD10.
- **Section 2.3:** only one sentence. Expand it with the Brotman Baty findings [6], or merge it into §2.4.
- **References:** [12] has no authors and [7] has no DOI.
- **Methods:** record DD1–DD16 as design choices.
- **Section 5.1:** say that the correctness cohort uses real reclassified variants (`ge_ehr_test_data_v2`).

---

## 13. Definition of done (per feature)

- Code merged through a PR with review by one teammate, and CI green (ruff + pytest)
- Unit tests for new logic, and scenario tests updated
- Migration included if the schema changed
- Decisions recorded in `docs/decisions.md`
- No real patient data and no secrets in the repo

---

## 14. First actions (day 1)

1. `git init`. Add a `.gitignore` covering `.venv/`, `data/clinvar/*.vcf.gz`, `*.db` and `instance/`. Make the first commit.
2. Rename `clinvar_old (1).vcf.gz` to `data/clinvar/clinvar_20230107.vcf.gz`, move the other releases there, and delete the empty `clinvar_old.vcf.gz`.
3. Export the four `.numbers` files to CSV in `data/synthetic/synthea/`.
4. Agree on the §7 interfaces and the DD1–DD12 decisions as a team.
5. Start week 0: scaffold, CI and models.
