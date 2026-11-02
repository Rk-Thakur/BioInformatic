"""Table VI metrics (paper §5.4, plan §10).

Every metric compares the state after a knowledge change with an *oracle*: the same
patients evaluated from scratch under the new knowledge. Definitions:

  re-alert sensitivity     treatment alerts the oracle has on upgraded variants that the
                           strategy also has after the run / all such oracle alerts
  stale alerts             open treatment alerts the oracle does not have (advice that no
                           longer applies but is still shown)
  missing alerts           oracle treatment alerts the strategy does not have
  notification precision   notifications that are correct / all notifications of the run.
                           A notification is a new treatment alert (correct if the oracle
                           has it) or a review notice (correct if its variant was really
                           downgraded or its rule really revised)
  records examined         patient-variant records run through Algorithm 1
  point-in-time correct    alerts that Algorithm 1 reproduces from the release and rule
                           version stored with them (DD10) / all rule alerts
  alert burden             notifications per 1,000 patients, and the share of changed
                           variants that produced no notification at all

"Treatment alert" = kind "alert" with severity other than "info" (D1-D5).
"""

from sqlalchemy import func, select

from app.models import Alert, Patient, PatientVariant
from app.services.reevaluate import active_alert_state
from app.services.rule_engine import RuleSpec, evaluate_patient


def is_treatment(t):
    """t = (patient, chrom, pos, ref, alt, rule, kind, severity)"""
    return t[6] == "alert" and t[7] != "info"


def treatment(state):
    return {t for t in state if is_treatment(t)}


def variant_key(t):
    return (t[1], t[2], t[3], t[4])


def ratio(num, den):
    return None if den == 0 else num / den


def notifications(session, run_ids):
    """Alerts the runs created that a clinician is told about."""
    rows = session.execute(
        select(Alert, PatientVariant)
        .join(PatientVariant, PatientVariant.id == Alert.patient_variant_id)
        .where(Alert.created_by_run_id.in_(run_ids))
    ).all()
    out = []
    for a, pv in rows:
        if a.kind == "review_notice" or (a.kind == "alert" and a.severity != "info"):
            out.append((a, pv))
    return out


def point_in_time(session, as_of):
    """DD10: share of rule alerts that their own stored knowledge versions reproduce."""
    alerts = session.scalars(select(Alert).where(Alert.kind.in_(("alert", "info_note")))).all()
    cache, ok = {}, 0
    for a in alerts:
        key = (a.patient_id, a.release_id, a.rule_id)
        if key not in cache:
            patient = session.get(Patient, a.patient_id)
            outs = evaluate_patient(session, patient, a.release, as_of, rules=[RuleSpec.of(a.rule)])
            cache[key] = {(o.patient_variant_id, o.rule_code, o.kind, o.severity) for o in outs}
        ok += (a.patient_variant_id, a.rule_code, a.kind, a.severity) in cache[key]
    return ratio(ok, len(alerts)), len(alerts)


def compute(session, *, before, oracle, run_ids, changes, as_of, revised_rule=None):
    """Metrics for one knowledge change.

    before:  active_alert_state() just before the change
    oracle:  active_alert_state() of a fresh evaluation under the new knowledge
    changes: {variant key: category} for release changes (empty for a rule revision)
    revised_rule: rule code for a rule revision
    """
    after = active_alert_state(session)
    upgraded = {k for k, c in changes.items() if c == "upgrade"}
    downgraded = {k for k, c in changes.items() if c == "downgrade"}

    if revised_rule:
        expected = treatment(oracle) - treatment(before)
    else:
        expected = {t for t in treatment(oracle) if variant_key(t) in upgraded} - treatment(before)
    stale = treatment(after) - treatment(oracle)
    missing = treatment(oracle) - treatment(after)

    notes = notifications(session, run_ids) if run_ids else []
    correct = 0
    for a, pv in notes:
        if a.kind == "review_notice":
            correct += bool(revised_rule) or pv.key in downgraded
        else:
            ext = session.get(Patient, a.patient_id).external_id
            correct += (
                ext,
                pv.chrom,
                pv.pos,
                pv.ref,
                pv.alt,
                a.rule_code,
                a.kind,
                a.severity,
            ) in oracle
    notified_keys = {pv.key for _, pv in notes}
    n_patients = session.scalar(select(func.count()).select_from(Patient))
    pit, n_alerts = point_in_time(session, as_of)

    return {
        "realert_sensitivity": ratio(len(expected & treatment(after)), len(expected)),
        "expected_realerts": len(expected),
        "stale_alerts": len(stale),
        "missing_alerts": len(missing),
        "state_equals_oracle": after == oracle,
        "notifications": len(notes),
        "notification_precision": ratio(correct, len(notes)),
        "point_in_time_correct": pit,
        "alerts_checked": n_alerts,
        "notifications_per_1000_patients": 1000 * len(notes) / n_patients if n_patients else 0,
        "silent_change_share": ratio(len(set(changes) - notified_keys), len(changes)),
    }
