# Weekly Growth Report

A weekly sales-pipeline report for **Talvette**, a Dhaka recruitment agency that helps
international tech, education and health companies hire vetted talent and build teams in
Dhaka. One command takes a CSV of leads, cleans it, scores every open deal, and produces a
single-page HTML report: who to call today, where the pipeline stands, which leads are going
cold, how this week compares with last week, plus a calendar reminder for overdue follow-ups.
The lead data is fake but built to behave like a real pipeline, so the analysis has real
patterns to find.

## Run it

Requires Python 3.10 or newer. No packages to install: it uses only the standard library,
and the report loads Chart.js from a CDN.

```bash
python generate_leads.py   # optional: rebuild the fake leads.csv (same output every time)
python run.py              # build everything as of today
python run.py --date 2026-10-24   # build as of a fixed date
```

`run.py` prints a short summary and writes:

| File | What it is |
|---|---|
| `report.html` | The report. Open it in any browser; it works on desktop and mobile, in light and dark mode. |
| `outbox.csv` | A personalised email draft (`to_name, title, company, subject, body`) for every "Call these 5 first" lead and every lead not contacted in 14+ days, ready to review and send. Three samples appear at the bottom of the report. |
| `followups.ics` | One calendar event on the next working day (Mon-Fri) at 10:00 Dhaka time, listing every lead not contacted in 14+ days with its next best action. Import it into Google Calendar via *Settings > Import & export*. |
| `history/leads_YYYY-MM-DD.csv` | A snapshot of the cleaned, scored leads. The next run compares against the latest earlier snapshot to show what changed. |

### What's in the report

An **Action today** box at the top, then: summary, changes since last run, call these 5
first, pipeline by status, leads by source (with the best source flagged), top 5 deals, leads
not contacted in 14+ days, auto-close suggestions, weighted forecast, stage conversion,
fresh funding, sector split, offshore hubs with a Dhaka pitch angle, data quality, and
sample email drafts.

### Cleaning

Before any numbers are calculated, `report.py`:

- splits the job title out of the name column (`Sarah Chen (CTO)` becomes name + title)
- removes exact duplicate rows
- fixes status typos by fuzzy-matching against the valid statuses
- flags rows with missing or unreadable dates instead of dropping them

Every fix is listed in the report's Data quality section.

## The fake data

`generate_leads.py` writes 40 leads with a fixed random seed (`42`) and a fixed reference
date, so the file is identical on every run. The leads are hiring decision-makers (CTO, Head
of HR, Founder, Engineering Manager) at companies with offshore teams in India, Nepal,
Vietnam, Cambodia, Indonesia or the Philippines, but not yet in Bangladesh.

Patterns built in:

- **Funding mix:** 13 startups funded in the last 6 months (about a third), 4 unicorns, plus
  older startups, scale-ups and enterprises.
- **Money means hiring:** recently funded companies get more open roles.
- **Deal value = open roles x placement fee.** The fee depends on sector (Tech pays most) and
  company size, so more roles always means a bigger deal.
- **Source quality:** Referral and Website leads are three times as likely to be Won.
- **Neglect:** exactly 10 of 40 leads (25%) were last contacted 14+ days ago.

Three rows are deliberately messy, to give the cleaning step something to do:

1. an exact duplicate (the last row copies Dosewise)
2. a missing `last_contact_date` (Fluxpoint AI)
3. a misspelled status, `Negotation` (CareNest)

## How leads are prioritised

### Call these 5 first

Every open lead (not Won or Lost) gets a score out of 100. The five highest are listed, each
with a one-line reason built from the parts that scored.

| Factor | Points | Rule |
|---|---|---|
| Deal value | up to 40 | proportional to the largest open deal |
| Stage | up to 30 | New 5, Contacted 10, Discovery call 18, Shortlist sent 25, Negotiation 30 |
| Recency | up to 15 | 3-13 days since contact scores highest (follow-up due); 14-45 days 12 (going cold); no date 10; under 3 days 4 (just spoke); 45+ days 5 |
| Funding | up to 15 | funded in the last 6 months 15; older unicorn 8 |

The **Action today** box is stricter: open deals in the top quarter by value that were also
contacted in the last 7 days or funded in the last 3 months.

### Next best action

One concrete step per open lead, chosen by stage:

| Stage | Next best action |
|---|---|
| New | Send an intro email with 2-3 vetted Dhaka profiles for their open roles |
| Contacted | Book a discovery call to scope the open roles and budget |
| Discovery call | Send a shortlist of vetted candidates within 48 hours |
| Shortlist sent | Chase feedback on the shortlist and lock in interview slots |
| Negotiation | Send final terms and agree a start date for the first hires |

If the lead hasn't been contacted in 14+ days (or has no date), the action starts with
"Re-engage with a short check-in". If the company raised money in the last 3 months, it adds
a prompt to mention the new round. Leads silent for 30+ days are also suggested for closing
or re-engagement; the data itself is never changed.

### Email drafts

Each draft in `outbox.csv` greets the lead by first name and combines four angles:

- **Stage:** the subject, opening and ask match their status (an intro for New, a shortlist
  check-in for Shortlist sent, a nudge to sign for Negotiation). Stale leads get a
  "since we last spoke" opening.
- **Sector:** Tech hears about engineering speed and cost, Health about vetting and
  reliability, Education about English-fluent talent for content and support roles.
- **Funding:** congratulations on a round raised in the last 6 months, or a scale angle for
  unicorns.
- **Hub:** Dhaka as a second hub alongside the offshore teams they already run, with a
  vetted shortlist in 7 days.

### Settings

The thresholds live as constants at the top of `report.py` (`REPORT_DATE`, `STALE_DAYS`,
`AUTO_CLOSE_DAYS`, `STAGE_PROBABILITY`, `HUB_PITCH`, ...) and `followups.py` (`WORKDAYS`,
`START`).

## Project layout

```
generate_leads.py   fake data generator -> leads.csv
run.py              runs the whole pipeline
report.py           cleaning, scoring, analysis, HTML
history.py          snapshots and week-over-week comparison
emails.py           personalised email drafts -> outbox.csv
followups.py        calendar reminder (.ics)
```

## What I'd build next

- **Real lead data.** Replace the fake CSV with leads found automatically from public
  signals: new funding rounds (Crunchbase, press releases) and hiring activity (job posts for
  roles in India, Vietnam, the Philippines and other hubs Talvette competes with).
- **A CRM connection.** Read leads from, and write stage changes back to, HubSpot or Pipedrive
  instead of a CSV, so the report always reflects the live pipeline.
- **AI-written emails.** Replace the template drafts in `outbox.csv` with emails written by an
  AI model from the lead's real funding news, job posts and past conversations, still
  reviewed by a human before sending.
- **A scheduled Monday run.** Run the pipeline automatically every Monday morning and send the
  report and calendar reminder to the team, so nobody has to remember to run it.
