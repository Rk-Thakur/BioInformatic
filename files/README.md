# GE-EHR Test VCF Toolkit (1000 Genomes + ClinVar)

Builds one realistic genetic test file (VCF) for each of 30 Synthea diabetes patients:
real background DNA from the 1000 Genomes Project, plus one chosen ClinVar diabetes variant per patient.

## Files in this folder

| File | Purpose |
| --- | --- |
| `GE_EHR_all_in_one.ipynb` | **Easiest:** one Google Colab notebook containing everything below plus the cleaned Synthea patients |
| `scenarios.csv` | Older hand-made plan (optional). Step 2 now builds the plan from the Synthea data instead |
| `step1_fetch_1000g_regions.py` | Downloads real genotypes for the 7 diabetes genes only (small download) |
| `step2_build_patient_vcfs.py` | Inserts the ClinVar variant and writes one VCF per patient |

## Before you start (one time)

1. Install Miniconda: https://docs.conda.io/en/latest/miniconda.html
   (Windows users: use WSL/Ubuntu or Google Colab.)
2. Install the tools:
   ```bash
   conda create -n geehr -c bioconda -c conda-forge python=3.11 bcftools htslib pandas
   conda activate geehr
   ```
3. Download the latest ClinVar VCF (GRCh38) into this folder:
   ```bash
   wget https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz
   wget https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz.tbi
   ```

## Step 1: Get real background DNA (1000 Genomes)

```bash
python step1_fetch_1000g_regions.py --samples 30
```

What it does:
1. Looks up the GRCh38 position of GCK, HNF1A, HNF4A, HNF1B, KCNJ11, ABCC8 and INS from Ensembl.
2. Finds the 1000 Genomes high-coverage GRCh38 files automatically.
3. Downloads only those 7 gene regions for 30 people (a few MB, not GB).
4. Renames chromosomes `chr7` -> `7` so they match ClinVar.

Output: `background_7genes.vcf.gz`, `samples_used.txt`, `gene_regions.tsv`.

If the script cannot find the files, open
https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/1000G_2504_high_coverage/working/
in a browser, find the newest folder with "phased" in its name, and tell the script by editing `BASE`.

## Step 2: Build the patient test files

```bash
python step2_build_patient_vcfs.py --clinvar clinvar.vcf.gz \
    --patients ../synthea_diabetes/diabetes_patients.csv --background background_7genes.vcf.gz
```

It loads patients **directly from the cleaned Synthea data** (`diabetes_patients.csv`), keeps the 64 living
patients, and assigns 30 of them to the paper's test scenarios based on their **real current therapy**
(for example, S1 needs a patient on insulin). The plan is saved as `scenarios_assigned.csv`.
The same input always gives the same plan.

Then, for each assigned patient:
1. Picks a ClinVar SNV in the right gene with the right class (P, LP, VUS, B), preferring
   **expert-panel** classifications.
2. Takes one 1000 Genomes person's real variants as background.
3. Adds the ClinVar variant as heterozygous (`0/1`), like real MODY inheritance.
4. Patients in the same `share_group` (FAMILY1) get the **same** variant, for the family re-alert test (RS6).

Output:
- `patients/<patient_id>.vcf.gz` - one test file per patient (upload these into GE-EHR)
- `inserted_variants.csv` - exactly which variant went to which patient, with their therapy (your answer key)
- `scenarios_assigned.csv` - which Synthea patient was chosen for which test scenario

**Older option:** `--scenarios scenarios.csv` still works if you want a hand-made plan.

**No 1000 Genomes?** Skip step 1 and leave out `--background`. You get small files containing only the
inserted variant, which is enough to test the system.

## Step 3: Check one file

```bash
bcftools view -H patients/<patient_id>.vcf.gz | grep INSERTED
```
You should see the inserted ClinVar variant.

## Manual edits needed (noted in scenarios_assigned.csv)

- **S2:** add a sulfonylurea row to those patients' medications (Synthea generated none).
- **S6 (KCNJ11):** set the diagnosis age below 6 months so rule D4 applies.
- **RS1 / RS6 / RS2:** these use a *new* ClinVar release. Run step 2 with the **older** ClinVar file,
  load the patients, then load the newer ClinVar release into GE-EHR and check the re-alerts.

## Test status

Both scripts were tested with mock ClinVar and 1000 Genomes files of the same format:
30 patient files were built, expert-panel variants were preferred, colliding positions were handled,
and the four FAMILY1 patients shared one variant. Step 1 needs internet access and was not run against
the live servers; check its output on first use.

## For your paper

"Background genotypes for 30 individuals were taken from the 1000 Genomes Project high-coverage GRCh38
release, restricted to the seven study genes. One ClinVar single-nucleotide variant of the required
classification, preferring expert-panel classifications, was inserted per patient as a heterozygous call."
