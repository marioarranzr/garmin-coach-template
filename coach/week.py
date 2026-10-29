"""The week as a whole: what you've already done since Monday, and the days ahead.

Each morning the coach compares this week's key sessions in your template
(long run, quality run, swims, long ride, bike intervals) with what you've
actually done since Monday. A key session you've already done on another day
isn't repeated: if you did the long run on Saturday, Sunday becomes an easy run;
if you've already swum as many times as planned, a later swim is dropped. A
quality run you skipped earlier in the week can move to a later easy day (see
adjust.py) when you're recovered.

It also drafts the coming days, applying the same rules as if you follow the
plan, so your Garmin calendar shows the week ahead. Each morning the draft for
today is replaced by the real plan.
"""
from __future__ import annotations

import datetime as dt

from .planner import WEEKDAYS, Session, make_bike, make_rest, make_run, monday_of


def _hard(a: dict) -> bool:
    return (a.get("aerobic_te") or 0) >= 3.8 or (a.get("anaerobic_te") or 0) >= 2.5


def ledger(acts: list[dict], d: dt.date, tpl: dict, dist: dict, bdist: dict | None,
           quality_ok: bool = True, quality_days: set[str] = frozenset(), include_today: bool = False) -> dict:
    """What's been done this week before day d, against the week's template
    (`include_today`: also what's been done on d itself, for showing the week so far).
    `quality_ok` is False when the plan has no hard runs (return to running, transition).
    A run on one of `quality_days` (days the coach planned a quality run) counts as
    the quality session even if Garmin's Training Effect stayed moderate."""
    mon = monday_of(d)
    end = d + dt.timedelta(days=1) if include_today else d
    week = [a for a in acts if mon.isoformat() <= a["date"] < end.isoformat()]
    kinds = [k for day in WEEKDAYS for k in tpl.get(day, []) if quality_ok or k != "run_quality"]
    before = [k for day in WEEKDAYS[:d.weekday()] for k in tpl.get(day, []) if quality_ok or k != "run_quality"]
    runs = [a for a in week if a["sport"] == "run"]
    run_kinds = [k for day in WEEKDAYS[:d.weekday() + 1] for k in tpl.get(day, []) if k.startswith("run_")]
    rides = [a for a in week if a["sport"] == "bike" and a.get("duration_min", 0) >= 25]   # not commutes
    long_km = dist.get("long") or 0
    long_run = max(runs, key=lambda a: a["distance_km"], default=None)
    floor = max(0.85 * long_km, (dist.get("easy") or 0) + 1.5, 6)
    if not (long_km and long_run and long_run["distance_km"] >= floor):
        long_run = None
    long_min = (bdist or {}).get("long") or 120
    long_ride = max(rides, key=lambda a: a["duration_min"], default=None)
    if not (long_ride and long_ride["duration_min"] >= max(0.8 * long_min, 75)):
        long_ride = None
    hard_runs = [a for a in runs if (_hard(a) or a["date"] in quality_days) and a is not long_run]
    yday = (d - dt.timedelta(days=1)).isoformat()
    return {
        "long_run": long_run, "long_ride": long_ride,
        "hard_runs": len(hard_runs), "hard_rides": sum(_hard(a) for a in rides if a is not long_ride),
        "swims": sum(a["sport"] == "swim" for a in week),
        # runs so far this week vs the template's runs up to today: a run done on
        # another day (e.g. moved by your note) isn't run again
        "runs": sum(a["distance_km"] >= 2 for a in runs), "runs_due": len(run_kinds),
        "planned": {"run_long": kinds.count("run_long"), "run_quality": kinds.count("run_quality"),
                    "swims": sum(k.startswith("swim_") for k in kinds), "bike_long": kinds.count("bike_long"),
                    "bike_quality": kinds.count("bike_quality")},
        # a quality run planned earlier this week that hasn't happened
        "quality_owed": before.count("run_quality") > len(hard_runs),
        "hard_yesterday": any(a["date"] == yday and (_hard(a) or a is long_run) for a in week),
    }


def _day(a: dict | None) -> str:
    return dt.date.fromisoformat(a["date"]).strftime("%A") if a else ""


