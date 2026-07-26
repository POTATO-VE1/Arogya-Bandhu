"""Google Fit REST API client — fetch health metrics.

Uses httpx (already pinned) — no SDK dependency.
Data types: https://developers.google.com/fit/rest/v1/reference/data-types
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger("health_fit.client")

GOOGLE_FIT_API = "https://www.googleapis.com/fitness/v1/users/me"

# ── Data type IDs → our metric_type mapping ───────────────────────────────────
METRIC_MAP = {
    "com.google.heart_rate.bpm": "heart_rate",
    "com.google.oxygen_saturation": "spo2",
    "com.google.step_count.delta": "steps",
    "com.google.sleep.segment": "sleep",
    "com.google.body.temperature": "body_temp",
    "com.google.blood_pressure": "blood_pressure",
    "com.google.weight": "weight",
}

# ── Units per metric ─────────────────────────────────────────────────────────
METRIC_UNITS = {
    "heart_rate": "bpm",
    "spo2": "%",
    "steps": "count",
    "sleep": "minutes",
    "body_temp": "°C",
    "blood_pressure": "mmHg",
    "weight": "kg",
}

# ── Derived data source names (for aggregation) ──────────────────────────────
DERIVED_SOURCES = {
    "heart_rate": "derived:com.google.heart_rate.bpm:merge_max",
    "spo2": "raw:com.google.oxygen_saturation:merge_min",
    "steps": "derived:com.google.step_count.delta:merge_daily",
    "body_temp": "derived:com.google.body.temperature:merge_avg",
    "weight": "raw:com.google.weight:merge_avg",
}

# Sleep uses special dataset
SLEEP_SOURCE = "raw:com.google.sleep.segment:merge_period"


def _ms_to_iso(ms: int) -> str:
    """Convert epoch milliseconds to ISO-8601."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _iso_to_ms(iso_str: str) -> int:
    """Convert ISO-8601 to epoch milliseconds."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def _parse_point_value(point: dict, data_type: str) -> float | None:
    """Extract numeric value from a Google Fit data point."""
    try:
        if data_type == "com.google.heart_rate.bpm":
            return point["value"][0]["fpVal"]
        elif data_type == "com.google.oxygen_saturation":
            return point["value"][0]["fpVal"] * 100  # Google returns 0-1, we want %
        elif data_type == "com.google.step_count.delta":
            return float(point["value"][0]["intVal"])
        elif data_type == "com.google.body.temperature":
            return point["value"][0]["fpVal"]
        elif data_type == "com.google.weight":
            return point["value"][0]["fpVal"]
        elif data_type == "com.google.blood_pressure":
            systolic = point["value"][0]["fpVal"]
            diastolic = point["value"][1]["fpVal"]
            return systolic  # store systolic as primary, diastolic in metadata
        elif data_type == "com.google.sleep.segment":
            # sleep segment: duration in ms, sleep level in intVal
            start_ns = int(point["startTimeNanos"])
            end_ns = int(point["endTimeNanos"])
            duration_min = (end_ns - start_ns) / (1e9 * 60)
            return duration_min
    except (KeyError, IndexError, TypeError) as e:
        log.debug("failed to parse point for %s: %s", data_type, e)
    return None


def fetch_metric(
    access_token: str,
    data_type: str,
    start_iso: str,
    end_iso: str,
) -> list[dict]:
    """Fetch a single metric from Google Fit API.

    Returns list of: {"value": float, "recorded_at": str, "source": str}
    """
    start_ms = _iso_to_ms(start_iso)
    end_ms = _iso_to_ms(end_iso)

    # Build dataset ID
    if data_type == "com.google.sleep.segment":
        dataset_id = f"{start_ms}-{end_ms}:{SLEEP_SOURCE}"
    elif data_type in DERIVED_SOURCES:
        dataset_id = f"{start_ms}-{end_ms}:{DERIVED_SOURCES[data_type]}"
    else:
        # raw source
        dataset_id = f"{start_ms}-{end_ms}:{data_type}"

    url = f"{GOOGLE_FIT_API}/dataSources/{data_type.replace('.', '%2E').replace('/', '%2F')}/datasets/{dataset_id}"

    # URL-encode the dataset ID properly
    # Actually, Google Fit API expects a specific URL structure:
    # /users/me/dataStreams/{dataSourceId}/datasets/{datasetId}
    # Let's use the aggregate endpoint instead for simpler parsing

    headers = {"Authorization": f"Bearer {access_token}"}

    # Use aggregate endpoint — simpler and handles bucketing
    aggregate_body = {
        "aggregateBy": [{"dataTypeName": data_type}],
        "bucketByTime": {"durationMillis": 86400000},  # 1-day buckets
        "startTimeMillis": start_ms,
        "endTimeMillis": end_ms,
    }

    try:
        r = httpx.post(
            f"{GOOGLE_FIT_API}/dataset:aggregate",
            headers=headers,
            json=aggregate_body,
            timeout=15.0,
        )
        if r.status_code != 200:
            log.warning("Google Fit aggregate %s failed %s: %s", data_type, r.status_code, r.text[:200])
            return []

        results = []
        for bucket in r.json().get("bucket", []):
            for dataset in bucket.get("dataset", []):
                for point in dataset.get("point", []):
                    val = _parse_point_value(point, data_type)
                    if val is not None:
                        start_ns = int(point.get("startTimeNanos", 0))
                        source = point.get("originDataSourceId", data_type)
                        results.append({
                            "value": round(val, 2),
                            "recorded_at": _ms_to_iso(start_ns // 1_000_000),
                            "source": source,
                        })
        return results

    except Exception as e:
        log.warning("Google Fit fetch %s error: %s", data_type, e)
        return []


def fetch_all_metrics(
    access_token: str,
    start_iso: str,
    end_iso: str,
) -> dict[str, list[dict]]:
    """Fetch all available metrics from Google Fit.

    Returns: {metric_type: [{value, recorded_at, source}, ...]}
    """
    results = {}
    for google_type, our_type in METRIC_MAP.items():
        if our_type == "blood_pressure":
            # skip BP for now — needs special parsing
            continue
        points = fetch_metric(access_token, google_type, start_iso, end_iso)
        if points:
            results[our_type] = points
    return results


def test_connection(access_token: str) -> dict | None:
    """Test if the access token is valid by fetching user info.

    Returns: {"display_name": str, "id": str} or None.
    """
    try:
        r = httpx.get(
            "https://www.googleapis.com/fitness/v1/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning("Google Fit connection test failed: %s", e)
    return None
