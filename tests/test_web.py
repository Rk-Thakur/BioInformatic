"""Stage 4: web pages, access control (S12), FHIR export (S11) and security basics."""

import io
import json

import pytest
from fhir.resources.R4B.bundle import Bundle
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import Alert, AuditEvent, Medication, Patient, PatientVariant, User
from app.services.reevaluate import reevaluate_release
from tests.factories import AS_OF, COHORT
from tests.test_reevaluate import build

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def users(session):
    out = {}
    for role in ("clinician", "lab_tech", "admin"):
        u = User(username=role, role=role)
        u.set_password(PASSWORD)
        session.add(u)
        out[role] = u
    disabled = User(username="former", role="clinician", active=False)
    disabled.set_password(PASSWORD)
    session.add(disabled)
    session.commit()
    return out


@pytest.fixture()
def world(session, tmp_path, users):
    patients = build(session, tmp_path)
    session.commit()
    return patients


def login(client, username, password=PASSWORD, next_url=None):
    url = "/login" + (f"?next={next_url}" if next_url else "")
    return client.post(url, data={"username": username, "password": password})


# ------------------------------------------------------------------ authentication


def test_pages_require_login(client, users):
    for url in ("/patients/", "/alerts/", "/knowledge/", "/patients/1"):
        resp = client.get(url)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], url


def test_login_logout(client, users, session):
    resp = login(client, "clinician")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/patients/")
    assert client.get("/patients/").status_code == 200
    client.post("/logout")
    assert client.get("/patients/").status_code == 302
    actions = [e.action for e in session.scalars(select(AuditEvent))]
    assert "login" in actions and "logout" in actions


@pytest.mark.parametrize(
    "username, password", [("clinician", "wrong"), ("nobody", PASSWORD), ("former", PASSWORD)]
)
def test_failed_login_same_message(client, users, username, password):
    resp = login(client, username, password)
    assert resp.status_code == 401 and b"Invalid username or password." in resp.data


@pytest.mark.parametrize(
    "target, expected",
    [
        ("/alerts/", "/alerts/"),
        ("https://evil.example/", "/patients/"),
        ("//evil.example", "/patients/"),
    ],
)
def test_login_redirect_is_never_off_site(client, users, target, expected):
    resp = login(client, "clinician", next_url=target)
    assert resp.headers["Location"].endswith(expected)


# ------------------------------------------------------------------ S12 role matrix


def test_lab_tech_never_sees_recommendations(client, world):
    """S12: lab technicians can upload and see variants, never recommendations."""
    login(client, "lab_tech")
    pid = world["fam1"].id
    page = client.get(f"/patients/{pid}")
    assert page.status_code == 200
    assert b"Upload VCF" in page.data and b"HNF1A" in page.data
    assert b"Alerts" not in page.data and b"monitored for reclassification" not in page.data
    for url in ("/alerts/", "/knowledge/", f"/patients/{pid}/fhir", "/patients/new"):
        assert client.get(url).status_code == 403, url
    alert = db.session.scalar(select(Alert))
    assert client.post(f"/alerts/{alert.id}/review").status_code == 403
    assert client.post(f"/patients/{pid}/medications", data={}).status_code == 403


def test_clinician_sees_alerts_and_worklist(client, world):
    login(client, "clinician")
    page = client.get(f"/patients/{world['gck'].id}")
    assert b"Medication usually not required" in page.data  # D1
    worklist = client.get("/alerts/")
    assert worklist.status_code == 200 and b"Rule D1" in worklist.data
    assert b"monitored for reclassification" not in worklist.data  # info-level hidden by default
    assert b"monitored for reclassification" in client.get("/alerts/?info=1").data


# ------------------------------------------------------------------ workflows


