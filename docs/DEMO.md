# GE-EHR demo script (about 10 minutes)

A walkthrough that shows each contribution of the paper in the running system. Every
patient named here comes from the scenario cohort; names look like
`Synthea 5dbf61ce (S8/RS1/RS6)`. Search for the 8-character ID on the Patients page.

## 0. Start

```bash
docker compose up --build
```

```bash
docker compose exec web cat /app/instance/demo_accounts.txt
```

Open http://localhost:5000. Visitors first see the **home page**, which explains the problem, how
GE-EHR works and the measured results, with a **Log in** button at the top. On first start the container loads ClinVar 2023-01-07, the
Synthea patients and their VCFs, 24 healthy relatives with their gene tests, then
re-evaluates everyone to ClinVar 2026-10-19, so the
app opens in the "after a new ClinVar release" state. Without Docker, run `flask run` with
the database built as in the README.

## 1. Lab technician: upload, but no recommendations (S10, S12)

Tip: on the login page, **"or try a demo account"** logs you in with one click (demo mode).

1. Log in as **demo_lab_tech**.
2. Open any patient, for example `0661fbba`. Variants and classifications are visible;
   alerts are not. The menu has no Alerts or Knowledge page (opening `/alerts/` returns 403).
3. Upload `ge_ehr_test_data_v2/malformed/S10_truncated_row.vcf`. It is rejected with the
   reason "expected 10 columns".
4. Log out.

## 2. Clinician: medication-aware alerts (S1, S2, S4)

Log in as **demo_clinician**. You land on the **Overview**: items waiting for review (62), the
latest re-evaluation as a flow (1,458 variants checked → 40 changed → 38 people re-evaluated →
39 new alerts: 23 patients with diabetes plus 15 healthy relatives), the high-priority
timeline, the advice in force per rule and the genetic risk of the relatives (section 7).
The moon button switches light and dark.

- `0661fbba (S1)`: HNF1A pathogenic, on insulin. **Rule D2, high**: consider low-dose
  sulfonylurea. **Rule D5, low**: cascade testing of relatives.
- `074c01f3 (S2)`: the same gene, but already on a sulfonylurea. D2 is an **info note**
  that says why ("already on sulfonylurea").
- `069da380 (S4)`: GCK pathogenic, no medication. D1 is an info note (nothing to stop).

Live demo of F3: on the S1 patient, add a medication (class *sulfonylurea*). The page says
"1 no longer apply": the D2 alert is superseded (see History) and becomes an info note.
Stop the medication again and D2 returns.

## 3. Re-evaluation after a ClinVar release (contribution 2, Table IV)

Open the **Alerts** worklist. High-priority items come first.

- **Upgrade (RS1/RS6):** the family `5dbf61ce`, `70c6b56d`, `803c19aa`, `a8a8e624` share an
  HNF1A variant that was a VUS in 2023 and is likely pathogenic in 2026. Each member has a
  new **D2 high** alert marked *reclassified* and *high priority*. Their old D6
  "monitored VUS" note is in History as superseded.
- **Downgrade (RS2):** `7c7e4fdc` and `85e496af`. A GCK variant went from LP to VUS. A
  **review notice** explains that D1 and D5 no longer apply and that GE-EHR never changes
  therapy automatically. The superseded alerts remain in History.
- **Within band (RS3):** `737a78a3`, HNF4A LP → P. Same alerts as before, no notification.
  The alert footer shows "confirmed under: ClinVar 2023-01-07 (LP), ClinVar 2026-10-19 (P)".

Click **Mark reviewed** on one alert: it leaves the worklist but stays in force on the
patient page.

## 4. Provenance and FHIR (contribution 1, S11)

- On any alert: "created under ClinVar …, rule D2 v1 · source [1, 7]".
- **FHIR export** on a family patient (`5dbf61ce`): one Observation per variant found in
  ClinVar (about 70). The HNF1A variant that went from VUS to LP has status **amended**,
  because its interpretation changed after it was first reported; all others are
  **final**. Compare `0eaaad98 (S1)`, whose variant was already pathogenic in 2023: every
  Observation is final. (`0661fbba (S1)` also shows one amended variant: LP in 2023, P in
  2026.)
- The **Knowledge** page lists all three ClinVar releases (2026-10-19 current), every rule
  version (D4 v2 pending), the genetic risk levels in force (G1–G6) and the re-evaluation
  run: 40 of 1,458 variants changed, 38 people re-evaluated.

## 5. Rule revision (Algorithm 3, RS5)