def apply(sessions: list[Session], led: dict, phase, dist: dict) -> tuple[list[Session], list[str], str]:
    """Drops or eases today's key sessions that you've already done this week.
    Returns the sessions, the notes, and a one-line why (empty if nothing changed)."""
    out, notes, why = [], [], ""
    p = led["planned"]
    easy_km = dist.get("easy") or 6
    for s in sessions:
        if s.kind == "run_long" and led["long_run"]:
            a = led["long_run"]
            km = max(min(easy_km, round(a["distance_km"] * 0.6 * 2) / 2), 4)   # an easy, shorter day after it
            s = make_run("run_easy", km, phase)
            notes.append(f"You already did your long run this week ({_day(a)}, {a['distance_km']:g} km), "
                         f"so this is an easy run of {km:g} km instead.")
            why = why or f"Easy run: you already did this week's long run on {_day(a)}."
        elif s.kind == "run_quality" and p["run_quality"] and led["hard_runs"] >= p["run_quality"]:
            s = make_run("run_strides" if easy_km >= 5 else "run_easy", easy_km, phase)
            notes.append("You've already done this week's hard run, so this quality session becomes an easy run.")
            why = why or "Easy run: you've already done this week's hard run."
        elif s.kind in ("run_easy", "run_strides") and led.get("runs_due") and led.get("runs", 0) >= led["runs_due"]:
            notes.append(f"You've already run {led['runs']}× this week, as many as planned up to today, "
                         "so there's no run today.")
            why = why or "No run today: you've already done this week's runs so far."
            continue
        elif s.sport == "swim" and p["swims"] and led["swims"] >= p["swims"]:
            notes.append(f"You've already swum {led['swims']}× this week ({p['swims']} planned), so this swim is dropped.")
            why = why or "No swim: you've already swum as often as planned this week."
            continue
        elif s.kind == "bike_long" and led["long_ride"]:
            a = led["long_ride"]
            s = make_bike("bike_easy", phase, 60)
            notes.append(f"You already did your long ride this week ({_day(a)}, {a['duration_min']:.0f}'), "
                         "so this ride is an easy hour.")
            why = why or f"Easy ride: you already did this week's long ride on {_day(a)}."
        elif s.kind == "bike_quality" and p["bike_quality"] and led["hard_rides"] >= p["bike_quality"]:
            s = make_bike("bike_easy", phase, 60)
            notes.append("You've already done this week's hard rides, so this ride is easy.")
            why = why or "Easy ride: you've already done this week's hard rides."
        out.append(s)
    if sessions and not out:
        out = [make_rest()]
    return out, notes, why


def no_back_to_back_runs(sessions: list[Session], d: dt.date, acts: list[dict], cfg: dict
                         ) -> tuple[list[Session], list[str], str]:
    """With `athlete.no_runs_on_consecutive_days`, no run the day after a run (one of
    2 km or more). Your note can still put a run there ("I can run tomorrow")."""
    if not (cfg.get("athlete") or {}).get("no_runs_on_consecutive_days"):
        return sessions, [], ""
    if any(k.startswith("run") and day == d for k, day in (cfg.get("_constraints") or {}).get("moves") or []):
        return sessions, [], ""                      # your note asked for a run that day
    yday = (d - dt.timedelta(days=1)).isoformat()
    ran = [a for a in acts if a["date"] == yday and a["sport"] == "run" and (a.get("distance_km") or 0) >= 2]
    out = [s for s in sessions if s.sport != "run"]
    if not ran or len(out) == len(sessions):
        return sessions, [], ""
    dropped = " + ".join(s.title for s in sessions if s.sport == "run")
    note = (f"No run today ({dropped}): you ran yesterday ({ran[0]['distance_km']:g} km), "
            "and you don't run two days in a row.")
    return out or [make_rest()], [note], "No run today: you ran yesterday."


def as_done(sessions: list[Session], day: dt.date) -> list[dict]:
    """Planned sessions as if done, to draft the following days."""
    return [{"date": day.isoformat(), "sport": s.sport, "name": s.title,
             "distance_km": s.distance_km or 0, "duration_min": s.duration_min or (s.distance_km or 0) * 6,
             "aerobic_te": 4.0 if s.intensity == "hard" else 2.5}
            for s in sessions if s.sport in ("run", "bike", "swim")]


def summary(led: dict) -> dict:
    """This week's key sessions, done vs planned (for the AI coach and the report)."""
    p = led["planned"]
    out = {"swims": f"{led['swims']} of {p['swims']}"} if p["swims"] else {}
    if p["run_long"]:
        a = led["long_run"]
        out["long_run"] = f"done {_day(a)}, {a['distance_km']:g} km" if a else "not yet"
    if p["run_quality"]:
        out["hard_runs"] = f"{led['hard_runs']} of {p['run_quality']}"
    if p["bike_long"]:
        a = led["long_ride"]
        out["long_ride"] = f"done {_day(a)}, {a['duration_min']:.0f}'" if a else "not yet"
    return out


def drafts(d: dt.date, cfg: dict, acts: list[dict], today: list[Session], days: int,
           quality_days: set[str] = frozenset()) -> list[tuple]:
    """[(day, phase, sessions, notes)] for the `days` after d, as if you follow the plan."""
    from .planner import compute_week_target, plan_day, week_parts
    done = list(acts) + as_done(today, d)
    out = []
    for i in range(1, days + 1):
        day = d + dt.timedelta(days=i)
        # same week: today's target; a later week: counting the drafted days as done
        wt = compute_week_target([a for a in done if a["date"] < monday_of(day).isoformat()]
                                 if monday_of(day) > monday_of(d) else [a for a in acts if a["date"] < d.isoformat()],
                                 day, cfg)
        phase, sess = plan_day(day, cfg, wt)
        notes: list[str] = []
        if not any(s.kind == "race" for s in sess):
            _, tpl, dist, bdist = week_parts(day, cfg, wt)
            led = ledger(done, day, tpl, dist, bdist, quality_ok=not (wt.get("return_mode") or wt.get("after_gap")) and phase.name != "transition",
                         quality_days=quality_days)
            sess, notes, _ = apply(sess, led, phase, dist)
            sess, more, _ = no_back_to_back_runs(sess, day, done, cfg)
            notes += more
        out.append((day, phase, sess, notes))
        done += as_done(sess, day)
    return out
