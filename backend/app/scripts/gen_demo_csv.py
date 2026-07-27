"""gen_demo_csv.py — generate a 500-row demo patient CSV for the bulk importer.

Writes `data/demo_patients_500.csv` with realistic Karnataka names, varied
wards / conditions / protocols / meds, and varied `discharge_date` from
0–45 days ago so the call scheduler has a full backlog.

Usage:
    python -m app.scripts.gen_demo_csv            # 500 rows
    python -m app.scripts.gen_demo_csv --rows 1000
"""
from __future__ import annotations

import argparse
import csv
import os
import random
from datetime import datetime, timedelta, timezone

# ── name pools (Karnataka-first, generic-Indian fallback) ────────────────────
_FIRST_F = [
    "Lakshmamma", "Gangamma", "Honnamma", "Parvathamma", "Savithramma",
    "Suma", "Vijaya", "Kamala", "Latha", "Padmavathi", "Rathnamma",
    "Sarojini", "Tulasi", "Vasantha", "Yashoda", "Annapurna",
    "Bhagyashree", "Chandrakala", "Durga", "Fathima", "Girija",
    "Jayalakshmi", "Kaveri", "Lalitha", "Manjula", "Nagarathna",
    "Pushpa", "Rajeshwari", "Sharada", "Thulasi", "Umadevi",
    "Vimala", "Yellamma", "Anita", "Asha", "Bhagya", "Chitra",
    "Deepa", "Geetha", "Indu", "Jyothi", "Kalpana",
]
_FIRST_M = [
    "Ramesh", "Venkatesh", "Manjunath", "Suresh", "Ganesh", "Mahesh",
    "Rajesh", "Santosh", "Dinesh", "Umesh", "Naveen", "Kiran",
    "Prakash", "Ravi", "Girish", "Vijay", "Anil", "Sunil", "Vinod",
    "Rahul", "Arun", "Ashok", "Basavaraj", "Darshan", "Gopal",
    "Krishna", "Lingaraj", "Mallikarjun", "Nagaraj", "Pramod",
    "Raghu", "Shankar", "Thimmaiah", "Uday", "Veeresh",
    "Yogesh", "Bharath", "Chetan", "Dharma", "Eshwar",
]
_LAST = [
    "Gowda", "Patil", "Shetty", "Hegde", "Kamath", "Rao", "Reddy",
    "Nair", "Menon", "Iyer", "Naik", "Kulkarni", "Desai", "Joshi",
    "Bhat", "Pai", "Prasad", "Murthy", "Swamy", "Kumar", "Devi",
    "Rathod", "Siddappa", "Naik", "Gowdru", "Kudachi", "Math",
]

_CONDITIONS = {
    "wound_care": [
        "Post-op appendectomy", "Post-op hernia repair", "C-section wound care",
        "Post-op knee replacement", "Abscess drainage", "Diabetic foot ulcer",
        "Post-op cholecystectomy", "Laceration repair", "Burn wound management",
        "Pilonidal sinus excision", "Post-op mastectomy", "Surgical site infection",
    ],
    "antibiotic_course": [
        "Lower RTI on Azithromycin", "UTI on Ciprofloxacin", "Wound infection on Cefixime",
        "Cellulitis on Amoxicillin", "Pneumonia on Levofloxacin",
        "Skin infection on Clindamycin", "Pharyngitis on Amoxicillin",
        "Dental infection on Metronidazole", "Typhoid on Ceftriaxone",
        "Otitis media on Amoxiclav",
    ],
    "fever_viral": [
        "Viral fever with cough", "Post-viral fatigue", "Fever with chills",
        "Dengue convalescence", "Chikungunya recovery", "Viral gastroenteritis",
        "Influenza-like illness", "Hep A convalescence",
    ],
}

_MEDS = {
    "wound_care": [
        ("Paracetamol 500mg", "other", 3),
        ("Amoxiclav 625mg", "antibiotic", 2),
        ("Ibuprofen 400mg", "other", 2),
        ("Cefuroxime 500mg", "antibiotic", 2),
        ("Metronidazole 400mg", "antibiotic", 3),
    ],
    "antibiotic_course": [
        ("Azithromycin 500mg", "antibiotic", 1),
        ("Ciprofloxacin 500mg", "antibiotic", 2),
        ("Amoxicillin 500mg", "antibiotic", 3),
        ("Cefixime 200mg", "antibiotic", 2),
        ("Doxycycline 100mg", "antibiotic", 2),
    ],
    "fever_viral": [
        ("Paracetamol 500mg", "other", 3),
        ("ORS packets", "other", 4),
    ],
}

