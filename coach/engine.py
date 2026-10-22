"""Orchestrates the day: data → plan → readiness → adjustment → Garmin → report."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

from . import garmin_api as G
from .adjust import adjust
from . import paces as P
from . import week as W
from .planner import (Session, compute_week_target, history_start, set_swim_capacity, swim_capacity, week_parts, key_day, key_session, learn_long_day, monday_of,
                      phase_for, plan_day,
                      planned_run_km, run_distribution, template_for, week_index, weekly_run_km)
from .readiness import compliance, compute, is_training, evening as evening_readiness, consecutive_days, own_acwr, recent_load
from . import ai
from .calendar import Calendar, fit, footer
from .notify import message, send
from .notes import (complete_notes, diary, diary_label, plan_tasks_on, read_preferences, recent_notes,
                    rest_day_marker, save_preferences, sync_plan)
from .calendar import session_from
from .why import substantial, why_line
from .workouts import to_garmin

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent


DRAFT_LINE = ("Draft: the coach plans this day the evening before and checks it that morning "
              "with your sleep, recovery and notes.")
COACH_ACTIVE_DAYS = 14  # if the coach put workouts in your calendar recently, an empty day was a rest day


def week_target(acts, d, cfg) -> dict:
    """Recomputed on every run from the latest activities and config."""
    return compute_week_target(acts, d, cfg)


def _time(cfg: dict, key: str, default: str) -> dt.time:
    raw = str((cfg.get("garmin") or {}).get(key, default))
    h, m = (int(x) for x in raw.split(":"))
    return dt.time(h, m)


def sleep_deadline(cfg: dict) -> dt.time:
    return _time(cfg, "wait_for_sleep_until", "10:00")


def morning_check(cfg: dict) -> dt.time:
    return _time(cfg, "morning_check", "08:00")


def evening_plan(cfg: dict) -> dt.time:
    return _time(cfg, "evening_plan", "21:00")


def scheduled(api, cfg: dict, now: dt.datetime, upload: bool = True, **kw) -> dict:
    """What a scheduled run does, by local time: nothing before the morning check;
    the morning check of today's plan until the evening; after the evening plan
    time, tomorrow's plan (once)."""
    if now.time() < morning_check(cfg):
        # GitHub sometimes runs the evening schedule hours late, after midnight: if
        # today wasn't planned the evening before, plan it now (the morning check revisits it)
        if now.hour < 4 and not coach_plan_for(api, cfg, now.date(), kw.get("notes_api")):
            return run_day(api, cfg, now.date(), upload=upload, now=now, evening=True, **kw)
        return {"status": "early", "report": f"Too early: the morning check starts at {morning_check(cfg):%H:%M}.\n"}
    if now.time() >= evening_plan(cfg):
        tomorrow = now.date() + dt.timedelta(days=1)
        done = coach_plan_for(api, cfg, tomorrow, kw.get("notes_api"))
        # a note written after tomorrow was planned ("tomorrow I want to swim and run") means plan it again
        fresh_notes = new_notes_since_evening_plan(cfg, now, done, tomorrow, kw.get("notes_api")) if done else []
        if done and not fresh_notes:
            return {"status": "done", "report": f"Tomorrow is already planned ({', '.join(e['name'] for e in done)}).\n"}
        return run_day(api, cfg, tomorrow, upload=upload, now=now, evening=True, **kw)
    return run_day(api, cfg, now.date(), upload=upload, now=now, wait_for_sleep=True, **kw)


def new_notes_since_evening_plan(cfg: dict, now: dt.datetime, plan: list[dict], day: dt.date,
                                 notes_api=None) -> list[dict]:
    """Open notes written after `day` was planned the evening before (their time is in the
    workout's footer). A plan without a readable time counts as up to date."""
    times = [e["meta"]["eve_at"] for e in plan if e["meta"].get("eve_at")]
    if not times:
        return []
    since = f"{(day - dt.timedelta(days=1)).isoformat()}T{max(times)}"
    return [n for n in recent_notes(cfg, now, api=notes_api) if not n["done"] and n["created"] >= since]


def _asked_sports(plan: list[dict], phase) -> list[str]:
    """The sports of a day already in the calendar when it holds a swim you asked for
    (a swim without "(optional)" outside the triathlon block), else []."""
    for e in plan:                    # recorded in the workout's footer when you chose them
        if e["meta"].get("asked"):
            return list(e["meta"]["asked"])
    if phase.focus == "tri" or not any(e["session"].get("sport") == "swim" and not e["session"].get("optional")
                                       for e in plan):
        return []
    out: list[str] = []
    for e in plan:
        sport = e["session"].get("sport")
        if sport in ("run", "bike", "swim", "strength") and sport not in out:
            out.append(sport)
    return out


def _notes_line(sc: dict) -> str:
    """One line for the notification about your notes: what was understood, or that they
    couldn't be read (so you know the plan may not include them)."""
    if sc.get("error"):
        return "⚠️ I couldn't read your notes just now, so this plan may not include them."
    return sc.get("summary") or ""


def _requested_sports(cfg: dict, d: dt.date) -> list[str]:
    """The sports your notes ask for on day d ("tomorrow swim and run", "I can run Thursday")."""
    c = cfg.get("_constraints") or {}
    out: list[str] = []
    for e in c.get("day_plan") or []:
        if e.get("date") == d:
            out += [x for x in e.get("sports", []) if x != "rest"]
    for kind, day in c.get("moves") or []:
        if day == d:
            out.append(kind.split("_")[0])
    return list(dict.fromkeys(out))


