# Raw inputs

These files are **never edited**. `python -m scripts.clean.run_all` reads them and writes `data/clean/`.
`MANIFEST.csv` records the size and SHA-256 of every file, so a run can be traced to its exact inputs.
The data files are git-ignored (too large); re-download them as below.

| Path | Source | How to get it |
|---|---|---|
| `clinvar/clinvar_20230107.vcf.gz` | ClinVar GRCh38 VCF, release 2023-01-07 | `https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/archive_2.0/2023/clinvar_20230107.vcf.gz` |
| `clinvar/clinvar_20231230.vcf.gz` | ClinVar GRCh38 VCF, release 2023-12-30 | `.../archive_2.0/2023/clinvar_20231230.vcf.gz` |
| `clinvar/clinvar_20261019.vcf.gz` | ClinVar GRCh38 VCF, release 2026-10-19 | `.../archive_2.0/2026/clinvar_20261019.vcf.gz` (was the current `clinvar.vcf.gz` when downloaded) |
| `clingen/erepo.tabbed.txt` | ClinGen Evidence Repository, full export | https://erepo.clinicalgenome.org/evrepo/ → Download → tab-separated |
| `synthea/*.numbers` | Synthea diabetes cohort (Apple Numbers) | Export each to CSV into this folder (cleaning rule C12) |

After downloading, compare checksums with `MANIFEST.csv`. A different checksum means a different file, and the
quality report and the paper's numbers will change.
