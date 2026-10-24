"""Dated snapshots of the cleaned, scored leads, and the comparison between runs.

Each run writes history/leads_YYYY-MM-DD.csv (re-running on the same date
overwrites that day's file). The comparison uses the latest snapshot dated
before the current report date.
"""

import csv
import re
from datetime import date
from pathlib import Path

from report import CLOSED, weighted_value

HISTORY_DIR = Path(__file__).with_name("history")
SNAPSHOT_NAME = re.compile(r"leads_(\d{4}-\d{2}-\d{2})\.csv")
FIELDS = [
    "snapshot_date", "name", "title", "company", "source", "status",
    "last_contact_date", "date_missing", "days_since", "deal_value_usd",
    "weighted_value_usd", "priority_score", "next_action", "sector", "company_type",
    "last_funding_round", "months_since_funding", "hq_country", "offshore_hubs",
    "open_roles",
]


def lead_key(company, name):
    return company.strip().lower(), name.strip().lower()


def snapshot_path(report_date):
    return HISTORY_DIR / f"leads_{report_date.isoformat()}.csv"


def _blank_if_none(value):
    return "" if value is None else value


def save(leads, report_date):
    HISTORY_DIR.mkdir(exist_ok=True)
    path = snapshot_path(report_date)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for l in leads:
            writer.writerow({
                "snapshot_date": report_date.isoformat(),
                "name": l["name"],
                "title": l["title"],
                "company": l["company"],
                "source": l["source"],
                "status": l["status"],
                "last_contact_date": l["last_contact_date"].isoformat()
                if l["last_contact_date"] else "",
                "date_missing": l["date_missing"],
                "days_since": _blank_if_none(l["days_since"]),
                "deal_value_usd": l["deal_value"],
                "weighted_value_usd": round(weighted_value(l), 2),
                "priority_score": _blank_if_none(l["score"]),
                "next_action": l["next_action"],
                "sector": l["sector"],
                "company_type": l["company_type"],
                "last_funding_round": l["funding_round"],
                "months_since_funding": _blank_if_none(l["months_since_funding"]),
                "hq_country": l["hq_country"],
                "offshore_hubs": "; ".join(l["hubs"]),
                "open_roles": l["open_roles"],
            })
    return path


def load_previous(report_date):
    """Return (date, rows) for the latest snapshot before report_date, or None."""
    if not HISTORY_DIR.is_dir():
        return None
    dated = []
    for path in HISTORY_DIR.glob("leads_*.csv"):
        m = SNAPSHOT_NAME.fullmatch(path.name)
        if m and date.fromisoformat(m[1]) < report_date:
            dated.append((date.fromisoformat(m[1]), path))
    if not dated:
        return None
    snap_date, path = max(dated)
    with path.open(newline="", encoding="utf-8") as f:
        return snap_date, list(csv.DictReader(f))


def compare(previous, leads, report_date):
    """Diff the current leads against a previous snapshot. None if there isn't one."""
    if previous is None:
        return None
    prev_date, rows = previous
    prev = {lead_key(r["company"], r["name"]): r for r in rows}

    changes = {"report_date": report_date, "previous_date": prev_date,
               "moved": [], "won": [], "lost": [], "new": [], "stalled": []}
    current_keys = set()
    for l in leads:
        key = lead_key(l["company"], l["name"])
        current_keys.add(key)
        old = prev.get(key)
        if old is None:
            changes["new"].append(l)
        elif l["status"] != old["status"]:
            bucket = {"Won": "won", "Lost": "lost"}.get(l["status"], "moved")
            changes[bucket].append((l, old["status"]))
        elif l["status"] not in CLOSED:
            contact = l["last_contact_date"].isoformat() if l["last_contact_date"] else ""
            if contact == old["last_contact_date"]:
                changes["stalled"].append(l)

    changes["removed"] = [r for key, r in prev.items() if key not in current_keys]
    changes["open_value_then"] = sum(int(r["deal_value_usd"] or 0) for r in rows
                                     if r["status"] not in CLOSED)
    changes["weighted_then"] = sum(float(r["weighted_value_usd"] or 0) for r in rows)
    return changes