def last_check(cfg: dict, d: dt.date, today_cal: list[dict], notes_api=None) -> tuple[str | None, int | None]:
    """When today's plan was last checked ("HH:MM") and how many of today's activities
    it had seen: from the workouts' footers and today's Todoist plan task (kept up to
    date even when nothing is re-uploaded). The latest check wins."""
    metas = [e["meta"] for e in today_cal if e["meta"].get("at")]
    marker = rest_day_marker(cfg, d, api=notes_api)
    if marker and marker["meta"].get("at"):
        metas.append(marker["meta"])
    if not metas:
        return None, None
    last = max(metas, key=lambda m: m["at"])
    return last["at"], last.get("seen")


def todays_training(acts: list[dict], d: dt.date) -> list[dict]:
    return [a for a in acts if a["date"] == d.isoformat() and is_training(a)]


def trained_since(api, d: dt.date, at: str | None, seen: int | None, strava_http=None) -> list[str]:
    """Training today (Garmin or Strava) the last check didn't see: more activities
    than it counted (a watch synced late still counts), or, for checks that didn't
    count them, ones that ended after it."""
    if not at:
        return []
    from . import strava as S
    acts = S.merge(G.fetch_activities(api, d, d), S.fetch(d, d, **({"http": strava_http} if strava_http else {})))[0]
    acts = todays_training(acts, d)
    if seen is not None:
        new = acts[seen:] if len(acts) > seen else []
    else:
        since = dt.datetime.combine(d, dt.time.fromisoformat(at))
        new = [a for a in acts if a.get("start") and dt.datetime.fromisoformat(a["start"])
               + dt.timedelta(minutes=a.get("duration_min") or 0) > since]
    return [f"{a.get('name') or a['sport']} ({a.get('duration_min', 0):.0f}')" for a in new]


def coach_plan_for(api, cfg: dict, day: dt.date, notes_api=None) -> list[dict]:
    """The coach's plan for `day`: its workouts in Garmin, or a rest day (no workout;
    read back from its Todoist plan task)."""
    found = Calendar(api, cfg).day(day)
    if found:
        return found
    rest = rest_day_marker(cfg, day, api=notes_api)
    return [{"name": rest["title"], "workoutId": None, "scheduleId": None, "meta": rest["meta"],
             "session": session_from(rest["title"], rest["meta"])}] if rest else []


def planned_on(day: dt.date, calendar: Calendar, active: bool, acts: list[dict], cfg: dict) -> list[dict]:
    """What the coach planned for a past day: read from your Garmin calendar.
    Days before the coach was used (or never uploaded) fall back to the base plan."""
    found = calendar.plan(day) or calendar.plan(day, draft=True)   # a draft if it never planned that day
    if found:
        return [e["session"] for e in found]
    if active:
        return []    # nothing in the calendar on a day the coach was running: a rest day
    wt = compute_week_target([a for a in acts if a["date"] < day.isoformat()], day, cfg)
    return [s.to_dict() for s in plan_day(day, cfg, wt)[1]]


