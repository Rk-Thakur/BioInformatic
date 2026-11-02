"""F4: export a patient's genomic results as a FHIR Bundle (paper §4.5).

FHIR R4B (the current R4-compatible release), following a simplified subset of the
HL7 Genomics Reporting IG variant profile: one Observation per stored panel variant
(gene, alleles, position, zygosity, clinical significance under the current release)
and one DiagnosticReport that lists them. Every resource is built with fhir.resources,
which validates it, so a bundle that serializes is structurally valid FHIR.

An Observation's status is "amended" when the variant's interpretation changed after
it was first reported (scenario RS1), otherwise "final".
"""

import uuid
from datetime import UTC, datetime

from fhir.resources.R4B.bundle import Bundle
from sqlalchemy import select

from app.models import Alert, AlertProvenance, PatientVariant
from app.services.classify import classify_keys

LOINC = "http://loinc.org"
HGNC = "http://www.genenames.org/geneid"
GENOMICS_VARIANT_PROFILE = "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/variant"
CATEGORY_LAB = {
    "coding": [
        {
            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
            "code": "laboratory",
        }
    ]
}

HGNC_IDS = {  # HGNC identifiers of the panel genes
    "GCK": "HGNC:4195",
    "HNF1A": "HGNC:11621",
    "HNF4A": "HGNC:5024",
    "HNF1B": "HGNC:11630",
    "KCNJ11": "HGNC:6257",
    "ABCC8": "HGNC:59",
    "INS": "HGNC:6081",
}
REFSEQ_CHROM = {  # GRCh38 RefSeq accessions of the panel chromosomes
    "7": "NC_000007.14",
    "11": "NC_000011.10",
    "12": "NC_000012.12",
    "17": "NC_000017.11",
    "20": "NC_000020.11",
}
ZYGOSITY = {  # LOINC answer list LL381-5
    "heterozygous": ("LA6706-1", "Heterozygous"),
    "homozygous": ("LA6705-3", "Homozygous"),
    "hemizygous": ("LA6707-9", "Hemizygous"),
}
SIGNIFICANCE = {  # LOINC answer list LL4034-6
    "P": ("LA6668-3", "Pathogenic"),
    "LP": ("LA26332-9", "Likely pathogenic"),
    "P/LP": ("LA26332-9", "Likely pathogenic"),  # LOINC has no combined answer: report the weaker
    "VUS": ("LA26333-7", "Uncertain significance"),
    "LB": ("LA26334-5", "Likely benign"),
    "B": ("LA6675-8", "Benign"),
    "B/LB": ("LA26334-5", "Likely benign"),
}


def _cc(system, code, display=None, text=None):
    coding = {"system": system, "code": code}
    if display:
        coding["display"] = display
    out = {"coding": [coding]}
    if text:
        out["text"] = text
    return out


def _component(code, display, **value):
    return {"code": _cc(LOINC, code, display), **value}


def _amended_variant_ids(session, patient_id, current):
    """Variants whose interpretation changed after it was first reported."""
    amended = set(
        session.scalars(
            select(Alert.patient_variant_id).where(
                Alert.patient_id == patient_id, Alert.reclassified
            )
        )
    )
    first = {}
    rows = session.execute(
        select(Alert.patient_variant_id, AlertProvenance.classification)
        .join(AlertProvenance, AlertProvenance.alert_id == Alert.id)
        .where(Alert.patient_id == patient_id)
        .order_by(AlertProvenance.id)
    )
    for pv_id, cls in rows:
        first.setdefault(pv_id, cls)
    amended |= {pv_id for pv_id, cls in first.items() if current.get(pv_id) not in (None, cls)}
    return amended


