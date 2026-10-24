"""Generate a reproducible CSV of fake sales leads for Talvette.

Talvette is a Dhaka recruitment agency that helps international tech,
education and health companies hire vetted talent and build teams in Dhaka.
Its best prospects already run offshore teams elsewhere in South/Southeast
Asia but not in Bangladesh.

Run:  python generate_leads.py   ->  writes leads.csv next to this script
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42
# Fixed "today" so dates are reproducible no matter when the script runs.
REFERENCE_DATE = date(2026, 10, 24)
OUTPUT = Path(__file__).with_name("leads.csv")

COLUMNS = [
    "name", "company", "source", "status", "last_contact_date", "deal_value_usd",
    "sector", "company_type", "last_funding_round", "months_since_funding",
    "hq_country", "offshore_hubs", "open_roles",
]

SOURCES = ["Referral", "LinkedIn", "Website", "Cold email", "Event", "Purple Page"]
STATUSES = ["New", "Contacted", "Discovery call", "Shortlist sent", "Negotiation", "Won", "Lost"]
TITLES = ["CTO", "Head of HR", "Founder", "Engineering Manager"]
HUBS = ["India", "Nepal", "Vietnam", "Cambodia", "Indonesia", "Philippines"]
HQ_COUNTRIES = ["USA", "UK", "Singapore", "Germany", "Australia", "Canada",
                "Netherlands", "UAE", "Japan", "Sweden"]

COMPANIES = {
    "Tech": ["Nimbus Stack", "Quantlo", "Bytegrove", "Fluxpoint AI", "Corelane",
             "Vantiq Labs", "Pixelforge", "Datahive", "Stratify Cloud", "Kodexa",
             "Synapse Grid", "Orbitly", "Tensorway"],
    "Education": ["Learnloop", "Brightpath Academy", "EduSpark", "Classwise",
                  "Mindmesh", "Tutorly", "Skillbridge", "Campusly", "Quizora",
                  "Lumen Learning Co", "Scholarly", "Pathfinder Ed", "Notebloom"],
    "Health": ["Medivo", "CareNest", "Pulsewell", "Healthline Bio", "Vitalis",
               "Clinix", "Remedi", "Nurturly", "Wellspring Digital", "Dosewise",
               "Theralink", "Heartbeat Labs", "Curaview"],
}

FIRST_NAMES = ["Sarah", "James", "Priya", "Daniel", "Mei", "Lukas", "Aisha", "Tom",
               "Yuki", "Elena", "Omar", "Hannah", "Raj", "Sofia", "Marcus", "Chloe",
               "Arjun", "Nina", "Ben", "Fatima", "Oliver", "Grace", "Kenji", "Leah",
               "David", "Ingrid", "Samuel", "Zara", "Ethan", "Maya", "Felix", "Ava",
               "Carlos", "Isla", "Noah", "Amira", "Henrik", "Ruby", "Leo"]
LAST_NAMES = ["Chen", "Miller", "Sharma", "Becker", "Tanaka", "Johansson", "Khan",
              "Walker", "Nakamura", "Rossi", "Haddad", "Schmidt", "Patel", "Garcia",
              "Lee", "Dubois", "Mehta", "Novak", "Wright", "Rahman", "Brown", "Kim",
              "Sato", "Cohen", "Evans", "Larsen", "Okafor", "Ali", "Turner", "Singh",
              "Weber", "Murphy", "Lopez", "Fraser", "Martin", "Aziz", "Berg", "Clarke",
              "Hughes"]

# Segment mix for the 39 unique leads (the 40th row is a duplicate).
# ~1/3 of 40 are startups funded in the last 6 months; 4 unicorns.
SEGMENTS = (
    ["recent_startup"] * 13
    + ["older_startup"] * 4
    + ["unicorn"] * 4
    + ["scaleup"] * 10
    + ["enterprise"] * 8
)

# Base win/loss weights per stage; Referral and Website get a boost to "Won".
STATUS_WEIGHTS = {"New": 12, "Contacted": 18, "Discovery call": 16,
                  "Shortlist sent": 12, "Negotiation": 10, "Won": 12, "Lost": 20}
HIGH_INTENT_SOURCES = {"Referral", "Website"}


def funding_profile(segment):
    """Return (company_type, funding_round, months_since_funding, open_roles)."""
    if segment == "recent_startup":
        return ("Startup", random.choice(["Seed", "Series A", "Series A"]),
                random.randint(1, 6), random.randint(6, 15))
    if segment == "older_startup":
        return ("Startup", random.choice(["Seed", "Series A"]),
                random.randint(10, 26), random.randint(2, 5))
    if segment == "unicorn":
        months = random.randint(2, 14)
        roles = random.randint(18, 30) if months <= 6 else random.randint(12, 20)
        return ("Unicorn", "Series C+", months, roles)
    if segment == "scaleup":
        months = random.randint(3, 24)
        roles = random.randint(8, 16) if months <= 6 else random.randint(3, 8)
        return ("Scale-up", random.choice(["Series B", "Series B", "Series C+"]), months, roles)
    # Enterprises are not VC-funded: no round, no months-since-funding.
    return ("Enterprise", "None", "", random.randint(3, 10))


def placement_fee(sector, company_type):
    """Per-hire fee in USD; tech roles and larger clients pay more."""
    base = {"Tech": 3500, "Education": 2500, "Health": 3000}[sector]
    bump = {"Startup": 0, "Scale-up": 300, "Unicorn": 600, "Enterprise": 400}[company_type]
    return base + bump + random.randint(-300, 300)


def pick_status(source):
    weights = dict(STATUS_WEIGHTS)
    if source in HIGH_INTENT_SOURCES:
        weights["Won"] *= 3
        weights["Lost"] //= 2
    return random.choices(list(weights), weights=list(weights.values()))[0]


def build_leads():
    random.seed(SEED)

    sector_companies = [(s, c) for s, names in COMPANIES.items() for c in names]
    random.shuffle(sector_companies)
    segments = SEGMENTS[:]
    random.shuffle(segments)
    first_names = random.sample(FIRST_NAMES, len(segments))
    last_names = random.sample(LAST_NAMES, len(segments))

    # Exactly ~25% of the 40 rows (10) are stale: last contacted 14+ days ago.
    stale = set(random.sample(range(len(segments)), 10))

    leads = []
    for i, segment in enumerate(segments):
        sector, company = sector_companies[i]
        company_type, funding_round, months, roles = funding_profile(segment)
        source = random.choice(SOURCES)
        days_ago = random.randint(14, 60) if i in stale else random.randint(0, 13)

        leads.append({
            "name": f"{first_names[i]} {last_names[i]} ({random.choice(TITLES)})",
            "company": company,
            "source": source,
            "status": pick_status(source),
            "last_contact_date": (REFERENCE_DATE - timedelta(days=days_ago)).isoformat(),
            "deal_value_usd": roles * placement_fee(sector, company_type),
            "sector": sector,
            "company_type": company_type,
            "last_funding_round": funding_round,
            "months_since_funding": months,
            "hq_country": random.choice(HQ_COUNTRIES),
            "offshore_hubs": "; ".join(sorted(random.sample(HUBS, random.randint(1, 3)))),
            "open_roles": roles,
        })

    # --- Messy rows (intentional data-quality issues for cleaning practice) ---
    # Use non-stale rows so exactly 10 of 40 rows stay 14+ days old.
    fresh = [i for i in range(len(segments)) if i not in stale]
    # 1) Exact duplicate of an existing lead, appended at the end.
    leads.append(dict(leads[fresh[3]]))
    # 2) Missing last_contact_date.
    missing_idx = fresh[0]
    leads[missing_idx]["last_contact_date"] = ""
    # 3) Misspelled status.
    leads[21]["status"] = "Negotation"

    return leads


def main():
    leads = build_leads()
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(leads)
    print(f"Wrote {len(leads)} leads to {OUTPUT}")


if __name__ == "__main__":
    main()
