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

Open http://localhost:5000. On first start the container loads ClinVar 2023-01-07, the
Synthea patients and their VCFs, then re-evaluates everyone to ClinVar 2026-10-19, so the
app opens in the "after a new ClinVar release" state. Without Docker, run `flask run` with
the database built as in the README.

## 1. Lab technician: upload, but no recommendations (S10, S12)

1. Log in as **demo_lab_tech**.
2. Open any patient, for example `0661fbba`. Variants and classifications are visible;
   alerts are not. The menu has no Alerts or Knowledge page (opening `/alerts/` returns 403).
3. Upload `ge_ehr_test_data_v2/malformed/S10_truncated_row.vcf`. It is rejected with the
   reason "expected 10 columns".
4. Log out.

## 2. Clinician: medication-aware alerts (S1, S2, S4)

Log in as **demo_clinician**.

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
  version (D4 v2 pending) and the re-evaluation run: 40 of 1458 variants changed,
  23 patients re-evaluated.

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

## Reset

```bash
docker compose down -v
```

This deletes the database volume. The next `docker compose up` loads everything again.
Set `REEVALUATE_TO: ""` in `docker-compose.yml` to start in the 2023 state, then run
`docker compose exec web flask reevaluate 2026-10-19` live.