def run_day(api, cfg: dict, d: dt.date, upload: bool = True, wait_for_sleep: bool = False,
            now: dt.datetime | None = None, notes_api=None, ai_client=None, poster=None,
            evening: bool = False, strava_http=None, keep_if_unchanged: bool = False, **_ignored) -> dict:
    """Plans day `d`.

    `evening`: d is tomorrow, planned the evening before from your training
    load and what you did today (no sleep data yet, no AI). It becomes the
    real workout in your calendar, and the morning check revisits it.

    Morning (`wait_for_sleep`, scheduled runs): checks today's plan with last
    night's sleep, HRV and Body Battery and your notes. It does nothing if
    today was already checked, and waits (returns without planning) while
    last night's sleep hasn't reached Garmin yet, until the configured deadline.

    `keep_if_unchanged` (manual runs): like the morning check, leave a plan that was already
    checked as it is unless there's a new note or new training since.

    Everything it knows about earlier runs comes from your Garmin calendar."""
    now = now or dt.datetime.now()
    deadline = sleep_deadline(cfg)
    calendar = Calendar(api, cfg)
    today_cal = calendar.day(d) or coach_plan_for(api, cfg, d, notes_api)       # a rest day lives in Todoist
    checked = bool(today_cal) and all(e["meta"].get("at") and not e["meta"].get("eve") for e in today_cal)
    planned_sleep = all(e["meta"].get("sr", 1) for e in today_cal)
    # your own notes for today (Todoist); a new one after planning means re-plan
    notes_today = recent_notes(cfg, now, api=notes_api)          # last 14 h, open and done
    open_notes = [n for n in notes_today if not n["done"]]
    new_notes = [n for n in open_notes if _added_after(n, today_cal, d)]
    if (wait_for_sleep or keep_if_unchanged) and checked and planned_sleep and not new_notes:
        # checked already: during the day, look again only if you've trained since
        trained = trained_since(api, d, *last_check(cfg, d, today_cal, notes_api), strava_http=strava_http)
        if not trained:
            msg = (f"Today's workout was already checked ({', '.join(e['name'] for e in today_cal)}); "
                   "no new training or notes since.")
            return {"status": "done", "report": msg + "\n"}
        log.info("New training since the last check (%s): checking the plan again.", ", ".join(trained))

    wellness = G.fetch_wellness(api, d - dt.timedelta(days=1) if evening else d)
    sleep_ready = evening or wellness.get("sleep_hours") is not None or wellness.get("sleep_score") is not None
    if wait_for_sleep and checked and not sleep_ready and not new_notes:
        # planned at the deadline without sleep data, and it still hasn't arrived
        return {"status": "done", "report": "Today's workout is already in your Garmin calendar.\n"}
    # Steps today mean you're up and wearing the watch: if it still has no sleep
    # for last night, it didn't record one (not worn, or another watch) — don't wait.
    awake = evening or (wellness.get("steps_today") or 0) >= 500
    if wait_for_sleep and not sleep_ready and not awake and now.time() < deadline:
        msg = (f"Last night's sleep isn't in Garmin yet (watch not synced, or still asleep). "
               f"Trying again next hour; planning anyway at {deadline:%H:%M} at the latest.")
        return {"status": "waiting", "report": msg + "\n"}

    since = history_start(d)                 # one window for every look back (see planner.LOOKBACK_WEEKS)
    acts = G.fetch_activities(api, since, d)
    G.add_swim_details(api, acts, d - dt.timedelta(days=28))     # swim intervals and rests
    # rides that reach Strava but not Garmin (MyWhoosh, Zwift…), if Strava is connected
    from . import strava as S
    acts, from_strava = S.merge(acts, S.fetch(since, d, **({"http": strava_http} if strava_http else {})))
    seen_today = len(todays_training(acts, d))
    set_swim_capacity(swim_capacity(acts, d))
    from .strength import set_loads
    set_loads(cfg)
    from .planner import set_venues
    set_venues(cfg)
    past = [a for a in acts if a["date"] < d.isoformat()]

    # learn where you actually do your long run/ride (3 of the last 4 weeks)
    learned = {}
    for sport in ("run", "bike"):
        day = learn_long_day(past, d, sport)
        if day:
            learned[sport] = day
    configured = cfg
    cfg = dict(cfg, _learned_days=learned)

    # your notes about the coming days ("can't train Sunday", "long run Saturday", "no swims this week")
    sched: dict = {}
    prefs_id, prefs = read_preferences(cfg, api=notes_api)
    if ai_client is not None or ai.enabled(cfg):
        earlier = [{"created": e["date"], "text": e["text"]}
                   for e in diary(cfg, d - dt.timedelta(days=7), d, now, api=notes_api)]
        earlier += [{"created": n["created"][:10], "text": n["text"]} for n in notes_today if n["done"]]
        if open_notes or earlier:
            sched = ai.schedule_changes(cfg, {
                "today": d.isoformat(), "weekday": d.strftime("%A"),
                "until": (d + dt.timedelta(days=13)).isoformat(),
                "open_notes": [{"id": n["id"], "created": n["created"], "text": n["text"]} for n in open_notes],
                "earlier_notes": earlier,
                "current_preferences": prefs,
            }, client=ai_client, prov=getattr(ai_client, "provider", None))
            new_prefs = sched.get("preferences")
            if new_prefs is not None and "error" not in sched and new_prefs != prefs and upload:
                if save_preferences(cfg, new_prefs, prefs_id, api=notes_api):
                    sched["prefs_saved"] = [x for x in new_prefs if x not in prefs]
                prefs = new_prefs
            if (sched.get("unavailable") or sched.get("moves") or sched.get("no_swim_until")
                    or sched.get("day_plan")):
                cfg = dict(cfg, _constraints=sched, _today=d)

    wt = week_target(past, d, cfg)
    # today's training paces, from Garmin's race predictor (your current fitness)
    pace_set = P.estimate(cfg, G.fetch_race_predictions(api), wt.get("chronic_km"))
    P.set_current(pace_set)
    week_km = wt["target_km"]
    ret = wt.get("return_mode", False)
    c = dict(cfg.get("_constraints") or {})
    new_choice = next((e["sports"] for e in c.get("day_plan") or [] if e.get("date") == d), None)
    if new_choice is None:
        # no new note about this day: the sports you chose earlier (kept in the calendar) still stand
        kept = _asked_sports(today_cal, phase_for(d, cfg))
        if kept:
            c["day_plan"] = list(c.get("day_plan") or []) + [{"date": d, "sports": kept}]
            cfg = dict(cfg, _constraints=c, _today=d)
            new_choice = kept
    asked = new_choice or []
    phase, planned = plan_day(d, cfg, wt)
    _, tomorrow = plan_day(d + dt.timedelta(days=1), cfg, wt)

    # what the coach planned on earlier days, from your Garmin calendar
    active = calendar.active_since(d - dt.timedelta(days=COACH_ACTIVE_DAYS), d - dt.timedelta(days=1))

    # ── the week as a whole: key sessions you've already done since Monday aren't repeated ──
    _, tpl, dist, bdist = week_parts(d, cfg, wt)
    quality_days = set()
    cur = monday_of(d)
    while cur < d:
        if any(x.get("kind") == "run_quality" for x in planned_on(cur, calendar, active, acts, cfg)):
            quality_days.add(cur.isoformat())
        cur += dt.timedelta(days=1)
    led = W.ledger(past, d, tpl, dist, bdist, quality_ok=not ret and phase.name != "transition",
                   quality_days=quality_days)
    for_today, week_notes, week_why = W.apply(planned, led, phase, dist)
    for_today, more, b2b_why = W.no_back_to_back_runs(for_today, d, acts, cfg)
    week_notes, week_why = week_notes + more, week_why or b2b_why

    # a long run done early counts as the week's long run, moved to that day
    moved = {}
    if led["long_run"]:
        a = led["long_run"]
        moved[a["date"]] = {"kind": "run_long", "sport": "run", "title": "Long run (moved)",
                            "intensity": "moderate", "distance_km": a["distance_km"], "duration_min": None}

    def planned_for(day: dt.date) -> list[dict]:
        plan = planned_on(day, calendar, active, acts, cfg)
        extra = moved.get(day.isoformat())
        if extra and not any(x.get("kind") == "run_long" for x in plan):
            plan = [x for x in plan if x.get("sport") != "run"] + [extra]
        return plan

    # ── yesterday's compliance ──
    yday = d - dt.timedelta(days=1)
    y_planned = planned_for(yday)
    y_done = [a for a in acts if a["date"] == yday.isoformat()]
    comp = compliance(y_planned, y_done)

    # ── current week ──
    mon = monday_of(d)
    week_done = weekly_run_km([a for a in past], mon)
    planned_so_far = 0.0
    cur = mon
    while cur < d:
        planned_so_far += sum(s.get("distance_km") or 0 for s in planned_for(cur) if s.get("sport") == "run")
        cur += dt.timedelta(days=1)
    week_over = planned_so_far > 0 and week_done > planned_so_far * 1.15 and week_done - planned_so_far >= 4

    swims_14d = sum(1 for a in past if a["sport"] == "swim" and a["date"] >= (d - dt.timedelta(days=14)).isoformat())

    ctx = {
        "compliance": comp,
        "yesterday_acts": y_done,
        "today_acts": [a for a in acts if a["date"] == d.isoformat()],
        "acwr_own": own_acwr(past, d),
        "consecutive_days": consecutive_days(past, d),
        "recent_load": recent_load(past, [a for a in acts if a["date"] == d.isoformat()], d),
        "week_over": week_over, "week_done": week_done, "week_planned_so_far": round(planned_so_far, 1),
        "swims_14d": swims_14d,
        "swim_target_week": sum(k.startswith("swim_") for day in template_for(phase, cfg).values() for k in day),
        "dist": dist,
        "week_idx": week_index(d, cfg),
        "quality_owed": led["quality_owed"], "hard_yesterday": led["hard_yesterday"],
        "this_week": W.summary(led),
        "swim_capacity": {k: v for k, v in swim_capacity(acts, d).items()},
        "paces": {k: P.fmt(pace_set[k]) for k in ("5k", "10k", "thr", "hm", "mp")} | {"source": pace_set["source"]},
    }
    # Readiness is a morning measure: how recovered you woke up. Later checks in
    # the day use the same waking values (Body Battery and Training Readiness on
    # waking, last 4 days without today), so the score doesn't fall just because
    # the day has gone on or you've already trained.
    wellness = dict(wellness, after_training=bool(ctx["today_acts"]))
    morning_ctx = dict(ctx, recent_load=recent_load(past, [], d))
    r = evening_readiness(ctx) if evening else compute(wellness, morning_ctx)
    final, notes = adjust(for_today, r, ctx, phase, tomorrow)
    notes = week_notes + notes
    kind, sport = key_session(phase)
    was, now_day = key_day(template_for(phase, configured), kind), key_day(template_for(phase, cfg), kind)
    if was is not None and now_day is not None and was != now_day:
        what = "long ride" if sport == "bike" else "long run"
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        notes.append(f"Your {what} has been on {names[now_day]} in most of the last 4 weeks, so the plan puts it "
                     f"there instead of {names[was]} (the rest of the week moves with it).")
    final = account_for_today(final, ctx["today_acts"], phase, notes)
    why = why_line(final, planned, r, wellness, ctx, wt, phase)
    if week_why and r["level"] == "green" and len(notes) == len(week_notes):
        why = week_why       # the only change today comes from what you've already done this week

    # ── AI coach: final call within the rule-based limits ──
    coach = {"used": False, "configured": ai_client is not None or ai.enabled(cfg), "evening": evening}
    history: list[dict] = []
    if coach["configured"] and final:     # evening: without sleep data; the morning check revisits it
        # notes already marked done (by the coach earlier, or by you) are context, not new input
        earlier = [{"created": n["created"], "text": n["text"]} for n in notes_today if n["done"]]
        history = notes_history(diary(cfg, since, d, now, api=notes_api), calendar, acts, d)
        preview_n = max(int((cfg.get("garmin") or {}).get("preview_days", 7)), 6)
        context = ai.build_context(d, phase, wt, wellness, r, ctx, final, planned,
                                   [{k: n[k] for k in ("id", "created", "text")} for n in open_notes],
                                   acts, notes, why, earlier, history,
                                   season=ai.season_context(d, cfg),
                                   coming_days=W.drafts(d, cfg, acts, final, preview_n, quality_days),
                                   planning="tomorrow" if evening else "today", preferences=prefs,
                                   asked_today=_requested_sports(cfg, d))
        coach["context"] = context
        coach.update(ai.decide(cfg, context, final, phase, week_index(d, cfg), client=ai_client,
                               prov=getattr(ai_client, "provider", None)))
        if coach.get("used"):
            final, why = coach["sessions"], coach["why"] or why
            notes += [f"Safety limit: {x}." for x in coach.get("limited", [])]
    final = ride_rules(final, cfg, phase, notes)
    final, more, b2b_why = W.no_back_to_back_runs(final, d, acts, cfg)     # also after the AI coach
    notes += more
    why = why or b2b_why
    coach["notes_today"] = notes_today
    coach["history_count"] = len(history) if coach.get("used") else 0
    used_ids = set(coach.get("notes_used") or []) | set(sched.get("notes_used") or [])
    used = [n for n in open_notes if n["id"] in used_ids]
    coach["schedule"] = sched

    # ── Garmin ──
    # Planned sessions you've already done today stay as they are: what's done is
    # history. Only what's still to do is replaced.
    done_acts = [a for a in ctx["today_acts"] if is_training(a)]
    done_sports = {a["sport"] for a in done_acts}
    kept = [e for e in today_cal if e["session"].get("sport") in done_sports]
    uploads = []
    if upload:
        G.remove_uploaded(api, [e for e in today_cal if e not in kept])
        uploads += [dict(e, kept=True) for e in kept]
        if not plan_tasks_on(cfg, notes_api):
            # without Todoist a rest day still needs a workout: it's how the coach knows it planned it
            for s in final:
                if s.kind == "rest" and not s.steps:
                    s.steps = [{"type": "interval", "end": "time", "value": 20 * 60, "zone": None}]
        for s in final:
            if not s.steps:
                continue
            body = _workout_description(s, r, [] if coach.get("used") else notes, why, coach.get("coach_note", ""))
            desc = fit(body, footer(phase.describe(), now, sleep_ready, evening, seen_today, asked))
            try:
                uploads.append(G.upload_and_schedule(api, to_garmin(s, cfg, desc), d.isoformat()))
            except Exception as e:  # noqa: BLE001
                if s.sport == "strength":      # if Garmin refuses a strength workout, send it as "other"
                    try:
                        w = to_garmin(s, cfg, desc)
                        w["sportType"] = w["workoutSegments"][0]["sportType"] = \
                            {"sportTypeId": 3, "sportTypeKey": "other", "displayOrder": 3}
                        uploads.append(G.upload_and_schedule(api, w, d.isoformat()))
                        continue
                    except Exception as e2:  # noqa: BLE001
                        e = e2
                log.error("Could not upload '%s' to Garmin: %s", s.title, e)
    else:
        uploads = today_cal

    # what you've already done today, shown as ✓ next to what's left
    # the plan shows what you were meant to train today, as planned (what you did is in Garmin)
    done_today_titles = [e["session"]["title"] for e in kept]

    # ── the days ahead, as drafts in your Garmin calendar (replaced each morning) ──
    # always a week ahead from today: in the evening, tomorrow is real and the rest are drafts
    preview = int((cfg.get("garmin") or {}).get("preview_days", 7))
    n_ahead = preview - (1 if evening else 0)
    ahead = W.drafts(d, cfg, acts, final, n_ahead, quality_days) if n_ahead > 0 else []
    # the drafts of the next days as they were, to tell you if they change
    old_drafts = ({day: calendar.plan(day, draft=True) for day, _, _, _ in ahead[:2]}
                  if today_cal and not evening else {})
    if upload:
        draft_cfg = dict(cfg, garmin=dict(cfg.get("garmin") or {},
                                          workout_prefix=(cfg.get("garmin") or {}).get("workout_prefix", "Coach") + " draft"))
        for i in range(0, max(preview, 7) + 2):
            G.remove_uploaded(api, calendar.plan(d + dt.timedelta(days=i), draft=True))
            # only the day being planned has a real workout: anything else ahead without
            # the coach's footer is a stale copy (e.g. a whole week planned in advance)
            if i:
                G.remove_uploaded(api, [e for e in calendar.plan(d + dt.timedelta(days=i))
                                        if not e["meta"].get("at") and not e["meta"].get("eve")])
        for day, ph, sess, wnotes in ahead:
            for s in sess:
                if not s.steps:
                    continue
                body = "\n\n".join(x for x in (DRAFT_LINE, s.detail, " ".join(wnotes), ph.describe()) if x)
                try:
                    G.upload_and_schedule(api, to_garmin(s, draft_cfg, fit(body, "")), day.isoformat())
                except Exception as e:  # noqa: BLE001
                    log.error("Could not upload the draft for %s to Garmin: %s", day, e)

    # the coming days as tasks in Todoist, to see the week at a glance
    if upload:
        coach["plan_tasks"] = sync_plan(cfg, [(d, final, why, done_today_titles)] + [(day, sess, (wn or [""])[0])
                                                                 for day, _, sess, wn in ahead], api=notes_api,
                                       # when it was planned/checked (rest days have no workout to say so)
                                       footer=footer(phase.describe(), now, sleep_ready, evening, seen_today, asked))

    # notes the coach used: label them for your diary and complete them in Todoist (real runs only)
    if upload and used:
        coach["completed"] = complete_notes([n["id"] for n in used], api=notes_api,
                                            recurring={n["id"] for n in used if n.get("recurring")},
                                            labels={n["id"]: n.get("labels", []) for n in used},
                                            label=diary_label(cfg))

    # a push to your phone (and so your watch) with the plan, if NTFY_TOPIC is set
    ahead_changed = bool(today_cal) and not evening and any(
        sorted(x["session"]["title"] for x in old_drafts.get(day, [])) != sorted(s.title for s in sess if s.steps)
        for day, _, sess, _ in ahead[:2])
    before = sorted(e["session"]["title"] for e in today_cal if e not in kept)     # what's done isn't a change
    after = sorted(s.title for s in final if s.steps)
    coach["unchanged"] = bool(today_cal) and before == after
    if evening:
        label = "Tomorrow"
    elif not today_cal:
        label = "Today"
    elif not coach["unchanged"] or ahead_changed:
        label = "Changed" if any(e["meta"].get("eve") for e in today_cal) else "Updated"
    else:   # the morning check keeps last night's plan: no need to buzz your wrist (manual runs still do)
        label = None if wait_for_sleep else "Today"
    coach["notified"] = bool(upload and label and send(*message(final, why, coach.get("coach_note", ""), r, label,
                                                               ahead if evening else ahead[:2],
                                                               done=done_today_titles,
                                                               notes_line=_notes_line(coach.get("schedule") or {})),
                                         poster=poster))
    coach["was_evening_plan"] = any(e["meta"].get("eve") for e in today_cal)

    report = build_report(d, phase, wt, wellness, r, comp, ctx, planned, final, notes, uploads, upload, why,
                          coach, cfg, ahead, evening)
    report += sync_note(wellness.get("last_sync"), now)
    recent_strava = [a for a in from_strava if a["date"] >= (d - dt.timedelta(days=7)).isoformat()]
    if recent_strava:
        report += ("\n> Only in Strava (not in Garmin), counted as training: "
                   + "; ".join(f"{dt.date.fromisoformat(a['date']):%a} {a['name']} ({a['duration_min']:.0f}')"
                               for a in recent_strava) + "\n")
    if coach.get("context"):
        # exactly what the AI coach was sent, folded away at the end of the run summary
        report += ("\n<details><summary>What the AI coach saw</summary>\n\n```json\n"
                   + json.dumps(coach["context"], indent=1, ensure_ascii=False, default=str)[:60000]
                   + "\n```\n</details>\n")
    if not sleep_ready:
        no_sleep = ("your watch shows you're up but recorded no sleep last night (not worn, or another watch)"
                    if awake else "no sleep data from last night reached Garmin")
        report += (f"\n> Planned without sleep data: {no_sleep}. Readiness uses Body Battery, resting HR "
                   "and training load instead. If last night's sleep shows up later, press *Run workflow* "
                   "to re-plan with it.\n")
    if upload and not os.environ.get("GITHUB_ACTIONS"):
        # local runs keep a copy; on GitHub the report is in the run summary
        rep_dir = ROOT / "reports"
        rep_dir.mkdir(exist_ok=True)
        (rep_dir / f"{d.isoformat()}.md").write_text(report, encoding="utf-8")
    return {"status": "planned", "report": report, "final": final, "readiness": r, "notes": notes}


