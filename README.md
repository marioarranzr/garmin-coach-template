# Garmin Coach

[![Tests](https://github.com/marioarranzr/garmin-coach-template/actions/workflows/tests.yml/badge.svg)](https://github.com/marioarranzr/garmin-coach-template/actions/workflows/tests.yml)

A self-adjusting training coach for Garmin users, set up for **your** races and
your week: running, cycling, triathlon, or a mix. Every morning it
reads your recovery data from Garmin Connect, decides what you should train
today, and **puts the workout on your Garmin calendar** so it's on your watch,
with structured steps and heart-rate zones.

A guided setup reads your Garmin history and suggests the answers (what you
train, run days, long-run day, easy pace…), so you only add your races.

It runs for free on GitHub Actions in your own private repository: no server,
no app, and your data never leaves your Garmin account and your repository.

## What it does

**1. Reads your Garmin data** as soon as you're up (it waits for your watch to sync last night's sleep): sleep score and hours, overnight HRV and its
normal range, Body Battery on waking, resting heart rate vs your 7-day average,
yesterday's stress, Training Readiness, acute/chronic load and your activities
from the last 5 weeks.

**2. Builds a periodized plan** towards your races (5K to marathon, sprint or
olympic triathlon, and cycling events with their distance and elevation gain)
around your own weekly schedule: which days you run, ride, swim and rest.
Each race gets base → specific → taper → recovery, with 3 load weeks + 1
deload week and ~80 % easy / 20 % quality training. The week adapts to what's
next: more swims, bike intervals and bricks before a triathlon; interval
rides and long rides before a cycling event, with long rides growing from your
current longest ride towards the event's duration, and climbing work if the
course is hilly. Secondary (B) races get a 3-day mini taper so they don't
interrupt your main build. Running volume starts from what you **actually** ran in
the last 4 weeks and grows ~8-10 % per week at most. If you've barely been
running (e.g. a cycling block), a **return-to-running mode** starts low and
keeps everything easy until your legs adapt. The long run never drops below
your longest recent run (+10 %), or a minimum you set.

**3. Scores your readiness (0-100)** and adjusts the day:

| Level | What happens |
|---|---|
| 🟢 Green | Follow the plan |
| 🟡 Amber | Quality → easy run, long run −20 %, hard bike/swim → easy |
| 🟠 Orange | Short, very easy session (swim only if a swim is planned) |
| 🔴 Red | Rest. Low HRV + high resting HR (typical of illness) always means rest |

**4. Checks yesterday against the plan:**
- Ran more than planned? Today's run is shortened.
- Went hard on an easy day? No quality today.
- Skipped intervals but feel good today? They move to today (unless the long run is tomorrow).
- Skipped the long run? It isn't made up: better to lose one than pile up fatigue.
- Ahead of this week's volume? 15 % cut.
- Trained a lot over the last 4 days, any sport (e.g. a big cycling block)?
  Even if the morning numbers look fine, the long run is cut by 30 % and
  hard sessions become easy, since tired legs absorb running impact worse.
- Skipping swims? The next swim is flagged as a priority.
- Doing your long run on a different day than planned? If your longest run
  falls on the same weekday in 3 of the last 4 weeks, the week moves so the
  long run lands there (the report tells you).
- Re-planning later in the day? Sessions you've already done today aren't
  added again, and readiness uses your Body Battery and Training Readiness
  as they are now (after that morning ride), not as they were on waking.

Everything is read fresh on every run: the latest values Garmin has, and the
weekly targets are recomputed each time. The report shows when your watch
last synced and warns you if it's been more than 2 hours, since anything
after that sync isn't in Garmin yet.

**5. Lets an AI coach make the final call** (optional). Claude gets the whole
picture (your Garmin data, the last two weeks of every sport, the plan and
your own notes from that morning) and decides: keep the plan, shorten it,
make it easier, swap it for an easy swim or spin, or rest. It can never add
volume or intensity beyond the rule-based plan; that's enforced in code, and
if the AI is unavailable the rule-based plan is used.

**Your notes:** anything you add to your Todoist Inbox in the morning ("legs
heavy", "knee sore", "slept badly"), typed, dictated or from a gadget, is
passed to the AI coach. Unrelated notes are ignored and nothing in Todoist is
changed. If you add a note after the day is planned, the next morning check
re-plans with it.

**6. Uploads the workout to Garmin**, starting with one short sentence on why
today looks the way it does (e.g. *"Easier than planned (readiness 48): you
slept 5.2 h and your Training Readiness is low (38)."*) and the details below it, and shows a daily report in the GitHub Actions run summary
(local runs also save it in `reports/`, which git ignores).

Example report:

```
# Training for Tuesday 13 Oct 2026

Easy and short while you rebuild running after weeks of little running: you're well recovered (readiness 78).

Phase: Aerobic base + speed — Autumn 10K in 61 days
Weekly running target: 10.0 km (last 4 weeks average 3.5 km, last week 2 km)
Return-to-running mode: volume grows ~20 %/week from 10 km and there are no hard sessions …

## Readiness: 78/100 — 🟢 Ready for the plan
- Training Readiness: 66 → 66/100
- HRV: 61 ms / balanced → 90/100
- Sleep: 79 pts / 7.4 h → 79/100
- Body Battery: 72 → 82/100
- Resting HR: 52 bpm (+0) → 100/100

## Today
- Easy run 4 km + 6 strides: 4 km in Z2, then 6 × 20" strides.
```

## Privacy: always use a private copy

The daily job commits two files to the repository: `data/state.json` (each
day's plan and readiness score, so it can compare yesterday's plan with what
you did and replace a re-run's workout instead of duplicating it) and your
**encrypted** Garmin session (`data/garmin_tokens.enc`, refreshed so it stays
logged in). That's why:

- You run it from your **own private** repository, never from a public one.
  The daily workflow checks this and refuses to run in a public repository.
- Your Garmin **password is never stored** anywhere. It's used once on your
  computer to get a session token from Garmin.
- The session token is encrypted with a key that only lives in your
  repository's GitHub secrets.
- With the AI coach on, each day's data (Garmin metrics, recent activities,
  the plan and your notes) is sent to the Claude API to make the call.
  Leave `ANTHROPIC_API_KEY` unset to keep everything rule-based.

## Setup (about 10 minutes)

You need a Garmin account, a GitHub account, git and Python 3.12+ on your
computer (only for the one-time login).

### 1. Create your private copy

Click **Use this template → Create a new repository**, choose **Private**, and
create it. Then clone it:

```bash
git clone https://github.com/<you>/<your-private-repo>
cd <your-private-repo>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
```

### 2. Log in to Garmin (once, on your computer)

```bash
.venv/bin/python -m coach login          # email, password and MFA code if asked
```

Garmin usually blocks logins coming from servers, which is why this step runs
on your computer. If you see `429` warnings but it ends with `✅ Logged in`,
it worked. Avoid logging in many times in a row.

### 3. Set up your plan

```bash
.venv/bin/python -m coach setup
```

It reads your last 12 weeks from Garmin and proposes your settings in one list:
what you train (running, running + cycling, triathlon or cycling), run days per
week, your usual long-run (or long-ride) and rest days, easy pace and time
zone. Press Enter to accept them all, or type the numbers you want to change.
Then add your races: name, type (with distance and elevation for cycling
events), date and priority. It writes `config.yaml` and sets the time zone and
daily time in `.github/workflows/coach.yml`. Commit `config.yaml` and the workflow file afterwards: GitHub runs whatever is
in the repository, so a new time zone or schedule applies once it's pushed.
Run it again whenever your goals change: it starts from your current settings and races (Enter keeps them, and
it shows where your Garmin data suggests something different), or edit
`config.yaml` by hand for finer control.

Check the result with your real data:

```bash
.venv/bin/python -m coach week           # the next 7 days
.venv/bin/python -m coach run --dry-run  # today's full analysis, uploads nothing
```

### 4. Hand the session to GitHub

```bash
.venv/bin/python -m coach export-tokens
git add config.yaml data/garmin_tokens.enc .github/workflows/coach.yml   # your setup + session
git commit -m "My plan and Garmin session"
git push
```

`export-tokens` prints a key. In your repository go to **Settings → Secrets and
variables → Actions → New repository secret**, name it `GARMIN_TOKENS_KEY` and
paste the key.

### 5. Optional: AI coach and your notes

**AI coach.** Create an API key in the [Claude Console](https://console.anthropic.com)
(Settings → API keys) and add it as the repository secret `ANTHROPIC_API_KEY`.
It's pay-per-use: one call per day, a few thousand tokens. Set a monthly
spending limit in the Console so there are no surprises. The model is set in
`config.yaml` (`ai.model`).

**Notes from Todoist.** Copy your API token from Todoist (Settings →
Integrations → Developer) and add it as the secret `TODOIST_API_TOKEN`. The
coach reads today's notes from the project in `config.yaml` (`todoist.project`,
default `Inbox`).

To use them when running on your computer, set the same names as
environment variables (`export ANTHROPIC_API_KEY=...`).

### 6. Test it

**Actions → Daily workout → Run workflow**, tick *Analyze only*, and run it.
The report appears in the run summary. From then on it runs on its own every
morning: it checks hourly from the time you chose, waits until last night's
sleep and HRV have reached Garmin (i.e. you're up and your watch has synced),
plans the day once and puts the workout in your Garmin calendar. If your watch
shows you're up but recorded no sleep (you didn't wear it, or used another
watch), it plans right away without it; if nothing reaches Garmin at all, it
plans at 10:00 (`wait_for_sleep_until` in `config.yaml`). Either way the
report says so.

You can re-run it any time from the GitHub mobile app: a manual run plans
straight away and replaces the day's earlier workout.

**If a run fails with a session error** (Garmin sometimes expires sessions),
repeat `login` (step 2) and step 4.

## Running it on your computer instead

```bash
.venv/bin/python -m coach run            # analysis + upload to the Garmin calendar
```

Schedule it with cron (`run_coach.sh`) on macOS/Linux or Task Scheduler
(`run_coach.bat`) on Windows. The session stays in `~/.garminconnect`.

## Heart-rate zones

Workouts target the Z1-Z5 heart-rate zones configured on your watch. Set them
from your **lactate threshold** (Garmin Connect → User Settings → Heart Rate
Zones) or do your watch's guided threshold test.

## How it's built

| File | Purpose |
|---|---|
| `coach/garmin_api.py` | Garmin login, data fetching, calendar upload |
| `coach/planner.py` | Phases, weekly volume and session library |
| `coach/readiness.py` | Readiness score and plan compliance |
| `coach/adjust.py` | Daily adjustment rules |
| `coach/workouts.py` | Conversion to Garmin structured workouts |
| `coach/engine.py` | Orchestrates the day and writes the report |
| `coach/ai.py` | AI coach: final call within code-enforced limits |
| `coach/notes.py` | Your morning notes from Todoist |
| `tests/` | Offline tests with a fake Garmin (no account needed) |

```bash
.venv/bin/python tests/test_scenarios.py
```

## Notes

- Uses the unofficial [`garminconnect`](https://github.com/cyberjunky/python-garminconnect)
  library. If Garmin changes something and the connection breaks, update it:
  `pip install -U garminconnect`.
- If your watch doesn't measure something (e.g. HRV), the score uses the rest.
- Not affiliated with Garmin.
- This is a training aid, not medical advice. If something hurts or you're
  ill, listen to your body and see a professional.

## License

MIT
