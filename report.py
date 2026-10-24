"""Clean leads.csv, analyse it, and render report.html.

The full weekly pipeline (report + history snapshot + calendar reminders) runs
from run.py; `python report.py` is kept as a shortcut to the same thing.

The page is a single self-contained HTML file; only Chart.js loads from a CDN.
Every table is rendered server-side, so the report still reads if the CDN is
unreachable (the charts are simply hidden).
"""

import difflib
import html
import json
import re
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

# Date used for every "days since contact" calculation. None = today.
REPORT_DATE = None

HERE = Path(__file__).parent
INPUT = HERE / "leads.csv"
OUTPUT = HERE / "report.html"
CHART_JS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"

STALE_DAYS = 14
AUTO_CLOSE_DAYS = 30
FRESH_FUNDING_MONTHS = 6
# "Action today": open deals in the top quarter by value, with contact in the
# last HOT_CONTACT_DAYS or funding in the last HOT_FUNDING_MONTHS.
HOT_CONTACT_DAYS = 7
HOT_FUNDING_MONTHS = 3

PIPELINE = ["New", "Contacted", "Discovery call", "Shortlist sent", "Negotiation", "Won"]
STATUSES = PIPELINE + ["Lost"]
CLOSED = {"Won", "Lost"}
OPEN_STAGES = [s for s in PIPELINE if s not in CLOSED]
# Chance an open deal closes, by the stage it has reached.
STAGE_PROBABILITY = {"New": 0.05, "Contacted": 0.10, "Discovery call": 0.25,
                     "Shortlist sent": 0.45, "Negotiation": 0.70}
# Lost leads don't record where they dropped out; assume they got at least this far.
LOST_REACHED = "Contacted"
SECTORS = ["Tech", "Education", "Health"]

HUB_PITCH = {
    "India": "Hedge concentration risk: a Dhaka team adds a second South Asian base "
             "with less competition for the same engineering profiles.",
    "Nepal": "Scale past Kathmandu's smaller talent pool: Dhaka adds volume in the "
             "same region and a near-identical time zone (UTC+6 vs UTC+5:45).",
    "Vietnam": "Keep the Asia overlap you have (Dhaka is one hour behind Hanoi) while "
               "adding a large, English-speaking graduate pipeline.",
    "Cambodia": "Phnom Penh is great for early teams; Dhaka is where you scale "
                "specialist tech, health and education roles.",
    "Indonesia": "Complement Jakarta with a second hub one hour behind, so hiring isn't "
                 "tied to one labour market.",
    "Philippines": "Pair customer-facing teams in Manila with Dhaka engineering, data "
                   "and back-office talent.",
}


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def to_int(value):
    try:
        return int(value)
    except ValueError:
        return None


def match_status(raw):
    """Map a status to its canonical spelling, or None if nothing is close."""
    lookup = {s.lower(): s for s in STATUSES}
    close = difflib.get_close_matches(raw.lower(), lookup, n=1, cutoff=0.75)
    return lookup[close[0]] if close else None


def clean(raw_rows, report_date):
    """Return (leads, fixes). Every change or flag is logged in fixes."""
    fixes = []
    leads = []
    seen = {}
    titles_split = 0

    for line_no, raw in enumerate(raw_rows, start=2):  # line 1 is the header
        row = {k: (v or "").strip() for k, v in raw.items()}
        where = f"Row {line_no} ({row['company']})"

        key = tuple(row.values())
        if key in seen:
            fixes.append(("Removed", f"{where} was an exact duplicate of row {seen[key]}."))
            continue
        seen[key] = line_no

        name, title = row["name"], ""
        m = re.fullmatch(r"(.+?)\s*\((.+)\)", name)
        if m:
            name, title = m.groups()
            titles_split += 1
        else:
            fixes.append(("Flagged", f"{where}: no job title found in name \"{name}\"."))

        status = row["status"]
        if status not in STATUSES:
            fixed = match_status(status)
            if fixed:
                fixes.append(("Fixed", f"{where}: status \"{status}\" corrected to \"{fixed}\"."))
                status = fixed
            else:
                fixes.append(("Flagged", f"{where}: unrecognised status \"{status}\" kept as-is."))

        contact = None
        if row["last_contact_date"]:
            try:
                contact = date.fromisoformat(row["last_contact_date"])
            except ValueError:
                fixes.append(("Flagged", f"{where}: unreadable date "
                                         f"\"{row['last_contact_date']}\" treated as missing."))
        else:
            fixes.append(("Flagged", f"{where}: last_contact_date is missing. Kept the row; "
                                     "it appears as \"no date\" in follow-up lists."))

        deal_value = to_int(row["deal_value_usd"])
        open_roles = to_int(row["open_roles"])
        if deal_value is None or open_roles is None:
            fixes.append(("Flagged", f"{where}: non-numeric deal value or open roles, set to 0."))

        leads.append({
            "name": name,
            "title": title,
            "company": row["company"],
            "source": row["source"],
            "status": status,
            "last_contact_date": contact,
            "date_missing": contact is None,
            "days_since": (report_date - contact).days if contact else None,
            "deal_value": deal_value or 0,
            "sector": row["sector"],
            "company_type": row["company_type"],
            "funding_round": row["last_funding_round"],
            "months_since_funding": to_int(row["months_since_funding"]),
            "hq_country": row["hq_country"],
            "hubs": [h.strip() for h in row["offshore_hubs"].split(";") if h.strip()],
            "open_roles": open_roles or 0,
            # Filled in by analyse() for open leads.
            "score": None,
            "why": "",
            "next_action": "",
        })

    fixes.insert(0, ("Fixed", f"Split the job title out of the name column into a "
                              f"\"title\" column for {titles_split} leads."))
    return leads, fixes


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def is_open(lead):
    return lead["status"] not in CLOSED