def sync_note(last_sync: str | None, now: dt.datetime) -> str:
    if not last_sync:
        return ""
    synced = dt.datetime.fromisoformat(last_sync)
    hours = (now - synced).total_seconds() / 3600
    line = f"\n_Garmin data as of your watch's last sync: {synced:%a %H:%M}._\n"
    if hours > 2:
        line += (f"\n> ⚠️ Your watch last synced {hours:.0f} h ago, so anything since then (sleep, workouts, "
                 "Body Battery) isn't included. Sync it and press *Run workflow* to re-plan.\n")
    return line


def notes_history(entries: list[dict], calendar: Calendar, acts: list[dict], d: dt.date) -> list[dict]:
    """Your diary notes from previous weeks (from Todoist), each with what you
    trained around it (Garmin activities) and what the coach had planned (Garmin calendar)."""
    def trained(day: str) -> list[str]:
        return [f"{a['sport']} {a['distance_km']:g} km, {a['duration_min']:.0f}'"
                + (f", TE {a['aerobic_te']}" if a.get("aerobic_te") else "")
                for a in acts if a["date"] == day]

    out = []
    for e in entries:
        if e["date"] >= d.isoformat():
            continue
        prev = (dt.date.fromisoformat(e["date"]) - dt.timedelta(days=1)).isoformat()
        plan = calendar.plan(dt.date.fromisoformat(e["date"]))
        out.append({"date": e["date"], "time": e.get("time"), "note": e["text"],
                    "trained_that_day": trained(e["date"]), "trained_day_before": trained(prev),
                    "readiness": next((p["meta"].get("r") for p in plan if p["meta"].get("r") is not None), None),
                    "coach_plan_that_day": [p["session"]["title"] for p in plan]})
    return out[-40:]


