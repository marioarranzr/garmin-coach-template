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

You always have **a week of training in your Garmin calendar**, and
**tomorrow's workout is already set the night before**, so you can look it
up in the evening. Each morning the coach checks it against how you slept.

1. **Every evening at 21:00** (`evening_plan`), it plans **tomorrow** from
   your training load and what you did today. That becomes the real workout
   in your Garmin calendar. The days after it, up to a week ahead, are drafts
   ("Coach draft · …"), redrawn every evening from what you've done. Your
   phone gets "Tomorrow: Easy run 8 km" if notifications are on.
2. **Every morning from 08:00** (`morning_check`), it checks today's workout
   with last night's sleep, HRV, Body Battery and Training Readiness, and
   your Todoist notes. That's also when the AI coach makes its call.
   - If the plan still makes sense, **it stays as it is**, quietly.
   - If you slept badly, trained too much yesterday, or wrote that your legs
     are heavy, **it changes the workout** and you get "Changed: …".
3. The morning check **waits for last night's sleep to reach Garmin**: until
   your watch syncs, it tries again every hour. If your watch shows you're up
   but recorded no sleep (not worn, another watch), it checks right away
   without it. If nothing reaches Garmin, it checks at 10:00
   (`wait_for_sleep_until`) at the latest. Later runs that morning stop
   straight away, unless you write a new note in Todoist.
4. **What you've already done stays as it was.** When the plan for today is
   updated (a new note, a re-run), sessions you've already done keep their
   workout in Garmin and stay in the Todoist task as they were planned; only
   what's still to do changes. The plan shows what to train; what you did is
   in Garmin. Past days are never touched.
5. You can **re-run it any time** from GitHub, including from the mobile app.
   Before 21:00 a manual run re-plans today; after 21:00, tomorrow.

GitHub sometimes delays scheduled runs by hours, or drops them, especially on
the hour. So the coach runs at :17 and :47 within each window, and runs that
find nothing to do stop straight away. If the evening plan only arrives after
midnight, it plans that day then, and the morning check revisits it.

The times are your local time all year: GitHub's schedule has no time zones,
so it runs at the UTC hours for both your winter and summer time, and the
coach only acts from 08:00 and from 21:00 on your clock (the run summary
says "Too early" otherwise).

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
- **What it planned:** your Garmin calendar. It reads its own workouts back
  from the readable parts: the name ("Coach · Long run 12 km") gives the
  session and distance, "Readiness 63/100" that day's readiness, and the
  footer whether it was "Planned the evening before" or "Checked 08:03" that
  morning (a Todoist note added after the check is new and triggers a
  re-plan). That's how it knows yesterday's plan, this week's plan so far,
  which workout to replace on a re-run, and whether today was already
  checked. Drafts ("Coach draft · …") are only the days ahead;
  if the coach never ran on a day, that day's draft stands in for the plan. A day the coach left empty while it was running counts as a rest
  day.
- **Your training diary:** Todoist. Notes the coach used are labelled
  `coach` and completed, so they're in your completed tasks.

The only file the daily job commits is your encrypted Garmin session
(`data/garmin_tokens.enc`), because Garmin refreshes it.

## How it plans

**Phases.** The plan always prepares for the next race on your calendar:
base → build/specific → taper → race week → recovery, in blocks of 3 load
weeks + 1 deload week, with about 80 % easy and 20 % quality training. Later
goals aren't forgotten: before a short race the long run still builds up to
14 km, so the block for a later half marathon starts from a solid base, and
the template switches (e.g. to triathlon training) only once that race is the
next one.

**Race priorities.** A races (main goals) get a full taper. B races (tune-ups)
get 3 easy days before and 2 days of recovery after, so they don't interrupt
your main build.

**Your week.** Each type of block has its own weekly template, so you decide
which days you run, ride, swim and rest:

| Next race | Template | Typical week |
|---|---|---|
| Running race | `run_focus` | quality run + recovery spin, bike intervals, easy run with strides, long ride, long run; an optional swim |
| Triathlon | `triathlon` | quality run + swim set, bike intervals, easy run + endurance swim, long ride + brick run, long run, technique swim |
| Cycling event | `cycling` | two interval rides, a long ride, two short maintenance runs |

