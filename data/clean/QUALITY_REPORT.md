# Data quality report (Stage 1a)

Generated 2026-10-29 by `python -m scripts.clean.run_all`. Raw inputs in `data/raw/` are never modified; every number below is recomputed on each run.

## C1 · Raw inputs

**Raw files (data/raw/MANIFEST.csv)**

| File | Size | SHA-256 (first 12) |
|---|---|---|
| clingen/erepo.tabbed.txt | 32,540,276 | 9eeef6fe5419 |
| clinvar/clinvar_20230107.vcf.gz | 62,605,025 | c463223213d7 |
| clinvar/clinvar_20231230.vcf.gz | 91,208,165 | 86f055615b67 |
| clinvar/clinvar_20261019.vcf.gz | 200,670,148 | 1dd89965cdd8 |
| synthea/diabetes_conditions.numbers | 326,907 | 4416c81f9e38 |
| synthea/diabetes_medications.numbers | 1,154,572 | 6c29a2427839 |
| synthea/diabetes_patients.numbers | 308,138 | 90688844e869 |
| synthea/scenarios.numbers | 234,970 | 8a6577813aca |

## C11 · Gene coordinates

**Panel genes (GRCh38, 1-based)**

| Gene | Chrom | Start | End | Strand |
|---|---|---|---|---|
| GCK | 7 | 44,143,213 | 44,189,439 | - |
| HNF1A | 12 | 120,978,543 | 121,002,512 | + |
| HNF4A | 20 | 44,355,699 | 44,434,596 | + |
| HNF1B | 17 | 37,686,431 | 37,745,059 | - |
| KCNJ11 | 11 | 17,385,248 | 17,389,346 | - |
| ABCC8 | 11 | 17,392,498 | 17,476,845 | - |
| INS | 11 | 2,159,779 | 2,161,209 | - |

- Source: NCBI Gene (RS_2025_08). No two panel genes overlap, so position decides C6.

## C2–C8 · ClinVar clinvar_20230107.vcf.gz

| Rule | What | Rows |
|---|---|---:|
| in | records listing a panel gene | 4,397 |
| C5 | dropped: ALT missing or multi-allelic | 11 |
| C4 | dropped: no germline CLNSIG | 0 |
| C4 |   of which oncogenicity/somatic only | 0 |
| C4 |   of which haplotype/genotype only (CLNSIGINCL) | 0 |
| C2 | kept: risk-allele combination mapped to its tier | 74 |
| C2 | kept as OTHER (no ACMG/AMP tier, non-actionable) | 50 |
| C3 | old 'Conflicting interpretations' term mapped to CONFLICT | 870 |
| C6 | several panel genes listed, resolved by position | 7 |
| C6 | several panel genes listed, nearest gene chosen (flagged) | 0 |
| C7 | kept, flagged: indel longer than 50 bp | 11 |
| C11 | kept, flagged: more than 5,000 bp outside the assigned gene | 0 |
| out | clean records | 4,386 |

**Clean records by gene**

| Gene | Records |
|---|---|
| GCK | 736 |
| HNF1A | 705 |
| HNF4A | 433 |
| HNF1B | 491 |
| KCNJ11 | 359 |
| ABCC8 | 1,535 |
| INS | 127 |

**Clean records by classification**

| Class | Records |
|---|---|
| VUS | 1,143 |
| CONFLICT | 870 |
| LB | 844 |
| P | 484 |
| LP | 419 |
| B | 275 |
| B/LB | 152 |
| P/LP | 149 |
| OTHER | 50 |

- OTHER terms: not_provided (26), Likely_risk_allele (12), Uncertain_risk_allele (9), drug_response (2), risk_factor (1)
- Release date 2023-01-07 → `data/clean/clinvar_20230107.panel.tsv`

## C2–C8 · ClinVar clinvar_20231230.vcf.gz