def coach_section(coach: dict, cfg: dict) -> list[str]:
    L = ["", "## Coach"]
    if coach.get("evening") and not coach.get("used"):
        L.append("- Rules only tonight: " + ("the AI coach didn't answer; the morning check tries again."
                                             if coach.get("configured") else "the morning check adds your sleep data."))
        return L
    if coach.get("used"):
        L.append(f"- Decided by the AI coach ({coach.get('model')}), within the plan's safety limits.")
        if coach.get("coach_note"):
            L.append(f"- {coach['coach_note']}")
    elif coach.get("configured"):
        L.append(f"- AI coach unavailable today ({coach.get('error', 'no answer')}): rule-based plan used.")
    else:
        L.append("- Rule-based plan (add an OPENAI_API_KEY or ANTHROPIC_API_KEY to let the AI coach decide).")
    if coach.get("history_count"):
        L.append(f"- Coach memory: {coach['history_count']} of your notes from the last weeks were considered.")
    notes = coach.get("notes_today") or []
    if notes:
        used_ids = set(coach.get("notes_used") or [])
        hours = (cfg.get("todoist") or {}).get("lookback_hours", 14)
        L.append(f"- Your notes (last {hours:g} h): " + "; ".join(
            f"“{n['text']}” ({n['time']}{', already done' if n.get('done') else ''})" for n in notes))
        if coach.get("used"):
            used = [n for n in notes if n["id"] in used_ids]
            L.append("- Taken into account: " + ("; ".join(f"“{n['text']}”" for n in used) if used
                                                  else "none were about training"))
            if coach.get("completed"):
                L.append(f"- Completed in Todoist: {len(coach['completed'])} note(s) (they're in your completed tasks).")
    return L


