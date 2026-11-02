import datetime as dt

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Alert,
    AuditEvent,
    Condition,
    Diagnosis,
    KbRelease,
    Medication,
    Patient,
    PatientVariant,
    Rule,
    User,
    audit,
)

TODAY = dt.date(2026, 10, 19)


def make_release(session, version="2023-01-07", current=False):
    r = KbRelease(
        version=version,
        release_date=dt.date.fromisoformat(version),
        source_file="x.tsv",
        source_sha256="0" * 64,
        record_count=0,
        is_current=current,
    )
    session.add(r)
    session.flush()
    return r


def make_patient(session, ext="p1"):
    p = Patient(external_id=ext, name="Test Patient")
    session.add(p)
    session.flush()
    return p


def test_password_is_hashed(session):
    u = User(username="dr", role="clinician")
    u.set_password("s3cret-pass")
    session.add(u)
    session.commit()
    assert u.password_hash != "s3cret-pass"
    assert u.check_password("s3cret-pass")
    assert not u.check_password("wrong")


def test_roles(session):
    assert User(username="a", role="clinician").can_view_recommendations()
    assert not User(username="b", role="lab_tech").can_view_recommendations()  # S12
    session.add(User(username="c", role="nurse", password_hash="x"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_only_one_current_release(session):
    make_release(session, "2023-01-07", current=True)
    make_release(session, "2023-12-30", current=False)
    with pytest.raises(IntegrityError):
        make_release(session, "2026-10-19", current=True)


def test_only_one_current_version_per_rule(session):
    base = dict(
        code="D4",
        genes=["KCNJ11"],
        genetic={"classes": ["P"]},
        severity="high",
        recommendation="r",
        source="s",
        effective_from=TODAY,
        source_file="f",
    )
    session.add_all(
        [Rule(version=1, is_current=True, **base), Rule(version=2, is_current=False, **base)]
    )
    session.flush()
    session.add(Rule(version=3, is_current=True, **base))
    with pytest.raises(IntegrityError):
        session.flush()


def test_one_active_alert_per_patient_variant_rule(session):
    """DD9: re-running evaluation can never create a duplicate active alert."""
    rel = make_release(session, current=True)
    p = make_patient(session)
    pv = PatientVariant(
        patient_id=p.id, chrom="12", pos=1, ref="A", alt="G", zygosity="heterozygous"
    )
    session.add(pv)
    session.flush()

    def alert(status="active"):
        return Alert(
            patient_id=p.id,
            patient_variant_id=pv.id,
            rule_code="D2",
            release_id=rel.id,
            kind="alert",
            severity="high",
            status=status,
            message="m",
        )

    session.add_all([alert(), alert("superseded"), alert("superseded")])
    session.flush()  # superseded history is allowed
    session.add(alert())
    with pytest.raises(IntegrityError):
        session.flush()


def test_foreign_keys_enforced(session):
    session.add(Medication(patient_id=999, description="x", drug_class="insulin", start=TODAY))
    with pytest.raises(IntegrityError):
        session.flush()


def test_current_therapy_and_diagnosis(session):
    p = make_patient(session)
    p.medications += [
        Medication(description="Humulin", drug_class="insulin", start=dt.date(2020, 1, 1)),
        Medication(
            description="Metformin",
            drug_class="metformin",
            start=dt.date(2019, 1, 1),
            stop=dt.date(2021, 1, 1),
        ),
        Medication(description="Glipizide", drug_class="sulfonylurea", start=dt.date(2027, 1, 1)),
    ]
    p.diagnoses += [
        Diagnosis(
            diabetes_type="type2", diagnosed_on=dt.date(2015, 1, 1), age_at_diagnosis_months=400
        ),
        Diagnosis(
            diabetes_type="neonatal", diagnosed_on=dt.date(2016, 1, 1), age_at_diagnosis_months=3
        ),
    ]
    p.conditions.append(
        Condition(
            code="72892002",
            description="Normal pregnancy",
            category="pregnancy",
            start=dt.date(2026, 5, 1),
        )
    )
    session.flush()
    assert p.current_therapy(TODAY) == {"insulin"}  # metformin stopped, glipizide not started
    assert p.diabetes_diagnosis().diabetes_type == "neonatal"
    assert p.is_pregnant_on(TODAY) and not p.is_pregnant_on(dt.date(2026, 1, 1))


def test_drug_class_checked(session):
    p = make_patient(session)
    session.add(Medication(patient_id=p.id, description="x", drug_class="statin", start=TODAY))
    with pytest.raises(IntegrityError):
        session.flush()


def test_audit_helper(session):
    audit(session, "import_release", "kb_release", 7, version="2023-01-07")
    session.flush()
    ev = session.query(AuditEvent).one()
    assert ev.entity_id == "7" and ev.details == {"version": "2023-01-07"}