| Rule | What | Rows |
|---|---|---:|
| in | records listing a panel gene | 5,248 |
| C5 | dropped: ALT missing or multi-allelic | 11 |
| C4 | dropped: no germline CLNSIG | 0 |
| C4 |   of which oncogenicity/somatic only | 0 |
| C4 |   of which haplotype/genotype only (CLNSIGINCL) | 0 |
| C2 | kept: risk-allele combination mapped to its tier | 84 |
| C2 | kept as OTHER (no ACMG/AMP tier, non-actionable) | 45 |
| C3 | old 'Conflicting interpretations' term mapped to CONFLICT | 989 |
| C6 | several panel genes listed, resolved by position | 7 |
| C6 | several panel genes listed, nearest gene chosen (flagged) | 0 |
| C7 | kept, flagged: indel longer than 50 bp | 13 |
| C11 | kept, flagged: more than 5,000 bp outside the assigned gene | 0 |
| out | clean records | 5,237 |

**Clean records by gene**

| Gene | Records |
|---|---|
| GCK | 933 |
| HNF1A | 801 |
| HNF4A | 494 |
| HNF1B | 547 |
| KCNJ11 | 423 |
| ABCC8 | 1,896 |
| INS | 143 |

**Clean records by classification**

| Class | Records |
|---|---|
| VUS | 1,351 |
| LB | 1,069 |
| CONFLICT | 989 |
| P | 599 |
| LP | 576 |
| B | 276 |
| P/LP | 170 |
| B/LB | 162 |
| OTHER | 45 |

- OTHER terms: Likely_risk_allele (18), not_provided (15), Uncertain_risk_allele (10), drug_response (2)
- Release date 2023-12-30 → `data/clean/clinvar_20231230.panel.tsv`

## C2–C8 · ClinVar clinvar_20261019.vcf.gz

| Rule | What | Rows |
|---|---|---:|
| in | records listing a panel gene | 8,410 |
| C5 | dropped: ALT missing or multi-allelic | 11 |
| C4 | dropped: no germline CLNSIG | 18 |
| C4 |   of which oncogenicity/somatic only | 0 |
| C4 |   of which haplotype/genotype only (CLNSIGINCL) | 0 |
| C2 | kept: risk-allele combination mapped to its tier | 66 |
| C2 | kept as OTHER (no ACMG/AMP tier, non-actionable) | 39 |
| C3 | old 'Conflicting interpretations' term mapped to CONFLICT | 0 |
| C6 | several panel genes listed, resolved by position | 1 |
| C6 | several panel genes listed, nearest gene chosen (flagged) | 0 |
| C7 | kept, flagged: indel longer than 50 bp | 14 |
| C11 | kept, flagged: more than 5,000 bp outside the assigned gene | 0 |
| out | clean records | 8,381 |

**Clean records by gene**

| Gene | Records |
|---|---|
| GCK | 1,419 |
| HNF1A | 1,285 |
| HNF4A | 805 |
| HNF1B | 973 |
| KCNJ11 | 585 |
| ABCC8 | 3,106 |
| INS | 208 |

**Clean records by classification**

| Class | Records |
|---|---|
| VUS | 2,660 |
| LB | 1,956 |
| P | 1,019 |
| LP | 988 |
| CONFLICT | 971 |
| B | 285 |
| P/LP | 247 |
| B/LB | 216 |
| OTHER | 39 |

- OTHER terms: Likely_risk_allele (15), not_provided (12), Uncertain_risk_allele (9), drug_response (2), association_not_found (1)
- Release date 2026-10-19 → `data/clean/clinvar_20261019.panel.tsv`

## Release-to-release changes (what F5 must handle)

**2023-01-07 → 2023-12-30: variants present in both releases (4,384)**

| Change | Variants |
|---|---|
| upgrade | 48 |
| downgrade | 45 |
| within_actionable | 69 |
| within_nonactionable | 177 |
| unchanged | 4,045 |

**2023-01-07 → 2023-12-30: upgrades and downgrades by gene**

