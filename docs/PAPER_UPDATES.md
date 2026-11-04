# Paper updates: from design to measured results

Draft text for the GE-EHR paper (`docs/paper/GE-EHR_Monogenic_Diabetes.pdf`), written from
the implemented system and the measured results of 2026-11-02. Each section says **where**
the text goes and gives **draft wording**. Numbers come from `eval/results/` and
`data/clean/QUALITY_REPORT.md`; re-run `python -m eval.benchmark` before submission if code
changes. Items marked **[check]** need a co-author to confirm a fact or a citation.

---

## 1. Fixes to the current draft

| # | Where | Problem | Fix |
|---|---|---|---|
| F1 | File name | The PDF was named "Machine_Learning_Based_Identification_of_Disease_Associated_Genes…" | Renamed `GE-EHR_Monogenic_Diabetes.pdf` (done in the repo) |
| F2 | §4.2 | Transcript versions are outdated | NM_000545.6 → **NM_000545.8** (HNF1A), NM_175914.4 → **NM_175914.5** (HNF4A); NM_000162.5 (GCK) unchanged. These are the versions ClinGen eRepo uses. |
| F3 | Table V, S2 and S4 | Expected "info note only", but D5 fires for every P/LP variant | Expected outcome: "Info note (D2/D1) **and** low alert D5" (DD1) |
| F4 | §4.4, Algorithm 2 | Covers ClinVar changes only; Table I and RS5 need rule revisions | Add **Algorithm 3** (§3 below) |
| F5 | §5.3 | Heading without text | Add: "Table V lists the correctness scenarios: S1–S12 test single evaluations, RS1–RS7 test re-evaluation after a knowledge change." |
| F6 | §5.4 | "Point-in-time correctness" is not defined | Use the definition in §4 below |
| F7 | §2.3 | One sentence | Expand with what [6] measured, or merge into §2.4 **[check]** |
| F8 | Ref [7] | No DOI | Add the DOI of Naylor et al. 2024, *Communications Medicine* 4:145 **[check]** |
| F9 | Ref [12] | No authors | Add the author list for doi:10.1038/s41591-026-04477-5 **[check]** |
| F10 | §4.5 | "FHIR R4" | Implemented with FHIR **R4B** (4.3), the current R4-compatible release in `fhir.resources` |
| F11 | §5.1 | "scale cohort of 10,000 … generated with Synthea" | The scale cohort is **derived** (DD37); use the §5.1 text below |
| F12 | References | New sources are used | Add citations for the 1000 Genomes Project high-coverage release, the ClinGen Evidence Repository and NCBI Gene **[check]** |

---

## 2. Abstract: replace the last sentence

> We implemented GE-EHR and evaluated it on real ClinVar releases (2023-01-07, 2023-12-30,
> 2026-10-19), a controlled release with 23 injected reclassifications, and a rule revision,
> using a 33-patient scenario cohort and a 10,000-patient scale cohort. Change-driven
> re-evaluation produced exactly the alerts of a fresh evaluation, with 100% re-alert
> sensitivity and no stale alerts, while re-evaluating 7× fewer patients than full
> recomputation for a real release pair (3.0× faster) and 365× fewer for a rule revision
> (19× faster). An alert-once baseline missed every re-alert and left stale advice in place.

---

## 3. Methods additions

### 3.1 New §4.0 "Data preparation" (before §4.1)

> **Knowledge sources.** Three ClinVar GRCh38 VCF releases (2023-01-07, 2023-12-30,
> 2026-10-19) were restricted to the seven study genes and cleaned with fourteen rules,
> each implemented as code with a unit test and reported with row counts. ClinVar
> classifications were normalized to ACMG/AMP tiers. Compound values such as
> "Pathogenic/Likely_risk_allele" keep their pathogenic tier, and the 2024 renaming of
> "Conflicting interpretations" to "Conflicting classifications" is treated as no change.
> Records without a germline classification (18) or a usable alternate allele (11 per
> release) were dropped. Records naming several genes were assigned to a panel gene by NCBI
> Gene GRCh38 coordinates. ClinGen Monogenic Diabetes VCEP classifications (1,167; GCK,
> HNF1A, HNF4A) were linked to ClinVar by Variation ID (772) or, when the ID was missing, by
> their GRCh38 genomic HGVS (381).
>
> **Expert-panel precedence is time-aware.** An expert classification overrides ClinVar
> only for releases dated on or after its approval date, so a 2026 expert opinion never
> alters the 2023 release. Only 103 of 1,167 expert classifications had been approved by
> 2023-01-07.
>
> **Clinical data.** The Synthea diabetes export (87 patients) was converted from its
> spreadsheet format automatically. Monthly refills (5,149 rows) were merged into 79
> therapy periods, and therapy was ended at death. The export's own "current therapy" was
> wrong for 15 deceased patients. Age at diagnosis was recomputed in months, as rule D4
> requires. Deceased patients (23) are kept for family structure but receive no new alerts.

