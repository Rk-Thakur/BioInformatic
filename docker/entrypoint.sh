#!/bin/sh
# GE-EHR container start-up. Idempotent: safe on every start.
#
#  1. a SECRET_KEY (from the environment, or generated once and kept in the volume)
#  2. database migrations
#  3. on an empty database: knowledge (ClinVar releases, ClinGen, rules v1, activating
#     list), then patients (Synthea + cohort VCFs), then optionally a re-evaluation
#  4. demo accounts (once), with the password in /app/instance/demo_accounts.txt
#
# Settings (environment variables):
#   SEED_PATIENTS=1          load Synthea patients on first start (0 = knowledge only)
#   REEVALUATE_TO=2026-10-19 after loading, re-evaluate to this ClinVar release ("" = no)
#   DEMO_PASSWORD=...        password for demo_clinician / demo_lab_tech / demo_admin
#                            (default: generated and written to instance/demo_accounts.txt)
set -eu
cd /app
mkdir -p instance

if [ -z "${SECRET_KEY:-}" ]; then
    if [ ! -s instance/secret_key ]; then
        python -c "import secrets; print(secrets.token_hex(32))" > instance/secret_key
        chmod 600 instance/secret_key
    fi
    SECRET_KEY="$(cat instance/secret_key)"
    export SECRET_KEY
fi

echo "[ge-ehr] applying database migrations"
flask db upgrade

if ! flask list-knowledge | grep -q CURRENT; then
    echo "[ge-ehr] empty database: loading knowledge"
    flask load-knowledge
    if [ "${SEED_PATIENTS:-1}" = "1" ]; then
        echo "[ge-ehr] loading patients (Synthea + cohort VCFs)"
        flask seed-synthea
        if [ -n "${REEVALUATE_TO:-}" ]; then
            echo "[ge-ehr] re-evaluating to ClinVar ${REEVALUATE_TO}"
            flask reevaluate "${REEVALUATE_TO}"
        fi
    fi
fi

python - <<'PY'
import os, secrets
from pathlib import Path
from sqlalchemy import func, select
from app import create_app
from app.extensions import db
from app.models import User

app = create_app()
with app.app_context():
    if db.session.scalar(select(func.count()).select_from(User)) == 0:
        password = os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(12)
        for role in ("clinician", "lab_tech", "admin"):
            u = User(username=f"demo_{role}", role=role)
            u.set_password(password)
            db.session.add(u)
        db.session.commit()
        f = Path("instance/demo_accounts.txt")
        f.write_text("usernames: demo_clinician, demo_lab_tech, demo_admin\npassword=" + password + "\n")
        f.chmod(0o600)
        print("[ge-ehr] demo accounts created; password in /app/instance/demo_accounts.txt")
PY

echo "[ge-ehr] starting: $*"
exec "$@"