| Gene | Upgrades | Downgrades | Within actionable | Within non-actionable |
|---|---|---|---|---|
| GCK | 43 | 8 | 30 | 17 |
| HNF1A | 2 | 1 | 2 | 11 |
| HNF4A | 3 | 8 | 10 | 87 |
| HNF1B | 0 | 0 | 5 | 10 |
| KCNJ11 | 0 | 0 | 3 | 5 |
| ABCC8 | 0 | 14 | 18 | 34 |
| INS | 0 | 14 | 1 | 13 |

**2023-12-30 → 2026-10-19: variants present in both releases (5,231)**

| Change | Variants |
|---|---|
| upgrade | 171 |
| downgrade | 56 |
| within_actionable | 136 |
| within_nonactionable | 274 |
| unchanged | 4,594 |

**2023-12-30 → 2026-10-19: upgrades and downgrades by gene**

| Gene | Upgrades | Downgrades | Within actionable | Within non-actionable |
|---|---|---|---|---|
| GCK | 80 | 24 | 60 | 43 |
| HNF1A | 18 | 8 | 20 | 61 |
| HNF4A | 22 | 3 | 10 | 35 |
| HNF1B | 1 | 6 | 8 | 26 |
| KCNJ11 | 10 | 3 | 5 | 16 |
| ABCC8 | 32 | 11 | 33 | 85 |
| INS | 8 | 1 | 0 | 8 |

**2023-01-07 → 2026-10-19: variants present in both releases (4,378)**

| Change | Variants |
|---|---|
| upgrade | 184 |
| downgrade | 71 |
| within_actionable | 166 |
| within_nonactionable | 385 |
| unchanged | 3,572 |

**2023-01-07 → 2026-10-19: upgrades and downgrades by gene**

| Gene | Upgrades | Downgrades | Within actionable | Within non-actionable |
|---|---|---|---|---|
| GCK | 107 | 23 | 74 | 52 |
| HNF1A | 19 | 7 | 18 | 65 |
| HNF4A | 18 | 8 | 16 | 97 |
| HNF1B | 0 | 6 | 10 | 34 |
| KCNJ11 | 10 | 1 | 7 | 19 |
| ABCC8 | 29 | 19 | 38 | 100 |
| INS | 1 | 7 | 3 | 18 |

- 2023-01-07 → 2023-12-30: 853 variants are new (292 of them P/LP, which a patient carrying them would see as an upgrade from 'not classified'); 2 disappeared (2 of them P/LP).
- 2023-12-30 → 2026-10-19: 3,150 variants are new (794 of them P/LP, which a patient carrying them would see as an upgrade from 'not classified'); 6 disappeared (0 of them P/LP).
- 2023-01-07 → 2026-10-19: 4,003 variants are new (1,091 of them P/LP, which a patient carrying them would see as an upgrade from 'not classified'); 8 disappeared (2 of them P/LP).

## C9–C10 · ClinGen eRepo

| Rule | What | Rows |
|---|---|---:|
| in | rows for panel genes | 1,167 |
| C10 | dropped: retracted | 0 |
| C10 | dropped: duplicate eRepo UUID | 0 |
| C10 | dropped: missing or invalid approval date | 0 |
| C10 | dropped: unknown assertion text | 0 |
| C10 | linked to ClinVar by Variation ID | 772 |
| C10 | has a Variation ID that is in none of the ClinVar releases | 1 |
| C10 | no Variation ID, linked by GRCh38 HGVS (CLNHGVS) | 381 |
| C10 | no Variation ID and no HGVS match: kept, unlinked | 13 |
| C9 | ClinVar variants with more than one expert classification | 0 |
| out | clean classifications | 1,167 |

- Approval dates range from 2021-08-11 to 2026-10-22.
- Approved on or before ClinVar release 2023-01-07: 103 of 1,167 (only these may override that release, DD6).
- Approved on or before ClinVar release 2023-12-30: 341 of 1,167 (only these may override that release, DD6).
- Approved on or before ClinVar release 2026-10-19: 1,165 of 1,167 (only these may override that release, DD6).
- Rows without HGVS on the reference transcript: 26.

## C12–C14 · Synthea (pending)

- Waiting for the `.numbers` files in `data/raw/synthea/` to be exported to CSV (C12).
