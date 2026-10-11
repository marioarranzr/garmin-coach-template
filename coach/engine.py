"""Orchestrates the day: data → plan → readiness → adjustment → Garmin → report."""
from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path

from . import garmin_api as G
from .adjust import adjust
from .planner import (Session, compute_week_target, history_start, key_day, key_session, learn_long_day, monday_of,
                      phase_for, plan_day,
                      planned_run_km, run_distribution, template_for, week_index, weekly_run_km)
from .readiness import compliance, compute, consecutive_days, own_acwr, recent_load
from . import ai
from .calendar import Calendar, marker, with_marker
from .notes import complete_notes, diary, diary_label, recent_notes
from .why import substantial, why_line
from .workouts import to_garmin

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent


COACH_ACTIVE_DAYS = 14  # if the coach put workouts in your calendar recently, an empty day was a rest day


def week_target(acts, d, cfg) -> dict:
    """Recomputed on every run from the latest activities and config."""
    return compute_week_target(acts, d, cfg)


def sleep_deadline(cfg: dict) -> dt.time:
    raw = str((cfg.get("garmin") or {}).get("wait_for_sleep_until", "10:00"))
    h, m = (int(x) for x in raw.split(":"))
    return dt.time(h, m)


def planned_on(day: dt.date, calendar: Calendar, active: bool, acts: list[dict], cfg: dict) -> list[dict]:
    """What the coach planned for a past day: read from your Garmin calendar.
    Days before the coach was used (or never uploaded) fall back to the base plan."""
    found = calendar.plan(day)
    if found:
        return [e["session"] for e in found]
    if active:
        return []    # nothing in the calendar on a day the coach was running: a rest day
    wt = compute_week_target([a for a in acts if a["date"] < day.isoformat()], day, cfg)
    return [s.to_dict() for s in plan_day(day, cfg, wt)[1]]


