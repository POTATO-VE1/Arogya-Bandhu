"""Bulk import API endpoints (docs/06 T18)."""
from __future__ import annotations

import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import current_user
from app import importer
from app.models import AuditLog, User

router = APIRouter(prefix="/api/import", tags=["import"])

# temp store for uploaded files (keyed by file_id)
_UPLOAD_DIR = Path(tempfile.gettempdir()) / "ab_imports"
_UPLOAD_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
UPLOAD_TTL = 3600  # 1 hour


def cleanup_old_uploads() -> None:
    """Remove upload files older than UPLOAD_TTL. Called at startup."""
    now = time.time()
    for f in _UPLOAD_DIR.glob("*.bin"):
        try:
            if now - f.stat().st_mtime > UPLOAD_TTL:
                f.unlink(missing_ok=True)
                f.with_suffix(".json").unlink(missing_ok=True)
        except Exception:
            pass


def _get_hospital(request: Request) -> str:
    return request.session.get("hospital_code", settings.HOSPITAL_CODE)


def _get_user_id(request: Request) -> str:
    return request.session.get("user_id", "")


@router.post("/preview")
async def preview_import(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
):
    """Upload a CSV/Excel file → get column mapping suggestions + first 20 rows preview."""
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "File too large — max 10MB")
    if not content:
        raise HTTPException(400, "Empty file")

    filename = file.filename or "upload.csv"
    try:
        rows = importer.parse_file(content, filename)
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")

    if not rows:
        raise HTTPException(400, "No data rows found in file")

    headers = list(rows[0].keys())
    mapping = importer.suggest_mapping(headers)

    hospital_code = _get_hospital(request)
    results = importer.preview(
        rows[:50], mapping, hospital_code, db,
        default_protocol="wound_care",
    )

    # save full file for confirm step
    file_id = uuid.uuid4().hex
    file_path = _UPLOAD_DIR / f"{file_id}.bin"
    file_path.write_bytes(content)
    meta_path = _UPLOAD_DIR / f"{file_id}.json"
    meta_path.write_text(json.dumps({
        "filename": filename,
        "total_rows": len(rows),
        "headers": headers,
    }))

    return {
        "file_id": file_id,
        "filename": filename,
        "headers": headers,
        "total_rows": len(rows),
        "mapping_suggestions": mapping,
        "rows": [r.to_dict() for r in results],
        "protocols": ["wound_care", "antibiotic_course", "fever_viral"],
    }


class ConfirmRequest(BaseModel):
    file_id: str
    mapping: dict[str, dict[str, Any]]
    selected_indices: list[int]
    default_protocol: str = "wound_care"
    default_ward: str | None = None


@router.post("/confirm")
async def confirm_import(
    req: ConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Accept user-adjusted mapping + selected rows → import into database."""
    file_path = _UPLOAD_DIR / f"{req.file_id}.bin"
    meta_path = _UPLOAD_DIR / f"{req.file_id}.json"
    if not file_path.exists():
        raise HTTPException(404, "Upload expired or not found — please upload again")

    meta = json.loads(meta_path.read_text())
    content = file_path.read_bytes()
    rows = importer.parse_file(content, meta["filename"])

    hospital_code = user.hospital_code
    user_id = user.id

    # filter to selected rows
    selected_rows = [rows[i] for i in req.selected_indices if i < len(rows)]

    results = importer.preview(
        selected_rows, req.mapping, hospital_code, db,
        default_protocol=req.default_protocol,
        default_ward=req.default_ward,
    )

    import_result = importer.execute_import(results, hospital_code, user_id, db)

    # audit log
    from app.db import now_utc
    db.add(AuditLog(
        hospital_code=hospital_code,
        actor=user.username,
        action="bulk_import",
        entity_id=req.file_id,
        meta=json.dumps({"imported": import_result.imported, "skipped": import_result.skipped}),
        created_at=now_utc(),
    ))
    db.commit()

    # cleanup temp files
    try:
        file_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
    except Exception:
        pass

    return import_result.to_dict()


@router.get("/template/{protocol_id}")
async def download_template(protocol_id: str, _user: User = Depends(current_user)):
    """Download a CSV template with correct headers for a given protocol."""
    csv_content = importer.generate_template(protocol_id)
    from fastapi.responses import Response
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=template_{protocol_id}.csv"},
    )