def test_upload_through_the_form(client, world, tmp_path):
    login(client, "lab_tech")
    pid = world["bystander"].id
    good = (COHORT / "patients" / "0661fbba-0fa1-1375-bc15-30a567e0ce32.vcf.gz").read_bytes()
    resp = client.post(
        f"/patients/{pid}/upload",
        data={"vcf": (io.BytesIO(good), "s1.vcf.gz")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"VCF accepted" in resp.data
    assert b"new alert" not in resp.data  # lab tech is not told about alerts

    bad = (COHORT / "malformed" / "S10_truncated_row.vcf").read_bytes()
    resp = client.post(
        f"/patients/{pid}/upload",
        data={"vcf": (io.BytesIO(bad), "bad.vcf")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"VCF rejected" in resp.data and b"expected 10 columns" in resp.data


def test_adding_sulfonylurea_turns_d2_alert_into_note(client, world, session):
    login(client, "clinician")
    p = world["fam1"]
    reevaluate_release(session, "2026-10-19", AS_OF)
    session.commit()
    resp = client.post(
        f"/patients/{p.id}/medications",
        data={"drug_class": "sulfonylurea", "description": "Gliclazide", "start": "2026-10-01"},
        follow_redirects=True,
    )
    assert b"1 no longer apply" in resp.data
    d2 = {
        a.kind
        for a in session.scalars(
            select(Alert).where(
                Alert.patient_id == p.id, Alert.rule_code == "D2", Alert.status == "active"
            )
        )
    }
    assert d2 == {"info_note"}

    med = session.scalar(
        select(Medication).where(
            Medication.patient_id == p.id, Medication.drug_class == "sulfonylurea"
        )
    )
    client.post(f"/patients/{p.id}/medications/{med.id}/stop")
    d2 = {
        a.kind
        for a in session.scalars(
            select(Alert).where(
                Alert.patient_id == p.id, Alert.rule_code == "D2", Alert.status == "active"
            )
        )
    }
    assert d2 == {"alert"}


def test_review_keeps_alert_in_force(client, world, session):
    """A reviewed alert is not recreated by re-evaluation (it stays open)."""
    login(client, "clinician")
    alert = session.scalar(
        select(Alert).where(Alert.patient_id == world["gck"].id, Alert.rule_code == "D1")
    )
    resp = client.post(f"/alerts/{alert.id}/review", data={"back": "/alerts/"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/alerts/")
    session.refresh(alert)
    assert alert.status == "reviewed" and alert.reviewed_by_id is not None

    from app.services.workflow import evaluate_and_apply

    res = evaluate_and_apply(session, world["gck"], AS_OF, trigger="medication")
    assert res.created == [] and alert in res.kept


def test_new_patient_form(client, users):
    login(client, "clinician")
    resp = client.post(
        "/patients/new",
        data={"name": "Ada Example", "diabetes_type": "neonatal", "age_at_diagnosis_years": "0.25"},
    )
    assert resp.status_code == 302
    p = db.session.scalar(select(Patient).where(Patient.name == "Ada Example"))
    assert p.diabetes_diagnosis().age_at_diagnosis_months == pytest.approx(3)


# ------------------------------------------------------------------ S11 FHIR


def test_fhir_export_is_a_valid_bundle(client, world):
    """S11: FHIR export of an HNF1A patient is a valid FHIR R4B Bundle."""
    login(client, "clinician")
    resp = client.get(f"/patients/{world['fam1'].id}/fhir")
    assert resp.status_code == 200 and resp.mimetype == "application/fhir+json"
    bundle = Bundle.model_validate_json(resp.data)
    types = [e.resource.get_resource_type() for e in bundle.entry]
    assert types[:2] == ["Patient", "DiagnosticReport"] and types.count("Observation") == 1
    obs = bundle.entry[2].resource
    assert obs.status == "final"
    codes = {c.code.coding[0].code: c for c in obs.component}
    assert codes["48018-6"].valueCodeableConcept.coding[0].display == "HNF1A"
    assert codes["53037-8"].valueCodeableConcept.coding[0].code == "LA26333-7"  # VUS


def test_fhir_marks_reclassified_variant_amended(client, world, session):
    """RS1 + Table II F4: after the VUS -> LP upgrade the Observation is 'amended'."""
    login(client, "clinician")
    reevaluate_release(session, "2026-10-19", AS_OF)
    session.commit()
    data = json.loads(client.get(f"/patients/{world['fam1'].id}/fhir").data)
    obs = [e["resource"] for e in data["entry"] if e["resource"]["resourceType"] == "Observation"]
    assert [o["status"] for o in obs] == ["amended"]
    report = next(
        e["resource"] for e in data["entry"] if e["resource"]["resourceType"] == "DiagnosticReport"
    )
    assert report["status"] == "amended"
    sig = next(c for c in obs[0]["component"] if c["code"]["coding"][0]["code"] == "53037-8")
    assert sig["valueCodeableConcept"]["coding"][0]["code"] == "LA26332-9"  # likely pathogenic
    # bystander's unchanged variant stays final
    data = json.loads(client.get(f"/patients/{world['bystander'].id}/fhir").data)
    assert {
        e["resource"]["status"]
        for e in data["entry"]
        if e["resource"]["resourceType"] == "Observation"
    } == {"final"}


# ------------------------------------------------------------------ security basics


def test_security_headers(client, users):
    resp = client.get("/login")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["Cache-Control"] == "no-store"


def test_csrf_is_enforced_outside_tests(tmp_path):
    app = create_app(
        "testing",
        overrides={
            "WTF_CSRF_ENABLED": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.db'}",
        },
    )
    with app.app_context():
        db.create_all()
        u = User(username="dr", role="clinician")
        u.set_password(PASSWORD)
        db.session.add(u)
        db.session.commit()
        resp = app.test_client().post("/login", data={"username": "dr", "password": PASSWORD})
        assert resp.status_code == 400  # no CSRF token
        db.session.remove()
        db.engine.dispose()


def test_unknown_patient_404(client, users):
    login(client, "clinician")
    assert client.get("/patients/9999").status_code == 404


def test_patient_variant_in_view_uses_current_release(client, world, session):
    login(client, "clinician")
    page = client.get(f"/patients/{world['fam1'].id}").data
    assert b"VUS" in page
    reevaluate_release(session, "2026-10-19", AS_OF)
    session.commit()
    page = client.get(f"/patients/{world['fam1'].id}").data
    assert b"interpreted with ClinVar 2026-10-19" in page and b">LP<" in page
    assert session.scalar(
        select(PatientVariant).where(PatientVariant.patient_id == world["fam1"].id)
    )
