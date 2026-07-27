"""Bulk CSV/Excel patient importer (docs/06 T18).

Parses uploaded files, fuzzy-maps columns to our schema, normalises data,
validates, previews, and imports in a single transaction.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.db import now_utc
from app.models import Enrollment, EnrollmentMed, Patient


# ── column aliases ────────────────────────────────────────────────────────────
COLUMN_ALIASES: dict[str, list[str]] = {
    "name": [
        "patient name", "patient_name", "name", "patient", "patientname",
        "patient name ", " name", "name ", "patient_name ",
        "ಹೆಸರು", "ರೋಗಿ ಹೆಸರು", "rogi hesaru",
    ],
    "age": [
        "age", "patient age", "patient_age", "age years", "patientage",
        "ವಯಸ್ಸು", "vayassu",
    ],
    "sex": [
        "sex", "gender", "patient sex", "patient gender", "patient_sex",
        "patient_gender", "ಲಿಂಗ", "linga",
    ],
    "caregiver_name": [
        "caregiver", "caregiver name", "caregiver_name", "attendant",
        "attendant name", "attendant_name", "cg name", "cg_name",
        "ಜವಾಬ್ದಾರಿ ವ್ಯಕ್ತಿ", "javabdari vyakti",
    ],
    "caregiver_phone": [
        "phone", "caregiver phone", "caregiver_phone", "mobile", "contact",
        "contact number", "phone number", "mobile number", "caregiver mobile",
        "cg phone", "cg_phone", "ದೂರವಾಣಿ", "dooravani",
    ],
    "condition_label": [
        "condition", "diagnosis", "discharge diagnosis", "condition_label",
        "condition label", "discharge_diagnosis", "primary diagnosis",
        "ರೋಗ", "roga",
    ],
    "protocol_id": [
        "protocol", "protocol_id", "follow-up type", "followup type",
        "follow up type", "followup_type", "follow-up_type",
        "ಪ್ರೊಟೋಕಾಲ್", "protocol id",
    ],
    "discharge_date": [
        "discharge date", "discharge_date", "date of discharge", "discharged",
        "discharge_date ", "date_of_discharge",
        "ಡಿಸ್ಚಾರ್ಜ್ ದಿನಾಂಕ", "discharge date ",
    ],
    "ward": [
        "ward", "floor", "floor/ward", "ward/floor", "floor_no",
        "ಬ್ಲಾಕ್", "block",
    ],
    "med_name": [
        "medication", "medicine", "drug", "med_name", "medicine name",
        "medication name", "drug name", "medication_name",
        "ಔಷಧ", "oushadha",
    ],
    "med_type": [
        "type", "med_type", "medicine type", "medication type",
        "medicine_type", "medication_type", "antibiotic flag",
    ],
    "doses_per_day": [
        "doses", "doses_per_day", "doses per day", "frequency",
        "daily doses", "daily_doses", "doses/day",
    ],
}

# required fields for a valid import row
REQUIRED_FIELDS = {"name", "caregiver_name", "caregiver_phone", "protocol_id", "condition_label"}

# protocol aliases → canonical id
PROTOCOL_ALIASES: dict[str, str] = {
    "wound care": "wound_care",
    "wound_care": "wound_care",
    "woundcare": "wound_care",
    "wound care ": "wound_care",
    "antibiotic course": "antibiotic_course",
    "antibiotic_course": "antibiotic_course",
    "antibioticcourse": "antibiotic_course",
    "antibiotic course ": "antibiotic_course",
    "fever viral": "fever_viral",
    "fever_viral": "fever_viral",
    "feverviral": "fever_viral",
    "fever viral ": "fever_viral",
    "ತುರ್ಬೆ ಆರೈಕೆ": "wound_care",
    "ಆಂಟಿಬಯೋಟಿಕ್ ಕೋರ್ಸ್": "antibiotic_course",
    "ಜ್ವರ": "fever_viral",
}

VALID_PROTOCOLS = {"wound_care", "antibiotic_course", "fever_viral"}
VALID_SEX = {"m": "M", "f": "F", "male": "M", "female": "F", "o": "O", "other": "O",
             "ಪುರುಷ": "M", "ಮಹಿಳೆ": "F"}
VALID_MED_TYPE = {"antibiotic": "antibiotic", "ab": "antibiotic", "other": "other"}


# ── file parsing ──────────────────────────────────────────────────────────────

def parse_file(content: bytes, filename: str) -> list[dict[str, str]]:
    """Parse CSV or Excel file into list of row dicts. Raises ValueError on bad format."""
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xls")):
        return _parse_excel(content)
    if lower.endswith(".csv"):
        return _parse_csv(content)
    # try CSV first (most common), fall back to Excel
    try:
        return _parse_csv(content)
    except Exception:
        return _parse_excel(content)


def _parse_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")  # handle BOM
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for row in reader:
        cleaned = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}
        if any(v for v in cleaned.values()):
            rows.append(cleaned)
    return rows


def _parse_excel(content: bytes) -> list[dict[str, str]]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        headers_raw = next(rows_iter)
    except StopIteration:
        return []
    headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(headers_raw)]
    rows: list[dict[str, str]] = []
    for row in rows_iter:
        d = {}
        for i, val in enumerate(row):
            if i < len(headers):
                d[headers[i]] = str(val).strip() if val is not None else ""
        if any(d.values()):
            rows.append(d)
    wb.close()
    return rows


# ── column mapping ────────────────────────────────────────────────────────────

def _fuzzy_score(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def suggest_mapping(
    csv_headers: list[str],
    extra_aliases: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Suggest a mapping from CSV headers to our schema fields."""
    aliases = {k: list(v) for k, v in COLUMN_ALIASES.items()}
    if extra_aliases:
        for k, v in extra_aliases.items():
            aliases.setdefault(k, []).extend(v)

    mapping: dict[str, dict[str, Any]] = {}
    used_fields: set[str] = set()

    for header in csv_headers:
        h_lower = header.lower().strip()
        best_field = None
        best_score = 0.0

        for field, alias_list in aliases.items():
            if field in used_fields:
                continue
            # exact alias match
            if h_lower in [a.lower() for a in alias_list]:
                best_field = field
                best_score = 1.0
                break
            # fuzzy match against each alias
            for alias in alias_list:
                score = _fuzzy_score(h_lower, alias)
                if score > best_score and score >= 0.6:
                    best_score = score
                    best_field = field

        if best_field and best_score >= 0.6:
            mapping[header] = {"field": best_field, "confidence": round(best_score, 2)}
            used_fields.add(best_field)
        else:
            mapping[header] = {"field": None, "confidence": 0}

    return mapping