### 3.2 §4.2: activating variants (rule D4)

> ClinVar does not record whether a KCNJ11/ABCC8 variant is activating. A variant was
> treated as activating when it is P/LP and ClinVar links it to neonatal diabetes but not to
> hyperinsulinism (54 variants). Variants linked to both were excluded. A P/LP variant in
> these genes that is not on the list produces an explanatory info note instead of the D4
> alert.

### 3.3 §4.4: add Algorithm 3 and the relevance filter

> **Algorithm 3: rule revision.** When a new version of rule *r* is approved, it becomes
> current, and Algorithm 1 is re-run only for patients carrying a variant, in the genes of
> either version, whose current classification either version could match. Alerts that
> appear are notified. Treatment alerts that disappear are superseded with a review notice,
> as for downgrades.
>
> **Relevance filter.** A classification change that no current rule can match before or
> after the change (for example Benign → Benign/Likely benign) cannot alter any outcome, so
> it triggers no re-evaluation. Without this filter, common benign background variants,
> which change label between releases, would make every patient a "carrier of a changed
> variant".
>
> **Notifications.** One review notice is issued per downgraded variant. It points at the
> most severe superseded alert and names every affected rule. Alerts are never deleted.
> Each confirmation under a new release or rule version appends a provenance record, and
> the release that created an alert is kept.

### 3.4 §4.5: implementation (replace "is designed for implementation")

> GE-EHR is implemented in Python 3.12+ with Flask, SQLAlchemy 2 over SQLite (with Alembic
> migrations), cyvcf2 and fhir.resources. Knowledge import, re-evaluation and benchmarking
> are command-line jobs. The web interface enforces role-based access: laboratory
> technicians can upload VCFs but never see recommendations. Passwords are hashed, forms
> are CSRF-protected, and every import, upload, review and export is audit-logged. The test
> suite has 191 tests (87% statement coverage; 92–100% for the services). A Docker image
> reproduces the full pipeline from the raw downloads.

---

## 4. §5 Evaluation, rewritten

### 4.1 §5.1 Data (replace)

> **Scenario cohort.** Thirty Synthea diabetes patients plus three constructed patients
> (RS3–RS5) received one VCF each: real background genotypes of a 1000 Genomes Project
> high-coverage GRCh38 sample in the seven genes (391–670 variants), plus one heterozygous
> ClinVar SNV chosen for the scenario. For the reclassification scenarios, the inserted
> variants are real changes between the 2023-01-07 and 2026-10-19 releases (for example an
> HNF1A VUS that became likely pathogenic, shared by a family of four), so the expected
> outcome of every re-evaluation is known (`expected_changes.csv`).
>
> **Scale cohort.** A 10,000-patient cohort was derived from these sources rather than
> generated anew. Each patient copies one of the 87 cleaned Synthea profiles (diagnosis,
> therapy periods, conditions, deceased status; 2,699 deceased) and one of the 30 real
> 1000 Genomes backgrounds. 2% (200) also carry one rare ClinVar SNV from the 2023 release
> (60% VUS, 40% P/LP), 26 of them in families of 2–4. This gives about 5.5 million stored
> variant records.
>
> **Knowledge changes.** (i) The real release pair 2023-01-07 → 2026-10-19. In variants
> present in both, 184 moved into and 71 out of the actionable band. (ii) Two consecutive
> real changes (2023-01-07 → 2023-12-30 → 2026-10-19). (iii) A controlled release: the 2023
> release with 23 injected changes to variants carried by the scenario cohort (10
> upgrades, 5 downgrades, 3 within the actionable band, 5 within the non-actionable band).
> (iv) A rule revision: D4 version 1 (KCNJ11) → version 2 (adds ABCC8).

### 4.2 §5.4 Metrics (replace the definitions)

> All metrics compare the state after a knowledge change with an *oracle*: the same
> patients evaluated from scratch under the new knowledge. **Re-alert sensitivity** is the
> share of the oracle's treatment alerts on upgraded variants that the strategy also has.
> **Stale alerts** are open treatment alerts the oracle does not have; **missing alerts**
> are oracle alerts the strategy lacks. **Notification precision** is the share of
> notifications (new treatment alerts and review notices) that are correct. **Records
> examined** counts patient-variant records run through Algorithm 1. **Point-in-time
> correctness** is the share of alerts that Algorithm 1 reproduces from the release and
> rule version stored with them. Times are wall clock, the median of repeats, on one
> machine (Apple M4, Python 3.14, SQLite 3.53).

### 4.3 New §5.5 Results

