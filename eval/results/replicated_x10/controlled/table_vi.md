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