def variant_observation(pv, classification, status, patient_ref, release_version):
    components = [
        _component(
            "48018-6",
            "Gene studied [ID]",
            valueCodeableConcept=_cc(HGNC, HGNC_IDS.get(pv.gene, pv.gene or ""), pv.gene),
        ),
        _component(
            "62374-4",
            "Human reference sequence assembly version",
            valueCodeableConcept=_cc(LOINC, "LA26806-2", "GRCh38"),
        ),
        _component("69547-8", "Genomic ref allele [ID]", valueString=pv.ref),
        _component("69551-0", "Genomic alt allele [ID]", valueString=pv.alt),
        _component(
            "81254-5",
            "Variant exact start-end",
            valueRange={"low": {"value": pv.pos}, "high": {"value": pv.pos + len(pv.ref) - 1}},
        ),
    ]
    if pv.chrom in REFSEQ_CHROM:
        components.append(
            _component(
                "48013-7",
                "Genomic reference sequence [ID]",
                valueCodeableConcept=_cc(
                    "http://www.ncbi.nlm.nih.gov/refseq", REFSEQ_CHROM[pv.chrom]
                ),
            )
        )
    if pv.zygosity in ZYGOSITY:
        code, display = ZYGOSITY[pv.zygosity]
        components.append(
            _component("53034-5", "Allelic state", valueCodeableConcept=_cc(LOINC, code, display))
        )
    if classification in SIGNIFICANCE:
        code, display = SIGNIFICANCE[classification]
        components.append(
            _component(
                "53037-8",
                "Genetic variation clinical significance [Imp]",
                valueCodeableConcept=_cc(
                    LOINC, code, display, text=f"{classification} (ClinVar {release_version})"
                ),
            )
        )
    return {
        "resourceType": "Observation",
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ge-ehr/pv/{pv.id}")),
        "meta": {"profile": [GENOMICS_VARIANT_PROFILE]},
        "status": status,
        "category": [CATEGORY_LAB],
        "code": _cc(LOINC, "69548-6", "Genetic variant assessment"),
        "subject": {"reference": patient_ref},
        "valueCodeableConcept": _cc(LOINC, "LA9633-4", "Present"),
        "component": components,
    }


def patient_bundle(session, patient, release, *, only_classified=True):
    """Build and validate the Bundle for one patient. Returns a fhir.resources Bundle."""
    pvs = list(
        session.scalars(
            select(PatientVariant)
            .where(PatientVariant.patient_id == patient.id)
            .order_by(PatientVariant.id)
        )
    )
    classes = classify_keys(session, [pv.key for pv in pvs], release)
    current = {pv.id: classes[pv.key].classification for pv in pvs}
    amended = _amended_variant_ids(session, patient.id, current)

    patient_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ge-ehr/patient/{patient.external_id}"))
    patient_ref = f"urn:uuid:{patient_id}"
    patient_res = {
        "resourceType": "Patient",
        "id": patient_id,
        "identifier": [{"system": "urn:ge-ehr:synthea", "value": patient.external_id}],
        "name": [{"text": patient.name}],
    }
    if patient.sex in ("M", "F"):
        patient_res["gender"] = {"M": "male", "F": "female"}[patient.sex]
    if patient.birth_date:
        patient_res["birthDate"] = patient.birth_date.isoformat()

    observations = []
    for pv in pvs:
        cls = current[pv.id]
        if only_classified and cls not in SIGNIFICANCE:
            continue  # not in ClinVar, conflicting or no tier: nothing to report
        status = "amended" if pv.id in amended else "final"
        observations.append(variant_observation(pv, cls, status, patient_ref, release.version))

    obs_refs = [{"reference": f"urn:uuid:{o['id']}"} for o in observations]
    report = {
        "resourceType": "DiagnosticReport",
        "id": str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"ge-ehr/report/{patient.external_id}/{release.version}")
        ),
        "status": "amended" if any(o["status"] == "amended" for o in observations) else "final",
        "category": [_cc("http://terminology.hl7.org/CodeSystem/v2-0074", "GE", "Genetics")],
        "code": _cc(LOINC, "51969-4", "Genetic analysis report"),
        "subject": {"reference": patient_ref},
        "issued": datetime.now(UTC).isoformat(),
        "conclusion": f"Monogenic diabetes gene panel interpreted with ClinVar {release.version}. "
        "Advisory only; requires specialist review.",
        "result": obs_refs,
    }
    entries = [patient_res, report, *observations]
    return Bundle.model_validate(
        {
            "resourceType": "Bundle",
            "type": "collection",
            "timestamp": datetime.now(UTC).isoformat(),
            "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in entries],
        }
    )
