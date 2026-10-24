"""Run the weekly pipeline: clean leads.csv, then write the report, email drafts,
a history snapshot and a calendar reminder.

Run:  python run.py                    ->  as of today (or report.REPORT_DATE)
      python run.py --date 2026-10-24  ->  as of a fixed date
"""

import argparse
import csv
from datetime import date

import emails
import followups
import history
import report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", type=date.fromisoformat,
                        help="report date as YYYY-MM-DD (default: REPORT_DATE, else today)")
    args = parser.parse_args()
    report_date = args.date or report.REPORT_DATE or date.today()

    with report.INPUT.open(newline="", encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))
    leads, fixes = report.clean(raw_rows, report_date)
    analysis = report.analyse(leads)

    stale = analysis["stale"]
    reminder_day = followups.next_workday(report_date)
    previous = history.load_previous(report_date)
    changes = history.compare(previous, leads, report_date)
    snapshot = history.snapshot_path(report_date)

    drafts = emails.build(analysis)

    page = report.render(analysis, fixes, report_date, changes,
                         snapshot.relative_to(report.HERE).as_posix(), reminder_day,
                         drafts, emails.samples(drafts))
    report.OUTPUT.write_text(page, encoding="utf-8")
    history.save(leads, report_date)
    followups.write(stale, reminder_day, report_date)
    emails.write(drafts)

    print(f"Talvette weekly growth report, {report_date.isoformat()}")
    print(f"  Leads:      read {len(raw_rows)} rows, kept {len(leads)}, "
          f"{len(fixes)} data-quality notes")
    print(f"  Action:     {len(analysis['hot'])} hot leads today, "
          f"{len(analysis['silent'])} suggested to close or re-engage")
    if stale:
        print(f"  Follow-ups: 1 reminder for {len(stale)} stale leads, "
              f"{report.short_date(reminder_day)} 10:00 Dhaka time")
    else:
        print("  Follow-ups: none needed, no stale leads")
    print(f"  Emails:     {len(drafts)} drafts for Call these 5 first and stale leads")
    if changes is None:
        print("  Changes:    first snapshot, comparison starts next run")
    else:
        print(f"  Changes:    vs {changes['previous_date'].isoformat()}: "
              f"{len(changes['moved'])} moved, {len(changes['won'])} won, "
              f"{len(changes['lost'])} lost, {len(changes['new'])} new, "
              f"{len(changes['stalled'])} stalled")
    print("Files written:")
    for path in (report.OUTPUT, emails.OUTBOX, followups.ICS_OUTPUT, snapshot):
        print(f"  {path.relative_to(report.HERE)}")


if __name__ == "__main__":
    main()