def ride_rules(final: list[Session], cfg: dict, phase, notes: list[str]) -> list[Session]:
    """No ride shorter than `athlete.min_ride_minutes` (default 30): shorter ones
    aren't worth getting on the bike for, so they become that long."""
    from .planner import make_bike
    min_ride = float((cfg.get("athlete") or {}).get("min_ride_minutes", 30) or 0)
    out = []
    for s in final:
        if s.sport == "bike" and s.duration_min and s.duration_min < min_ride:
            notes.append(f"{s.title} → {min_ride:g}': shorter rides aren't worth getting on the bike for.")
            s = make_bike(s.kind, phase, min_ride)
        out.append(s)
    return out


def account_for_today(final: list[Session], done_today: list[dict], phase, notes: list[str]) -> list[Session]:
    """Takes into account what you've already trained today (when planning later in the day)."""
    from .planner import make_run
    if not done_today:
        return final
    kept = []
    for s in final:
        same = [a for a in done_today if a["sport"] == s.sport and s.sport in ("run", "bike", "swim")
                and is_training(a)]                     # a short ride to the pool isn't the planned ride
        if same:
            a = same[0]
            notes.append(f"Already done today: {a['name']} ({a['distance_km']:g} km, {a['duration_min']:.0f}'), "
                         f"so “{s.title}” isn't added to your calendar.")
            continue
        kept.append(s)
    big = [a for a in done_today if substantial(a)]
    out = []
    for s in kept:
        other = [a for a in big if a["sport"] != s.sport]
        if s.sport == "run" and other:
            a = other[0]
            if s.kind == "run_quality":
                s = make_run("run_easy", (s.distance_km or 8) * 0.85, phase)
                notes.append(f"You already did {a['name']} today ({a['duration_min']:.0f}'), "
                             "so today's quality run becomes an easy run.")
            else:
                notes.append(f"You already did {a['name']} today ({a['duration_min']:.0f}'): keep this run easy, in Z2.")
        out.append(s)
    return out