def funded_within(lead, months):
    m = lead["months_since_funding"]
    return m is not None and m <= months


def is_fresh(lead):
    return funded_within(lead, FRESH_FUNDING_MONTHS)


def is_stale(lead):
    return lead["days_since"] is None or lead["days_since"] >= STALE_DAYS


def is_silent(lead):
    """30+ days without contact. Leads with no date stay in the stale list instead."""
    return lead["days_since"] is not None and lead["days_since"] >= AUTO_CLOSE_DAYS


def has_recent_activity(lead):
    d = lead["days_since"]
    return (d is not None and d <= HOT_CONTACT_DAYS) or funded_within(lead, HOT_FUNDING_MONTHS)


def activity_signals(lead):
    signals = []
    d = lead["days_since"]
    if d is not None and d <= HOT_CONTACT_DAYS:
        signals.append("contacted today" if d == 0 else
                       f"contacted {d} day{'s' * (d != 1)} ago")
    if funded_within(lead, HOT_FUNDING_MONTHS):
        m = lead["months_since_funding"]
        signals.append(f"raised {lead['funding_round']} {m} month{'s' * (m != 1)} ago")
    return signals


def rate(part, whole):
    return part / whole if whole else 0


def win_rate(group):
    return rate(sum(l["status"] == "Won" for l in group), len(group))


def weighted_value(lead):
    return lead["deal_value"] * STAGE_PROBABILITY.get(lead["status"], 0)


def priority(lead, max_value):
    """Score an open lead for "call these first" (0-100) and explain the score."""
    value_pts = 40 * rate(lead["deal_value"], max_value)
    stage_pts = {"New": 5, "Contacted": 10, "Discovery call": 18,
                 "Shortlist sent": 25, "Negotiation": 30}.get(lead["status"], 0)

    # Most urgent: deals due a follow-up. Just-spoken-to and long-cold score lower.
    d = lead["days_since"]
    if d is None:
        recency_pts, recency = 10, "no contact date on record, so check in"
    elif d < 3:
        recency_pts, recency = 4, f"spoke {d} day{'s' * (d != 1)} ago"
    elif d < STALE_DAYS:
        recency_pts, recency = 15, f"last contact {d} days ago, follow-up due"
    elif d <= 45:
        recency_pts, recency = 12, f"{d} days since contact and going cold"
    else:
        recency_pts, recency = 5, f"{d} days since contact"

    funding_pts, funding = 0, ""
    if is_fresh(lead):
        funding_pts = 15
        m = lead["months_since_funding"]
        funding = f"raised {lead['funding_round']} {m} month{'s' * (m != 1)} ago"
    elif lead["company_type"] == "Unicorn":
        funding_pts, funding = 8, "unicorn"

    reason = (f"{compact(lead['deal_value'])} deal for {lead['open_roles']} roles at "
              f"{lead['status']}; {recency}")
    if funding:
        reason += f"; {funding}"
    return round(value_pts + stage_pts + recency_pts + funding_pts, 1), reason + "."


def next_action(lead):
    """One concrete next step for an open lead, based on stage and context."""
    roles = lead["open_roles"]
    action = {
        "New": f"Send an intro email with 2-3 vetted Dhaka profiles for their "
               f"{roles} open roles",
        "Contacted": f"Book a discovery call to scope the {roles} open roles and budget",
        "Discovery call": "Send a shortlist of vetted candidates within 48 hours",
        "Shortlist sent": "Chase feedback on the shortlist and lock in interview slots",
        "Negotiation": "Send final terms and agree a start date for the first hires",
    }.get(lead["status"], "Review the lead and agree a next step")
    d = lead["days_since"]
    if d is None or d >= STALE_DAYS:
        action = "Re-engage with a short check-in, then " + action[0].lower() + action[1:]
    if funded_within(lead, HOT_FUNDING_MONTHS):
        action += f"; mention their recent {lead['funding_round']} round"
    return action + "."


def reached(lead):
    """Index of the furthest pipeline stage this lead reached."""
    status = LOST_REACHED if lead["status"] == "Lost" else lead["status"]
    return PIPELINE.index(status) if status in PIPELINE else 0


