# Healthy relatives demo dataset (Stage 7, DD43)

24 synthetic people **without diabetes**. Each is a parent, sibling or child of a patient
in `ge_ehr_test_data_v2`: the relatives that rule D5 says should be offered cascade
testing. 18 carry the family variant, 6 do not. They are not real people.

Rebuild (deterministic, same bytes every time):

```bash
python -m scripts.build_healthy_relatives
```

Load into the app (after `flask seed-synthea`, which loads their families):

```bash
flask seed-relatives
```

## Files

| File | Contents |
|---|---|
| `relatives.csv` | One row per person: ID, name, proband (the patient they are related to), relation, sex, birth date, `diabetes = none`, carrier yes/no, the family gene and variant, which 1000 Genomes backgrounds their genome came from |
| `vcfs/<relative_id>.vcf.gz` | Their gene test: a single-sample GRCh38 VCF, the same format as the cohort |
| `expected_risk.csv` | The risk level each person should get under each ClinVar release (2023-01-07, 2023-12-30, 2026-10-19), and which variant decides it. Ground truth for the Step 6 tests |
| `manifest.json` | Counts per level and release |

## How the genomes are made

The cohort VCFs are phased 1000 Genomes backgrounds. For every chromosome a relative
inherits one haplotype from the proband's background and one from a different
1000 Genomes background (the "other parent"), so about half their variants are shared
with the proband. Carriers also get the family's inserted ClinVar variant. Siblings use
the same model as parents and children; this is a demo, not a pedigree simulator.

## What the dataset shows (ClinVar 2023-01-07 → 2026-10-19)

| Situation | People | Level 2023 → 2026 |
|---|---|---|
| FAMILY1 carriers: HNF1A VUS became LP | 3 | uncertain → **high** |
| HNF1A / HNF4A / HNF1B / INS / ABCC8 pathogenic in both releases | 7 | high → high |
| Family variant not yet in ClinVar in 2023 (HNF4A, KCNJ11, GCK) | 4 | normal → high or mild |
| RS2 carriers: GCK LP became VUS | 2 | mild → **uncertain** |
| RS4 carrier: HNF1A VUS became LB | 1 | uncertain → **normal** |
| Non-carriers | 6 | normal, except one who inherited an ABCC8 VUS from the background (→ uncertain in 2026) |
| S9 carrier: GCK variant VUS in 2023, benign in 2026 | 1 | uncertain → uncertain (an unrelated ABCC8 VUS in the background) |

Every level (high, mild, uncertain, normal) appears, and every kind of reclassification
moves at least one healthy person's risk.

## Why "conflicting" ClinVar entries do not count

Every healthy genome carries 11–20 variants in these genes that ClinVar lists as
"conflicting classifications"; most are common polymorphisms (one has a 1000 Genomes
frequency of 47%). Counting them as "uncertain" made all 24 people uncertain, so
`risk_levels_v1.yaml` treats CONFLICT like the treatment rules do: not used (level
normal). Only a true VUS makes a person "uncertain".
