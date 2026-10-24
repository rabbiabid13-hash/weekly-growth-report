"""Draft a personalised email for every stale lead and every "Call these 5 first"
lead, and save them to outbox.csv for a human to review and send.

Each email combines four angles: the lead's stage, their sector, their funding
(if recent or a unicorn) and their current offshore hubs (Dhaka as a second hub).
"""

import csv
from pathlib import Path

from report import is_fresh, is_stale

OUTBOX = Path(__file__).with_name("outbox.csv")
FIELDS = ["to_name", "title", "company", "subject", "body"]
SHORTLIST_DAYS = 7
SIGNATURE = "Best regards,\n[Your name]\nTalvette | Hire vetted talent in Dhaka"

SECTOR_ANGLE = {
    "Tech": "For engineering teams, Dhaka means speed and cost: we place developers who "
            "can ship from week one, at a cost per hire that lets you grow the team "
            "faster on the same budget.",
    "Health": "In health, a wrong hire is expensive, so every candidate we put forward has "
              "passed technical assessments, reference checks and background screening. "
              "You get people you can rely on, not just CVs.",
    "Education": "Dhaka has a deep pool of English-fluent graduates who are a strong fit for "
                 "content, curriculum and learner-support roles, the work that keeps "
                 "your students happy.",
}

# Per stage: (subject, opening line, call to action). {company}/{roles} are filled in.
STAGE_ANGLE = {
    "New": (
        "A second hiring hub for {company}'s {roles} open roles",
        "I'm reaching out from Talvette, a Dhaka recruitment agency that helps "
        "international companies hire vetted talent and build teams in Bangladesh.",
        "Would you be open to a 20-minute call next week to see if it's a fit?",
    ),
    "Contacted": (
        "Following up: Dhaka hiring for {company}",
        "Following up on my earlier note about helping {company} hire in Dhaka.",
        "Could we book 20 minutes this week to scope the roles and budget?",
    ),
    "Discovery call": (
        "Next step: your Dhaka shortlist for {company}",
        "Thanks again for walking me through {company}'s hiring plans.",
        "If you can confirm which roles to prioritise, we'll start the search this week.",
    ),
    "Shortlist sent": (
        "Your shortlist: which candidates should we line up?",
        "I wanted to check in on the shortlist we sent for {company}.",
        "Which candidates would you like to interview? I can set up slots this week.",
    ),
    "Negotiation": (
        "Final terms for {company}'s Dhaka team",
        "Following up on our proposal for {company}'s Dhaka team.",
        "Is anything holding up sign-off? I'm happy to adjust the terms so your first "
        "hires can start sooner.",
    ),
}
STALE_OPENING = "It's been a little while since we last spoke."
# A stale "New" lead has heard from us before, so remind rather than introduce.
STALE_NEW_OPENING = ("As a quick reminder, Talvette is a Dhaka recruitment agency that helps "
                     "international companies hire vetted talent and build teams in Bangladesh.")
COUNTRY_NAMES = {"Philippines": "the Philippines"}


def first_name(lead):
    return lead["name"].split()[0]


def join_names(items):
    items = [COUNTRY_NAMES.get(i, i) for i in items]
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def funding_angle(lead):
    if is_fresh(lead):
        round_name = ("latest funding round" if lead["funding_round"] == "Series C+"
                      else f"{lead['funding_round']} round")
        return (f"Congratulations on the {round_name}. Teams usually need to hire quickly "
                f"after a raise, and that's exactly where we help.")
    if lead["company_type"] == "Unicorn":
        return ("At {company}'s scale, spreading hiring across more than one market keeps "
                "growth from stalling on any single talent pool.").format(**lead)
    return ""


def hub_angle(lead):
    roles = lead["open_roles"]
    hubs = join_names(lead["hubs"]) if lead["hubs"] else "your current offshore hub"
    base = (f"You already run a team in {hubs}. Adding Dhaka as a second hub gives you "
            f"another deep talent pool in a nearby time zone")
    if lead["status"] in ("Shortlist sent", "Negotiation"):
        return (f"{base}, and if you need more options for any of the {roles} roles, we "
                f"can have a fresh vetted shortlist to you within {SHORTLIST_DAYS} days.")
    return (f"{base}, and we can have a vetted shortlist for your {roles} open roles in "
            f"front of you within {SHORTLIST_DAYS} days.")


def draft(lead):
    subject, opening, cta = STAGE_ANGLE.get(lead["status"], STAGE_ANGLE["Contacted"])
    fill = {"company": lead["company"], "roles": lead["open_roles"]}
    opening = opening.format(**fill)
    if is_stale(lead):
        opening = (f"{STALE_OPENING} {STALE_NEW_OPENING}" if lead["status"] == "New"
                   else f"{STALE_OPENING} {opening}")

    sector = SECTOR_ANGLE.get(lead["sector"], "")
    funding = funding_angle(lead)
    paragraphs = [
        f"Hi {first_name(lead)},",
        opening,
        hub_angle(lead),
        f"{funding} {sector}".strip() if funding else sector,
        cta.format(**fill),
        SIGNATURE,
    ]
    return {
        "to_name": lead["name"],
        "title": lead["title"],
        "company": lead["company"],
        "subject": subject.format(**fill),
        "body": "\n\n".join(p for p in paragraphs if p),
    }


def build(analysis):
    """Drafts for "Call these 5 first" leads, then stale leads, one per lead."""
    leads, seen = [], set()
    for lead in analysis["call_first"] + analysis["stale"]:
        if id(lead) not in seen:
            seen.add(id(lead))
            leads.append(lead)
    return [(lead, draft(lead)) for lead in leads]


def write(drafts, path=OUTBOX):
    # utf-8-sig so Excel opens it with the right encoding.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(d for _, d in drafts)
    return path


def samples(drafts, n=3):
    """Pick up to n drafts, preferring a different sector and stage for each."""
    picked = []
    for lead, d in drafts:
        if all(lead["sector"] != l["sector"] and lead["status"] != l["status"]
               for l, _ in picked):
            picked.append((lead, d))
    for item in drafts:
        if len(picked) >= n:
            break
        if item not in picked:
            picked.append(item)
    return picked[:n]
