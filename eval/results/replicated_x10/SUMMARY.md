# Evaluation results (paper Table VI)

Generated 2026-11-02T13:41:53 at commit 388550a (uncommitted changes); Python 3.14.7, SQLite 3.53.4, Apple M4.

Re-run with `python -m eval.benchmark`. Metric definitions: `eval/metrics.py`.

### real: Real ClinVar releases 2023-01-07 -> 2026-10-19

330 patients (33-patient test cohort replicated x10, not Synthea); 1 knowledge change(s).

| Metric | Alert-once | Full recompute | GE-EHR (change-driven) |
|---|---|---|---|
| Re-alert sensitivity | 0% | 100% | 100% |
| Stale alerts after release | 40 | 0 | 0 |
| Missing alerts after release | 210 | 0 | 0 |
| Notification precision | n/a | 100% | 100% |
| Notifications | 0 | 230 | 230 |
| Records examined | 0 | 173,680 | 121,550 |
| Patients re-evaluated | 0 | 330 | 230 |
| Point-in-time correctness | 100% | 100% | 100% |
| Final alerts identical to fresh evaluation | **no** | yes | yes |
| Notifications per 1,000 patients | 0.0 | 697.0 | 697.0 |
| Changed variants with no notification | 100% | 72% | 72% |
| Time (median of repeats) | 282 ms | 4,157 ms | 3,165 ms |

### stepwise: Two real releases in a row: 2023-01-07 -> 2023-12-30 -> 2026-10-19

330 patients (33-patient test cohort replicated x10, not Synthea); 2 knowledge change(s).

| Metric | Alert-once | Full recompute | GE-EHR (change-driven) |
|---|---|---|---|
| Re-alert sensitivity | 0% | 100% | 100% |
| Stale alerts after release | 40 | 0 | 0 |
| Missing alerts after release | 210 | 0 | 0 |
| Notification precision | n/a | 100% | 100% |
| Notifications | 0 | 230 | 230 |
| Records examined | 0 | 347,360 | 133,950 |
| Patients re-evaluated | 0 | 660 | 250 |
| Point-in-time correctness | 100% | 100% | 100% |
| Final alerts identical to fresh evaluation | **no** | yes | yes |
| Notifications per 1,000 patients | 0.0 | 697.0 | 697.0 |
| Changed variants with no notification | 100% | 76% | 76% |
| Time (median of repeats) | 624 ms | 8,344 ms | 4,281 ms |

### controlled: Controlled release: 2023-01-07 with 23 injected changes (eval/controlled/)

330 patients (33-patient test cohort replicated x10, not Synthea); 1 knowledge change(s).

| Metric | Alert-once | Full recompute | GE-EHR (change-driven) |
|---|---|---|---|
| Re-alert sensitivity | 0% | 100% | 100% |
| Stale alerts after release | 100 | 0 | 0 |
| Missing alerts after release | 380 | 0 | 0 |
| Notification precision | n/a | 100% | 100% |
| Notifications | 0 | 430 | 430 |
| Records examined | 0 | 173,680 | 108,640 |
| Patients re-evaluated | 0 | 330 | 210 |
| Point-in-time correctness | 100% | 100% | 100% |
| Final alerts identical to fresh evaluation | **no** | yes | yes |
| Notifications per 1,000 patients | 0.0 | 1303.0 | 1303.0 |
| Changed variants with no notification | 100% | 35% | 35% |
| Time (median of repeats) | 395 ms | 4,088 ms | 2,882 ms |

### rule_revision: Rule revision D4 v1 -> v2 (adds ABCC8) under ClinVar 2026-10-19

330 patients (33-patient test cohort replicated x10, not Synthea); 1 knowledge change(s).

| Metric | Alert-once | Full recompute | GE-EHR (change-driven) |
|---|---|---|---|
| Re-alert sensitivity | 0% | 100% | 100% |
| Stale alerts after release | 0 | 0 | 0 |
| Missing alerts after release | 10 | 0 | 0 |
| Notification precision | n/a | 100% | 100% |
| Notifications | 0 | 10 | 10 |
| Records examined | 0 | 173,680 | 20,260 |
| Patients re-evaluated | 0 | 330 | 40 |
| Point-in-time correctness | 100% | 100% | 100% |
| Final alerts identical to fresh evaluation | **no** | yes | yes |
| Notifications per 1,000 patients | 0.0 | 30.3 | 30.3 |
| Changed variants with no notification | n/a | n/a | n/a |
| Time (median of repeats) | 6 ms | 3,394 ms | 561 ms |