> **Correctness.** All twelve S-scenarios and RS1–RS7 behaved as specified in Table V, with
> each patient's real Synthea therapy. In every scenario, change-driven re-evaluation ended
> in exactly the alerts of a fresh evaluation, as did full recomputation. Both achieved
> 100% re-alert sensitivity, 100% notification precision and no stale or missing alerts.
> The alert-once baseline missed every re-alert: 21 missing and 4 stale alerts for the real
> release pair in the scenario cohort, and 38 missing and 10 stale for the controlled
> release.

**Table VI (measured): 10,000-patient scale cohort.**

| Metric | Alert-once | Full recompute | GE-EHR |
|---|---|---|---|
| *Real release pair 2023-01-07 → 2026-10-19* | | | |
| Re-alert sensitivity | 0% | 100% | 100% |
| Stale / missing alerts after release | 11 / 4 | 0 / 0 | 0 / 0 |
| Notification precision | n/a | 100% | 100% |
| Patients re-evaluated | 0 | 7,301 | 1,031 |
| Records examined | 0 | 3,833,690 | 527,888 |
| Time per release | 9.4 s¹ | 88.6 s | 29.6 s |
| Point-in-time correctness | 100% | 100% | 100% |
| *Two consecutive releases* | | | |
| Patients re-evaluated (sum) | 0 | 14,602 | 1,033 |
| Time (sum) | 18.4 s¹ | 181.8 s | 52.8 s |
| *Rule revision D4 v1 → v2* | | | |
| Patients re-evaluated | 0 | 7,301 | 20 |
| Time | 0.007 s | 71.9 s | 3.8 s |

¹ Alert-once performs no re-evaluation. Its time is spent computing the change set for
the run log.

**Scenario cohort (33 patients).**

| Knowledge change | Full recompute (patients, time) | GE-EHR (patients, time) |
|---|---|---|
| Real release pair | 33, 0.49 s | 23, 0.39 s |
| Two consecutive releases | 66, 0.91 s | 25, 0.49 s |
| Controlled release (23 changes) | 33, 0.49 s | 21, 0.31 s |
| Rule revision | 33, 0.42 s | 4, 0.07 s |

> **Efficiency.** In the scale cohort, change-driven re-evaluation examined 14% of the
> records full recomputation examined for the real release pair (3.0× faster), and 0.3%
> for the rule revision (19× faster). The scenario cohort is enriched for carriers by
> design, so its gain is smaller (Figure 2, Figure 3).
>
> **Alert burden.** A real release produced 1.1 notifications per 1,000 patients in the
> scale cohort, and 86% of changed variants produced no notification at all.

**Figures.** These are in `eval/results/figures/`; regenerate them with
`python -m eval.figures`.

- *Figure 2. Patients re-evaluated per knowledge change (10,000 patients).*
  `fig_patients_reevaluated.svg`
- *Figure 3. Time per knowledge change (10,000 patients).* `fig_time_per_change.svg`
- *Figure 4. Fate of 2023 VUS by 2026, per gene.* `fig_vus_resolution.svg`

---

## 5. §6 Discussion additions

> **Reclassification is frequent in exactly the treatment genes.** Between 2023-01-07 and
> 2026-10-19, 22.9% of GCK VUS (39 of 170) and 6.2% of HNF1A VUS (15 of 243) became P/LP.
> This is consistent with the GCK re-curation result of [4], and these are the genes where
> a reclassification changes daily treatment.
>
> **Why the relevance filter matters in practice.** Real genomes share common benign
> variants whose ClinVar labels drift (for example Benign → Benign/Likely benign). A naive
> change-driven design would treat nearly every patient as affected. Filtering on whether
> any rule can see the change keeps the work proportional to clinically meaningful
> changes, with results unchanged.
>
> **Time-aware expert precedence.** Applying today's expert classifications to an old
> release would silently hide the very reclassifications the system is meant to detect.
> Only 9% of the expert classifications existed at the 2023 release.

### §6.1 Limitations: add or replace

- The scale cohort is derived from 87 Synthea profiles and 30 real backgrounds. Shared
  backgrounds make some background changes reach about 1/30 of the cohort at once; a
  cohort with more distinct genomes would make the change-driven advantage larger.
- "Activating" for D4 is inferred from ClinVar disease names (54 variants), not curated
  functional evidence.
- Synthea prescribes no sulfonylureas and records no pregnancies. Scenario S2's
  sulfonylurea is added for the test, and the D1 pregnancy caveat is implemented but never
  triggered by this cohort.
- Variant matching is exact on (chromosome, position, ref, alt). Cohort variants come from
  ClinVar's own representation, so normalization was not needed here, but real laboratory
  VCFs would need it.
- Timings are from one machine with SQLite; absolute values will differ on a server
  database, while the ratios between strategies are the result.

### §6.2 Future work: update

Remove "implementation of the prototype and reporting of measured results" and
"analysis of real ClinVar history" (both done). Keep the MODY probability calculator and
the ingestion of laboratory amended reports. Add a Synthea-generated cohort with more
distinct genomes, and curated activating-variant evidence.