**Built for performance, not maintenance.** Hard days are hard and easy days
are easy, so multi-sport weeks use **doubles**: after the run intervals, a
35' recovery spin (Z1, no impact, it flushes the legs and adds aerobic time);
in a triathlon block a swim set the same day; a long ride followed by a brick
run. The midweek ride has intervals, which build aerobic power without running
impact, so you can improve with just 3 run days. In build and specific phases
the long run finishes at goal pace (the last 15-30 %, up to 6 km at half
marathon pace), which is the most race-specific session there is. Every day
is still checked against your recovery: on a tired day the hard parts go
first, and a recovery spin stays as it is.

**Quality runs** are structured like a running app's sessions, with **pace
targets from your current fitness** rather than only heart-rate zones. The
paces come from Garmin's race predictor (your predicted 5K, 10K and half
marathon, which follow your VO2 max), so they move as you get fitter. Without
a prediction they're estimated from your easy pace, and while you've run
little lately they're 3 % easier. Examples, rotated week by week:

| Block | Sessions |
|---|---|
| Base, short race | Rolling 400s (400 m at 5K pace, 400 m at marathon pace, no rest), over-unders 1K (1 km at threshold, 1 km at 5K pace, 90" jog), hill reps, 1'/1' fartlek |
| Specific, short race | 1 km reps at 10K pace, pyramid 400-800-1200-800-400, 2 km at 10K pace, 800s |
| Half marathon | tempo, cruise intervals, threshold 10', over-unders 2K, progression, 3-5 km at HM pace, long tempo |
| Taper / race week | a few short reps at race pace to stay sharp |

The number of reps fits the week's quality distance, and the watch gets a
pace range (±5 s/km) for each rep. The report and the workout description list
your paces, e.g. "5K 4:25 · 10K 4:36 · threshold 4:43 · HM 4:50 /km".

**Swims while running is the focus.** If your next goals are running races,
swims are **optional** ("Optional · Technique swim 1500 m"): easy aerobic work
that keeps your feel for the water. Skipping one isn't counted as missed and
never makes a later swim a priority. Once a triathlon is your next race, swims
are part of the plan again.

**Strength sessions** for a small home gym (bar, plates, a dumbbell, a box),
built like a running app's: a dynamic warm-up (walking lunges, bear crawl, heel
walks, press-up walk-outs), then 3-4 blocks of 2-3 exercises done as a circuit
for 2-3 rounds.
- `strength` (~35-45'): lower-body strength (front squat, step-ups,
  single-leg RDL, hip thrust, Bulgarian split squat, calf raises) and a core
  finisher. In build phases it becomes **power**: heavier, fewer reps, box
  jumps and hurdle hops.
- `strength_core` (~30'): rows, press-ups, snatches, Y-T-W raises for the
  swimming shoulders, dead bugs, calves, side planks.

Lower-body strength goes on the bike-intervals day (hard days hard), far from
the long run; strength & core the day after the long run. Fewer rounds near a
race, only core in the taper, nothing in the last days before it. Tired days:
strength becomes strength & core, or is dropped. Loads come from
`strength.loads` and go up 2 kg a week within each 4-week block (whole kilos,
since plates go on in pairs), never above `strength.max_bar_kg`. On the watch
each exercise is one of Garmin's own (front squat, Bulgarian split squat…), so
it shows its name, reps and weight, and the watch counts the reps.

**Swims build up from what you actually swam.** The coach reads each pool
swim's intervals from Garmin (how far you swam, where you stopped and for how
long, your longest stretch without stopping). The next swim is sized to it:
at most ~25 % more than the most you've swum in the last 4 weeks, in reps no
longer than what you held without stopping (a "Build-up swim": drills, then
50/100/200 m reps with rest). After 4 weeks without swimming, it restarts at
600 m. The AI sees each swim's intervals and the plan vs what you did.

**Triathlons** can be sprint, Olympic, half Ironman (70.3), Ironman, or any
distance you type as swim-bike-run km (`kind: tri`, `distances: 1.5-40-10`).
For long-course races the long run builds towards ~85 % of the run leg (up to
30 km), the long ride towards the bike leg (up to 5 h), swims get longer, and
the taper and recovery are longer (3 weeks before an Ironman).

**Running volume** starts from what you **actually** ran in the last 4 weeks
and grows by about 8-10 % per week at most, up to a ceiling per goal type.

- **Return-to-running mode:** if you've averaged under 10 km/week (e.g. after
  a cycling block), it starts at 10 km, grows about 20 % per week and has no
  hard sessions until your legs are used to running again.
- **No run shorter than your usual run:** easy runs are at least as long as
  your typical run of the last 6 weeks (between 4 and 8 km), so a week back
  from a cycling block doesn't give you 4 km runs when you've been running
  6 km. If that makes the week bigger than the volume ramp, the report says so.
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
- Skipped this week's intervals (yesterday or earlier) and you're recovered
  today? They move to today's easy run, unless yesterday was hard or the long
  run is tomorrow.