def analyse(leads):
    open_leads = [l for l in leads if is_open(l)]
    won = [l for l in leads if l["status"] == "Won"]
    max_open = max((l["deal_value"] for l in open_leads), default=0)

    for l in open_leads:
        l["score"], l["why"] = priority(l, max_open)
        l["next_action"] = next_action(l)
    ranked = sorted(open_leads, key=lambda l: -l["score"])

    open_values = [l["deal_value"] for l in open_leads]
    hot_floor = (statistics.quantiles(open_values, n=4)[-1] if len(open_values) > 1
                 else sum(open_values))
    hot = [l for l in ranked if l["deal_value"] >= hot_floor and has_recent_activity(l)]

    by_status = []
    for s in STATUSES + sorted({l["status"] for l in leads} - set(STATUSES)):
        group = [l for l in leads if l["status"] == s]
        by_status.append((s, len(group), sum(l["deal_value"] for l in group)))

    sources = defaultdict(list)
    for l in leads:
        sources[l["source"]].append(l)
    by_source = sorted(
        ((src, len(g), sum(x["status"] == "Won" for x in g), win_rate(g),
          rate(sum(x["deal_value"] for x in g), len(g))) for src, g in sources.items()),
        key=lambda r: (-r[3], -r[4]))
    # Best = highest win rate among sources with enough leads to mean something.
    eligible = [r for r in by_source if r[1] >= 3] or by_source
    best_source = eligible[0][0] if eligible else None

    stale = sorted((l for l in open_leads if is_stale(l)), key=lambda l: -l["deal_value"])
    silent = sorted((l for l in open_leads if is_silent(l)), key=lambda l: -l["days_since"])

    weighted_by_stage = [
        (s, STAGE_PROBABILITY[s], [l for l in open_leads if l["status"] == s])
        for s in OPEN_STAGES]

    conversion = []
    for i, s in enumerate(PIPELINE):
        n = sum(reached(l) >= i for l in leads)
        prev = conversion[-1][1] if conversion else len(leads)
        conversion.append((s, n, rate(n, len(leads)), rate(n, prev)))

    fresh = sorted((l for l in leads if is_fresh(l) or l["company_type"] == "Unicorn"),
                   key=lambda l: (l["months_since_funding"] is None, l["months_since_funding"]))

    by_sector = []
    for s in SECTORS:
        g = [l for l in leads if l["sector"] == s]
        by_sector.append((s, len(g), sum(l["deal_value"] for l in g),
                          sum(l["status"] == "Won" for l in g), win_rate(g)))

    hubs = defaultdict(list)
    for l in leads:
        for h in l["hubs"]:
            hubs[h].append(l)
    by_hub = sorted(((h, len(g), sum(l["deal_value"] for l in g if is_open(l)))
                     for h, g in hubs.items()), key=lambda r: (-r[1], r[0]))

    return {
        "leads": leads,
        "open": open_leads,
        "total_value": sum(l["deal_value"] for l in leads),
        "open_value": sum(l["deal_value"] for l in open_leads),
        "won_value": sum(l["deal_value"] for l in won),
        "won_count": len(won),
        "overall_win_rate": win_rate(leads),
        "weighted_total": sum(weighted_value(l) for l in open_leads),
        "hot": hot,
        "hot_floor": hot_floor,
        "by_status": by_status,
        "by_source": by_source,
        "best_source": best_source,
        "call_first": ranked[:5],
        "top_deals": sorted(leads, key=lambda l: -l["deal_value"])[:5],
        "stale": stale,
        "at_risk": sum(l["deal_value"] for l in stale),
        "silent": silent,
        "weighted_by_stage": weighted_by_stage,
        "conversion": conversion,
        "fresh": fresh,
        "by_sector": by_sector,
        "by_hub": by_hub,
    }


def summary_sentences(a):
    best = next((r for r in a["by_source"] if r[0] == a["best_source"]), None)
    s1 = (f"After cleaning, the pipeline holds {len(a['leads'])} leads worth "
          f"{money(a['total_value'])}; {len(a['open'])} are still open "
          f"({money(a['open_value'])}), and weighting them by stage gives a forecast of "
          f"{money(a['weighted_total'])}.")
    s2 = (f"{best[0]} is the strongest source, winning {best[3]:.0%} of its leads against "
          f"{a['overall_win_rate']:.0%} overall, and {a['won_count']} deals worth "
          f"{money(a['won_value'])} have been won so far.") if best else ""

    # The action matches section 2's #1 lead, then the biggest stale lead to rescue.
    hot = a["call_first"][0] if a["call_first"] else None
    rescue = a["stale"][0] if a["stale"] else None
    stale_group = (f"{len(a['stale'])} open leads (worth {money(a['at_risk'])} in total) "
                   f"that have gone {STALE_DAYS}+ days without contact or have no date on record")
    if hot is None:
        s3 = "There are no open leads to chase right now."
    else:
        s3 = (f"Start with {hot['company']} ({money(hot['deal_value'])}, "
              f"{hot['status']}), the top lead in Call these 5 first")
        if rescue is None:
            s3 += f"; every open lead has been contacted in the last {STALE_DAYS} days."
        elif rescue is hot:
            s3 += f", which is also the biggest of the {stale_group}."
        else:
            s3 += (f", then rescue {rescue['company']} ({money(rescue['deal_value'])}), "
                   f"the biggest of the {stale_group}.")
    return [s for s in (s1, s2, s3) if s]


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

class Raw(str):
    """Markup that table() should insert without escaping."""


