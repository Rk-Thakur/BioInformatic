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
    assert resp.status_code == 302 and resp.headers["Location"] == "/"
    assert client.get("/").headers["Location"].endswith("/overview")  # clinicians land on Overview
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
        ("https://evil.example/", "/"),
        ("//evil.example", "/"),
    ],
)
def test_login_redirect_is_never_off_site(client, users, target, expected):
    resp = login(client, "clinician", next_url=target)
    assert resp.headers["Location"] == expected  # exact: never an off-site URL


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


def test_knowledge_page(client, world, session):
    login(client, "clinician")
    reevaluate_release(session, "2026-10-19", AS_OF)
    session.commit()
    page = client.get("/knowledge/").data
    assert b"2023-01-07" in page and b"2026-10-19" in page and b"current" in page
    assert b"D4</strong> v2" in page and b"pending / retired" in page  # v2 imported, not current
    assert b"#" in page and b"release" in page  # the run log lists the re-evaluation


def test_overview_page(client, world, session):
    """Clinician dashboard: counts and the latest re-evaluation flow; lab techs are redirected."""
    login(client, "clinician")
    reevaluate_release(session, "2026-10-19", AS_OF)
    session.commit()
    page = client.get("/overview").data
    assert b"Latest re-evaluation" in page and b"stored variants checked" in page
    assert b"Waiting for review" in page and b"2026-10-19" in page
    assert b"High priority" in page and b"reclassified" in page
    client.post("/logout")
    login(client, "lab_tech")
    assert client.get("/").headers["Location"].endswith("/patients/")
    assert client.get("/overview").status_code == 403