def run_day(api, cfg: dict, d: dt.date, upload: bool = True, wait_for_sleep: bool = False,
            now: dt.datetime | None = None, notes_api=None, ai_client=None, **_ignored) -> dict:
    """Plans day `d`. With `wait_for_sleep` (scheduled runs), it does nothing if
    today is already planned, and waits (returns without planning) while last
    night's sleep hasn't reached Garmin yet, until the configured deadline.
    Everything it knows about earlier runs comes from your Garmin calendar."""
    now = now or dt.datetime.now()
    deadline = sleep_deadline(cfg)
    calendar = Calendar(api, cfg)
    today_cal = calendar.day(d)
    seen_ids = {i for e in today_cal for i in e["meta"].get("n", [])}
    planned_sleep = all(e["meta"].get("sr", 1) for e in today_cal)
    # your own notes for today (Todoist); a new one after planning means re-plan
    notes_today = recent_notes(cfg, now, api=notes_api)          # last 12 h, open and done
    open_notes = [n for n in notes_today if not n["done"]]
    new_notes = [n for n in open_notes if n["id"] not in seen_ids]
    if wait_for_sleep and today_cal and planned_sleep and not new_notes:
        msg = f"Today's workout is already in your Garmin calendar ({', '.join(e['name'] for e in today_cal)})."
        return {"status": "done", "report": msg + "\n"}

    wellness = G.fetch_wellness(api, d)
    sleep_ready = wellness.get("sleep_hours") is not None or wellness.get("sleep_score") is not None
    if wait_for_sleep and today_cal and not sleep_ready and not new_notes:
        # planned at the deadline without sleep data, and it still hasn't arrived
        return {"status": "done", "report": "Today's workout is already in your Garmin calendar.\n"}
    # Steps today mean you're up and wearing the watch: if it still has no sleep
    # for last night, it didn't record one (not worn, or another watch) — don't wait.
    awake = (wellness.get("steps_today") or 0) >= 500
    if wait_for_sleep and not sleep_ready and not awake and now.time() < deadline:
        msg = (f"Last night's sleep isn't in Garmin yet (watch not synced, or still asleep). "
               f"Trying again next hour; planning anyway at {deadline:%H:%M} at the latest.")
        return {"status": "waiting", "report": msg + "\n"}

    since = history_start(d)                 # one window for every look back (see planner.LOOKBACK_WEEKS)
    acts = G.fetch_activities(api, since, d)
    past = [a for a in acts if a["date"] < d.isoformat()]

    # learn where you actually do your long run/ride (3 of the last 4 weeks)
    learned = {}
    for sport in ("run", "bike"):
        day = learn_long_day(past, d, sport)
        if day:
            learned[sport] = day
    configured = cfg
    cfg = dict(cfg, _learned_days=learned)

    wt = week_target(past, d, cfg)
    week_km = wt["target_km"]
    ret = wt.get("return_mode", False)
    phase, planned = plan_day(d, cfg, wt)
    _, tomorrow = plan_day(d + dt.timedelta(days=1), cfg, wt)

    # what the coach planned on earlier days, from your Garmin calendar
    active = calendar.active_since(d - dt.timedelta(days=COACH_ACTIVE_DAYS), d - dt.timedelta(days=1))

    # ── yesterday's compliance ──
    yday = d - dt.timedelta(days=1)
    y_planned = planned_on(yday, calendar, active, acts, cfg)
    y_done = [a for a in acts if a["date"] == yday.isoformat()]
    comp = compliance(y_planned, y_done)

    # ── current week ──
    mon = monday_of(d)
    week_done = weekly_run_km([a for a in past], mon)
    planned_so_far = 0.0
    cur = mon
    while cur < d:
        planned_so_far += sum(s.get("distance_km") or 0 for s in planned_on(cur, calendar, active, acts, cfg)
                              if s.get("sport") == "run")
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
        "dist": run_distribution(template_for(phase, cfg), week_km, phase, ret),
        "week_idx": week_index(d, cfg),
    }
    # Always judge readiness on the latest values Garmin has (first thing in the
    # morning they're the waking values; later in the day, how you are now).
    bb_now, tr_now = wellness.get("body_battery_now"), wellness.get("training_readiness_now")
    current = dict(wellness, after_training=bool(ctx["today_acts"]))
    if bb_now is not None:
        current["bb_is_now"] = wellness.get("body_battery_wake") is not None and bb_now != wellness["body_battery_wake"]
        current["body_battery_wake"] = bb_now
    if tr_now is not None:
        current["tr_is_now"] = wellness.get("training_readiness") is not None and tr_now != wellness["training_readiness"]
        current["training_readiness"] = tr_now
    wellness = current
    r = compute(wellness, ctx)
    final, notes = adjust(planned, r, ctx, phase, tomorrow)
    kind, sport = key_session(phase)
    was, now_day = key_day(template_for(phase, configured), kind), key_day(template_for(phase, cfg), kind)
    if was is not None and now_day is not None and was != now_day:
        what = "long ride" if sport == "bike" else "long run"
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        notes.append(f"Your {what} has been on {names[now_day]} in most of the last 4 weeks, so the plan puts it "
                     f"there instead of {names[was]} (the rest of the week moves with it).")
    final = account_for_today(final, ctx["today_acts"], phase, notes)
    why = why_line(final, planned, r, wellness, ctx, wt, phase)

    # ── AI coach: final call within the rule-based limits ──
    coach = {"used": False, "configured": ai_client is not None or ai.enabled(cfg)}
    history: list[dict] = []
    if coach["configured"] and final:
        # notes already marked done (by the coach earlier, or by you) are context, not new input
        earlier = [{"time": n["time"], "text": n["text"]} for n in notes_today if n["done"]]
        history = notes_history(diary(cfg, since, d, now, api=notes_api), calendar, acts, d)
        context = ai.build_context(d, phase, wt, wellness, r, ctx, final, planned,
                                   [{k: n[k] for k in ("id", "time", "text")} for n in open_notes],
                                   acts, notes, why, earlier, history)
        coach.update(ai.decide(cfg, context, final, phase, week_index(d, cfg), client=ai_client,
                               prov=getattr(ai_client, "provider", None)))
        if coach.get("used"):
            final, why = coach["sessions"], coach["why"] or why
            notes += [f"Safety limit: {x}." for x in coach.get("limited", [])]
    coach["notes_today"] = notes_today
    coach["history_count"] = len(history) if coach.get("used") else 0
    used = [n for n in open_notes if n["id"] in set(coach.get("notes_used") or [])]

    # ── Garmin ──
    uploads = []
    if upload:
        G.remove_uploaded(api, today_cal)
        for s in final:
            if not s.steps:
                continue
            tag = marker(s, {"r": r["score"], "sr": 0 if not sleep_ready else None,
                             "n": [n["id"] for n in open_notes], "u": [n["id"] for n in used]})
            desc = with_marker(_workout_description(s, r, notes, phase, why, coach.get("coach_note", "")), tag)
            try:
                uploads.append(G.upload_and_schedule(api, to_garmin(s, cfg, desc), d.isoformat()))
            except Exception as e:  # noqa: BLE001
                log.error("Could not upload '%s' to Garmin: %s", s.title, e)
    else:
        uploads = today_cal

    # notes the coach used: label them for your diary and complete them in Todoist (real runs only)
    if upload and used:
        coach["completed"] = complete_notes([n["id"] for n in used], api=notes_api,
                                            recurring={n["id"] for n in used if n.get("recurring")},
                                            labels={n["id"]: n.get("labels", []) for n in used},
                                            label=diary_label(cfg))

    report = build_report(d, phase, wt, wellness, r, comp, ctx, planned, final, notes, uploads, upload, why,
                          coach, cfg)
    report += sync_note(wellness.get("last_sync"), now)
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
        L.append("- Your notes (last 12 h): " + "; ".join(
            f"“{n['text']}” ({n['time']}{', already done' if n.get('done') else ''})" for n in notes))
        if coach.get("used"):
            used = [n for n in notes if n["id"] in used_ids]
            L.append("- Taken into account: " + ("; ".join(f"“{n['text']}”" for n in used) if used
                                                  else "none were about training"))
            if coach.get("completed"):
                L.append(f"- Completed in Todoist: {len(coach['completed'])} note(s) (they're in your completed tasks).")
    return L