def esc(value):
    return html.escape(str(value))


def money(v):
    return f"${v:,.0f}"


def signed_money(v):
    return f"{'+' if v >= 0 else '-'}${abs(v):,.0f}"


def compact(v):
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


def pct(v):
    return f"{v:.0%}"


def short_date(d):
    return f"{d:%a} {d.day} {d:%b}"


def table(headers, rows, numeric=(), cls=""):
    head = "".join(f'<th{" class=num" if i in numeric else ""} scope="col">{esc(h)}</th>'
                   for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(
            f'<td{" class=num" if i in numeric else ""}>'
            f'{c if isinstance(c, Raw) else esc(c)}</td>'
            for i, c in enumerate(r)) + "</tr>"
        for r in rows)
    return Raw(f'<div class="table-wrap"><table class="{cls}"><thead><tr>{head}</tr></thead>'
               f'<tbody>{body}</tbody></table></div>')


def badge(text, kind="neutral"):
    return Raw(f'<span class="badge badge-{kind}">{esc(text)}</span>')


def lead_cell(l):
    return Raw(f'<strong>{esc(l["company"])}</strong>'
               f'<span class="sub">{esc(l["name"])}, {esc(l["title"])}</span>')


def contact_cell(l):
    if l["date_missing"]:
        return badge("No date", "warn")
    return f"{l['days_since']}d ago"


def section(num, key, title, intro, body):
    intro_html = f'<p class="intro">{intro}</p>' if intro else ""
    return (f'<section class="card" id="{key}"><h2><span class="num-tag">{num}</span>'
            f'{esc(title)}</h2>{intro_html}{body}</section>')


def tile(label, value, note=""):
    note_html = f'<span class="tile-note">{esc(note)}</span>' if note else ""
    return (f'<div class="tile"><span class="tile-label">{esc(label)}</span>'
            f'<span class="tile-value">{esc(value)}</span>{note_html}</div>')


class Charts:
    """Collects chart specs; the page's script turns them into Chart.js bars."""

    def __init__(self):
        self.specs = []

    def bar(self, labels, values, fmt, label, highlight=None):
        cid = f"chart{len(self.specs)}"
        self.specs.append({"id": cid, "labels": labels, "values": values,
                           "fmt": fmt, "highlight": highlight})
        height = max(150, 34 * len(labels) + 40)
        return Raw(f'<div class="chart" style="height:{height}px">'
                   f'<canvas id="{cid}" role="img" aria-label="{esc(label)}"></canvas></div>')


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def action_box(a):
    """The highlighted "Action today" box at the top of the page."""
    if a["hot"]:
        items = "".join(
            f'<li><div class="hot-head"><span>{lead_cell(l)}</span>'
            f'<span class="hot-value">{money(l["deal_value"])}</span></div>'
            f'<div class="hot-meta">{badge(l["status"])} {esc("; ".join(activity_signals(l)))}</div>'
            f'<div class="hot-action"><span class="hot-label">Next best action</span>'
            f'{esc(l["next_action"])}</div></li>'
            for l in a["hot"])
        body = f'<ol class="hot-list">{items}</ol>'
    else:
        body = "<p>No open lead combines a high deal value with recent activity today.</p>"
    return (f'<section class="action-box" id="action" aria-labelledby="action-title">'
            f'<h2 id="action-title">Action today</h2>'
            f'<p class="intro">Open deals worth {money(a["hot_floor"])}+ (the top quarter by '
            f'value) '
            f'with contact in the last {HOT_CONTACT_DAYS} days or funding in the last '
            f'{HOT_FUNDING_MONTHS} months.</p>{body}</section>')


def changes_body(changes, snapshot_name, a):
    if changes is None:
        return (f'<p class="note">This is the first snapshot, saved as '
                f'<code>{esc(snapshot_name)}</code>. The comparison starts next run.</p>')

    gap = (changes["report_date"] - changes["previous_date"]).days
    open_delta = a["open_value"] - changes["open_value_then"]
    weighted_delta = a["weighted_total"] - changes["weighted_then"]
    tiles = "".join([
        tile("Moved stage", str(len(changes["moved"]))),
        tile("Newly won", str(len(changes["won"]))),
        tile("Newly lost", str(len(changes["lost"]))),
        tile("New leads", str(len(changes["new"]))),
        tile("Stalled", str(len(changes["stalled"]))),
        tile("Open pipeline", signed_money(open_delta), f"now {compact(a['open_value'])}"),
        tile("Weighted forecast", signed_money(weighted_delta),
             f"now {compact(a['weighted_total'])}"),
    ])

    parts = [f'<p class="intro">Compared with the snapshot from '
             f'{esc(changes["previous_date"].isoformat())} ({gap} days earlier).</p>'
             f'<div class="tiles">{tiles}</div>']

    def group(title, headers, rows, numeric):
        if rows:
            parts.append(f"<h3>{esc(title)}</h3>" + table(headers, rows, numeric))

    group("Moved stage", ["Lead", "From", "To", "Value"],
          [(lead_cell(l), old, Raw(f"{badge(l['status'])} "
                                   f"{'forward' if reached(l) > PIPELINE.index(old) else 'back'}"
                                   if old in PIPELINE else badge(l["status"])),
            money(l["deal_value"])) for l, old in changes["moved"]], {3})
    group("Newly won", ["Lead", "Previous stage", "Value"],
          [(lead_cell(l), old, money(l["deal_value"])) for l, old in changes["won"]], {2})
    group("Newly lost", ["Lead", "Previous stage", "Value"],
          [(lead_cell(l), old, money(l["deal_value"])) for l, old in changes["lost"]], {2})
    group("New leads", ["Lead", "Stage", "Source", "Value"],
          [(lead_cell(l), badge(l["status"]), l["source"], money(l["deal_value"]))
           for l in changes["new"]], {3})
    group("Stalled (same stage, no new contact since last run)",
          ["Lead", "Stage", "Last contact", "Value"],
          [(lead_cell(l), badge(l["status"]), contact_cell(l), money(l["deal_value"]))
           for l in sorted(changes["stalled"], key=lambda l: -l["deal_value"])], {3})
    group("No longer in the data", ["Company", "Contact", "Last status"],
          [(r["company"], r["name"], r["status"]) for r in changes["removed"]], ())

    if len(parts) == 1:
        parts.append("<p>No changes since the last run.</p>")
    return "".join(parts)


