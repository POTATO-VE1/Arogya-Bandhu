import asyncio
import json
import time
from collections import deque

_subscribers: list[dict] = []


def publish(kind: str, entity_id: str) -> None:
    """Thread-safe enough for our tiny fan-out (GIL). Called from sync engine/threads."""
    evt = {"type": kind, "id": entity_id, "t": time.time()}
    for sub in list(_subscribers):
        sub["events"].append(evt)


def _subscribe():
    evts: deque = deque()
    ref = {"events": evts}
    _subscribers.append(ref)
    return evts, ref


def _unsubscribe(ref) -> None:
    if ref in _subscribers:
        _subscribers.remove(ref)


async def event_stream():
    evts, ref = _subscribe()
    last_beat = time.time()
    try:
        while True:
            while evts:
                yield f"data: {json.dumps(evts.popleft())}\n\n"
            await asyncio.sleep(1)
            if time.time() - last_beat > 20:
                yield ": heartbeat\n\n"
                last_beat = time.time()
    finally:
        _unsubscribe(ref)