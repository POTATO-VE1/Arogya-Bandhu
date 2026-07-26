"""Generate a 150-patient CSV for demo import."""
import csv
import random
import sys

random.seed(42)

# Karnataka patient names
MALE_FIRST = [
    "Ramesh", "Venkatesh", "Manjunath", "Suresh", "Ganesh", "Mahesh", "Rajesh",
    "Santosh", "Dinesh", "Umesh", "Naveen", "Kiran", "Prakash", "Ravi", "Girish",
    "Vijay", "Anil", "Sunil", "Vinod", "Rahul", "Arun", "Ashok", "Basavaraj",
    "Chandrashekar", "Darshan", "Gopal", "Harish", "Ishwar", "Jagadish", "Keshav",
    "Lokesh", "Mohan", "Nagaraj", "Paramesh", "Raghavendra", "Shankar", "Tukaram",
    "Uttam", "Vishwanath", "Yogesh", "Shivakumar", "Srinivas", "Thimmappa",
    "Basavaraju", "Devendrappa", "Eshwarappa", "Fakirappa", "Gundappa", "Huligeshi",
]

FEMALE_FIRST = [
    "Lakshmamma", "Gangamma", "Honnamma", "Parvathamma", "Savithramma", "Shivali",
    "Suma", "Vijaya", "Kamala", "Latha", "Mahadevi", "Nagaratna", "Padmavathi",
    "Rathnamma", "Sarojini", "Tulasi", "Vasantha", "Yashoda", "Annapurna",
    "Bhagyashree", "Chandrakala", "Durga", "Eshwari", "Fathima", "Girija",
    "Hema", "Indira", "Jayalakshmi", "Kamalakshi", "Lalitha", "Manjula",
    "Nirmala", "Parvathi", "Rajeshwari", "Saroja", "Shobha", "Usha",
    "Vimala", "Zakirabi", "Aktherabi", "Banu", "Chikmath", "Devi",
    "Gouri", "Hemapala", "Jyothi", "Kaveri", "Lakshmi", "Malathi",
]

LAST_NAMES = [
    "Gowda", "Patil", "Shetty", "Hegde", "Kamath", "Rao", "Reddy", "Nair",
    "Menon", "Iyer", "Naik", "Kulkarni", "Desai", "Joshi", "Bhat", "Pai",
    "Prasad", "Murthy", "Swamy", "Hegde", "Kumar", "Devi", "Amma", "Ayya",
]

CAREGIVERS_M = ["Sita", "Geeta", "Lakshmi", "Kavita", "Asha", "Priya", "Rani"]
CAREGIVERS_F = ["Ramu", "Giri", "Mallik", "Suresh", "Ramesh", "Kumar", "Prakash"]

CONDITIONS = {
    "wound_care": [
        "Post-op appendectomy", "Post-op hernia repair", "Circumcision post-op",
        "C-section wound care", "Post-op knee replacement", "Abscess drainage",
        "Diabetic foot ulcer", "Post-op cholecystectomy", "Laceration repair",
        "Post-op thyroidectomy", "Burn wound management", "Post-op ACL reconstruction",
    ],
    "antibiotic_course": [
        "Lower RTI on azithromycin", "UTI on ciprofloxacin", "Wound infection, ceftriaxone",
        "Cellulitis on amoxicillin", "Pharyngitis on amoxicillin", "Typhoid on cefixime",
        "Pneumonia on levofloxacin", "Skin infection on clindamycin", "Ear infection on cefpodoxime",
        "Dental infection on metronidazole", "Bone infection on linezolid",
    ],
    "fever_viral": [
        "Viral fever", "Dengue fever", "Chikungunya", "Influenza-like illness",
        "Typhoid fever", "Malaria", "Unexplained fever", "Post-viral fatigue",
    ],
}

MEDS = {
    "wound_care": [
        ("Amoxiclav 625mg", "antibiotic", "Watch", 5, 2),
        ("Cefuroxime 500mg", "antibiotic", "Watch", 5, 2),
        ("Metronidazole 400mg", "antibiotic", "Watch", 7, 3),
        ("Doxycycline 100mg", "antibiotic", "Access", 7, 2),
    ],
    "antibiotic_course": [
        ("Azithromycin 500mg", "antibiotic", "Watch", 3, 1),
        ("Ciprofloxacin 500mg", "antibiotic", "Watch", 5, 2),
        ("Amoxicillin 500mg", "antibiotic", "Access", 7, 3),
        ("Cefixime 200mg", "antibiotic", "Watch", 5, 1),
        ("Levofloxacin 500mg", "antibiotic", "Reserve", 7, 1),
    ],
    "fever_viral": [
        ("Paracetamol 500mg", "other", None, 5, 3),
        ("Paracetamol 650mg", "other", None, 5, 3),
        ("Ibuprofen 400mg", "other", None, 3, 2),
    ],
}

WARDS = ["Ward-1", "Ward-2", "Ward-3", "Ward-4", "Ward-5", "OPD", "ICU", "Emergency"]

def gen_phone():
    return f"+91{random.randint(7000000000, 9999999999)}"

def gen_age():
    return random.randint(18, 85)

def gen_sex():
    return random.choice(["M", "F"])

def gen_discharge_date():
    day = random.randint(0, 14)
    from datetime import datetime, timedelta
    d = datetime.now() - timedelta(days=day)
    return d.strftime("%Y-%m-%d")

rows = []
for i in range(150):
    sex = gen_sex()
    first = random.choice(MALE_FIRST if sex == "M" else FEMALE_FIRST)
    last = random.choice(LAST_NAMES)
    name = f"{first} {last}"

    protocol = random.choices(
        ["wound_care", "antibiotic_course", "fever_viral"],
        weights=[40, 35, 25]
    )[0]

    condition = random.choice(CONDITIONS[protocol])
    med = random.choice(MEDS[protocol])
    ward = random.choice(WARDS)

    cg_name = random.choice(CAREGIVERS_F if sex == "M" else CAREGIVERS_M)

    rows.append({
        "Patient Name": name,
        "Age": gen_age(),
        "Sex": sex,
        "Caregiver Name": f"{cg_name} {last}",
        "Caregiver Phone": gen_phone(),
        "Condition": condition,
        "Protocol": protocol,
        "Discharge Date": gen_discharge_date(),
        "Ward": ward,
        "Medication": med[0],
        "Med Type": med[1],
        "AWaRe Category": med[2] or "",
        "Course Days": med[3],
        "Doses/Day": med[4],
    })

outpath = sys.argv[1] if len(sys.argv) > 1 else "/tmp/patients_150.csv"
with open(outpath, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)

print(f"generated {len(rows)} patients → {outpath}")