def email_samples(drafts, samples):
    if not samples:
        return "<p>No leads need an email this week.</p>"
    cards = "".join(
        f'<article class="email"><div class="email-head">'
        f'<span class="email-to">To: {esc(d["to_name"])}, {esc(d["title"])}, '
        f'{esc(d["company"])}</span>'
        f'<span>{badge(lead["status"])} {badge(lead["sector"])}</span></div>'
        f'<p class="email-subject">{esc(d["subject"])}</p>'
        f'<div class="email-body">{esc(d["body"])}</div></article>'
        for lead, d in samples)
    return f'<div class="emails">{cards}</div>'


def build_sections(a, fixes, charts, changes, snapshot_name, reminder_day, drafts, samples):
    """Return [(anchor, nav label, html)] in page order, numbered automatically."""
    out = []

    def add(key, nav, title, intro, body):
        out.append((key, nav, section(len(out) + 1, key, title, intro, body)))

    add("summary", "Summary", "Summary", "",
        "".join(f"<p class='summary'>{esc(s)}</p>" for s in summary_sentences(a)))

    add("changes", "Changes", "Changes since last run", "",
        changes_body(changes, snapshot_name, a))

    rows = [(i, lead_cell(l), badge(l["status"]), money(l["deal_value"]), l["why"])
            for i, l in enumerate(a["call_first"], start=1)]
    add("call", "Call first", "Call these 5 first",
        "Open leads ranked on deal value, stage, time since last contact and recent funding.",
        table(["#", "Lead", "Stage", "Deal value", "Why now"], rows, numeric={3}, cls="stack"))

    labels = [s for s, _, _ in a["by_status"]]
    total = a["total_value"] or 1
    add("status", "Status", "Pipeline by status", "Lead count and deal value at each stage.",
        '<div class="split">'
        + charts.bar(labels, [v for _, _, v in a["by_status"]], "money", "Deal value by status")
        + table(["Status", "Leads", "Value", "Share"],
                [(s, n, money(v), pct(v / total)) for s, n, v in a["by_status"]],
                numeric={1, 2, 3})
        + "</div>")

    best = a["best_source"]
    rows = [(Raw(esc(src) + (" " + badge("Best source", "good") if src == best else "")),
             n, w, pct(r), money(avg)) for src, n, w, r, avg in a["by_source"]]
    labels = [r[0] for r in a["by_source"]]
    add("sources", "Sources", "Leads by source",
        "Win rate is won leads divided by all leads from that source. The best source has "
        "the highest win rate among sources with 3 or more leads.",
        '<div class="split">'
        + charts.bar(labels, [round(r[3] * 100, 1) for r in a["by_source"]], "pct",
                     "Win rate by source", highlight=labels.index(best) if best else None)
        + table(["Source", "Leads", "Won", "Win rate", "Avg deal"], rows, numeric={1, 2, 3, 4})
        + "</div>")

    rows = [(i, lead_cell(l), badge(l["status"]), l["open_roles"], money(l["deal_value"]))
            for i, l in enumerate(a["top_deals"], start=1)]
    add("top", "Top deals", "Top 5 deals by value", "All leads, including won and lost.",
        table(["#", "Lead", "Stage", "Open roles", "Deal value"], rows, numeric={3, 4}))

    rows = [(lead_cell(l), badge(l["status"]), contact_cell(l), money(l["deal_value"]))
            for l in a["stale"]]
    body = (f'<div class="tiles">{tile("Revenue at risk", money(a["at_risk"]))}'
            f'{tile("Leads", str(len(a["stale"])))}</div>'
            + (table(["Lead", "Stage", "Last contact", "Deal value"], rows, numeric={3})
               if rows else "<p>None. Every open lead is current.</p>"))
    reminder_note = (f" All of them are in one follow-up reminder in "
                     f"<code>followups.ics</code>, {esc(short_date(reminder_day))} at 10:00 "
                     f"Dhaka time." if rows and reminder_day else "")
    add("stale", "Stale", f"Not contacted in {STALE_DAYS}+ days",
        "Open leads only (Won and Lost excluded). Leads with no contact date are included "
        "because nobody can say when they were last touched." + reminder_note, body)

    rows = [(lead_cell(l), badge(l["status"]), f"{l['days_since']} days",
             money(l["deal_value"]),
             Raw(f'{badge("Suggest: mark as Lost or re-engage", "warn")}'
                 f'<span class="sub">{esc(l["next_action"])}</span>'))
            for l in a["silent"]]
    add("close", "Suggest closing", "Auto-close suggestions",
        f"Open leads with {AUTO_CLOSE_DAYS}+ days of silence. These are suggestions only: "
        f"nothing in leads.csv is changed. Leads with no contact date are not suggested "
        f"for closing; they stay in the {STALE_DAYS}+ days list until a date is added.",
        table(["Lead", "Stage", "Silent for", "Value", "Suggestion"], rows, numeric={3})
        if rows else f"<p>No open lead has been silent for {AUTO_CLOSE_DAYS}+ days.</p>")

    stage_rows = [(s, pct(p), len(g), money(sum(l["deal_value"] for l in g)),
                   money(sum(weighted_value(l) for l in g)))
                  for s, p, g in a["weighted_by_stage"]]
    deal_rows = [(lead_cell(l), l["status"], money(l["deal_value"]),
                  pct(STAGE_PROBABILITY.get(l["status"], 0)), money(weighted_value(l)))
                 for l in sorted(a["open"], key=lambda l: -weighted_value(l))]
    add("forecast", "Forecast", "Weighted forecast",
        "Each open deal's value multiplied by the chance of closing at its current stage.",
        f'<div class="tiles">{tile("Weighted forecast", money(a["weighted_total"]))}'
        f'{tile("Open pipeline", money(a["open_value"]))}</div>'
        '<div class="split">'
        + charts.bar([r[0] for r in stage_rows],
                     [round(sum(weighted_value(l) for l in g))
                      for _, _, g in a["weighted_by_stage"]],
                     "money", "Weighted value by stage")
        + table(["Stage", "Probability", "Deals", "Value", "Weighted"], stage_rows,
                numeric={1, 2, 3, 4})
        + "</div>"
        + f"<details><summary>All {len(deal_rows)} open deals</summary>"
        + table(["Lead", "Stage", "Value", "Probability", "Weighted"], deal_rows,
                numeric={2, 3, 4})
        + "</details>")

    add("conversion", "Conversion", "Stage conversion",
        f"Share of all leads that reached each stage. Lost leads are counted as reaching "
        f"\"{LOST_REACHED}\" because the data doesn't say where they dropped out.",
        '<div class="split">'
        + charts.bar([s for s, *_ in a["conversion"]],
                     [round(r[2] * 100, 1) for r in a["conversion"]],
                     "pct", "Share of leads reaching each stage")
        + table(["Stage", "Reached", "% of all leads", "% of previous stage"],
                [(s, n, pct(r), pct(p)) for s, n, r, p in a["conversion"]],
                numeric={1, 2, 3})
        + "</div>")

    rows = [(lead_cell(l), l["company_type"], l["funding_round"],
             "" if l["months_since_funding"] is None else f"{l['months_since_funding']} mo",
             l["open_roles"], badge(l["status"]), money(l["deal_value"]))
            for l in a["fresh"]]
    add("funding", "Funding", "Fresh funding",
        f"Leads funded in the last {FRESH_FUNDING_MONTHS} months, plus every unicorn. "
        "New money usually means hiring.",
        table(["Lead", "Type", "Round", "Funded", "Open roles", "Stage", "Value"],
              rows, numeric={3, 4, 6}))

    add("sectors", "Sectors", "Sector split",
        "Win rate is won leads divided by all leads in the sector.",
        '<div class="split">'
        + charts.bar([r[0] for r in a["by_sector"]],
                     [round(r[4] * 100, 1) for r in a["by_sector"]],
                     "pct", "Win rate by sector")
        + table(["Sector", "Leads", "Pipeline value", "Won", "Win rate"],
                [(s, n, money(v), w, pct(r)) for s, n, v, w, r in a["by_sector"]],
                numeric={1, 2, 3, 4})
        + "</div>")

    rows = [(h, n, money(v), HUB_PITCH.get(h, "")) for h, n, v in a["by_hub"]]
    add("hubs", "Hubs", "Current offshore hubs",
        "Where our leads already have offshore teams, and how to pitch adding Dhaka. "
        "Many leads use more than one hub.",
        charts.bar([r[0] for r in a["by_hub"]], [r[1] for r in a["by_hub"]],
                   "count", "Leads by offshore hub")
        + table(["Hub", "Leads", "Open pipeline", "Pitch angle for Dhaka"], rows,
                numeric={1, 2}))

    kinds = {"Fixed": "good", "Removed": "neutral", "Flagged": "warn"}
    items = "".join(f"<li>{badge(k, kinds[k])} {esc(t)}</li>" for k, t in fixes)
    add("quality", "Data quality", "Data quality",
        f"{len(a['leads'])} clean leads kept. Every change made during cleaning is "
        "listed below.",
        f'<ul class="fixes">{items}</ul>')

    add("emails", "Email drafts", "Email drafts",
        f"{len(drafts)} personalised drafts (every Call these 5 first lead and every lead "
        f"not contacted in {STALE_DAYS}+ days) are saved to <code>outbox.csv</code> for "
        f"review. {len(samples)} samples below.",
        email_samples(drafts, samples))
    return out


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

