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
```

See [data/raw/README.md](data/raw/README.md) for where each raw file comes from.

## Run

```bash
flask db upgrade        # create or upgrade the database (instance/ge_ehr.db)
flask run               # http://127.0.0.1:5000  (health check: /health)
flask show-config       # print the settings in use
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