- Skipped the long run? It isn't made up: better to lose one than pile up fatigue.
- Ahead of this week's running volume? Today's run is cut by 15 %.
- A heavy last 4 days across all sports? Even with good morning numbers, the
  long run is cut by 30 % (even below your minimum) and hard sessions become
  easy, because tired legs absorb running impact worse.
- Already trained today (when re-planning later)? A session you've already
  done isn't added again, and after a substantial ride or swim (45'+, 25 km
  ride, 1.5 km swim, or a hard effort) today's run stays easy.
- Skipping swims? The next swim is marked as a priority.

**The week as a whole.** Each morning the coach also checks this week's key
sessions (long run, quality run, swims, long ride, bike intervals) against
what you've done since Monday, so a session done on another day isn't
repeated:
- Did the long run on Saturday instead of Sunday? Sunday becomes a shorter
  easy run, and Saturday counts as the week's long run (not as "ran more than
  planned").
- Already swum as many times as planned this week? A later swim is dropped.
- Already done this week's hard run or hard rides? A later quality session
  becomes easy.
- A run on a day the coach planned intervals counts as this week's quality
  session, even if Garmin's Training Effect stayed moderate.

**The days ahead.** Your Garmin calendar always shows a week: tomorrow's
real workout and drafts ("Coach draft · …", `preview_days`) for the days
after, built with the same rules as if you follow the plan. So the drafts
already reflect what you've done: after an early long run, Sunday's draft is
an easy run. Every evening tomorrow's draft becomes the real workout and the
drafts are redrawn. The run report and the notification list the next days.

## AI coach and your notes

**The AI coach is optional.** Without an AI key, the rules above decide.
With one, every day:

1. The rules prepare the day's proposal and its **safety limits**.
2. The AI (OpenAI or Claude) gets the whole picture:
   - **your season:** every upcoming race with its date, priority and
     distances, the phase of each week until the next race, and how your
     weeks are built (run days, long-run minimum, this block's week);
   - **the plan:** the next days, the rules' proposal and its reasons;
   - **your training:** every activity of the last 6 weeks with a
     week-by-week summary per sport, your last 4 days of load, and this
     week's key sessions done so far;
   - **you:** your Garmin data, your paces, your notes and your diary.

   It's told the goal is your best result at the A races, not maintenance.
   It plans tomorrow in the evening (from training load and notes) and
   checks it in the morning with your sleep.
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
the last 14 hours, so a note from last night counts in the morning. The AI
uses only notes about how you feel or your training. Those are then
**labelled `coach` and marked done** in Todoist (never deleted). If Todoist made a note recurring (e.g. it
read part of the text as a date), its date is removed first, because
completing a recurring task would only move it to its next date. Unrelated notes ("buy milk") are left
alone. Notes already marked done in that window, by the coach or by you, are
still passed as context, so a re-plan later in the day remembers them. Notes
are only marked done on real runs, never on "Analyze only" test runs.

**Your week at a glance.** The coming days also go to a Todoist project,
"Training plan" (`plan_project`, created if needed): one task per day, due
that day, e.g. "Quality · Rolling 400s, 8 km", with the details in the task.
Todoist's *Upcoming* view or a widget shows the week, next to where you write
your notes. Days that are over are ticked off: the evening plan completes
today's task, so in the morning today's workout is on top, never yesterday's.
Each run replaces the coach's tasks from the first day it plans (they carry
the `coach-plan` label and end with "(Garmin Coach plan)"); anything else you
put in that project is left alone.

**Notes about your week.** Notes like "I can't train on Sunday", "long run
on Saturday this week", "intervals on Thursday" or "no swimming this week"
rearrange the coming days:
- A day you can't train becomes rest. Its long run or quality session moves to
  the nearest free day (earlier first, not next to another hard session of
  the same sport, not on top of another sport). A session it displaces moves
  on the same way.
- "Long run on Saturday" swaps that day with the long-run day.
- "No swimming" drops swims until the day you say.

The AI reads these notes (it needs an AI key) and works out the dates from
when each note was written. Like your other notes, they're labelled and
completed in Todoist, and every run reads the last week of them again. So a
note keeps applying until its date has passed, without the coach storing
anything. The report shows what changed under "Your notes about the week".
For a permanent change (a different long-run day every week), change your
week in `config.yaml` or re-run `python -m coach setup`.

