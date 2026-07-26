import json
import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def write_audit(
    db: Session,
    *,
    hospital_code: str,
    actor: str,
    action: str,
    entity_id: str | None = None,
    meta: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            hospital_code=hospital_code,
            actor=actor,
            action=action,
            entity_id=entity_id,
            meta=json.dumps(meta) if meta is not None else None,
        )
    )
    # caller is responsible for committing the transaction