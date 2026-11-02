"""F3: medication-aware treatment rules, Algorithm 1 of the paper (§4.3).

Two halves, kept apart on purpose (plan §7):

  evaluate()        pure: patient facts + classified variants + rules -> outcomes.
                    No database access, so the benchmark and the point-in-time check
                    (DD10) can call it with any release and rule versions.
  apply_outcomes()  writes outcomes as alerts: creates new ones, keeps existing ones
                    (appending provenance when the knowledge version changed) and
                    supersedes active alerts that no longer apply. Idempotent (DD9).

evaluate_patient() connects the two for a patient stored in the database.
"""

from dataclasses import dataclass, field

from sqlalchemy import select

from app.domain import ACTIONABLE
from app.models import Alert, AlertProvenance, CuratedFlag, PatientVariant, Rule, audit
from app.models.base import OPEN_STATUSES, utcnow
from app.services.classify import classify_keys

# DD15: rules whose recommendation does not hold in pregnancy.
PREGNANCY_CAVEAT_RULES = {"D1"}
PREGNANCY_CAVEAT = "Pregnancy: specialist guidance required; do not stop treatment on this basis."


@dataclass(frozen=True)
class PatientFacts:
    therapy: frozenset  # current drug classes
    age_at_diagnosis_months: float | None = None
    pregnant: bool = False
    active: bool = True  # False for deceased patients (C14): no new alerts


@dataclass(frozen=True)
class VariantFacts:
    patient_variant_id: int
    gene: str
    classification: str
    flags: frozenset = field(default_factory=frozenset)
    label: str = ""  # e.g. "HNF1A 12:120978486 A>C", for messages


@dataclass(frozen=True)
class RuleSpec:
    """The parts of a Rule that evaluation needs (a Rule row works too)."""

    id: int | None
    code: str
    version: int
    genes: tuple
    genetic: dict
    therapy: dict | None
    extra: dict | None
    severity: str
    recommendation: str

    @classmethod
    def of(cls, rule):
        return cls(
            rule.id,
            rule.code,
            rule.version,
            tuple(rule.genes),
            rule.genetic,
            rule.therapy,
            rule.extra,
            rule.severity,
            rule.recommendation,
        )


@dataclass(frozen=True)
class Outcome:
    patient_variant_id: int
    rule_id: int | None
    rule_code: str
    kind: str  # "alert" | "info_note"
    severity: str
    message: str
    classification: str


def _therapy_ok(therapy_rule, therapy):
    """Return None if the therapy condition holds, otherwise the reason it does not."""
    if not therapy_rule:
        return None
    any_of = set(therapy_rule.get("any_of", []))
    none_of = set(therapy_rule.get("none_of", []))
    if therapy & none_of:  # checked first: the more useful explanation
        return f"already on {', '.join(sorted(therapy & none_of))}"
    if any_of and not (therapy & any_of):
        current = ", ".join(sorted(therapy)) or "no glucose-lowering medication"
        return f"current therapy ({current}) includes none of: {', '.join(sorted(any_of))}"
    return None


def _extra_ok(extra, facts):
    if not extra:
        return None
    limit = extra.get("age_at_diagnosis_months_lt")
    if limit is not None:
        age = facts.age_at_diagnosis_months
        if age is None:
            return "age at diagnosis is not recorded"
        if age >= limit:
            return f"diagnosed at {age:g} months (rule applies below {limit:g} months)"
    return None


def evaluate(facts, variants, rules):
    """Algorithm 1 for one patient. Returns a list of Outcome (no side effects)."""
    if not facts.active:
        return []
    outcomes = []
    for rule in rules:
        classes = set(rule.genetic["classes"])
        flag = rule.genetic.get("flag")
        for v in variants:
            if v.gene not in rule.genes or v.classification not in classes:
                continue
            if flag and flag not in v.flags:
                # DD8: gene and class match but the variant is not known to be activating.
                outcomes.append(
                    Outcome(
                        v.patient_variant_id,
                        rule.id,
                        rule.code,
                        "info_note",
                        "info",
                        f"{v.label} is {v.classification} in a neonatal-diabetes gene, "
                        f"but it is not on the curated '{flag}' list, "
                        f"so rule {rule.code} does not apply. "
                        "Specialist review of the variant is suggested.",
                        v.classification,
                    )
                )
                continue
            reason = _therapy_ok(rule.therapy, facts.therapy) or _extra_ok(rule.extra, facts)
            if reason is None:
                message = rule.recommendation
                if rule.code in PREGNANCY_CAVEAT_RULES and facts.pregnant:
                    message = f"{message} {PREGNANCY_CAVEAT}"
                outcomes.append(
                    Outcome(
                        v.patient_variant_id,
                        rule.id,
                        rule.code,
                        "alert",
                        rule.severity,
                        message,
                        v.classification,
                    )
                )
            else:
                outcomes.append(
                    Outcome(
                        v.patient_variant_id,
                        rule.id,
                        rule.code,
                        "info_note",
                        "info",
                        f"{v.label} ({v.classification}) matches rule {rule.code}, "
                        f"but no alert because {reason}. Rule advice: {rule.recommendation}",
                        v.classification,
                    )
                )
    return outcomes