def _added_after(note: dict, today_cal: list[dict], d: dt.date) -> bool:
    """Whether an open note was written after today's workout was planned."""
    if not today_cal:
        return True
    times = [e["meta"]["at"] for e in today_cal if e["meta"].get("at")]
    if times:
        return note["created"] >= f"{d.isoformat()}T{max(times)}"
    seen = {i for e in today_cal for i in e["meta"].get("n", [])}   # workouts from before the footer
    return bool(seen) and note["id"] not in seen


def _workout_description(s: Session, r: dict, notes: list[str], why: str = "", coach_note: str = "") -> str:
    """What you read on the watch and in Garmin Connect: the why first, then
    the advice, the session, readiness with its main reasons, and the rules'
    changes (only when no AI coach made the final call)."""
    blocks = [why, coach_note, s.detail]
    if r["score"] is not None:        # the evening plan has no sleep data yet: the morning check adds readiness
        level = r["label"][2:].split(":")[0].strip()
        blocks.append("\n".join([f"Readiness {r['score']}/100 · {level}"] + [f"• {x}" for x in r["reasons"][:3]]))
    if notes:
        blocks.append("\n".join(["Changes:"] + [f"• {n}" for n in notes[:3]]))
    return "\n\n".join(b.strip() for b in blocks if b and b.strip())


