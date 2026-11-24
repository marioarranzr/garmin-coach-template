# Garmin Coach — notes for Claude

Mario's daily training coach: plans towards his races from Garmin data and puts the
plan in his Garmin calendar (workouts on the watch), Todoist (the week at a glance)
and ntfy (notifications). Runs on GitHub Actions (`.github/workflows/coach.yml`).

## Repositories
- **This repo** (`marioarranzr/garmin-coach`, private): Mario's copy, with his
  `config.yaml` and `data/*.enc` (encrypted Garmin session and Strava token).
- **Template** (`marioarranzr/garmin-coach-template`, public): the same code with a
  generic `config.yaml`. Code changes go there first, then into this repo:
  `git fetch template && git merge template/main` (remote `template`).
  Before committing a merge, check `git diff --name-only --diff-filter=U` and grep for
  conflict markers (a marker in `config.yaml` once broke CI). Keep `config.yaml` here
  as Mario's.
- Tests: `python tests/test_scenarios.py` (no pytest). CI runs them on every push.
- Never commit secrets. Only `data/garmin_tokens.enc` and `data/strava_token.enc`
  (encrypted) are committed. Secrets: GARMIN_TOKENS_KEY, OPENAI_API_KEY,
  TODOIST_API_TOKEN, NTFY_TOPIC, STRAVA_CLIENT_ID/SECRET/REFRESH_TOKEN.

## Working with Mario
- Everything in **English** (code, docs, replies), even though he writes in Spanish.
- He reads results on his watch, Todoist and the run summary; keep texts short and readable.
- Commit messages end with the Co-Authored-By line for Claude.

## How it works (see README for the full picture)
- `coach/engine.py` `scheduled()`: before `morning_check` nothing; morning check with
  sleep (waits for sleep until `wait_for_sleep_until`); during the day re-checks only
  if there's new training (counted in the footer "Activities seen today: N") or a new
  note; from `evening_plan` plans tomorrow. GitHub drops many scheduled runs; an
  outside scheduler (cron-job.org) can dispatch the workflow with input `scheduled=true`.