_WARDS = [
    "Ward-1", "Ward-2", "Ward-3", "Ward-4",
    "Orthopedics", "Cardiology", "Pediatrics", "ICU",
    "Emergency", "General-Surgery", "Obstetrics",
]

_AWARE = {"Access": "Access", "Watch": "Watch", "Reserve": "Reserve"}

# Header order matches the existing demo_patients_150.csv
_HEADER = [
    "Patient Name", "Age", "Sex", "Caregiver Name", "Caregiver Phone",
    "Condition", "Protocol", "Discharge Date", "Ward",
    "Medication", "Med Type", "AWaRe Category", "Course Days", "Doses/Day",
]


def _gen_patient(rng: random.Random, i: int) -> list:
    f = rng.choice(_FIRST_F + _FIRST_M)
    l = rng.choice(_LAST)
    name = f"{f} {l}"
    # Caregiver — opposite-gender bias for realism
    if f in _FIRST_F:
        cg_first = rng.choice(_FIRST_M)
    else:
        cg_first = rng.choice(_FIRST_F)
    cg_name = f"{cg_first} {rng.choice(_LAST)}"
    sex = "F" if f in _FIRST_F else "M"
    age = rng.randint(2, 84)  # peds → elderly
    # Realistic Indian mobile (E.164, +91 prefix)
    phone = f"+919{rng.randint(10**8, 10**9 - 1):09d}"

    protocol = rng.choices(
        list(_CONDITIONS.keys()),
        weights=[45, 35, 20],  # wound_care most common
    )[0]
    condition = rng.choice(_CONDITIONS[protocol])
    ward = rng.choice(_WARDS)
    # 0–45 days ago — a backlog a real hospital would have
    days_ago = rng.choices(
        list(range(0, 46)),
        weights=[6, 6, 5, 5, 4, 4, 3, 3, 2, 2, 2, 2, 2, 1, 1] + [1] * 31,
    )[0]
    discharge = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()

    med_name, med_type, doses = rng.choice(_MEDS[protocol])
    if rng.random() < 0.3 and len(_MEDS[protocol]) > 1:
        # 30% chance of a 2nd med
        med_name2, med_type2, doses2 = rng.choice(_MEDS[protocol])
        if med_name2 != med_name:
            med_name = f"{med_name} + {med_name2}"
            med_type = f"{med_type}+{med_type2}"
            doses = max(doses, doses2)

    aware = _AWARE["Watch"] if med_type.startswith("antibiotic") else _AWARE["Access"]
    course_days = rng.choice([3, 5, 7, 10, 14])

    return [name, age, sex, cg_name, phone, condition, protocol,
            discharge, ward, med_name, med_type, aware, course_days, doses]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=500,
                    help="Number of patient rows to generate (default 500)")
    ap.add_argument("--out", type=str, default="data/demo_patients_500.csv",
                    help="Output path (relative to repo root)")
    ap.add_argument("--seed", type=int, default=42,
                    help="RNG seed for reproducibility")
    args = ap.parse_args()

    rng = random.Random(args.seed)

    # Resolve output path (relative to the repo root, not backend/)
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    if not os.path.isabs(args.out):
        out_path = os.path.join(repo_root, args.out)
    else:
        out_path = args.out
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Distribution summary so the user can see what they're getting
    by_proto: dict[str, int] = {}
    by_ward: dict[str, int] = {}
    by_med_type: dict[str, int] = {}
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        for i in range(args.rows):
            row = _gen_patient(rng, i)
            w.writerow(row)
            by_proto[row[6]] = by_proto.get(row[6], 0) + 1
            by_ward[row[8]] = by_ward.get(row[8], 0) + 1
            by_med_type[row[10].split("+")[0]] = by_med_type.get(row[10].split("+")[0], 0) + 1

    print(f"wrote {args.rows} rows to {out_path}")
    print("")
    print("distribution by protocol:")
    for k, v in sorted(by_proto.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<22} {v:>4}  ({100*v/args.rows:.0f}%)")
    print("")
    print("distribution by ward (top 8):")
    for k, v in sorted(by_ward.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {k:<22} {v:>4}")
    print("")
    print("distribution by med type:")
    for k, v in sorted(by_med_type.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<22} {v:>4}  ({100*v/args.rows:.0f}%)")
    print("")
    print("import with:")
    print(f"  POST /api/import/preview  (upload {os.path.basename(out_path)})")
    print("  POST /api/import/confirm  (map columns + select all + import)")


if __name__ == "__main__":
    main()