def build_report(d, phase, wt, w, r, comp, ctx, planned, final, notes, uploads, upload, why="",
                 coach: dict | None = None, cfg: dict | None = None, ahead: list | None = None,
                 evening: bool = False) -> str:
    L = [f"# Training for {d.strftime('%A %d %b %Y')}", ""]
    if evening:
        L += ["_Planned the evening before, from your training load and what you did today. "
              "The morning check adjusts it to your sleep, HRV and Body Battery, and tells you if it changes._", ""]
    if why:
        L += [f"**{why}**", ""]
    L += [
         f"**Phase:** {phase.describe()}  ",
         f"**Weekly running target:** {wt['target_km']} km "
         f"(last 4 weeks average {wt['chronic_km']} km, last week {wt['last_week_km']} km)  ",
         f"**Run so far this week:** {ctx['week_done']} km  "]
    if phase.focus == "bike":
        L.append(f"**Riding this week:** {wt.get('bike_hours')} h target "
                 f"(4-week average {wt.get('bike_chronic_hours')} h, longest recent ride "
                 f"{wt.get('longest_ride_min')}', typical speed {wt.get('ride_speed_kmh')} km/h)  ")
    L.append("")
    if wt.get("return_mode"):
        L.insert(-1, "**Return-to-running mode:** volume grows ~20 %/week from 10 km and there are "
                     "no hard sessions until you average 10+ km/week.  ")
    if wt.get("ramp_km") and wt["ramp_km"] < wt["target_km"]:
        L.insert(-1, f"**Week raised from {wt['ramp_km']:g} to {wt['target_km']:g} km** so no run is shorter than "
                     f"your usual run lately ({wt.get('typical_run_km', 0):g} km) or the long-run minimum.  ")
    if r["score"] is None:
        L += ["## Readiness: checked in the morning", ""]
    else:
        L += [f"## Readiness: {r['score']}/100 — {r['label']}", ""]
    for k, v in r["components"].items():
        L.append(f"- {k}: {v['value']} (scores {v['score']}/100)")
    if ctx.get("acwr_own"):
        L.append(f"- Acute/chronic load (computed): {ctx['acwr_own']}")
    if w.get("training_status"):
        L.append(f"- Garmin training status: {w['training_status']}")
    if r["reasons"]:
        L += ["", "**Details:**"] + [f"- {x}" for x in r["reasons"]]

    L += ["", "## Today so far" if evening else "## Yesterday",
          f"- Planned: {' + '.join(comp.get('planned_titles') or []) or 'rest'}",
          f"- Done: {', '.join(comp['done_titles']) or 'nothing recorded'}"]
    if comp["missed"]:
        L.append(f"- Skipped: {', '.join(comp['missed'])}")

    L += ["", "## Tomorrow" if evening else "## Today"]
    if ctx.get("today_acts"):
        L.append("- Done today so far: " + ", ".join(
            f"{a['name']} ({round(a['distance_km']) if a['distance_km'] >= 10 else a['distance_km']:g} km, "
            f"{a['duration_min']:.0f}')" for a in ctx["today_acts"]))
    if [s.title for s in planned] != [s.title for s in final]:
        L.append(f"- Original plan: {' + '.join(s.title for s in planned)}")
    for s in final:
        star = " ⭐" if s.priority else ""
        L.append(f"- **{s.title}**{star}: {s.detail}")
    if not final:
        L.append("- Nothing left for today: you've already done it. 🎉")
    if notes:
        title = ("**What the rules proposed** (the AI coach made the final call):" if (coach or {}).get("used")
                 else "**Coach adjustments:**")
        L += ["", title] + [f"- {n}" for n in notes]
    if ctx.get("paces"):
        L += ["", f"**Your paces:** {P.summary()}"]
    if ctx.get("this_week"):
        L += ["", "**This week so far:** " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in ctx["this_week"].items())]
    if ahead:
        L += ["", "## Coming up (drafts, redrawn every evening)"]
        for day, _, sess, wnotes in ahead:
            L.append(f"- {day:%a %d/%m}: " + " + ".join(s.title for s in sess) + (f" — {wnotes[0]}" if wnotes else ""))
    sc = (coach or {}).get("schedule") or {}
    if sc.get("prefs_saved"):
        L += ["", "## Saved to your coach preferences"] + [f"- {x}" for x in sc["prefs_saved"]] + [
            "- (They're in the “📌 Coach preferences” task in your Todoist “Training plan” project: edit it there.)"]
    if sc.get("error"):
        L += ["", "## Your notes about the week", "- ⚠️ Couldn't read them this time (" + str(sc["error"])[:120] + ")."]
    if sc.get("unavailable") or sc.get("moves") or sc.get("no_swim_until") or sc.get("day_plan"):
        L += ["", "## Your notes about the week"]
        for e in sc.get("day_plan") or []:
            L.append(f"- {e['date']:%a %d/%m}: " + " + ".join(e["sports"]))
        if sc.get("summary"):
            L.append(f"- {sc['summary']}")
        if sc.get("unavailable"):
            L.append("- No training: " + ", ".join(f"{x:%a %d/%m}" for x in sorted(sc["unavailable"])))
        for kind, day in sc.get("moves") or []:
            L.append(f"- {kind.replace('run_', '').replace('bike_', 'bike ').replace('_', ' ')} on {day:%a %d/%m}")
        if sc.get("no_swim_until"):
            L.append(f"- No swims until {sc['no_swim_until']:%a %d/%m}")
    L += coach_section(coach or {}, cfg or {})
    L += ["", "## Garmin"]
    if upload:
        added = [u["name"] for u in uploads if not u.get("kept")]
        L.append(f"- Added to calendar: {', '.join(added) or 'nothing'}")
        done = [u["name"] for u in uploads if u.get("kept")]
        if done:
            L.append(f"- Kept as they were (already done today): {', '.join(done)}")
        if (coach or {}).get("was_evening_plan"):
            L.append("- Last night's plan still stands: no change after this morning's check."
                     if coach.get("unchanged") else "- Changed from last night's plan after this morning's check.")
        if ahead:
            L.append(f"- Drafts for the next {len(ahead)} days are in your calendar too.")
        if (coach or {}).get("plan_tasks"):
            L.append(f"- The next {coach['plan_tasks']} days are in your Todoist “Training plan” project.")
        if (coach or {}).get("notified"):
            L.append("- Sent to your phone and watch as a notification.")
    else:
        L.append("- Dry run: nothing was uploaded.")
    return "\n".join(L) + "\n"