# --------------------------------------------------------------------------- database side


def current_rules(session):
    return [
        RuleSpec.of(r)
        for r in session.scalars(select(Rule).where(Rule.is_current).order_by(Rule.code))
    ]


def patient_facts(patient, as_of):
    dx = patient.diabetes_diagnosis()
    return PatientFacts(
        therapy=frozenset(patient.current_therapy(as_of)),
        age_at_diagnosis_months=dx.age_at_diagnosis_months if dx else None,
        pregnant=patient.is_pregnant_on(as_of),
        active=patient.is_active,
    )


def variant_facts(session, patient, release):
    """Classify the patient's stored variants under `release` and attach curated flags.

    Returns (list of VariantFacts, {patient_variant_id: Classification}).
    """
    pvs = list(
        session.scalars(select(PatientVariant).where(PatientVariant.patient_id == patient.id))
    )
    classes = classify_keys(session, [pv.key for pv in pvs], release)
    actionable_keys = [pv.key for pv in pvs if classes[pv.key].classification in ACTIONABLE]
    flags = {}
    if actionable_keys:
        for f in session.scalars(
            select(CuratedFlag).where(CuratedFlag.pos.in_({k[1] for k in actionable_keys}))
        ):
            flags.setdefault((f.chrom, f.pos, f.ref, f.alt), set()).add(f.flag)
    facts, by_id = [], {}
    for pv in pvs:
        c = classes[pv.key]
        by_id[pv.id] = c
        if c.classification == "NC":
            continue  # not in ClinVar: no rule can match (DD4)
        label = f"{pv.gene} {pv.chrom}:{pv.pos} {pv.ref}>{pv.alt}"
        facts.append(
            VariantFacts(pv.id, pv.gene, c.classification, frozenset(flags.get(pv.key, ())), label)
        )
    return facts, by_id


def evaluate_patient(session, patient, release, as_of, rules=None):
    """Read the patient's facts from the database and run Algorithm 1."""
    variants, _ = variant_facts(session, patient, release)
    rules = rules if rules is not None else current_rules(session)
    return evaluate(patient_facts(patient, as_of), variants, rules)


@dataclass
class ApplyResult:
    created: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    superseded: list = field(default_factory=list)
    provenance_added: int = 0


def apply_outcomes(session, patient, outcomes, release, *, run=None, reclassified_ids=frozenset()):
    """Write outcomes as alerts for one patient. Idempotent: same outcomes -> no change.

    reclassified_ids: patient_variant ids whose classification was just upgraded (F5).
    New alerts on them are marked reclassified, and treatment alerts get high priority
    (paper Table IV, DD14).

    - an outcome with no matching active alert -> new alert (+ provenance row)
    - an outcome matching an active alert       -> kept; provenance appended if the
                                                   release or rule version changed (DD13)
    - an active rule alert with no outcome       -> superseded (e.g. therapy changed)
    Review notices are F5's business and are never touched here.
    """
    result = ApplyResult()
    run_id = run.id if run is not None else None
    active = {
        (a.patient_variant_id, a.rule_code, a.kind): a
        for a in session.scalars(
            select(Alert).where(
                Alert.patient_id == patient.id,
                Alert.status.in_(OPEN_STATUSES),
                Alert.kind.in_(("alert", "info_note")),
            )
        )
    }
    wanted = {(o.patient_variant_id, o.rule_code, o.kind): o for o in outcomes}

    for key, o in wanted.items():
        alert = active.get(key)
        if alert is not None:
            last = alert.provenance[-1] if alert.provenance else None
            if last is None or (last.release_id, last.rule_id, last.classification) != (
                release.id,
                o.rule_id,
                o.classification,
            ):
                result.provenance_added += 1
                session.add(
                    AlertProvenance(
                        alert_id=alert.id,
                        release_id=release.id,
                        rule_id=o.rule_id,
                        run_id=run_id,
                        classification=o.classification,
                    )
                )
            result.kept.append(alert)
            continue
        upgraded = o.patient_variant_id in reclassified_ids
        alert = Alert(
            patient_id=patient.id,
            patient_variant_id=o.patient_variant_id,
            rule_id=o.rule_id,
            rule_code=o.rule_code,
            release_id=release.id,
            kind=o.kind,
            severity=o.severity,
            message=o.message,
            priority="high" if upgraded and o.kind == "alert" else "normal",
            reclassified=upgraded,
            created_by_run_id=run_id,
        )
        session.add(alert)
        session.flush()
        session.add(
            AlertProvenance(
                alert_id=alert.id,
                release_id=release.id,
                rule_id=o.rule_id,
                run_id=run_id,
                classification=o.classification,
            )
        )
        result.created.append(alert)

    for key, alert in active.items():
        if key not in wanted:
            alert.status = "superseded"
            alert.status_changed_at = utcnow()
            result.superseded.append(alert)
    session.flush()
    if result.created or result.superseded:
        audit(
            session,
            "alerts_applied",
            "patient",
            patient.id,
            run_id=run_id,
            created=len(result.created),
            superseded=len(result.superseded),
            release=release.version,
        )
    return result
