# Garmin Coach

[![Tests](https://github.com/marioarranzr/garmin-coach-template/actions/workflows/tests.yml/badge.svg)](https://github.com/marioarranzr/garmin-coach-template/actions/workflows/tests.yml)

A self-adjusting training coach for Garmin users, set up for **your** races and
your week: running only, running with cycling, or triathlon. Every morning it
reads your recovery data from Garmin Connect, decides what you should train
today, and **puts the workout on your Garmin calendar** so it's on your watch,
with structured steps and heart-rate zones.

A guided setup reads your Garmin history and suggests the answers (what you
train, run days, long-run day, easy pace…), so you only add your races.

It runs for free on GitHub Actions in your own private repository: no server,
no app, and your data never leaves your Garmin account and your repository.

## What it does

**1. Reads your Garmin data:** sleep score and hours, overnight HRV and its
normal range, Body Battery on waking, resting heart rate vs your 7-day average,
yesterday's stress, Training Readiness, acute/chronic load and your activities
from the last 5 weeks.

**2. Builds a periodized plan** towards your races (5K to marathon, sprint or
olympic triathlon) around your own weekly schedule: which days you run, ride,
swim and rest. Cycling is used as cross-training in your week; cycling races
aren't a goal type yet. Each race gets base → specific → taper → recovery,
with 3 load weeks + 1 deload week and ~80 % easy / 20 % quality training, and
the week switches to triathlon mode (more swims, bike intervals, bricks)
before a triathlon. Running volume starts from what you **actually** ran in
the last 4 weeks and grows ~8-10 % per week at most. If you've barely been
running (e.g. a cycling block), a **return-to-running mode** starts low and
keeps everything easy until your legs adapt.

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
- Skipping swims? The next swim is flagged as a priority.

**5. Uploads the workout to Garmin** with the reasons for any change in its
description, and shows a daily report in the GitHub Actions run summary
(local runs also save it in `reports/`, which git ignores).

Example report:

```
# Training for Tuesday 13 Oct 2026
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

It reads your last 12 weeks from Garmin and suggests the answers: what you
train (running, running + cycling, or triathlon), how many days a week you
run, your usual long-run and rest days, your easy pace, pool length and time
zone. Press Enter to accept a suggestion or type your own. Then add your races
(name, date and type). It writes `config.yaml` and sets the time zone and
daily time in `.github/workflows/coach.yml`. Run it again whenever your goals
change, or edit `config.yaml` by hand for finer control.

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

### 5. Test it

**Actions → Daily workout → Run workflow**, tick *Analyze only*, and run it.
The report appears in the run summary. From then on it runs every morning on
its own and the workout shows up in your Garmin calendar. Sync your watch when
you wake up so it sees last night's sleep and HRV.

You can re-run it any time from the GitHub mobile app (e.g. after syncing your
watch late): the day's earlier workout is replaced.

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
