# Garmin Coach

[![Tests](https://github.com/marioarranzr/garmin-coach-template/actions/workflows/tests.yml/badge.svg)](https://github.com/marioarranzr/garmin-coach-template/actions/workflows/tests.yml)

A self-adjusting training coach for Garmin users, set up for **your** races and
your week: running, cycling, triathlon, or a mix. Every morning it reads your
recovery data from Garmin Connect, decides what you should train today, and
**puts the workout on your Garmin calendar** so it's on your watch, with
structured steps, heart-rate zones and one sentence explaining why.

- **Plans towards your races**: 5K to marathon, sprint and olympic triathlon,
  and cycling events with distance and elevation gain.
- **Adapts every day** to your sleep, HRV, Body Battery, resting heart rate,
  Training Readiness, training load across all sports, and what you actually
  did yesterday and today.
- **Optional AI coach** (OpenAI or Claude) that makes the final call within
  safety limits, taking into account **notes you write in Todoist** ("legs
  heavy", "knee sore").
- **Runs for free on GitHub Actions** in your own private repository: no
  server, no app.

## Contents

- [How a day works](#how-a-day-works)
- [How it plans](#how-it-plans)
- [How it adjusts each day](#how-it-adjusts-each-day)
- [AI coach and your notes](#ai-coach-and-your-notes)
- [What you get](#what-you-get)
- [Privacy](#privacy)
- [Setup](#setup-about-15-minutes)
- [Commands](#commands)
- [Configuration reference](#configuration-reference)
- [Keeping your copy up to date](#keeping-your-copy-up-to-date)
- [Troubleshooting](#troubleshooting)
- [Running it on your computer](#running-it-on-your-computer)
- [How it's built](#how-its-built)

## How a day works

1. **Every hour in the morning** (from the time you choose, e.g. 7:00), GitHub
   runs the coach.
2. It **waits until last night's sleep has reached Garmin**, i.e. you're up
   and your watch has synced. Until then it does nothing and tries again the
   next hour.
3. As soon as the data is there, it **plans the day once** and puts the
   workout in your Garmin calendar. Later checks that morning stop straight
   away, unless you've written a new note in Todoist, in which case it
   re-plans with it.
4. If your watch shows you're up (steps today) but recorded **no sleep**
   (you didn't wear it, or slept with another watch), it plans right away
   without sleep data. If **nothing reaches Garmin at all**, it plans at
   10:00 (`wait_for_sleep_until`). The report says which case applied.
5. You can **re-run it any time** from GitHub, including from the mobile app:
   a manual run plans straight away and replaces the day's earlier workout.

Every run reads fresh data: the latest values Garmin has (first thing in the
morning those are your waking values; later in the day, how you are now), and
the weekly targets are recomputed each time. The report shows when your watch
last synced and warns you if that was more than 2 hours ago, because anything
after that sync isn't in Garmin yet.

### What it looks back at, and why 6 weeks

Every run looks back over **the same window: the 6 full weeks before this
week's Monday, plus this week so far** (42 to 48 days). That one window is
used for everything: your Garmin activities, the coach's own past workouts in
your Garmin calendar, your training diary in Todoist, and what the AI coach
sees.

| What | Uses | Why |
|---|---|---|
| Weekly running target, riding hours | last 4 weeks | The usual "chronic load" window: long enough to smooth out one odd week, short enough to follow your current fitness |
| Long-run floor | longest run of the last 6 weeks | A long run you did within 6 weeks is one your body can still do; older than that, after a break, it isn't |
| Learned long-run / long-ride day | last 4 weeks (3 of 4 must agree) | Your current habit, not last season's |
| Heavy-block check, load ratio | last 4 days vs. the 4 weeks before | Spots a big block of training against your normal |
| Yesterday's plan, this week's plan so far | Garmin calendar | To compare what was planned with what you did |
| AI coach | all 6 weeks: every activity plus a weekly summary per sport, and your diary notes with what you trained around each | Enough to see the trend into today and how you responded, without stale history |

Six weeks is the longest any rule needs, so a longer window wouldn't change a
single decision. It would only add older training that no longer reflects
your fitness, and more data to send to the AI. The setup wizard reads the
same window to suggest your settings.

### Where the coach keeps its memory

**Nowhere in the repository.** Everything it needs from earlier days is
read back each run:

- **Your training:** Garmin activities.
- **What it planned:** your Garmin calendar. Every workout it uploads ends
  its description with one short line, `coach:{…}`, holding the session type,
  distance or minutes, intensity, that day's readiness score and the ids of
  the notes it saw. That's how it knows yesterday's plan, this week's plan
  so far, which workout to replace on a re-run, and whether today is already
  planned. A day the coach left empty while it was running counts as a rest
  day.
- **Your training diary:** Todoist. Notes the coach used are labelled
  `coach` and completed, so they're in your completed tasks.

The only file the daily job commits is your encrypted Garmin session
(`data/garmin_tokens.enc`), because Garmin refreshes it.

## How it plans

**Phases.** The plan always prepares for the next race on your calendar:
base → build/specific → taper → race week → recovery, in blocks of 3 load
weeks + 1 deload week, with about 80 % easy and 20 % quality training.

**Race priorities.** A races (main goals) get a full taper. B races (tune-ups)
get 3 easy days before and 2 days of recovery after, so they don't interrupt
your main build.

**Your week.** Each type of block has its own weekly template, so you decide
which days you run, ride, swim and rest:

| Next race | Template | Typical week |
|---|---|---|
| Running race | `run_focus` | quality run, easy run with strides, long run; easy and long rides; a swim |
| Triathlon | `triathlon` | two or three swims, bike intervals, a bike→run brick, long run |
| Cycling event | `cycling` | two interval rides, a long ride, two short maintenance runs |

**Running volume** starts from what you **actually** ran in the last 4 weeks
and grows by about 8-10 % per week at most, up to a ceiling per goal type.

- **Return-to-running mode:** if you've averaged under 10 km/week (e.g. after
  a cycling block), it starts at 10 km, grows about 20 % per week and has no
  hard sessions until your legs are used to running again.
- **Long run floor:** the long run is never shorter than ~10 % above your
  longest run of the last 6 weeks, or a minimum you set (`min_long_run_km`),
  except in taper and race weeks.
- **Learned long-run day:** if your longest run falls on the same weekday in 3
  of the last 4 weeks, and that's not the planned day, the whole week shifts so
  the long run lands there. The report tells you when this happens.

**Cycling events** are set with distance and elevation gain. Weekly riding
hours grow about 8 % per week from your last 4 weeks. Long rides build towards
the event's estimated duration, never more than 15 % above your longest recent
ride. Hilly courses (10+ m of climbing per km) get hill repeats, long climbs
and climbing blocks within the long rides. The day before is a 45' ride with
a few short efforts.

## How it adjusts each day

**Readiness score (0-100).** Each metric is turned into a 0-100 score and
weighted:

| Metric | Weight | Notes |
|---|---|---|
| Garmin Training Readiness | 30 % | the latest value; Garmin lowers it after a workout |
| HRV (overnight) | 20 % | vs your normal range and weekly average |
| Sleep | 15 % | Garmin sleep score and hours |
| Body Battery | 15 % | the latest value (on waking, or now if you've trained) |
| Resting heart rate | 12 % | vs your 7-day average |
| Yesterday's stress | 8 % | Garmin average stress |

Missing metrics (e.g. a watch without HRV) are left out and the rest are
reweighted. Points are then taken off for a high acute/chronic training load,
running more than planned yesterday, a session harder than planned, 6+ days in
a row, or **a heavy last 4 days across all sports** (1.5× your usual hours or
more).

| Level | Score | What happens |
|---|---|---|
| 🟢 Green | 70+ | Follow the plan |
| 🟡 Amber | 50-69 | Quality → easy run, long run −20 %, hard bike/swim → easy |
| 🟠 Orange | 35-49 | Short, very easy session (swim only if a swim is planned) |
| 🔴 Red | under 35 | Rest |

Two hard rules: low HRV together with a raised resting heart rate (a typical
sign of illness) always means rest, and under 5.5 h of sleep means no hard
session.

**Checks against what you actually did:**
- Ran more than planned yesterday? Today's run is shortened.
- Went hard on an easy day? No quality today.
- Skipped yesterday's intervals and you're recovered today? They move to today,
  unless the long run is tomorrow.
- Skipped the long run? It isn't made up: better to lose one than pile up fatigue.
- Ahead of this week's running volume? Today's run is cut by 15 %.
- A heavy last 4 days across all sports? Even with good morning numbers, the
  long run is cut by 30 % (even below your minimum) and hard sessions become
  easy, because tired legs absorb running impact worse.
- Already trained today (when re-planning later)? A session you've already
  done isn't added again, and after a substantial ride or swim (45'+, 25 km
  ride, 1.5 km swim, or a hard effort) today's run stays easy.
- Skipping swims? The next swim is marked as a priority.

## AI coach and your notes

**The AI coach is optional.** Without an AI key, the rules above decide.
With one, every day:

1. The rules prepare the day's proposal and its **safety limits**.
2. The AI (OpenAI or Claude) gets the whole picture: your Garmin data, every
   activity of the last 6 weeks with a week-by-week summary per sport, your
   last 4 days of load, the plan, the rules' proposal and your notes.
3. It makes the final call: keep the plan, shorten it, make it easier, swap it
   for an easy swim or spin, or rest.
4. The code checks its answer. **It can never add volume or intensity beyond
   the rules' proposal**: a longer run is capped at the planned distance,
   sessions that weren't planned aren't added, and a rest day stays a rest
   day. If the AI fails or answers something invalid, the rule-based plan is
   used and the report says so.
5. It writes the one-line why shown first on your watch, plus a short coach's
   note with practical advice for the day.

**Your notes (Todoist, optional).** Anything you add to your Todoist Inbox
(typed, dictated, or from a gadget) is passed to the AI if it was created in
the last 12 hours, so a note from last night counts in the morning. The AI
uses only notes about how you feel or your training. Those are then
**labelled `coach` and marked done** in Todoist (never deleted). If Todoist made a note recurring (e.g. it
read part of the text as a date), its date is removed first, because
completing a recurring task would only move it to its next date. Unrelated notes ("buy milk") are left
alone. Notes already marked done in that window, by the coach or by you, are
still passed as context, so a re-plan later in the day remembers them. Notes
are only marked done on real runs, never on "Analyze only" test runs.

**Training diary.** The notes labelled `coach` in your completed Todoist tasks
are your training diary. Each day the AI sees the ones from the last 6 weeks,
each next to what you trained that day and the day before (from Garmin), and
that day's readiness and plan (from your Garmin calendar). That lets it learn how *you* respond, e.g. that two big ride
days in a row leave your legs heavy, or that a niggle keeps coming back.

## What you get

**On your watch:** the day's workout(s) in your Garmin calendar, with steps
(warm-up, intervals, recoveries, cool-down), heart-rate zone targets, and a
description that starts with the one-line why.

**A daily report** in each GitHub Actions run summary (local runs also save it
in `reports/`, which git ignores). Example:

```
# Training for Sunday 18 Oct 2026

Easier than planned (readiness 58): you've trained 7.9 h in the last 4 days (about 2.9× your usual).

Phase: Aerobic base + speed — Autumn 10K in 56 days
Weekly running target: 24.0 km (last 4 weeks average 22.3 km, last week 21.0 km)
Run so far this week: 14.0 km

## Readiness: 58/100 — 🟡 Some fatigue: intensity goes down
- Training Readiness now: 61 (scores 61/100)
- HRV: 58 ms / balanced (scores 90/100)
- Sleep: 81 pts / 7.6 h (scores 81/100)
- Body Battery now: 44 (scores 41/100)
- Resting HR: 51 bpm (+0) (scores 100/100)
- Yesterday's stress: 31 (scores 88/100)

## Yesterday
- Planned: 0 km running
- Done: Long Ride (92 km, 207')

## Today
- Done today so far: Morning Ride (30 km, 57')
- Original plan: Long run 10 km
- Easy run 5 km: 5 km in Z2. If you can't hold Z2, walk the hills.

## Coach
- Decided by the AI coach (gpt-5.4-mini), within the plan's safety limits.
- Keep it truly conversational and stop if your legs feel flat or niggly. No strides today.
- Your notes (last 12 h): “legs heavy” (07:45)
- Taken into account: “legs heavy”
- Completed in Todoist: 1 note(s) (they're in your completed tasks).

_Garmin data as of your watch's last sync: Sun 08:12._
```

## Privacy

The coach keeps **no health data in your repository**: what it planned lives
in your Garmin calendar and your diary in Todoist (see
[Where the coach keeps its memory](#where-the-coach-keeps-its-memory)). The
daily job commits only `data/garmin_tokens.enc`, your **encrypted** Garmin
session, refreshed so it stays logged in.

- Still, run it from your **own private** repository, never from a public
  one: each run's summary on GitHub (sleep, HRV, readiness, your notes) is
  visible to anyone who can see the repository's Actions. The daily workflow
  checks this and refuses to run in a public repository.
- Your Garmin **password is never stored** anywhere. It's used once on your
  computer to get a session token from Garmin.
- The session token is encrypted with a key that only lives in your
  repository's GitHub secrets.
- With the AI coach on, each day's data (Garmin metrics, recent activities,
  the plan and your notes) is sent to the AI provider's API (OpenAI or
  Anthropic). Leave both AI keys unset to keep everything rule-based.

## Setup (about 15 minutes)

You need a Garmin account, a GitHub account, and git and Python 3.12+ on your
computer (only for the one-time login and setup).

### 1. Create your private copy

Click **Use this template → Create a new repository**, choose **Private**,
and create it. Then:

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

It reads your last 6 weeks from Garmin and proposes your settings in one list:
what you train (running, running + cycling, triathlon or cycling), run days
per week, your usual long-run (or long-ride) day and rest day, easy pace,
time zone, and the time of the first morning check. Press Enter to accept
them all, or type the numbers you want to change. Then add your races: name,
type (with distance, and elevation gain for cycling events), date and
priority.

It writes `config.yaml` and sets your time zone and morning schedule in
`.github/workflows/coach.yml`. Check the result with your real data:

```bash
.venv/bin/python -m coach week           # the next 7 days
.venv/bin/python -m coach run --dry-run  # today's full analysis, uploads nothing
```

Run `setup` again whenever your goals change. It starts from your current
settings and races (Enter keeps them, `a` adds races) and shows where your
Garmin data suggests something different. Values you added by hand, like
`min_long_run_km`, are kept. The weekly templates are rebuilt, so redo any
hand edits to them afterwards. `config.yaml.bak` keeps the previous version.

### 4. Hand the session to GitHub

```bash
.venv/bin/python -m coach export-tokens
git add config.yaml data/garmin_tokens.enc .github/workflows/coach.yml
git commit -m "My plan and Garmin session"
git push
```

`export-tokens` prints a key. In your repository, go to **Settings → Secrets
and variables → Actions → New repository secret**, name it
`GARMIN_TOKENS_KEY` and paste the key.

### 5. Optional: AI coach and notes

**AI coach** (pay-per-use, one call per day with a few thousand tokens; set a
monthly spending limit with your provider):
- **OpenAI:** create a key at [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
  and add it as the secret `OPENAI_API_KEY`.
- **Claude:** create a key in the [Claude Console](https://console.anthropic.com)
  and add it as the secret `ANTHROPIC_API_KEY`.

With `ai.provider: auto` it uses whichever key is set (OpenAI if both).

**Notes from Todoist:** copy your API token (Todoist → Settings →
Integrations → Developer) and add it as the secret `TODOIST_API_TOKEN`.

### 6. Test it

**Actions → Daily workout → Run workflow**, tick *Analyze only*, and run it.
The report appears in the run summary. Check its **Coach** section if you
added an AI key. From then on it runs on its own every morning.

## Commands

| Command | What it does |
|---|---|
| `python -m coach login` | Logs in to Garmin and saves the session on your computer |
| `python -m coach setup` | Guided setup: writes `config.yaml` and your schedule |
| `python -m coach week` | Base plan for the next 7 days, with your data |
| `python -m coach run --dry-run` | Today's full analysis; uploads nothing, changes nothing |
| `python -m coach run` | Plans today and uploads it to your Garmin calendar |
| `python -m coach run --date 2026-10-14` | Plans a specific day |
| `python -m coach export-tokens` | Encrypts your session for GitHub and prints the key |
| `python -m coach update` | Brings in the latest template code, keeping your settings |

Add `-v` to any command for detailed logs.

## Configuration reference

All settings live in `config.yaml`. The time zone and schedule live in the
workflow file, and keys in GitHub secrets.

| Setting | Default | What it does |
|---|---|---|
| `athlete.sports` | from setup | `run`, `run_bike`, `tri` or `bike` (used by the setup) |
| `athlete.easy_pace_min_per_km` | from setup | Only used to estimate workout duration |
| `athlete.pool_length_m` | 25 | Pool length for swim workouts on the watch |
| `plan_start` | next Monday | Monday from which the 4-week load/deload cycles count |
| `races` | — | `name`, `date`, `kind` (`5k`, `10k`, `half`, `marathon`, `road`, `tri_sprint`, `tri_olympic`, `bike`), `distance_km` (road and bike), `elevation_m` (bike), `priority` (`A` or `B`) |
| `peak_weekly_run_km` | 32-62 | Maximum weekly running km per goal type (`short`, `half`, `tri`, `bike`) |
| `min_weekly_run_km` | 15 | Minimum weekly running km outside return-to-running mode |
| `return_to_run_below_km` | 10 | Below this 4-week average, return-to-running mode applies |
| `min_long_run_km` | 0 | Your own minimum long run (0 = automatic) |
| `min_weekly_bike_hours` / `peak_weekly_bike_hours` | 3 / 9-12 | Riding hours range in cycling blocks |
| `weekly_templates` | from setup | Sessions per weekday for `run_focus`, `triathlon` and `cycling` blocks |
| `garmin.wait_for_sleep_until` | `"10:00"` | Plans without sleep data at this time at the latest |
| `garmin.workout_prefix` | `"Coach"` | Name prefix of the workouts in Garmin |
| `garmin.upload` | `true` | `false` = analysis only, never upload |
| `garmin.tokenstore` | `~/.garminconnect` | Where `login` saves your session on your computer |
| `ai.enabled` | `true` | Use the AI coach if a key is set |
| `ai.provider` | `auto` | `auto`, `openai` or `anthropic` |
| `ai.openai_model` / `ai.anthropic_model` | `gpt-5.4-mini` / `claude-sonnet-5-5` | Model per provider |
| `todoist.enabled` | `true` | Read your notes if a token is set |
| `todoist.project` | `Inbox` | Todoist project the notes are read from |
| `todoist.lookback_hours` | 12 | How far back notes count as today's state |
| `todoist.diary_label` | coach | Label given to the notes the coach used; completed notes with it are your training diary |

**Session types** for the weekly templates: `rest`, `run_easy`,
`run_strides`, `run_quality`, `run_long`, `run_brick`, `bike_easy`,
`bike_long`, `bike_quality`, `swim_technique`, `swim_endurance`, `swim_css`.

**Workflow** (`.github/workflows/coach.yml`): `TZ` is your time zone, and
`cron` sets the hourly morning checks (in UTC). `setup` writes both. GitHub
runs whatever is in your repository, so commit and push the file after
changing them.

**Secrets:** `GARMIN_TOKENS_KEY` (required), `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY` (AI coach), `TODOIST_API_TOKEN` (notes).

## Keeping your copy up to date

When this template gets improvements, bring them into your copy from your
computer:

```bash
.venv/bin/python -m coach update
git push
```

It keeps your `config.yaml`, your data and your time zone and schedule, and
takes everything else from the template. The first time, it also links your
copy to the template's history, because a copy made with "Use this template"
starts with its own. After an update, run `setup` and press Enter twice if you
want new settings written into your `config.yaml`. Settings that aren't in
your file use their defaults.

## Troubleshooting

| Problem | What to do |
|---|---|
| `No saved Garmin session` | Run `python -m coach login` and finish all prompts |
| `429` warnings during login | Normal if it ends with `✅ Logged in`; otherwise wait a while before trying again |
| A workout you deleted from your Garmin calendar counts as a rest day | The calendar is the coach's record of what it planned. Re-run the workflow to put today's workout back |
| GitHub run fails with a session error | Garmin expired the session: run `login`, then `export-tokens`, push `data/garmin_tokens.enc` and update the `GARMIN_TOKENS_KEY` secret |
| "Last night's sleep isn't in Garmin yet" | Sync your watch (open Garmin Connect on your phone); the next hourly check picks it up |
| "Your watch last synced X h ago" | Sync your watch and press *Run workflow* to re-plan |
| "AI coach unavailable today (…)" | The text in brackets is the error: check the key and your provider's billing. The rule-based plan was used |
| Notes don't appear | Check the `TODOIST_API_TOKEN` secret, `todoist.project`, and that the note was created in the last 12 hours |
| Schedule or time zone didn't change | Commit and push `.github/workflows/coach.yml` |
| `git pull` asks how to reconcile divergent branches | The daily job also pushes: run `git config pull.rebase false`, then `git pull` |

## Running it on your computer

```bash
.venv/bin/python -m coach run            # analysis + upload to the Garmin calendar
```

Schedule it with cron (`run_coach.sh`) on macOS/Linux or Task Scheduler
(`run_coach.bat`) on Windows. The session stays in `~/.garminconnect`. For the
AI coach and notes, set the same names as environment variables (e.g.
`export OPENAI_API_KEY=...`).

## Heart-rate zones

Workouts target the Z1-Z5 heart-rate zones configured on your watch. Set them
from your **lactate threshold** (Garmin Connect → User Settings → Heart Rate
Zones) or do your watch's guided threshold test.

## How it's built

| File | Purpose |
|---|---|
| `coach/garmin_api.py` | Garmin login, data fetching, calendar upload |
| `coach/planner.py` | Phases, weekly volume and the session library |
| `coach/readiness.py` | Readiness score, plan compliance and recent load |
| `coach/adjust.py` | Daily adjustment rules |
| `coach/why.py` | The one-line explanation of each day |
| `coach/ai.py` | AI coach: final call within code-enforced limits |
| `coach/notes.py` | Your notes and training diary in Todoist |
| `coach/calendar.py` | Reads back what the coach planned from your Garmin calendar |
| `coach/engine.py` | Orchestrates the day and writes the report |
| `coach/workouts.py` | Conversion to Garmin structured workouts |
| `coach/setup_wizard.py` | The guided setup |
| `coach/update.py` | Updating your copy from the template |
| `.github/workflows/coach.yml` | The daily runs on GitHub |
| `tests/` | Offline tests with fake Garmin, AI and Todoist (no accounts needed) |

```bash
.venv/bin/python tests/test_scenarios.py
```

## Notes

- Uses the unofficial [`garminconnect`](https://github.com/cyberjunky/python-garminconnect)
  library. If Garmin changes something and the connection breaks, update it:
  `pip install -U garminconnect`.
- Not affiliated with Garmin, OpenAI, Anthropic or Todoist.
- This is a training aid, not medical advice. If something hurts or you're
  ill, listen to your body and see a professional.

## License

MIT
