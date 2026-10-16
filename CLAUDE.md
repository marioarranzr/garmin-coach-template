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
- AI coach (OpenAI) decides the day within limits (`ai.apply_limits`); a second call
  reads schedule notes (`ai.schedule_changes`): relative days are resolved from the
  date each note was written; one-offs are never preferences.

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
- Strength: home gym (50 cm box, bar up to 48 kg, 10 kg dumbbell); Garmin exercise
  names on the watch, one-line how-to per exercise, warm-up 30" per exercise, sides
  as left/right steps or alternating sets, Y-T-W as three exercises.
- Rest days: no Garmin workout (marked in Todoist).

## Dashboard
- `coach/dashboard.py` — `python -m coach dashboard` serves http://localhost:8765.
- Five tabs: Status (race countdown, phase, week progress, 12-week chart), Why
  (training logic), Decisions (14-day coach reasoning log), Training (filterable
  activity list), Upcoming (this week + next week with full descriptions).
- `/api/data` returns the raw JSON for debugging.
- Design tokens and schema: `docs/DESIGN_BRIEF.md`, `docs/BACKEND_SCHEMA.md`.

## Working style
Every change follows these steps:
1. Implement.
2. Run `python tests/test_scenarios.py` (no pytest).
3. Push to the template (`git push template main`).
4. Merge into the private repo (`git fetch template && git merge template/main`).
   Check for conflicts: `git diff --name-only --diff-filter=U` and grep for `<<<<`.
   Keep Mario's `config.yaml`.
5. Push (`git push origin main`).
6. Confirm CI is green.
7. Where relevant, trigger a real run (`gh api .../dispatches`).
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