def test_static_assets_served(client):
    for path in ("/assets/css/app.css", "/assets/js/app.js", "/assets/js/theme.js"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        resp.close()  # static files are streamed


# ------------------------------------------------------------------ demo-mode quick login


@pytest.fixture()
def demo_users(session, users):
    for role in ("clinician", "lab_tech"):
        u = User(username=f"demo_{role}", role=role)
        u.set_password("not-used-by-quick-login")
        session.add(u)
    session.commit()


def test_demo_login_off_by_default(client, app, demo_users):
    assert not app.config["DEMO_LOGIN"]  # testing/production default
    assert b"or try a demo account" not in client.get("/login").data
    assert client.post("/login/demo", data={"username": "demo_clinician"}).status_code == 404


def test_demo_login_dropdown_and_one_click(client, app, demo_users, session):
    app.config["DEMO_LOGIN"] = True
    page = client.get("/login").data
    assert b"or try a demo account" in page
    assert b'value="demo_clinician"' in page and b'value="demo_lab_tech"' in page
    assert b'value="clinician"' not in page  # real accounts are never listed
    assert b"not-used-by-quick-login" not in page  # no password is ever shown

    resp = client.post("/login/demo", data={"username": "demo_clinician"})
    assert resp.status_code == 302
    assert client.get("/").headers["Location"].endswith("/overview")
    assert "demo_login" in [e.action for e in session.scalars(select(AuditEvent))]


def test_demo_login_refuses_real_accounts(client, app, demo_users):
    app.config["DEMO_LOGIN"] = True
    assert client.post("/login/demo", data={"username": "clinician"}).status_code == 403
    assert client.post("/login/demo", data={"username": "admin"}).status_code == 403
    assert client.get("/patients/").status_code == 302  # still logged out


# ------------------------------------------------------------------ Stage 7: genetic risk


def add_relative(client, world, name="Rel One", relative_of="fam1", diabetes="none"):
    return client.post(
        "/patients/new",
        data={
            "name": name,
            "sex": "F",
            "diabetes_type": diabetes,
            "relative_of": world[relative_of].external_id if relative_of else "",
            "relation": "child",
        },
    )


def upload(client, patient_id, variants, tmp_path):
    from tests.factories import write_vcf

    vcf = write_vcf(tmp_path / f"rel{patient_id}.vcf", [(*v, "0/1") for v in variants])
    return client.post(
        f"/patients/{patient_id}/upload",
        data={"vcf": (io.BytesIO(vcf.read_bytes()), "rel.vcf")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_new_person_without_diabetes_linked_to_family(client, world):
    login(client, "clinician")
    assert add_relative(client, world).status_code == 302
    rel = db.session.scalar(select(Patient).where(Patient.name == "Rel One"))
    assert rel.has_diabetes is False and not rel.diagnoses
    assert rel.relative_of_id == world["fam1"].id and rel.relation == "child"

    page = client.get(f"/patients/{rel.id}").data
    assert b"No diabetes" in page and b"Not tested yet" in page
    assert b"Rel One" in client.get(f"/patients/{world['fam1'].id}").data  # family section


def test_new_patient_relative_errors(client, world, session):
    login(client, "clinician")
    resp = add_relative(client, world, relative_of=None)
    assert resp.status_code == 302  # no link is fine
    resp = client.post(
        "/patients/new",
        data={"name": "X", "diabetes_type": "none", "relative_of": "nope", "relation": "child"},
    )
    assert resp.status_code == 400 and b"No patient with this ID." in resp.data
    resp = client.post(
        "/patients/new",
        data={"name": "X", "diabetes_type": "none", "relative_of": "fam", "relation": "child"},
    )
    assert resp.status_code == 400 and b"More than one patient matches" in resp.data
    resp = client.post(
        "/patients/new",
        data={"name": "X", "diabetes_type": "none", "relative_of": "fam1", "relation": ""},
    )
    assert resp.status_code == 400 and b"Choose how they are related." in resp.data


def test_risk_card_follows_reclassification(client, world, session, tmp_path):
    """A healthy relative carrying the family's HNF1A variant: uncertain while it is a
    VUS, high risk (reclassified, high priority) once ClinVar calls it LP."""
    from tests.test_reevaluate import A

    login(client, "clinician")
    add_relative(client, world)
    rel = db.session.scalar(select(Patient).where(Patient.name == "Rel One"))
    resp = upload(client, rel.id, [A], tmp_path)
    assert b"VCF accepted" in resp.data and b"1 new alert" in resp.data

    page = client.get(f"/patients/{rel.id}").data
    assert b"risk-card risk-uncertain" in page and b"Risk findings" in page
    assert b"Rule D" not in page  # never treatment advice

    reevaluate_release(session, "2026-10-19", AS_OF)
    session.commit()
    page = client.get(f"/patients/{rel.id}").data
    assert b"risk-card risk-high" in page and b"High genetic risk" in page
    assert b"Genetic risk \xc2\xb7 High genetic risk" in page  # alert label
    assert b"risk levels v1 \xc2\xb7 G1" in page  # provenance line
    worklist = client.get("/alerts/").data
    assert b"Rel One" in worklist and b"high priority" in worklist

    listing = client.get("/patients/?group=no_diabetes").data
    assert b"Rel One" in listing and b"risk-high" in listing and b"fam2" not in listing
    assert b"Rel One" not in client.get("/patients/?group=diabetes").data
    overview = client.get("/overview").data
    assert b"Genetic risk in relatives" in overview and b"risk-badge risk-high" in overview
    knowledge = client.get("/knowledge/").data
    assert b"Genetic risk levels" in knowledge and b"G1" in knowledge


def test_lab_tech_never_sees_risk(client, world, session, tmp_path):
    from tests.test_reevaluate import A

    login(client, "clinician")
    add_relative(client, world)
    rel = db.session.scalar(select(Patient).where(Patient.name == "Rel One"))
    upload(client, rel.id, [A], tmp_path)
    client.post("/logout")

    login(client, "lab_tech")
    page = client.get(f"/patients/{rel.id}").data
    assert b"No diabetes" in page and b"HNF1A" in page  # record and variants: yes
    assert b"risk-card" not in page and b"Risk findings" not in page and b"Genetic risk" not in page
    fam = client.get(f"/patients/{world['fam1'].id}").data
    assert b"Rel One" in fam and b"risk-badge" not in fam
    listing = client.get("/patients/?group=no_diabetes").data
    assert b"Rel One" in listing and b"Genetic risk" not in listing and b"risk-badge" not in listing


def test_family_tree_on_both_pages(client, world, session, tmp_path):
    from tests.test_reevaluate import A

    login(client, "clinician")
    add_relative(client, world)
    rel = db.session.scalar(select(Patient).where(Patient.name == "Rel One"))
    upload(client, rel.id, [A], tmp_path)
    reevaluate_release(session, "2026-10-19", AS_OF)
    session.commit()

    for pid in (world["fam1"].id, rel.id):
        page = client.get(f"/patients/{pid}").data
        assert b"data-pedigree" in page and b'class="ped-svg"' in page
        assert b'data-release="2023-01-07"' in page and b"Replay history" in page
        assert b"ped-ring risk-high" in page  # current release: the relative is high risk
        assert b"Jan 2023" in page and b"Oct 2026" in page
    # without relatives there is no tree
    assert b"data-pedigree" not in client.get(f"/patients/{world['gck'].id}").data

    client.post("/logout")
    login(client, "lab_tech")
    page = client.get(f"/patients/{world['fam1'].id}").data
    assert b'class="ped-svg"' in page  # the tree itself: yes
    assert b"ped-ring" not in page and b"data-release" not in page  # risk: never
    assert b"ring = genetic risk" not in page