# ── normalisation helpers ─────────────────────────────────────────────────────

def _normalise_phone(raw: str) -> str | None:
    """Normalise to E.164 (+CountryNumber). Returns None if invalid."""
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return None
    # strip leading 0 or double-0
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    # Indian numbers: 10-digit → prepend 91
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) < 10 or len(digits) > 15:
        return None
    return "+" + digits


def _normalise_age(raw: str) -> int | None:
    """Extract integer age from strings like '62', '62 years', '62y'."""
    m = re.search(r"(\d+)", raw)
    if m:
        age = int(m.group(1))
        return age if 0 < age < 150 else None
    return None


def _normalise_sex(raw: str) -> str | None:
    s = raw.lower().strip()
    return VALID_SEX.get(s)


def _normalise_date(raw: str) -> str | None:
    """Try common date formats, return YYYY-MM-DD."""
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y",
                "%d.%m.%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # try ISO parse
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d")
    except Exception:
        return None


def _normalise_protocol(raw: str) -> str | None:
    s = raw.lower().strip()
    return PROTOCOL_ALIASES.get(s)


def _normalise_med_type(raw: str) -> str:
    s = raw.lower().strip()
    return VALID_MED_TYPE.get(s, "other")


def _normalise_int(raw: str) -> int | None:
    m = re.search(r"(\d+)", raw)
    return int(m.group(1)) if m else None


# ── preview / validate ───────────────────────────────────────────────────────

