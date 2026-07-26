"""FHIR R4 DischargeSummaryRecord export (docs/03 §9). ABDM-ready artifact.

Pragmatic: correct structure + SNOMED codes; no encryption/HIE-CM exchange.
Validate against the NRCeS IG before any production submission.
"""
import json
import uuid
from datetime import datetime, timezone

from app.models import Enrollment, EnrollmentMed, Patient

DISCHARGE_SUMMARY_SCT = "373942005"  # SNOMED-CT "Discharge summary"
PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DischargeSummaryRecord"


def build_bundle(patient: Patient, enrollment: Enrollment, meds: list[EnrollmentMed]) -> dict:
    bid = f"urn:uuid:{uuid.uuid4()}"
    now = datetime.now(timezone.utc).isoformat()
    p_id = f"urn:uuid:{patient.id}"
    en_id = f"urn:uuid:{enrollment.id}"

    composition = {
        "resourceType": "Composition",
        "id": enrollment.id,
        "meta": {"profile": [PROFILE]},
        "status": "final",
        "type": {"coding": [{"system": "http://snomed.info/sct",
                             "code": DISCHARGE_SUMMARY_SCT, "display": "Discharge summary"}]},
        "subject": {"reference": p_id},
        "encounter": {"reference": en_id},
        "date": now,
        "section": [
            {"title": "Diagnoses",
             "text": {"div": f"<div>{enrollment.condition_label}</div>", "status": "additional"}},
            {"title": "Medications on discharge", "entry": [
                {"reference": f"urn:uuid:{m.id}"} for m in meds]},
        ],
    }
    pat = {
        "resourceType": "Patient", "id": patient.id,
        "name": [{"text": patient.name}],
    }
    if patient.age:
        pat["birthDate"] = str((datetime.now(timezone.utc).year) - patient.age)
    if patient.sex:
        pat["gender"] = {"M": "male", "F": "female", "O": "other"}.get(patient.sex, "unknown")
    if patient.abha_number:
        pat["identifier"] = [{"system": "https://healthid.ndhm.gov.in",
                              "value": patient.abha_number}]
    encounter = {
        "resourceType": "Encounter", "id": enrollment.id,
        "status": "finished",
        "class": {"code": "AMB", "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode"},
        "subject": {"reference": p_id},
        "period": {"start": enrollment.discharge_date},
        "serviceProvider": {"display": "District Hospital Demo"},
    }
    cond = {
        "resourceType": "Condition", "id": f"cond-{enrollment.id}",
        "subject": {"reference": p_id},
        "code": {"text": enrollment.condition_label},
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                                       "code": "resolved"}]},
    }
    med_reqs = []
    for m in meds:
        mr = {
            "resourceType": "MedicationRequest", "id": m.id,
            "subject": {"reference": p_id},
            "status": "active",
            "intent": "order",
            "medicationCodeableConcept": {"text": m.med_name},
        }
        if m.course_days:
            mr["dispenseRequest"] = {"expectedSupplyDuration": {"value": m.course_days,
                                                                "unit": "d"}}
        med_reqs.append(mr)

    return {
        "resourceType": "Bundle", "id": bid, "type": "document",
        "timestamp": now,
        "entry": [
            {"fullUrl": f"urn:uuid:{enrollment.id}", "resource": composition},
            {"fullUrl": p_id, "resource": pat},
            {"fullUrl": en_id, "resource": encounter},
            {"fullUrl": f"urn:uuid:cond-{enrollment.id}", "resource": cond},
        ] + [{"fullUrl": f"urn:uuid:{m.id}", "resource": mr}
             for m, mr in zip(meds, med_reqs)],
    }