#!/usr/bin/env python3
"""simulate_hmis_push.py — end-to-end demo of a hospital's HMIS pushing
a discharge to Aarogya Bandhu.

What it does
------------
1. Builds a realistic discharge JSON (Karnataka patient, NIC HMIS field
   names — the same shape a real district hospital's EMR exports).
2. Computes the HMAC-SHA256(HMIS_SHARED_SECRET, body) signature.
3. POSTs it to the running app's `/api/hmis/discharge-intake`.
4. Prints the response (created / duplicate / 4xx) and the
   enrollment id so the operator can click it on the dashboard.

How to use it
-------------
    # 1. start the app (in another terminal):
    cd backend && ../venv/bin/uvicorn app.main:app --port 8000

    # 2. set the same secret the app uses:
    export HMIS_SHARED_SECRET='demo-secret-1234'
    export AB_BASE_URL='http://localhost:8000'

    # 3. run the simulator:
    cd backend && ../venv/bin/python -m app.scripts.simulate_hmis_push

    # 4. open the dashboard: http://localhost:8000/board
    #    the new patient appears in < 1 second (SSE event).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import sys
import time
from datetime import date, timedelta

import httpx

DEFAULT_BASE = os.getenv("AB_BASE_URL", "http://localhost:8000")
DEFAULT_SECRET = os.getenv("HMIS_SHARED_SECRET", "demo-secret-1234")
DEFAULT_HOSPITAL = os.getenv("HOSPITAL_CODE", "KA-DIST-01")

_FIRST = [
    "Lakshmamma", "Gangamma", "Honnamma", "Parvathamma", "Savithramma",
    "Suma", "Vijaya", "Kamala", "Ramesh", "Venkatesh", "Manjunath",
    "Suresh", "Ganesh", "Mahesh",
]
_LAST = ["Gowda", "Patil", "Shetty", "Hegde", "Kamath", "Rao", "Reddy", "Nair"]
_WARDS = ["Ward-1", "Ward-2", "Ward-3", "ICU", "Orthopedics", "Cardiology"]
_DIAGNOSES = [
    "Post-op appendectomy", "C-section wound care", "Lower RTI on Azithromycin",
    "UTI on Ciprofloxacin", "Post-op hernia repair", "Diabetic foot ulcer",
]
_PROTOCOLS = ["wound_care", "antibiotic_course", "fever_viral"]


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def build_discharge(rng: random.Random) -> dict:
    """Build one realistic discharge event using NIC HMIS field names."""
    name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
    age = rng.randint(28, 78)
    sex = "F" if rng.random() < 0.5 else "M"
    phone = f"+919{rng.randint(10**8, 10**9 - 1):09d}"
    cg = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
    days_ago = rng.randint(0, 5)
    discharge = (date.today() - timedelta(days=days_ago)).isoformat()
    mr = f"MR-{date.today().year}-{rng.randint(1000, 9999)}"
    abha = f"14-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"

    # Match NIC HMIS field names verbatim so the hospital's IT sees
    # the same shape in the audit log / dashboard.
    return {
        "patient_name": name,
        "AGE_YEARS": age,
        "GENDER": sex,
        "ATTENDANT_NAME": cg,
        "mobile_no": phone,
        "date_of_discharge": discharge,
        "WARD": rng.choice(_WARDS),
        "diagnosis_at_discharge": rng.choice(_DIAGNOSES),
        "ABHA": abha,
        "MRN": mr,
        "consent": True,
        "emr_source": "nic_hmis",
        "emr_patient_id": mr,
        "hospital_code": DEFAULT_HOSPITAL,
        "medications": [
            {"name": "Paracetamol 500mg", "type": "other", "doses_per_day": 3},
            {"name": "Amoxiclav 625mg", "type": "antibiotic", "doses_per_day": 2},
        ][:rng.randint(1, 3)],
        "protocol_id": rng.choice(_PROTOCOLS),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=DEFAULT_BASE, help="App base URL")
    ap.add_argument("--secret", default=DEFAULT_SECRET, help="HMIS shared secret")
    ap.add_argument("--count", type=int, default=1, help="Number of discharges to push")
    ap.add_argument("--source", default="nic_hmis",
                    choices=["nic_hmis", "e_hospital", "medmantra", "custom"],
                    help="EMR source for the X-HMIS-Source header")
    args = ap.parse_args(argv)

    rng = random.Random(int(time.time()))
    print(f"== HMIS Push Simulator ==")
    print(f"  base:    {args.base}")
    print(f"  source:  {args.source}")
    print(f"  count:   {args.count}")
    print(f"  secret:  {args.secret[:4]}… (len={len(args.secret)})")
    print()

    created = 0
    duplicates = 0
    errors = 0
    with httpx.Client(base_url=args.base, timeout=10.0) as cli:
        for i in range(args.count):
            ev = build_discharge(rng)
            body = json.dumps(ev).encode()
            sig = sign(args.secret, body)
            headers = {
                "Content-Type": "application/json",
                "X-HMIS-Signature": sig,
                "X-HMIS-Source": args.source,
                "X-HMIS-Hospital-Code": DEFAULT_HOSPITAL,
            }
            r = cli.post("/api/hmis/discharge-intake",
                         content=body, headers=headers)
            status = r.status_code
            try:
                ack = r.json()
            except Exception:
                ack = {"raw": r.text[:200]}
            mark = {201: "✓", 200: "✓", 400: "✗", 401: "✗", 404: "✗",
                    503: "✗"}.get(status, "?")
            print(f"  [{i+1}/{args.count}] {mark} HTTP {status}  "
                  f"patient={ev['patient_name']:<22}  "
                  f"mrn={ev['MRN']}  "
                  f"abha=…{ev['ABHA'][-4:]}  "
                  f"action={ack.get('action', '?')}")
            if r.status_code in (200, 201) and ack.get("action") == "created":
                created += 1
                eid = ack.get("enrollment_id")
                if eid:
                    print(f"             → open the dashboard: {args.base}/board?enrollment={eid}")
            elif ack.get("action", "").startswith("duplicate"):
                duplicates += 1
            else:
                errors += 1
                if r.status_code >= 400:
                    print(f"             → error: {ack}")

    print()
    print(f"== Summary: {created} created, {duplicates} duplicates, {errors} errors ==")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