```bash
docker compose exec web sh -c "flask import-rules data/curated/rules_v2.yaml && flask apply-rule D4 2"
```

Patient `781eee69 (RS5)` (ABCC8, diagnosed at 3 months, on insulin) now has a **D4 high**
alert: "refer to specialist to consider high-dose sulfonylurea". The run re-checked only
the few carriers of a KCNJ11/ABCC8 variant D4 can match.

## 6. The evaluation (Table VI)

```bash
docker compose run --rm web sh -c "python -m scripts.make_controlled_release && python -m eval.benchmark"
```

About a minute. It writes `eval/results/SUMMARY.md`: alert-once vs full recompute vs
GE-EHR on four knowledge changes. The 10,000-patient run takes about 30 minutes; see the
README.

## 7. Genetic risk in healthy relatives (Stage 7 extension, not in the paper)

24 people **without diabetes**, each a relative of a cohort patient, were tested because
rule D5 recommends cascade testing. They never get treatment advice; they get a genetic
risk level instead: **high** (P/LP in HNF1A, HNF4A, HNF1B, KCNJ11, ABCC8, INS), **mild**
(P/LP in GCK), **uncertain** (a VUS) or **normal**. Levels and advice live in
`data/curated/risk_levels_v1.yaml` (current) and `risk_levels_v2.yaml` (pending, step 8).

1. **Patients → Without diabetes** (24 people). The **Genetic risk** column shows each level.
   The Overview box "Genetic risk in relatives" counts 12 high, 2 mild, 4 uncertain, 6 normal.
2. **Upgrade moves a relative's risk:** open `Relative 4ad3850a (child of 5dbf61ce)`. She
   carries the FAMILY1 HNF1A variant (VUS in 2023, LP in 2026). The **Genetic risk card** says
   **High genetic risk**; the finding is marked *reclassified* and *high priority*, and the
   old "uncertain" note is in History. Advice: regular glucose checks and genetic counselling.
3. **Family tree:** open the patient `5dbf61ce` and scroll to **Family**. The tree draws itself:
   untested parents above, the patient (filled = diabetes, arrow = P) with two siblings, and
   three children below. Rings show each relative's risk; a red dot marks the carriers.
   Click **Jan 2023**: the three carriers turn grey (uncertain) and their dots disappear,
   because the family variant was only a VUS. Click **Replay history** to watch ClinVar
   Jan 2023 → Dec 2023 → Oct 2026: the rings turn red with a ripple, and the caption says
   "3 risk levels changed since ClinVar Dec 2023". The table below lists the same people.
4. **Downgrade:** `Relative 55e82252 (child of 7c7e4fdc)` carried a GCK variant that went
   from LP to VUS. Mild became uncertain, and a **review notice** asks the clinician to review
   any follow-up arranged because of it.
5. **Pregnancy note:** `Relative 4c4be9c0 (child of 3a54d594)` is a woman carrying an HNF4A
   variant; her finding adds the note for the obstetric team.
6. **Lab technician:** log in as **demo_lab_tech** and open any relative. The record, the
   variants and the family names are visible; no risk card, column or level anywhere.
7. **Live:** as the clinician, **New patient** → diabetes type **No diabetes**, family link
   `5dbf61ce`, relation *Child*. Upload
   `ge_ehr_healthy_relatives/vcfs/4ad3850a-059a-5b5e-9b72-1db5d5f6d170.vcf.gz`: the card shows
   **High genetic risk** straight away.

8. **Revising the risk levels (v1 → v2):** the Knowledge page shows risk levels v1
   *current* and v2 *pending*. Version 2 counts rare "conflicting" variants as uncertain
   again and ignores common ones (found in 5% of people or more). Apply it:

   ```bash
   docker compose exec web flask apply-risk-levels 2
   ```

   Only the 8 people whose findings change are re-checked; 2 of them move from normal
   to **uncertain** (Overview: 12 high, 2 mild, 6 uncertain, 4 normal). Other findings
   record "confirmed under risk levels v2" only when re-checked.

The 24 people and their expected levels for every ClinVar release are in
`ge_ehr_healthy_relatives/` (README, `expected_risk.csv`). Set `SEED_RELATIVES: "0"` in
`docker-compose.yml` to leave them out.

## Reset

```bash
docker compose down -v
```

This deletes the database volume. The next `docker compose up` loads everything again.
Set `REEVALUATE_TO: ""` in `docker-compose.yml` to start in the 2023 state, then run
`docker compose exec web flask reevaluate 2026-10-19` live.