**Lasting preferences.** Feedback about how you like to train ("give me 1'
before the strides", "I prefer long runs on Saturday") is kept in one Todoist
task, "📌 Coach preferences", in the "Training plan" project, one per line.
The AI keeps that list up to date from your notes, and every run passes it to
the AI coach, which respects it and reminds you in the workout notes when one
applies. Edit or delete lines in that task yourself any time. The AI decides
which session, how long, how hard and on which day; the exact structure of a
workout (warm-up, reps, recoveries) is set in code, so for that kind of change,
open an issue or change `coach/planner.py`.

**See what the AI saw.** Every run summary ends with a folded "What the AI
coach saw" section: the exact data it was sent that day.

**Training diary.** The notes labelled `coach` in your completed Todoist tasks
are your training diary. Each day the AI sees the ones from the last 6 weeks,
each next to what you trained that day and the day before (from Garmin), and
that day's readiness and plan (from your Garmin calendar). That lets it learn how *you* respond, e.g. that two big ride
days in a row leave your legs heavy, or that a niggle keeps coming back.

## What you get

**On your watch:** the day's workout(s) in your Garmin calendar, with steps
(warm-up, intervals, recoveries, cool-down), heart-rate zone targets, and a
description that starts with the one-line why:

```
Long run would add too much load after today's ride and heavy legs.

Skip the run today. The bike already gave your legs a solid workout.
Resume running tomorrow only if the legs feel normal.

Your coach chose rest today. Full rest or 20' of gentle mobility and stretching.

Readiness 63/100 · Some fatigue
• Low Body Battery right now (37), after today's training.
• You've trained 10.4 h in the last 4 days, about 1.6× your usual.

Aerobic base + speed — Zürcher Silvesterlauf in 63 days
Checked 08:03.
```

When the AI coach made the call, the rules' own changes are left out of the
workout (they're in the run report, marked as the rules' proposal).

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
- Your notes (last 14 h): “legs heavy” (07:45)
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
- With `NTFY_TOPIC` set, the day's plan, its why and your readiness score go
  through ntfy.sh to your phone.

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

### 5. Optional: AI coach, notes and watch notification

**AI coach** (pay-per-use, one call per day with a few thousand tokens; set a
monthly spending limit with your provider):
- **OpenAI:** create a key at [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
  and add it as the secret `OPENAI_API_KEY`.
- **Claude:** create a key in the [Claude Console](https://console.anthropic.com)
  and add it as the secret `ANTHROPIC_API_KEY`.

With `ai.provider: auto` it uses whichever key is set (OpenAI if both).

**Notes from Todoist:** copy your API token (Todoist → Settings →
Integrations → Developer) and add it as the secret `TODOIST_API_TOKEN`.

**Today's plan on your watch.** Garmin watches only show calendar workouts
when you start an activity, and the watch's Calendar glance shows your phone's
calendar, not Garmin's. So the coach can also send the plan as a phone
notification, which your watch mirrors and keeps in its notification list:
1. Install the free [ntfy](https://ntfy.sh) app (iOS or Android), no account.
2. Subscribe to a topic with a long random name, e.g. `coach-7f3k9q2xw8`.
   Anyone who knows the name can read it, so don't use a guessable one.
3. Add that name as the secret `NTFY_TOPIC`.
4. Make sure your watch shows ntfy notifications (Garmin Connect app →
   your watch → Notifications).

You get "Tomorrow: Long run 12 km" with the why and the rest of the week when the evening plan is made,
and "Changed: …" only if the morning check changes it.

### 6. Test it

**Actions → Daily workout → Run workflow**, tick *Analyze only*, and run it.
The report appears in the run summary. Check its **Coach** section if you
added an AI key. From then on it runs on its own every morning.

## Commands

| Command | What it does |
|---|---|
| `python -m coach login` | Logs in to Garmin and saves the session on your computer |
| `python -m coach setup` | Guided setup: writes `config.yaml` and your schedule |
| `python -m coach week` | The next 7 days with your data, following what you've done this week |
| `python -m coach run --dry-run` | Today's full analysis; uploads nothing, changes nothing |
| `python -m coach run` | Plans (or re-plans) today and uploads it to your Garmin calendar |
| `python -m coach run --tomorrow` | Plans tomorrow, as the evening run does |
| `python -m coach run --auto` | Today, or tomorrow after the evening plan time (what the *Run workflow* button does) |
| `python -m coach run --scheduled` | What GitHub's schedule runs: nothing before the morning check, the morning check, or the evening plan |
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
| `athlete.easy_pace_min_per_km` | from setup | Estimates workout durations, and your training paces when Garmin has no race prediction |
| `athlete.pool_length_m` | 25 | Pool length for swim workouts on the watch |
| `athlete.track_warmup_km` / `athlete.path_warmup_km` | — | How far you run easy to the track / to a flat path: interval sessions use that run as the warm-up and the way back as the cool-down (track: short reps; path: tempo and longer blocks) |
| `athlete.min_ride_minutes` | 30 | Shortest ride worth planning: shorter ones become this long. Recorded rides under 25' are commutes, never training |
| `plan_start` | next Monday | Monday from which the 4-week load/deload cycles count |
| `races` | — | `name`, `date`, `kind` (`5k`, `10k`, `half`, `marathon`, `road`, `tri_sprint`, `tri_olympic`, `tri_half`, `tri_full`, `tri`, `bike`), `distance_km` (road and bike), `elevation_m` (bike), `distances` (`tri`: swim-bike-run km, e.g. `1.5-40-10`), `priority` (`A` or `B`) |
| `peak_weekly_run_km` | 32-62 | Maximum weekly running km per goal type (`short`, `half`, `tri`, `bike`) |
| `min_weekly_run_km` | 15 | Minimum weekly running km outside return-to-running mode |
| `return_to_run_below_km` | 10 | Below this 4-week average, return-to-running mode applies |
| `min_long_run_km` | 0 | Your own minimum long run (0 = automatic) |
| `min_weekly_bike_hours` / `peak_weekly_bike_hours` | 3 / 9-12 | Riding hours range in cycling blocks |
| `weekly_templates` | from setup | Sessions per weekday for `run_focus`, `triathlon` and `cycling` blocks |
| `garmin.evening_plan` | `"21:00"` | When tomorrow is planned (local time) |
| `garmin.morning_check` | `"08:00"` | When the morning check starts (local time) |
| `garmin.wait_for_sleep_until` | `"10:00"` | The morning check goes ahead without sleep data at this time at the latest |
| `garmin.workout_prefix` | `"Coach"` | Name prefix of the workouts in Garmin (drafts get `<prefix> draft`) |
| `garmin.preview_days` | 7 | How many days ahead your calendar shows (tomorrow real, the rest drafts); 0 turns drafts off |
| `garmin.upload` | `true` | `false` = analysis only, never upload |
| `garmin.tokenstore` | `~/.garminconnect` | Where `login` saves your session on your computer |
| `ai.enabled` | `true` | Use the AI coach if a key is set |
| `ai.provider` | `auto` | `auto`, `openai` or `anthropic` |
| `ai.openai_model` / `ai.anthropic_model` | `gpt-5.4-mini` / `claude-sonnet-5-5` | Model per provider |
| `todoist.enabled` | `true` | Read your notes if a token is set |
| `todoist.project` | `Inbox` | Todoist project the notes are read from |
| `strength.loads` | 30 / 30 / 20 / 10 | Your current loads in kg: `front_squat`, `hip_thrust`, `row`, `dumbbell` |
| `strength.max_bar_kg` | 48 | The most you can put on the bar |
| `todoist.plan_project` | `"Training plan"` | Todoist project with your coming days, one task per day; `""` turns it off |
| `todoist.lookback_hours` | 14 | How far back notes count as today's state (14 h at 08:00 = since 18:00 the evening before) |
| `todoist.diary_label` | coach | Label given to the notes the coach used; completed notes with it are your training diary |

**Session types** for the weekly templates: `rest`, `run_easy`,
`run_strides`, `run_quality`, `run_long`, `run_brick`, `bike_easy`,
`bike_spin`, `bike_long`, `bike_quality`, `swim_technique`, `swim_endurance`, `swim_css`,
`strength`, `strength_core`.

**Workflow** (`.github/workflows/coach.yml`): `TZ` is your time zone, and
`cron` lists the UTC hours of the evening plan and the morning checks for
both your winter and summer time. `setup` writes both from your
`evening_plan`, `morning_check` and `wait_for_sleep_until`, and
`python -m coach update` keeps them in sync. GitHub
runs whatever is in your repository, so commit and push the file after
changing them.

**Secrets:** `GARMIN_TOKENS_KEY` (required), `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY` (AI coach), `TODOIST_API_TOKEN` (notes), `NTFY_TOPIC` (plan as a watch notification).

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
| Notes don't appear | Check the `TODOIST_API_TOKEN` secret, `todoist.project`, and that the note was created in the last 14 hours |
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
| `coach/notify.py` | The optional notification with today's plan |
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