- Stateless memory: the plan is read back from the Garmin calendar (workout
  titles/descriptions, footers) and from Todoist (plan tasks with "(Garmin Coach plan)",
  rest days' footer, "📌 Coach preferences" task, diary notes labelled `coach`).
- Strava (`coach/strava.py`): activities only in Strava (MyWhoosh rides) count.
  A ski day counts only the runs down (Garmin activity summary, or Strava's figures), not the lifts.
- AI coach (OpenAI) decides the day within limits (`ai.apply_limits`); daily check on `ai.openai_day_model` (cheap), notes/week design/reviews on `ai.openai_model`; costs are logged per call. `coach/load.py` gives it CTL/ATL/TSB per sport and a leg-fatigue index (ski counts as leg work). Design and plan: `docs/COACH_DESIGN.md`; a second call
  reads schedule notes (`ai.schedule_changes`): relative days are resolved from the
  date each note was written; one-offs are never preferences. "I won't run until Friday" → `no_run_until`
  (the day before): runs are dropped from the template until then, other training stays.

## Mario's rules and preferences (already in code/config)
- Races: Zürcher Silvesterlauf 8.5 km (2026-12-13), Zürich half (2027-04-11),
  Zurich City Triathlon sprint (2027-06-27). Run focus now; swims optional.
- 3 runs a week; rest Thursday; long run Sunday; **no runs on consecutive days**
  (`athlete.no_runs_on_consecutive_days`), unless a note asks.
- Rides at least 30'; a ride to the pool (<25') isn't training.
- Track 2.5 km away, flat path 3 km away (warm-up run there).
- Strides count in the run's distance. A run done earlier in the week isn't repeated.
- Plan shows only what's planned (no done marks); sessions already done keep their
  workout; past days are never touched.
- Strength: 3 sessions a week (Mon core, Wed lower body, Fri core; Thursday stays rest);
  home gym (50 cm box, bar up to 48 kg, 10 kg dumbbell); Garmin exercise
  names on the watch, one-line how-to per exercise, warm-up 30" per exercise, sides
  as left/right steps or alternating sets, Y-T-W as three exercises. Everything written is English (web,
  Todoist, Garmin description: compact `Session.brief`, so it isn't cut at Garmin's 1000 characters); only
  the per-exercise cues on the watch follow `strength.language`.
- Spare strength workouts: two (core + the phase's heavy one) named "Coach spare · …" sit unscheduled in the watch's
  workout list, refreshed on every plan, so there's always one more than planned.
- Bike sessions give watts per zone from `athlete.ftp_w` (Strava's Coggan zones): power is the
  step target, the heart-rate zone the second target (no FTP: heart rate only).
- Followed without "Follow workout" (Strava, or Garmin without the workout): `coach/match.py` counts an
  activity as the day's planned session when sport and duration/distance match ≥85 % (hard session → counts as
  the week's hard one); the Week tab colours it green.
- Week design (`coach/design.py`, config `ai.week_design`, off until Mario turns it on): once per week the AI picks which session kinds go on which day (same kinds as the template); `validate` enforces the hard limits, one repair try, else the template. Saved in `data/coach_log/` (committed by the workflow). Preview without saving: workflow input `design_preview` / `python -m coach design`.
- Weekly review (`coach/review.py`, config `ai.weekly_review`): the first run of a new week reviews the week before (planned vs done from the Garmin calendar, load change, notes); saved in `data/coach_log/review-<monday>.json`, shown in the run report and read by the next week's design and the daily check.
- `run_override: {YYYY-MM-DD: km}` (config): that day's run is exactly an easy run of that km, added if none (careful return from injury; remove when done).
- Rest days: no Garmin workout (marked in Todoist).

## Dashboard
- `coach/dashboard.py` — `python -m coach dashboard` serves http://localhost:8765.
- Tabs: Week, Log, Strength (only coach-made strength workouts: calendar + draft/spare ones; other apps' workouts are hidden, with
  exercises, sets, weights, short cue and a picture from free-exercise-db). Older notes: Status (race countdown, phase, week progress, 12-week chart), Why
  (training logic), Decisions (14-day coach reasoning log), Training (filterable
  activity list), Upcoming (this week + next week with full descriptions).
- Week tab: each done activity is coloured: green followed the plan (≥85 %), yellow partly (≥35 %), plain when
  it wasn't planned that day or not what the day asked. No percentages shown.
- The page is a snapshot from the last run. "Update data" starts the workflow with input `dashboard_only`
  (dashboard only, ~30 s, no planning) straight from the page, waits and reloads; at most one update per 30 min
  (a second click joins the run in progress). One-time setup: the page asks
  for a GitHub fine-grained token (Actions: read and write on this repo), kept in the browser's localStorage only.
- `/api/data` returns the raw JSON for debugging.
- Design tokens and schema: `docs/DESIGN_BRIEF.md`, `docs/BACKEND_SCHEMA.md`.

## Working style
Every change follows these steps:
1. Implement.
2. Run `python tests/test_scenarios.py` (no pytest).
3. Push to the template **from its own clone** (`/home/claude/garmin-coach-template`): copy only the changed code, tests and docs there, never `config.yaml`, `data/` or `reports/`, commit and `git push origin main`. Never push this repo's `main` to the template (it carries Mario's config and bot commits; the `template` remote's push URL is disabled for that reason).
4. Merge into the private repo (`git fetch template && git merge template/main`).
   Check for conflicts: `git diff --name-only --diff-filter=U` and grep for `<<<<`.
   Keep Mario's `config.yaml`.
5. Push (`git push origin main`).
6. Confirm CI is green.
7. Trigger a real run after every push, not only "where relevant": the dashboard is deployed by the
   workflow, so a change isn't visible until it runs. `gh api repos/marioarranzr/garmin-coach/actions/workflows/coach.yml/dispatches -f ref=main`
   (a manual run re-plans today, or tomorrow after the evening plan time).
- Mario gives feedback from real watch/Todoist use. Fixes say what changed, why,
  and when it takes effect — no technical narration.

## Planning docs (in `docs/`)
- `PRD.md` — product requirements for the dashboard
- `TRD.md` — technical decisions and stack
- `APP_FLOW.md` — navigation and user journey
- `DESIGN_BRIEF.md` — colour palette, typography, components
- `BACKEND_SCHEMA.md` — `build_data()` output schema
- `IMPL_PLAN.md` — build sequence

## Open ideas
- Withings scale (weight, muscle, fat %) via the Withings API, also written to Garmin
  body composition. Not built yet.
- HRV / sleep trend chart in the dashboard Status tab.
- Long-run progression chart in the Training tab.
