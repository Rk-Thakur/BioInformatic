# Evaluation results (paper Table VI)

Generated 2026-11-02T13:36:18 at commit b8387b7 (uncommitted changes); Python 3.14.7, SQLite 3.53.4, Apple M4.

Re-run with `python -m eval.benchmark`. Metric definitions: `eval/metrics.py`.

### real: Real ClinVar releases 2023-01-07 -> 2026-10-19

33 patients (33-patient test cohort); 1 knowledge change(s).

| Metric | Alert-once | Full recompute | GE-EHR (change-driven) |
|---|---|---|---|
| Re-alert sensitivity | 0% | 100% | 100% |
| Stale alerts after release | 4 | 0 | 0 |
| Missing alerts after release | 21 | 0 | 0 |
| Notification precision | n/a | 100% | 100% |
| Notifications | 0 | 23 | 23 |
| Records examined | 0 | 17,368 | 12,155 |
| Patients re-evaluated | 0 | 33 | 23 |
| Point-in-time correctness | 100% | 100% | 100% |
| Final alerts identical to fresh evaluation | **no** | yes | yes |
| Notifications per 1,000 patients | 0.0 | 697.0 | 697.0 |
| Changed variants with no notification | 100% | 72% | 72% |
| Time (median of repeats) | 47 ms | 488 ms | 386 ms |

### stepwise: Two real releases in a row: 2023-01-07 -> 2023-12-30 -> 2026-10-19

33 patients (33-patient test cohort); 2 knowledge change(s).

| Metric | Alert-once | Full recompute | GE-EHR (change-driven) |
|---|---|---|---|
| Re-alert sensitivity | 0% | 100% | 100% |
| Stale alerts after release | 4 | 0 | 0 |
| Missing alerts after release | 21 | 0 | 0 |
| Notification precision | n/a | 100% | 100% |
| Notifications | 0 | 23 | 23 |
| Records examined | 0 | 34,736 | 13,395 |
| Patients re-evaluated | 0 | 66 | 25 |
| Point-in-time correctness | 100% | 100% | 100% |
| Final alerts identical to fresh evaluation | **no** | yes | yes |
| Notifications per 1,000 patients | 0.0 | 697.0 | 697.0 |
| Changed variants with no notification | 100% | 76% | 76% |
| Time (median of repeats) | 92 ms | 911 ms | 493 ms |

### controlled: Controlled release: 2023-01-07 with 23 injected changes (eval/controlled/)

33 patients (33-patient test cohort); 1 knowledge change(s).

| Metric | Alert-once | Full recompute | GE-EHR (change-driven) |
|---|---|---|---|
| Re-alert sensitivity | 0% | 100% | 100% |
| Stale alerts after release | 10 | 0 | 0 |
| Missing alerts after release | 38 | 0 | 0 |
| Notification precision | n/a | 100% | 100% |
| Notifications | 0 | 43 | 43 |
| Records examined | 0 | 17,368 | 10,864 |
| Patients re-evaluated | 0 | 33 | 21 |
| Point-in-time correctness | 100% | 100% | 100% |
| Final alerts identical to fresh evaluation | **no** | yes | yes |
| Notifications per 1,000 patients | 0.0 | 1303.0 | 1303.0 |
| Changed variants with no notification | 100% | 35% | 35% |
| Time (median of repeats) | 47 ms | 490 ms | 308 ms |

### rule_revision: Rule revision D4 v1 -> v2 (adds ABCC8) under ClinVar 2026-10-19

33 patients (33-patient test cohort); 1 knowledge change(s).

| Metric | Alert-once | Full recompute | GE-EHR (change-driven) |
|---|---|---|---|
| Re-alert sensitivity | 0% | 100% | 100% |
| Stale alerts after release | 0 | 0 | 0 |
| Missing alerts after release | 1 | 0 | 0 |
| Notification precision | n/a | 100% | 100% |
| Notifications | 0 | 1 | 1 |
| Records examined | 0 | 17,368 | 2,026 |
| Patients re-evaluated | 0 | 33 | 4 |
| Point-in-time correctness | 100% | 100% | 100% |
| Final alerts identical to fresh evaluation | **no** | yes | yes |
| Notifications per 1,000 patients | 0.0 | 30.3 | 30.3 |
| Changed variants with no notification | n/a | n/a | n/a |
| Time (median of repeats) | 6 ms | 415 ms | 72 ms |
