"""Entry points that tie F1-F3 together (paper §3.1):

  - a VCF upload runs F1 (store), then F2 + F3 for that patient;
  - a medication or diagnosis change runs F2 + F3 for that patient.

Each call is logged as a ReevaluationRun. The caller owns the transaction (commit).
"""

from dataclasses import dataclass

from app.models import ReevaluationRun
from app.models.base import utcnow
from app.services.knowledge_import import current_release
from app.services.rule_engine import apply_outcomes, evaluate_patient
from app.services.vcf_service import store_vcf


class NoCurrentReleaseError(RuntimeError):
    pass


@dataclass
class EvaluationResult:
    run: ReevaluationRun
    created: list
    kept: list
    superseded: list


def _require_release(session):
    release = current_release(session)
    if release is None:
        raise NoCurrentReleaseError("no current ClinVar release; run `flask load-knowledge` first")
    return release


def evaluate_and_apply(session, patient, as_of, *, trigger):
    """F2 + F3 for one patient under the current release and current rules."""
    release = _require_release(session)
    run = ReevaluationRun(trigger=trigger, mode="change_driven", new_ref=release.version)
    session.add(run)
    session.flush()
    outcomes = evaluate_patient(session, patient, release, as_of)
    applied = apply_outcomes(session, patient, outcomes, release, run=run)
    run.patients_touched = 1
    run.alerts_created = len(applied.created)
    run.alerts_superseded = len(applied.superseded)
    run.provenance_updates = len(applied.kept)
    run.finished_at = utcnow()
    return EvaluationResult(run, applied.created, applied.kept, applied.superseded)


def process_upload(session, patient, path, genes, as_of, *, filename=None, uploaded_by_id=None):
    """Store a VCF and, if it is valid, evaluate the patient. Returns (upload, result|None)."""
    _require_release(session)
    upload = store_vcf(
        session, patient, path, genes, filename=filename, uploaded_by_id=uploaded_by_id
    )
    if upload.status != "accepted":
        return upload, None
    return upload, evaluate_and_apply(session, patient, as_of, trigger="upload")
