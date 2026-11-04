# GE-EHR for Monogenic Diabetes: one image with the app, the cleaned data and the
# evaluation harness.
#
#   docker compose up --build           # build, load data on first start, serve on :5000
#
# Stage 1 (build) installs the Python dependencies and runs the Stage 1a cleaning
# pipeline on data/raw/, so the final image carries data/clean/ but not the 372 MB of
# raw downloads. Stage 2 (runtime) is a slim image with the virtualenv and the project.

ARG PYTHON_VERSION=3.12

# --------------------------------------------------------------------------- build
FROM python:${PYTHON_VERSION}-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
# Compilers and htslib's libraries, in case a dependency (cyvcf2) has no wheel for
# this platform and must be built from source.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential zlib1g-dev libbz2-dev \
       liblzma-dev libcurl4-openssl-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app

# Dependencies first, so editing code does not reinstall them.
COPY pyproject.toml README.md ./
COPY app/__init__.py app/__init__.py
RUN pip install --upgrade pip setuptools \
    && pip install ".[server,dev]" \
    && pip uninstall -y ge-ehr

COPY . .
RUN pip install --no-deps -e .

# Stage 1a: clean ClinVar, ClinGen and Synthea into data/clean/ (about a minute).
RUN python -m scripts.clean.run_all --skip-manifest \
    && python -m scripts.curate_activating

# --------------------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GE_EHR_CONFIG=production \
    FLASK_APP=wsgi.py

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 geehr

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=geehr:geehr app/ app/
COPY --chown=geehr:geehr migrations/ migrations/
COPY --chown=geehr:geehr scripts/ scripts/
COPY --chown=geehr:geehr eval/ eval/
COPY --chown=geehr:geehr tests/ tests/
COPY --chown=geehr:geehr docs/ docs/
COPY --chown=geehr:geehr ge_ehr_test_data_v2/ ge_ehr_test_data_v2/
COPY --chown=geehr:geehr data/curated/ data/curated/
COPY --chown=geehr:geehr --from=build /app/data/clean/ data/clean/
COPY --chown=geehr:geehr wsgi.py pyproject.toml README.md ./
COPY --chown=geehr:geehr docker/entrypoint.sh /usr/local/bin/ge-ehr-entrypoint
# The project was installed in editable mode at /app in the build stage, and the
# source is copied to the same /app here, so the import paths stay valid.
RUN chmod +x /usr/local/bin/ge-ehr-entrypoint \
    && mkdir -p instance eval/results \
    && chown geehr:geehr instance eval/results

USER geehr
EXPOSE 5000
VOLUME ["/app/instance"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4)"

ENTRYPOINT ["ge-ehr-entrypoint"]
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "--access-logfile", "-", "wsgi:app"]
