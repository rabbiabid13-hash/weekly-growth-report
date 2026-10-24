"""Write followups.ics: one calendar reminder covering every stale lead.

The reminder goes on the next working day after the report date (Monday-Friday)
at 10:00 Dhaka time, and lists each lead with its next best action. Times are
written in UTC so every calendar app (Google Calendar included) imports them
without needing timezone definitions.
"""

from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from report import money

ICS_OUTPUT = Path(__file__).with_name("followups.ics")
DHAKA = timezone(timedelta(hours=6))  # Bangladesh has no daylight saving
WORKDAYS = {0, 1, 2, 3, 4}  # date.weekday(): Mon-Fri
START = time(10, 0)
MINUTES_PER_LEAD = 15
MIN_DURATION = timedelta(minutes=30)


def next_workday(day):
    day += timedelta(days=1)
    while day.weekday() not in WORKDAYS:
        day += timedelta(days=1)
    return day


def _escape(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _fold(line):
    """Fold to 75 octets per line (RFC 5545) without splitting a UTF-8 character."""
    chunks, current, limit = [], b"", 75
    for ch in line:
        encoded = ch.encode("utf-8")
        if len(current) + len(encoded) > limit:
            chunks.append(current)
            current, limit = b"", 74  # continuation lines start with a space
        current += encoded
    chunks.append(current)
    return "\r\n ".join(c.decode("utf-8") for c in chunks)


def _utc(moment):
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _summary(leads):
    names = ", ".join(l["company"] for l in leads[:3])
    more = f" +{len(leads) - 3} more" if len(leads) > 3 else ""
    return f"Follow up: {len(leads)} stale lead{'s' * (len(leads) != 1)} ({names}{more})"


def _description(leads):
    blocks = []
    for i, l in enumerate(leads, start=1):
        contact = ("no contact date on record" if l["date_missing"]
                   else f"last contact {l['days_since']} days ago")
        blocks.append(f"{i}. {l['company']} – {l['name']}, {l['title']}\n"
                      f"{l['status']} | {money(l['deal_value'])} | {l['open_roles']} open roles"
                      f" | {contact}\n"
                      f"Next: {l['next_action']}")
    return "\n\n".join(blocks)


def write(leads, day, report_date, path=ICS_OUTPUT):
    """Write one event on `day` listing `leads`. With no leads the calendar is empty."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Talvette//Weekly Growth Report//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Talvette follow-ups"]
    if leads:
        start = datetime.combine(day, START, DHAKA)
        end = start + max(MIN_DURATION, timedelta(minutes=MINUTES_PER_LEAD * len(leads)))
        lines += [
            "BEGIN:VEVENT",
            # Stable per run, so re-importing the same file doesn't duplicate the event.
            f"UID:followups-{report_date:%Y%m%d}@talvette-followups",
            f"DTSTAMP:{_utc(datetime.now(timezone.utc))}",
            f"DTSTART:{_utc(start)}",
            f"DTEND:{_utc(end)}",
            f"SUMMARY:{_escape(_summary(leads))}",
            f"DESCRIPTION:{_escape(_description(leads))}",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(_fold(l) for l in lines) + "\r\n")
    return path
