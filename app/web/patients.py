import datetime as dt
import os
import tempfile
import uuid
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy import func, or_, select
from werkzeug.utils import secure_filename

from app.extensions import db
from app.genes import load_genes_cached
from app.models import Alert, Diagnosis, Medication, Patient, audit
from app.services.fhir_export import patient_bundle
from app.services.knowledge_import import current_release
from app.services.patient_view import patient_alerts, patient_uploads, patient_variants
from app.services.workflow import NoCurrentReleaseError, evaluate_and_apply, process_upload
from app.web.forms import EmptyForm, MedicationForm, PatientForm, UploadForm
from app.web.security import ANY_ROLE, CLINICAL, roles_required

bp = Blueprint("patients", __name__, url_prefix="/patients")
PAGE_SIZE = 50


def as_of():
    return dt.date.fromisoformat(current_app.config["THERAPY_REFERENCE_DATE"])


def get_patient(patient_id):
    return db.session.get(Patient, patient_id) or abort(404)


def genes():
    return load_genes_cached(str(Path(current_app.config["DATA_CURATED_DIR"]) / "panel_genes.tsv"))


@bp.get("/")
@roles_required(*ANY_ROLE)
def index():
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    stmt = select(Patient)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Patient.name.ilike(like), Patient.external_id.ilike(like)))
    total = db.session.scalar(select(func.count()).select_from(stmt.subquery()))
    patients = db.session.scalars(
        stmt.order_by(Patient.name).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    ).all()
    open_counts = {}
    if current_user.can_view_recommendations() and patients:
        rows = db.session.execute(
            select(Alert.patient_id, func.count())
            .where(
                Alert.patient_id.in_([p.id for p in patients]),
                Alert.status == "active",
                Alert.kind.in_(("alert", "review_notice")),
                Alert.severity != "info",
            )
            .group_by(Alert.patient_id)
        )
        open_counts = dict(rows.all())
    return render_template(
        "patients/index.html",
        patients=patients,
        q=q,
        page=page,
        pages=max(1, -(-total // PAGE_SIZE)),
        total=total,
        open_counts=open_counts,
    )


@bp.route("/new", methods=["GET", "POST"])
@roles_required(*CLINICAL)
def new():
    form = PatientForm()
    if form.validate_on_submit():
        ext = (form.external_id.data or "").strip() or str(uuid.uuid4())
        if db.session.scalar(select(Patient).where(Patient.external_id == ext)):
            form.external_id.errors.append("A patient with this ID already exists.")
            return render_template("patients/new.html", form=form), 400
        p = Patient(
            external_id=ext,
            name=form.name.data.strip(),
            sex=form.sex.data or None,
            birth_date=form.birth_date.data,
        )
        years = form.age_at_diagnosis_years.data
        p.diagnoses.append(
            Diagnosis(
                diabetes_type=form.diabetes_type.data,
                age_at_diagnosis_months=float(years) * 12 if years is not None else None,
            )
        )
        db.session.add(p)
        db.session.flush()
        audit(db.session, "patient_created", "patient", p.id, user_id=current_user.id)
        db.session.commit()
        flash("Patient created.", "success")
        return redirect(url_for("patients.detail", patient_id=p.id))
    return render_template("patients/new.html", form=form)


@bp.get("/<int:patient_id>")
@roles_required(*ANY_ROLE)
def detail(patient_id):
    p = get_patient(patient_id)
    release = current_release(db.session)
    show_all = request.args.get("all") == "1"
    variants, total_variants = patient_variants(
        db.session, p, release, include_unclassified=show_all
    )
    clinical = current_user.can_view_recommendations()
    open_alerts, past_alerts = patient_alerts(db.session, p) if clinical else ([], [])
    meds = sorted(p.medications, key=lambda m: (m.stop is not None, m.start), reverse=False)
    return render_template(
        "patients/detail.html",
        patient=p,
        release=release,
        variants=variants,
        total_variants=total_variants,
        show_all=show_all,
        clinical=clinical,
        open_alerts=open_alerts,
        past_alerts=past_alerts,
        meds=meds,
        as_of=as_of(),
        therapy=sorted(p.current_therapy(as_of())),
        diagnosis=p.diabetes_diagnosis(),
        uploads=patient_uploads(db.session, p),
        upload_form=UploadForm(),
        med_form=MedicationForm(start=as_of()),
        action_form=EmptyForm(),
    )


@bp.post("/<int:patient_id>/upload")
@roles_required(*ANY_ROLE)
def upload(patient_id):
    p = get_patient(patient_id)
    form = UploadForm()
    if not form.validate_on_submit():
        flash("Choose a VCF file to upload.", "error")
        return redirect(url_for("patients.detail", patient_id=p.id))
    filename = secure_filename(form.vcf.data.filename or "upload.vcf") or "upload.vcf"
    fd, tmp = tempfile.mkstemp(prefix="ge-ehr-", suffix=".vcf")
    os.close(fd)
    try:
        form.vcf.data.save(tmp)
        up, result = process_upload(
            db.session, p, tmp, genes(), as_of(), filename=filename, uploaded_by_id=current_user.id
        )
        db.session.commit()
    except NoCurrentReleaseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("patients.detail", patient_id=p.id))
    finally:
        os.unlink(tmp)
    if up.status == "rejected":
        flash(f"VCF rejected: {up.error}", "error")
    else:
        msg = f"VCF accepted: {up.panel_records} variants in panel genes."
        if result and current_user.can_view_recommendations():
            msg += f" {len(result.created)} new alert(s)."
        flash(msg, "success")
    return redirect(url_for("patients.detail", patient_id=p.id))


def _reevaluate_after_therapy_change(p):
    try:
        res = evaluate_and_apply(db.session, p, as_of(), trigger="medication")
    except NoCurrentReleaseError:
        return None
    return res


@bp.post("/<int:patient_id>/medications")
@roles_required(*CLINICAL)
def add_medication(patient_id):
    p = get_patient(patient_id)
    form = MedicationForm()
    if not form.validate_on_submit():
        flash(
            "Medication not saved: "
            + "; ".join(f"{k}: {', '.join(v)}" for k, v in form.errors.items()),
            "error",
        )
        return redirect(url_for("patients.detail", patient_id=p.id))
    p.medications.append(
        Medication(
            drug_class=form.drug_class.data,
            description=form.description.data.strip(),
            start=form.start.data,
        )
    )
    db.session.flush()
    audit(
        db.session,
        "medication_added",
        "patient",
        p.id,
        user_id=current_user.id,
        drug_class=form.drug_class.data,
    )
    res = _reevaluate_after_therapy_change(p)
    db.session.commit()
    flash(_therapy_message(res), "success")
    return redirect(url_for("patients.detail", patient_id=p.id))


@bp.post("/<int:patient_id>/medications/<int:med_id>/stop")
@roles_required(*CLINICAL)
def stop_medication(patient_id, med_id):
    p = get_patient(patient_id)
    med = db.session.get(Medication, med_id)
    if med is None or med.patient_id != p.id:
        abort(404)
    if EmptyForm().validate_on_submit() and med.stop is None:
        med.stop = as_of()
        audit(
            db.session,
            "medication_stopped",
            "patient",
            p.id,
            user_id=current_user.id,
            medication=med.id,
        )
        res = _reevaluate_after_therapy_change(p)
        db.session.commit()
        flash(_therapy_message(res), "success")
    return redirect(url_for("patients.detail", patient_id=p.id))


def _therapy_message(res):
    if res is None:
        return "Therapy updated."
    return (
        f"Therapy updated; alerts re-checked: {len(res.created)} new, "
        f"{len(res.superseded)} no longer apply."
    )


@bp.get("/<int:patient_id>/fhir")
@roles_required(*CLINICAL)
def fhir(patient_id):
    """F4: FHIR R4B Bundle of the patient's genomic results (scenario S11)."""
    p = get_patient(patient_id)
    release = current_release(db.session)
    if release is None:
        abort(409, "No current ClinVar release.")
    bundle = patient_bundle(db.session, p, release)
    audit(db.session, "fhir_export", "patient", p.id, user_id=current_user.id)
    db.session.commit()
    response = current_app.response_class(
        bundle.model_dump_json(indent=2), mimetype="application/fhir+json"
    )
    response.headers["Content-Disposition"] = f'inline; filename="patient-{p.id}-fhir.json"'
    return response


@bp.app_errorhandler(409)
def conflict(err):
    return jsonify(error=str(err.description)), 409
