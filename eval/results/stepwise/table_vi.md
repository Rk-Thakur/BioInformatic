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