CSS = """
:root{color-scheme:light;
  --page:#f9f9f7;--surface:#fcfcfb;--text:#0b0b0b;--text-2:#52514e;--muted:#898781;
  --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--wash:#f0efec;
  --series:#2a78d6;--series-dim:#b7d3f6;--good:#006300;--good-bg:#e3f3e3;
  --warn:#7a5200;--warn-bg:#fdf0d2;--hot-bg:#edf4fd}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;
  --page:#0d0d0d;--surface:#1a1a19;--text:#f0efec;--text-2:#c3c2b7;--muted:#898781;
  --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--wash:#262624;
  --series:#3987e5;--series-dim:#184f95;--good:#0ca30c;--good-bg:#132a13;
  --warn:#fab219;--warn-bg:#33270c;--hot-bg:#132033}}
:root[data-theme="dark"]{color-scheme:dark;
  --page:#0d0d0d;--surface:#1a1a19;--text:#f0efec;--text-2:#c3c2b7;--muted:#898781;
  --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--wash:#262624;
  --series:#3987e5;--series-dim:#184f95;--good:#0ca30c;--good-bg:#132a13;
  --warn:#fab219;--warn-bg:#33270c;--hot-bg:#132033}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:32px 16px 64px}
header{margin-bottom:24px}
.eyebrow{color:var(--text-2);font-size:13px;letter-spacing:.04em;text-transform:uppercase;margin:0}
h1{font-size:28px;line-height:1.2;margin:4px 0 6px}
.meta{color:var(--text-2);margin:0}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 0}
nav a{font-size:13px;color:var(--text-2);text-decoration:none;padding:3px 10px;
  border:1px solid var(--border);border-radius:999px;background:var(--surface)}
nav a:hover{color:var(--text);background:var(--wash)}
code{font:13px ui-monospace,Consolas,monospace;background:var(--wash);padding:1px 5px;border-radius:4px}
.action-box{background:var(--hot-bg);border:1px solid var(--border);border-left:4px solid var(--series);
  border-radius:14px;padding:20px;margin:0 0 16px}
.action-box h2{font-size:20px}
.hot-list{list-style:none;margin:0;padding:0;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.hot-list li{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.hot-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.hot-head .sub{display:block;color:var(--text-2);font-size:12px}
.hot-value{font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.hot-meta{color:var(--text-2);font-size:13px}
.hot-action{font-size:14px}
.hot-label{display:block;color:var(--text-2);font-size:11px;font-weight:600;
  text-transform:uppercase;letter-spacing:.04em}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:0 0 16px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;
  display:flex;flex-direction:column;gap:2px}
.card .tile{background:var(--page)}
.tile-label{color:var(--text-2);font-size:13px}
.tile-value{font-size:24px;font-weight:600}
.tile-note{color:var(--muted);font-size:12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;
  padding:20px;margin:0 0 16px}
h2{font-size:18px;margin:0 0 4px;display:flex;align-items:center;gap:10px}
h3{font-size:15px;margin:20px 0 6px}
.num-tag{display:inline-grid;place-items:center;min-width:24px;height:24px;border-radius:6px;
  background:var(--wash);color:var(--text-2);font-size:12px;font-weight:600}
.intro{color:var(--text-2);margin:0 0 16px;font-size:14px}
.note{color:var(--text-2);margin:8px 0 0}
.summary{font-size:16px;margin:12px 0 0;max-width:72ch}
.split{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px;align-items:start}
.chart{position:relative;width:100%;min-width:0}
.no-charts .chart{display:none}
.no-charts .split{grid-template-columns:1fr}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--text-2);font-weight:600;font-size:12px;
  border-bottom:1px solid var(--axis);padding:8px 10px;white-space:nowrap}
td{border-bottom:1px solid var(--grid);padding:9px 10px;vertical-align:top}
tbody tr:hover{background:var(--wash)}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td .sub{display:block;color:var(--text-2);font-size:12px;margin-top:2px}
.badge{display:inline-block;font-size:12px;line-height:18px;padding:0 8px;border-radius:999px;
  white-space:nowrap;background:var(--wash);color:var(--text-2)}
.badge-good{background:var(--good-bg);color:var(--good)}
.badge-warn{background:var(--warn-bg);color:var(--warn)}
details{margin-top:16px}
summary{cursor:pointer;color:var(--text-2);font-size:14px;padding:6px 0}
.fixes{list-style:none;padding:0;margin:0}
.fixes li{padding:8px 0;border-bottom:1px solid var(--grid)}
.fixes li:last-child{border-bottom:0}
.emails{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.email{background:var(--page);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.email-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px;
  color:var(--text-2);font-size:12px}
.email-subject{font-weight:600;margin:8px 0}
.email-body{white-space:pre-line;font-size:14px;color:var(--text)}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:24px}
@media (max-width:760px){
  .wrap{padding-top:20px}
  h1{font-size:23px}
  .card,.action-box{padding:16px}
  .hot-list{grid-template-columns:1fr}
  .split{grid-template-columns:1fr}
  td,th{padding:8px 6px}
  /* "Call first" rows become cards: # | lead | value, then stage, then reason */
  table.stack thead{display:none}
  table.stack tr{display:grid;grid-template-columns:22px 1fr auto;column-gap:8px;
    padding:10px 0;border-bottom:1px solid var(--grid)}
  table.stack td{border:0;padding:2px 0}
  table.stack td:nth-child(1){grid-area:1/1}
  table.stack td:nth-child(2){grid-area:1/2}
  table.stack td:nth-child(4){grid-area:1/3}
  table.stack td:nth-child(3){grid-area:2/2/3/4}
  table.stack td:nth-child(5){grid-area:3/2/4/4;color:var(--text-2)}
}
"""

