# GE-EHR for Monogenic Diabetes

A Genomic-Enhanced EHR prototype that stores VCF variants in seven monogenic diabetes genes,
classifies them against versioned ClinVar releases, applies medication-aware treatment rules, and
re-evaluates stored patients when ClinVar or the rules change.

Research prototype: synthetic data only, and the recommendations are advisory.
See [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) for the design and the stages.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Prepare the data (Stage 1a)

```bash
python -m scripts.clean.run_all   # data/raw -> data/clean + data/clean/QUALITY_REPORT.md
python -m scripts.curate_activating   # data/curated/activating_variants.tsv (rule D4)
```

See [data/raw/README.md](data/raw/README.md) for where each raw file comes from.

## Run

```bash
flask db upgrade        # create or upgrade the database (instance/ge_ehr.db)
flask load-knowledge    # load cleaned ClinVar releases, ClinGen, transcripts and rules v1
flask import-flags data/curated/activating_variants.tsv   # activating KCNJ11/ABCC8 list (rule D4)
flask seed-cohort       # load the 33 test patients (ge_ehr_test_data_v2) and their VCFs
flask list-knowledge    # show releases and rule versions (which one is current)
flask kb-diff 2023-01-07 2026-10-19   # upgrades/downgrades between two releases
flask reevaluate 2026-10-19            # F5 Algorithm 2: switch release, re-evaluate affected patients
flask reevaluate 2026-10-19 --mode full   # same, recomputing everyone (benchmark)
flask import-rules data/curated/rules_v2.yaml && flask apply-rule D4 2   # F5 Algorithm 3
flask runs                              # the re-evaluation run log
flask create-user alice --role clinician   # roles: clinician, lab_tech, admin
flask run               # http://127.0.0.1:5000  (health check: /health)
flask show-config       # print the settings in use
```

## Evaluation (Stage 5, paper Table VI)

```bash
python -m scripts.make_controlled_release   # eval/controlled/: release with 23 known injected changes
python -m eval.benchmark                    # alert-once vs full vs change-driven -> eval/results/SUMMARY.md
python -m eval.benchmark --replicate 10     # larger (replicated) cohort for timing
python -m eval.reclassification             # per-gene ClinVar reclassification rates
```

## Test and lint

```bash
pytest -m "not slow"    # fast tests (what CI runs)
pytest                  # also the slow tests that need the full ClinVar files in data/raw/
ruff check . && ruff format --check .
```

## Layout

| Path | Contents |
|---|---|
| `app/` | Flask app factory, config, models, services (F1–F5), web blueprints, CLI |
| `migrations/` | Alembic migrations (Flask-Migrate) |
| `data/raw/` | Original downloads, never edited (ignored by git) |
| `data/clean/` | Output of the cleaning scripts plus `QUALITY_REPORT.md` |
| `data/curated/` | Hand-maintained inputs: rules YAML, activating variants, overrides |
| `ge_ehr_test_data_v2/` | 33-patient reclassification test cohort |
| `scripts/` | Data preparation scripts |
| `eval/` | Benchmark harness and results |
| `tests/` | pytest suite |

## Configuration

Settings come from environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `GE_EHR_CONFIG` | `development` | `development`, `testing` or `production` |
| `SECRET_KEY` | dev placeholder | **Required** in production |
| `DATABASE_URL` | `sqlite:///instance/ge_ehr.db` | SQLAlchemy URL |
| `THERAPY_REFERENCE_DATE` | `2026-10-19` | Date at which "current therapy" is evaluated |
