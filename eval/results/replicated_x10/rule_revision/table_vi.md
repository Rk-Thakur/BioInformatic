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