SCRIPT = """
(function(){
  if(!window.Chart){document.body.classList.add('no-charts');return;}
  var specs = __SPECS__;
  var built = [];
  function v(name){return getComputedStyle(document.documentElement).getPropertyValue(name).trim();}
  function fmt(kind, x, axis){
    if(kind==='pct') return Math.round(x)+'%';
    if(kind==='money'){
      if(axis && x>=1000) return '$'+(x>=1e6?(x/1e6).toFixed(1)+'M':Math.round(x/1000)+'K');
      return '$'+Math.round(x).toLocaleString('en-US');
    }
    return Math.round(x)===x ? String(x) : '';
  }
  function build(){
    built.forEach(function(c){c.destroy();}); built=[];
    Chart.defaults.font.family='system-ui,-apple-system,"Segoe UI",sans-serif';
    Chart.defaults.color=v('--muted');
    specs.forEach(function(s){
      var colors=s.values.map(function(_,i){
        return s.highlight===null||s.highlight===i ? v('--series') : v('--series-dim');});
      built.push(new Chart(document.getElementById(s.id),{
        type:'bar',
        data:{labels:s.labels,datasets:[{data:s.values,backgroundColor:colors,
          hoverBackgroundColor:colors,borderRadius:4,borderSkipped:'start',
          maxBarThickness:24,categoryPercentage:.8,barPercentage:.9}]},
        options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,animation:false,
          layout:{padding:{right:8}},
          plugins:{legend:{display:false},
            tooltip:{displayColors:false,callbacks:{label:function(c){return fmt(s.fmt,c.parsed.x);}}}},
          scales:{
            x:{beginAtZero:true,max:s.fmt==='pct'?100:undefined,
               grid:{color:v('--grid')},border:{display:false},
               ticks:{precision:0,maxTicksLimit:6,callback:function(x){return fmt(s.fmt,x,true);}}},
            y:{grid:{display:false},border:{color:v('--axis')},ticks:{color:v('--text-2')}}}}
      }));
    });
  }
  build();
  var mq=window.matchMedia('(prefers-color-scheme: dark)');
  if(mq.addEventListener) mq.addEventListener('change',build);
})();
"""


