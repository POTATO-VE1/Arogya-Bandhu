from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from app.deps import current_user
from app.events import event_stream

router = APIRouter(tags=["events"])


@router.get("/api/events")
def events(_=Depends(current_user)):
    return StreamingResponse(event_stream(), media_type="text/event-stream")