class RowResult:
    __slots__ = ("index", "raw", "mapped", "valid", "warnings", "errors")

    def __init__(self, index: int, raw: dict[str, str], mapped: dict[str, Any],
                 valid: bool, warnings: list[str], errors: list[str]):
        self.index = index
        self.raw = raw
        self.mapped = mapped
        self.valid = valid
        self.warnings = warnings
        self.errors = errors

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "raw": self.raw,
            "mapped": self.mapped,
            "valid": self.valid,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def preview(
    rows: list[dict[str, str]],
    mapping: dict[str, dict[str, Any]],
    hospital_code: str,
    db: Session,
    *,
    default_protocol: str | None = None,
    default_ward: str | None = None,
) -> list[RowResult]:
    """Map + validate each row. Returns list of RowResult."""
    # build reverse map: csv_header → schema_field
    field_map: dict[str, str] = {}
    for header, info in mapping.items():
        if info.get("field"):
            field_map[header] = info["field"]

    # check existing patients for duplicates
    existing = set()
    for p in db.query(Patient.name, Patient.caregiver_phone).filter(
        Patient.hospital_code == hospital_code
    ).all():
        existing.add((p.name.lower().strip(), _normalise_phone(p.caregiver_phone) or ""))

    results: list[RowResult] = []
    for i, row in enumerate(rows):
        mapped: dict[str, Any] = {}
        warnings: list[str] = []
        errors: list[str] = []

        # map each CSV column to schema field
        for csv_header, schema_field in field_map.items():
            raw_val = row.get(csv_header, "").strip()
            if not raw_val:
                continue
            mapped[schema_field] = raw_val

        # apply defaults
        if "protocol_id" not in mapped and default_protocol:
            mapped["protocol_id"] = default_protocol
        if "ward" not in mapped and default_ward:
            mapped["ward"] = default_ward

        # normalise fields
        name = (mapped.get("name") or "").strip()
        age = _normalise_age(str(mapped.get("age", ""))) if mapped.get("age") else None
        sex = _normalise_sex(str(mapped.get("sex", ""))) if mapped.get("sex") else None
        cg_name = (mapped.get("caregiver_name") or "").strip()
        cg_phone_raw = str(mapped.get("caregiver_phone", "")).strip()
        cg_phone = _normalise_phone(cg_phone_raw)
        condition = (mapped.get("condition_label") or "").strip()
        protocol = _normalise_protocol(str(mapped.get("protocol_id", ""))) if mapped.get("protocol_id") else None
        discharge = _normalise_date(str(mapped.get("discharge_date", ""))) if mapped.get("discharge_date") else None
        ward = (mapped.get("ward") or "").strip() or None
        med_name = (mapped.get("med_name") or "").strip() or None
        med_type = _normalise_med_type(str(mapped.get("med_type", ""))) if mapped.get("med_type") else None
        doses = _normalise_int(str(mapped.get("doses_per_day", ""))) if mapped.get("doses_per_day") else None

        # validation
        if not name:
            errors.append("missing patient name")
        if not cg_name:
            errors.append("missing caregiver name")
        if not cg_phone:
            errors.append(f"invalid phone: '{cg_phone_raw}'")
        if not protocol:
            errors.append(f"unknown protocol: '{mapped.get('protocol_id', '')}' — use wound_care, antibiotic_course, or fever_viral")
        if not condition:
            errors.append("missing condition/diagnosis")

        # warnings
        if not discharge:
            discharge = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            warnings.append("no discharge date — using today")
        if age is None:
            warnings.append("age missing or unparseable")
        if sex is None:
            warnings.append("sex missing or unparseable")
        if med_name and not med_type:
            med_type = "other"
            warnings.append("medication type missing — defaulting to 'other'")

        # duplicate check
        if name and cg_phone:
            key = (name.lower().strip(), cg_phone)
            if key in existing:
                warnings.append(f"duplicate: patient '{name}' with phone {cg_phone} already exists")

        result_mapped = {
            "name": name,
            "age": age,
            "sex": sex,
            "caregiver_name": cg_name,
            "caregiver_phone": cg_phone,
            "condition_label": condition,
            "protocol_id": protocol,
            "discharge_date": discharge,
            "ward": ward,
        }
        if med_name:
            result_mapped["med_name"] = med_name
            result_mapped["med_type"] = med_type or "other"
            result_mapped["doses_per_day"] = doses or 3

        results.append(RowResult(
            index=i,
            raw=row,
            mapped=result_mapped,
            valid=len(errors) == 0,
            warnings=warnings,
            errors=errors,
        ))

    return results


