# Product Requirements Document — Garmin Coach Dashboard

## Overview

A local web dashboard (`python -m coach dashboard`, port 8765) that gives Mario a
complete picture of his training. Nothing is uploaded; the page reads live from
Garmin Connect, Todoist and Strava every time it loads.

The coach already decides and acts (Garmin calendar, Todoist, ntfy). The dashboard
makes the reasoning visible so Mario can understand and trust the decisions without
having to read raw workout descriptions on the watch.

## User

Mario Arranz — endurance athlete training for:
- Zürcher Silvesterlauf 8.5 km (2026-12-13)
- Zürich Half Marathon (2027-04-11)
- Zurich City Triathlon sprint (2027-06-27)

He checks the dashboard on a MacBook, not a phone, but the page must be responsive.

---

## Five sections (tabs)

### 1 — Status
One-glance overview of where Mario stands vs his goal.

- Next race: name, date, days and weeks away.
- Current training phase: name and what it's building towards.
- Weekly running target: km this week vs 4-week average and last week.
- Week progress: run km done vs target (progress bar), bike time, swim distance.
- 12-week volume chart: running km per week as a bar chart, current week highlighted.
- Current training paces (from Garmin race predictor).

### 2 — Why
The logic behind the plan, in plain words.

- Phase card: what the current phase is and why (from `phase.describe()`).
- Race goal card: name, date, weeks to go.
- Volume logic: target km, how it relates to the 4-week average, ramp rate.
- Training paces: easy / threshold / VO2 / race pace.

### 3 — Decisions
The coach's day-by-day decisions for the last 14 days, read back from Garmin and
Todoist (no new storage — stateless).

- Each day: date, planned session(s), what was done, coach reasoning.
- Reasoning from the Garmin workout description (first workout of the day).
- Expandable "Coach reasoning" detail showing the full description.

### 4 — Training
All recent activities, filterable by sport.

- Filter pills: All / Running / Cycling / Swimming / Strength.
- Table: date, activity name (with sport colour dot), distance, duration, Training
  Effect (TE) score.
- Activities cover the same lookback window as the planner (≈ 8–12 weeks).

### 5 — Upcoming
This week and next week, day by day.

- This week: each day card shows what's planned (or "Rest"), ✓ if done, and an
  expandable "Why / details" section from the workout description.
- Next week: draft plan, same card layout.

---

## Non-functional requirements

- Loads entirely on the user's machine; no external requests except Garmin/Strava/Todoist.
- No JavaScript framework — vanilla JS only.
- No external CSS or JS CDN — everything inline.
- `/api/data` returns the raw JSON (for debugging).
- Works in Safari and Chrome on macOS.
