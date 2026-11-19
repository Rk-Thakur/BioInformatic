"""F5: the re-evaluation engine (paper §4.4, the main contribution).

reevaluate_release()  Algorithm 2. A new ClinVar release arrives: classify only the
                      variant keys patients actually carry under the old and the new
                      release, find the ones whose class changed (Delta), and act on the
                      carriers of each changed variant (paper Table IV):
                        upgrade              -> Algorithm 1 again; new alerts are marked
                                                reclassified with high priority
                        downgrade            -> alerts on that variant are superseded and a
                                                review notice is issued; therapy is never
                                                changed automatically (DD12)
                        within a band        -> provenance updated, no notification
                      Then the new release becomes current and the run is logged.

reevaluate_rule()     Algorithm 3 (DD2). A new rule version: re-run it for carriers of
                      its genes and notify only when the outcome changes.

reevaluate_risk_levels()  Algorithm 3 for the genetic risk levels of people without
                      diabetes (DD47): only people whose finding for some variant differs
                      between the old and the new version are re-evaluated.

Modes (for the evaluation, paper §5.2):
  change_driven  - only carriers of changed variants are re-evaluated (GE-EHR)
  full           - every patient is re-evaluated (full recomputation)
  alert_once     - nothing is re-evaluated; only the current release switches (baseline)

Everything happens in the caller's transaction: commit on success, roll back on error.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.domain import change_category
from app.models import (
    Alert,
    KbRelease,
    Patient,
    PatientVariant,
    ReevaluationRun,
    RiskLevelSet,
    Rule,
    audit,
)
from app.models.base import OPEN_STATUSES, RUN_MODES, utcnow
from app.services.classify import classify_keys
from app.services.knowledge_import import current_release
from app.services.risk import current_risk_levels, parse_risk_levels_text
from app.services.rule_engine import apply_outcomes, current_rules, evaluate_patient


class ReevaluationError(RuntimeError):
    pass


@dataclass
class ReleaseChange:
    key: tuple
    old: str
    new: str
    category: str
    carriers: list = field(default_factory=list)  # patient ids


# ------------------------------------------------------------------------- helpers


def _start_run(session, trigger, mode, old_ref, new_ref):
    if mode not in RUN_MODES:
        raise ReevaluationError(f"mode must be one of {RUN_MODES}")
    run = ReevaluationRun(trigger=trigger, mode=mode, old_ref=old_ref, new_ref=new_ref)
    session.add(run)
    session.flush()
    return run


def _finish_run(session, run, counts):
    for k, v in counts.items():
        setattr(run, k, v)
    run.finished_at = utcnow()
    session.flush()
    audit(
        session,
        f"reevaluate_{run.trigger}",
        "reevaluation_run",
        run.id,
        run_id=run.id,
        mode=run.mode,
        old=run.old_ref,
        new=run.new_ref,
        **counts,
    )
    return run


def _review_notice(session, patient, pv_id, superseded, reason, release, run):
    """One review notice per patient variant, pointing at its most severe superseded alert."""
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    main = min(superseded, key=lambda a: order[a.severity])
    rules = ", ".join(sorted({a.rule_code for a in superseded}))
    if all(a.rule_code.startswith("G") for a in superseded):  # risk findings (DD45)
        what = (
            f"The genetic risk finding ({rules}) no longer applies and was superseded. "
            "Please review any follow-up arranged because of it (for example glucose "
            "monitoring) and inform the person."
        )
    else:
        what = (
            f"The recommendation(s) from rule {rules} no longer apply and were "
            "superseded. Please review any treatment change made because of them. "
            "GE-EHR never changes therapy automatically."
        )
    notice = Alert(
        patient_id=patient.id,
        patient_variant_id=pv_id,
        rule_id=None,
        rule_code=None,
        release_id=release.id,
        kind="review_notice",
        severity=main.severity,
        priority="high",
        reclassified=True,
        supersedes_id=main.id,
        created_by_run_id=run.id,
        message=f"{reason} {what}",
    )
    session.add(notice)
    return notice


def _reconcile_patient(session, patient, release, as_of, run, *, upgraded_ids, notices):
    """Algorithm 1 for one patient under `release`, then the Table IV notifications.

    upgraded_ids: patient_variant ids just upgraded (new alerts get reclassified/high).
    notices: {patient_variant_id: reason} -> review notice if treatment alerts on that
             variant were superseded (downgrades, rule revisions).
    Returns (ApplyResult, number of review notices, number of variant records evaluated).
    """
    outcomes = evaluate_patient(session, patient, release, as_of)
    n_records = session.scalar(
        select(func.count())
        .select_from(PatientVariant)
        .where(PatientVariant.patient_id == patient.id)
    )
    applied = apply_outcomes(
        session, patient, outcomes, release, run=run, reclassified_ids=upgraded_ids
    )
    n_notices = 0
    for pv_id, reason in notices.items():
        gone = [
            a
            for a in applied.superseded
            if a.patient_variant_id == pv_id and a.kind == "alert" and a.severity != "info"
        ]
        if gone:
            _review_notice(session, patient, pv_id, gone, reason, release, run)
            n_notices += 1
    return applied, n_notices, n_records


# ------------------------------------------------------------------------- Algorithm 2


def rule_relevant(rules, gene, old, new):
    """Can a change old -> new in `gene` alter any rule outcome? (DD23)

    True when some current rule covering the gene matches the old or the new class.
    """
    return any(
        gene in r.genes and (old in r.genetic["classes"] or new in r.genetic["classes"])
        for r in rules
    )


def release_changes(session, old, new):
    """Delta: stored variant keys whose classification differs between two releases."""
    rows = session.execute(
        select(
            PatientVariant.chrom,
            PatientVariant.pos,
            PatientVariant.ref,
            PatientVariant.alt,
            PatientVariant.patient_id,
        )
    ).all()
    carriers = {}
    for chrom, pos, ref, alt, pid in rows:
        carriers.setdefault((chrom, pos, ref, alt), []).append(pid)
    keys = list(carriers)
    before = classify_keys(session, keys, old)
    after = classify_keys(session, keys, new)
    changes = []
    for key in keys:
        o, n = before[key].classification, after[key].classification
        if o != n:
            changes.append(
                ReleaseChange(key, o, n, change_category(o, n), sorted(set(carriers[key])))
            )
    return changes, len(keys)


def reevaluate_release(session, new_version, as_of, *, mode="change_driven"):
    """Algorithm 2: move from the current release to `new_version`. Returns the run."""
    old = current_release(session)
    new = session.scalar(select(KbRelease).where(KbRelease.version == new_version))
    if new is None:
        raise ReevaluationError(f"release {new_version} is not imported")
    if old is None:
        raise ReevaluationError("there is no current release to re-evaluate from")
    if old.id == new.id:
        raise ReevaluationError(f"release {new_version} is already current")

    run = _start_run(session, "release", mode, old.version, new.version)
    changes, examined = release_changes(session, old, new)
    counts = {
        "variants_examined": examined,
        "variants_changed": len(changes),
        "patients_touched": 0,
        "records_evaluated": 0,
        "alerts_created": 0,
        "alerts_superseded": 0,
        "provenance_updates": 0,
    }
    notes = {}

    if mode != "alert_once":
        # Per patient: which of their variants were upgraded / downgraded, and whether
        # any change can matter at all (DD23: a change that no rule could match before
        # or after, e.g. B -> B/LB, cannot alter any outcome, so it triggers no work).
        changed_by_key = {c.key: c for c in changes}
        rules = current_rules(session)
        carrier_ids = sorted({pid for c in changes for pid in c.carriers})
        pv_rows = (
            session.execute(
                select(
                    PatientVariant.id,
                    PatientVariant.patient_id,
                    PatientVariant.gene,
                    PatientVariant.chrom,
                    PatientVariant.pos,
                    PatientVariant.ref,
                    PatientVariant.alt,
                )
                .join(Patient, Patient.id == PatientVariant.patient_id)
                .where(PatientVariant.patient_id.in_(carrier_ids), Patient.has_diabetes)
            ).all()
            if carrier_ids
            else []
        )
        upgraded, downgraded, relevant = {}, {}, set()
        n_relevant = set()

        def note(pid, pv_id, c):
            label = f"{c.key[0]}:{c.key[1]} {c.key[2]}>{c.key[3]}"
            if c.category == "upgrade":
                upgraded.setdefault(pid, set()).add(pv_id)
            elif c.category == "downgrade":
                downgraded.setdefault(pid, {})[pv_id] = (
                    f"Variant {label} was reclassified from {c.old} to {c.new} "
                    f"in ClinVar {new.version}."
                )

        # Patients with diabetes: a classification change that a treatment rule can
        # match before or after (DD23).
        for pv_id, pid, gene, *key in pv_rows:
            c = changed_by_key.get(tuple(key))
            if c is None or not rule_relevant(rules, gene, c.old, c.new):
                continue
            relevant.add(pid)
            n_relevant.add(c.key)
            note(pid, pv_id, c)

        # People without diabetes: any variant whose risk finding differs between the two
        # releases (DD45, DD47). Compared on the finding itself, so a change in population
        # frequency alone (e.g. ClinVar adding the frequency of a common variant) counts.
        risk = current_risk_levels()
        h_rows = session.execute(
            select(
                PatientVariant.id,
                PatientVariant.patient_id,
                PatientVariant.gene,
                PatientVariant.chrom,
                PatientVariant.pos,
                PatientVariant.ref,
                PatientVariant.alt,
            )
            .join(Patient, Patient.id == PatientVariant.patient_id)
            .where(~Patient.has_diabetes)
        ).all()
        if h_rows:
            h_keys = [tuple(r[3:]) for r in h_rows]
            h_old = classify_keys(session, h_keys, old)
            h_new = classify_keys(session, h_keys, new)
            for pv_id, pid, gene, *key in h_rows:
                k = tuple(key)
                a, b = h_old[k], h_new[k]
                fa = risk.finding(gene, a.classification, a.allele_frequency)
                fb = risk.finding(gene, b.classification, b.allele_frequency)
                if (fa.code if fa else None) == (fb.code if fb else None):
                    continue
                relevant.add(pid)
                n_relevant.add(k)
                c = changed_by_key.get(k)
                if c is not None:
                    note(pid, pv_id, c)
        notes["rule_relevant_changes"] = len(n_relevant)
        if mode == "full":
            patient_ids = list(session.scalars(select(Patient.id)))
        else:
            patient_ids = sorted(relevant)

        notices_total = 0
        for pid in patient_ids:
            patient = session.get(Patient, pid)
            if not patient.is_active:
                continue  # C14: deceased patients keep their history and get nothing new
            applied, n, n_records = _reconcile_patient(
                session,
                patient,
                new,
                as_of,
                run,
                upgraded_ids=frozenset(upgraded.get(pid, ())),
                notices=downgraded.get(pid, {}),
            )
            counts["patients_touched"] += 1
            counts["records_evaluated"] += n_records
            counts["alerts_created"] += len(applied.created)
            counts["alerts_superseded"] += len(applied.superseded)
            counts["provenance_updates"] += applied.provenance_added
            notices_total += n
        notes["review_notices"] = notices_total

    by_cat = {}
    for c in changes:
        by_cat[c.category] = by_cat.get(c.category, 0) + 1
    notes.update(by_cat)
    run.notes = ", ".join(f"{k}={v}" for k, v in sorted(notes.items()))

    old.is_current = False
    session.flush()
    new.is_current = True
    return _finish_run(session, run, counts)


# ------------------------------------------------------------------------- Algorithm 3


def reevaluate_rule(session, code, version, as_of, *, mode="change_driven"):
    """Algorithm 3: make rule `code` version `version` current and re-run it where needed."""
    new_rule = session.scalar(select(Rule).where(Rule.code == code, Rule.version == version))
    if new_rule is None:
        raise ReevaluationError(f"rule {code} v{version} is not imported")
    if new_rule.is_current:
        raise ReevaluationError(f"rule {code} v{version} is already current")
    old_rule = session.scalar(select(Rule).where(Rule.code == code, Rule.is_current))
    release = current_release(session)
    if release is None:
        raise ReevaluationError("there is no current release")

    run = _start_run(
        session,
        "rule",
        mode,
        f"{code} v{old_rule.version}" if old_rule else None,
        f"{code} v{version}",
    )
    genes = set(new_rule.genes) | set(old_rule.genes if old_rule else ())

    if old_rule is not None:
        old_rule.is_current = False
        session.flush()
    new_rule.is_current = True
    session.flush()

    counts = {
        "variants_examined": 0,
        "variants_changed": 0,
        "patients_touched": 0,
        "records_evaluated": 0,
        "alerts_created": 0,
        "alerts_superseded": 0,
        "provenance_updates": 0,
    }
    if mode != "alert_once":
        if mode == "full":
            # Treatment rules never apply to people without diabetes (DD44).
            patient_ids = list(session.scalars(select(Patient.id).where(Patient.has_diabetes)))
        else:
            # Only carriers of a variant the old or the new version could match (DD23):
            # a benign variant in the rule's genes cannot change the rule's outcome.
            classes = set(new_rule.genetic["classes"]) | set(
                old_rule.genetic["classes"] if old_rule else ()
            )
            rows = session.execute(
                select(
                    PatientVariant.patient_id,
                    PatientVariant.chrom,
                    PatientVariant.pos,
                    PatientVariant.ref,
                    PatientVariant.alt,
                )
                .join(Patient, Patient.id == PatientVariant.patient_id)
                .where(PatientVariant.gene.in_(genes), Patient.has_diabetes)
            ).all()
            classified = classify_keys(session, [tuple(r[1:]) for r in rows], release)
            patient_ids = sorted(
                {r[0] for r in rows if classified[tuple(r[1:])].classification in classes}
            )
        rules = current_rules(session)
        notices_total = 0
        for pid in sorted(patient_ids):
            patient = session.get(Patient, pid)
            if not patient.is_active:
                continue
            pv_ids = list(
                session.scalars(
                    select(PatientVariant.id).where(
                        PatientVariant.patient_id == pid, PatientVariant.gene.in_(genes)
                    )
                )
            )
            counts["variants_examined"] += len(pv_ids)
            reason = f"Rule {code} was revised to version {version}."
            applied, n, n_records = _reconcile_patient(
                session,
                patient,
                release,
                as_of,
                run,
                upgraded_ids=frozenset(),
                notices=dict.fromkeys(pv_ids, reason),
            )
            counts["patients_touched"] += 1
            counts["records_evaluated"] += n_records
            counts["alerts_created"] += len(applied.created)
            counts["alerts_superseded"] += len(applied.superseded)
            counts["provenance_updates"] += applied.provenance_added
            notices_total += n
        run.notes = f"review_notices={notices_total}, rules_in_force={len(rules)}"
    return _finish_run(session, run, counts)


# ------------------------------------------------------------------------- risk levels


def reevaluate_risk_levels(session, version, as_of, *, mode="change_driven"):
    """Algorithm 3 for genetic risk levels (DD47): make risk levels `version` current
    and re-evaluate the people without diabetes whose findings it can change."""
    new_set = session.scalar(select(RiskLevelSet).where(RiskLevelSet.version == version))
    if new_set is None:
        raise ReevaluationError(f"risk levels v{version} are not imported")
    if new_set.is_current:
        raise ReevaluationError(f"risk levels v{version} are already current")
    old_set = session.scalar(select(RiskLevelSet).where(RiskLevelSet.is_current))
    release = current_release(session)
    if release is None:
        raise ReevaluationError("there is no current release")
    old = parse_risk_levels_text(old_set.content) if old_set else current_risk_levels()
    new = parse_risk_levels_text(new_set.content)

    run = _start_run(
        session,
        "rule",
        mode,
        f"risk levels v{old.version}",
        f"risk levels v{new.version}",
    )
    counts = {
        "variants_examined": 0,
        "variants_changed": 0,
        "patients_touched": 0,
        "records_evaluated": 0,
        "alerts_created": 0,
        "alerts_superseded": 0,
        "provenance_updates": 0,
    }

    # Which stored variants of people without diabetes get a different finding?
    rows = session.execute(
        select(
            PatientVariant.id,
            PatientVariant.patient_id,
            PatientVariant.gene,
            PatientVariant.chrom,
            PatientVariant.pos,
            PatientVariant.ref,
            PatientVariant.alt,
        )
        .join(Patient, Patient.id == PatientVariant.patient_id)
        .where(~Patient.has_diabetes)
    ).all()
    classified = classify_keys(session, [tuple(r[3:]) for r in rows], release)
    changed = {}
    for pv_id, pid, gene, *key in rows:
        c = classified[tuple(key)]
        a = old.finding(gene, c.classification, c.allele_frequency)
        b = new.finding(gene, c.classification, c.allele_frequency)
        if (a.code if a else None) != (b.code if b else None):
            changed.setdefault(pid, []).append(pv_id)
    counts["variants_examined"] = len(rows)
    counts["variants_changed"] = sum(len(v) for v in changed.values())

    if old_set is not None:
        old_set.is_current = False
        session.flush()
    new_set.is_current = True
    new_set.applied_at = utcnow()
    session.flush()

    notices_total = 0
    if mode != "alert_once":
        if mode == "full":
            patient_ids = list(session.scalars(select(Patient.id).where(~Patient.has_diabetes)))
        else:
            patient_ids = sorted(changed)
        reason = f"The genetic risk levels were revised to version {new.version}."
        for pid in sorted(patient_ids):
            patient = session.get(Patient, pid)
            if not patient.is_active:
                continue
            applied, n, n_records = _reconcile_patient(
                session,
                patient,
                release,
                as_of,
                run,
                upgraded_ids=frozenset(),
                notices=dict.fromkeys(changed.get(pid, ()), reason),
            )
            counts["patients_touched"] += 1
            counts["records_evaluated"] += n_records
            counts["alerts_created"] += len(applied.created)
            counts["alerts_superseded"] += len(applied.superseded)
            counts["provenance_updates"] += applied.provenance_added
            notices_total += n
    run.notes = f"review_notices={notices_total}, people_with_changes={len(changed)}"
    return _finish_run(session, run, counts)


# ------------------------------------------------------------------------- inspection


def active_alert_state(session):
    """{(patient external id, chrom, pos, ref, alt, rule, kind, severity)} for open
    (active or reviewed) alerts and info notes. Used to prove change-driven == full recompute."""
    rows = session.execute(
        select(
            Patient.external_id,
            PatientVariant.chrom,
            PatientVariant.pos,
            PatientVariant.ref,
            PatientVariant.alt,
            Alert.rule_code,
            Alert.kind,
            Alert.severity,
        )
        .join(Alert, Alert.patient_id == Patient.id)
        .join(PatientVariant, PatientVariant.id == Alert.patient_variant_id)
        .where(Alert.status.in_(OPEN_STATUSES), Alert.kind.in_(("alert", "info_note")))
    ).all()
    return {tuple(r) for r in rows}


__all__ = [
    "ReevaluationError",
    "ReleaseChange",
    "active_alert_state",
    "reevaluate_release",
    "reevaluate_risk_levels",
    "reevaluate_rule",
    "release_changes",
]