def account_for_today(final: list[Session], done_today: list[dict], phase, notes: list[str]) -> list[Session]:
    """Takes into account what you've already trained today (when planning later in the day)."""
    from .planner import make_run
    if not done_today:
        return final
    kept = []
    for s in final:
        same = [a for a in done_today if a["sport"] == s.sport and s.sport in ("run", "bike", "swim")]
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


def _workout_description(s: Session, r: dict, notes: list[str], phase, why: str = "", coach_note: str = "") -> str:
    parts = ([why] if why else []) + ([coach_note] if coach_note else []) + \
            [s.detail, f"Readiness today: {r['score']}/100 – {r['label'][2:]}."]
    if r["reasons"]:
        parts.append("Details: " + " ".join(r["reasons"][:3]))
    if notes:
        parts.append("Changes: " + " ".join(notes[:3]))
    parts.append(f"Phase: {phase.describe()}.")
    return "\n".join(parts)


def build_report(d, phase, wt, w, r, comp, ctx, planned, final, notes, uploads, upload, why="",
                 coach: dict | None = None, cfg: dict | None = None) -> str:
    L = [f"# Training for {d.strftime('%A %d %b %Y')}", ""]
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
    L += [
         f"## Readiness: {r['score']}/100 — {r['label']}", ""]
    for k, v in r["components"].items():
        L.append(f"- {k}: {v['value']} (scores {v['score']}/100)")
    if ctx.get("acwr_own"):
        L.append(f"- Acute/chronic load (computed): {ctx['acwr_own']}")
    if w.get("training_status"):
        L.append(f"- Garmin training status: {w['training_status']}")
    if r["reasons"]:
        L += ["", "**Details:**"] + [f"- {x}" for x in r["reasons"]]

    L += ["", "## Yesterday", f"- Planned: {comp['run_planned']} km running",
          f"- Done: {', '.join(comp['done_titles']) or 'nothing recorded'}"]
    if comp["missed"]:
        L.append(f"- Skipped: {', '.join(comp['missed'])}")

    L += ["", "## Today"]
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
        L += ["", "**Coach adjustments:**"] + [f"- {n}" for n in notes]
    L += coach_section(coach or {}, cfg or {})
    L += ["", "## Garmin"]
    if upload:
        L.append(f"- Added to calendar: {', '.join(u['name'] for u in uploads) or 'nothing'}")
    else:
        L.append("- Dry run: nothing was uploaded.")
    return "\n".join(L) + "\n"
