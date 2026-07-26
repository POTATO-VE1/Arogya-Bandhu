from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
CALLING_OPEN = time(9, 0)
CALLING_CLOSE = time(21, 0)


def schedule_at_ist_10(discharge_date: str, day_index: int) -> str:
    """discharge_date (YYYY-MM-DD) + day_index, at 10:00 IST → stored as UTC ISO."""
    y, m, d = map(int, discharge_date.split("-"))
    local = datetime(y, m, d, tzinfo=IST) + timedelta(days=day_index)
    local = local.replace(hour=10, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc).isoformat()


def clamp_to_calling_window(utc_iso: str) -> str:
    """If a scheduled time falls outside 09:00–21:00 IST, defer to 09:00 IST that day."""
    dt = datetime.fromisoformat(utc_iso).astimezone(IST)
    if CALLING_OPEN <= dt.time() < CALLING_CLOSE:
        return utc_iso
    local = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc).isoformat()


def within_calling_window(now_utc_iso: str | None = None) -> bool:
    now = (datetime.fromisoformat(now_utc_iso) if now_utc_iso
           else datetime.now(timezone.utc)).astimezone(IST)
    return CALLING_OPEN <= now.time() < CALLING_CLOSE