def render(a, fixes, report_date, changes, snapshot_name, reminder_day, drafts, samples):
    """reminder_day is the date of the follow-up event in followups.ics; drafts and
    samples are (lead, email) pairs from emails.py."""
    charts = Charts()
    sections = build_sections(a, fixes, charts, changes, snapshot_name, reminder_day,
                              drafts, samples)
    nav = '<a href="#action">Action today</a>' + "".join(
        f'<a href="#{key}">{esc(label)}</a>' for key, label, _ in sections)
    tiles = "".join([
        tile("Total pipeline", compact(a["total_value"]), f"{len(a['leads'])} leads"),
        tile("Open pipeline", compact(a["open_value"]), f"{len(a['open'])} open deals"),
        tile("Weighted forecast", compact(a["weighted_total"]), "value x stage probability"),
        tile("Won", compact(a["won_value"]), f"{a['won_count']} deals"),
        tile("At risk", compact(a["at_risk"]), f"{len(a['stale'])} leads, {STALE_DAYS}+ days"),
    ])
    specs = json.dumps(charts.specs).replace("</", "<\\/")
    stamp = f"{report_date.day} {report_date:%B %Y}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Talvette Growth Report</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
<p class="eyebrow">Talvette · Weekly growth report</p>
<h1>Sales pipeline, {esc(stamp)}</h1>
<p class="meta">Built from {esc(INPUT.name)}. "Days since contact" counts back from {esc(report_date.isoformat())}.</p>
<nav aria-label="Sections">{nav}</nav>
</header>
{action_box(a)}
<div class="tiles">{tiles}</div>
{"".join(html_ for _, _, html_ in sections)}
<footer>Generated by run.py on {esc(report_date.isoformat())}</footer>
</div>
<script src="{CHART_JS}"></script>
<script>{SCRIPT.replace("__SPECS__", specs)}</script>
</body>
</html>
"""


if __name__ == "__main__":
    import run
    run.main()
