from fastapi import APIRouter, Depends, HTTPException

from app.deps import current_user
from app.protocol_loader import get_protocol, protocol_meta_list

router = APIRouter(prefix="/api/protocols", tags=["protocols"])


@router.get("")
def list_protocols(_=Depends(current_user)):
    return protocol_meta_list()


@router.get("/{pid}/detail")
def protocol_detail(pid: str, _=Depends(current_user)):
    """Return full protocol nodes with option reasons for label resolution."""
    try:
        proto = get_protocol(pid)
    except Exception:
        raise HTTPException(404, "protocol not found")
    # extract question nodes with their option labels
    questions = {}
    for node_id, node in proto.get("nodes", {}).items():
        if node.get("type") == "question":
            questions[node_id] = {
                "clip": node.get("clip", ""),
                "options": {
                    digit: {
                        "reason": opt.get("reason", ""),
                        "score": opt.get("score", 0),
                    }
                    for digit, opt in node.get("options", {}).items()
                },
            }
    return {
        "id": proto["id"],
        "name_en": proto.get("name_en", ""),
        "name_kn": proto.get("name_kn", ""),
        "questions": questions,
    }