# ── import execution ──────────────────────────────────────────────────────────

class ImportResult:
    __slots__ = ("imported", "skipped", "enrollment_ids", "errors")

    def __init__(self, imported: int, skipped: int, enrollment_ids: list[str], errors: list[str]):
        self.imported = imported
        self.skipped = skipped
        self.enrollment_ids = enrollment_ids
        self.errors = errors

    def to_dict(self) -> dict:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "enrollment_ids": self.enrollment_ids,
            "errors": self.errors,
        }


def execute_import(
    results: list[RowResult],
    hospital_code: str,
    user_id: str,
    db: Session,
) -> ImportResult:
    """Import valid rows into the database in a single transaction."""
    imported = 0
    skipped = 0
    enrollment_ids: list[str] = []
    errors: list[str] = []
    now = now_utc()

    for r in results:
        if not r.valid:
            skipped += 1
            continue

        m = r.mapped
        try:
            # create patient
            patient = Patient(
                id=uuid.uuid4().hex,
                hospital_code=hospital_code,
                name=m["name"],
                age=m.get("age"),
                sex=m.get("sex"),
                abha_number=None,
                caregiver_name=m["caregiver_name"],
                caregiver_phone=m["caregiver_phone"],
                consent_at=now,
                created_by=user_id,
                created_at=now,
            )
            db.add(patient)
            db.flush()

            # create enrollment
            enrollment = Enrollment(
                id=uuid.uuid4().hex,
                hospital_code=hospital_code,
                patient_id=patient.id,
                protocol_id=m["protocol_id"],
                condition_label=m["condition_label"],
                ward=m.get("ward"),
                discharge_date=m.get("discharge_date", now[:10]),
                status="active",
                number_verified=0,
                created_at=now,
            )
            db.add(enrollment)
            db.flush()

            # create medication if present
            if m.get("med_name"):
                med = EnrollmentMed(
                    id=uuid.uuid4().hex,
                    enrollment_id=enrollment.id,
                    med_name=m["med_name"],
                    med_type=m.get("med_type", "other"),
                    doses_per_day=m.get("doses_per_day", 3),
                )
                db.add(med)

            imported += 1
            enrollment_ids.append(enrollment.id)

        except Exception as e:
            errors.append(f"row {r.index}: {e}")
            skipped += 1

    db.commit()
    return ImportResult(imported=imported, skipped=skipped, enrollment_ids=enrollment_ids, errors=errors)


# ── template generation ───────────────────────────────────────────────────────

def generate_template(protocol_id: str = "wound_care") -> str:
    """Generate a CSV template string with correct headers and example rows."""
    headers = [
        "Patient Name", "Age", "Sex", "Caregiver Name", "Caregiver Phone",
        "Condition", "Protocol", "Discharge Date", "Ward",
        "Medication", "Med Type", "Doses/Day",
    ]
    examples = {
        "wound_care": [
            ["Lakshmamma", "62", "F", "Ramu", "+919876543210", "Post-op appendectomy", "wound_care", "2026-07-25", "Ward-4", "Amoxiclav 625mg", "antibiotic", "2"],
            ["Ramesh", "38", "M", "Geeta", "+919876500001", "Circumcision post-op", "wound_care", "2026-07-24", "OPD", "Doxycycline 100mg", "antibiotic", "2"],
        ],
        "antibiotic_course": [
            ["Manjunath", "45", "M", "Sita", "+919876543210", "Lower RTI on azithromycin", "antibiotic_course", "2026-07-23", "Ward-2", "Azithromycin 500mg", "antibiotic", "1"],
        ],
        "fever_viral": [
            ["Honnamma", "55", "F", "Mallik", "+919999999999", "Viral fever", "fever_viral", "2026-07-25", "OPD", "Paracetamol 500mg", "other", "3"],
        ],
    }
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in examples.get(protocol_id, examples["wound_care"]):
        writer.writerow(row)
    return buf.getvalue()
