"""Guided setup: proposes your settings from your Garmin history, asks for
your races, then writes config.yaml and sets your time zone in the workflow."""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
LABELS = {"rest": "Rest", "run_easy": "Easy run", "run_strides": "Easy run + strides",
          "run_quality": "Quality run (intervals/tempo)", "run_long": "Long run", "run_brick": "Brick run",
          "bike_easy": "Easy ride", "bike_long": "Long ride", "bike_quality": "Bike intervals",
          "swim_technique": "Technique swim", "swim_endurance": "Endurance swim", "swim_css": "Hard swim"}

SPORTS = {
    "1": ("run", "Running only"),
    "2": ("run_bike", "Running + cycling"),
    "3": ("tri", "Triathlon (run, bike, swim)"),
    "4": ("bike", "Cycling (with some running)"),
}
SPORT_NAME = {v[0]: v[1] for v in SPORTS.values()}
RACE_KINDS = {
    "1": ("5k", "5K run"),
    "2": ("10k", "10K run"),
    "3": ("half", "Half marathon"),
    "4": ("marathon", "Marathon"),
    "5": ("road", "Other running distance"),
    "6": ("tri_sprint", "Sprint triathlon (0.75 / 20 / 5 km)"),
    "7": ("tri_olympic", "Olympic triathlon (1.5 / 40 / 10 km)"),
    "9": ("tri_half", "Half Ironman / 70.3 (1.9 / 90 / 21.1 km)"),
    "10": ("tri_full", "Ironman (3.8 / 180 / 42.2 km)"),
    "11": ("tri", "Other triathlon distance (you type swim-bike-run)"),
    "8": ("bike", "Cycling event (distance + elevation)"),
}
KINDS_FOR_SPORT = {"run": ["1", "2", "3", "4", "5"], "run_bike": ["1", "2", "3", "4", "5", "8"],
                   "tri": ["1", "2", "3", "4", "5", "6", "7", "9", "10", "11", "8"],
                   "bike": ["8", "1", "2", "3", "4", "5"]}


# ── schedule building (pure functions, tested) ──────────────────────────────
def _next_free(start: int, used: set[int]) -> int:
    for k in range(7):
        d = (start + k) % 7
        if d not in used:
            return d
    raise ValueError("no free day left")


def build_templates(sport: str, run_days: int, long_day: int, rest_day: int) -> dict:
    """Weekly templates for running blocks, triathlon blocks and cycling blocks.

    Runs are spread around the long run so hard days aren't back to back:
    quality 2 days after the long run, strides 4 days after, extra easy runs
    in between. Bike and swim sessions fill the remaining days. For cyclists,
    `long_day` is the long-ride day.
    """
    def runs(n: int, used: set[int], anchor: int) -> dict[int, list[str]]:
        plan: dict[int, list[str]] = {anchor: ["run_long"]}
        used.add(anchor)
        order = [(2, "run_quality"), (4, "run_strides"), (5, "run_easy"), (3, "run_easy"), (1, "run_easy")]
        for offset, kind in order[: n - 1]:
            d = _next_free(anchor + offset, used)
            used.add(d)
            plan[d] = [kind]
        return plan

    def week(n_runs: int, anchor: int, extras: list[tuple[str, int]], swims: list[str]) -> dict:
        used = {rest_day}
        sched = runs(max(2, min(n_runs, 6)), used, anchor)
        easy_days = [d for d, s in sched.items() if s in (["run_easy"], ["run_strides"])]
        for kind, pref in extras:            # bike sessions, at a preferred offset from the long run
            if len(used) < 7:
                d = _next_free(anchor + pref, used)
                used.add(d)
                sched.setdefault(d, []).append(kind)
            elif easy_days and kind in ("bike_easy", "bike_quality"):
                sched[easy_days.pop()].append("bike_easy")   # no free day: easy spin after an easy run
        for kind in swims:                   # swims on free days, else alongside an easy run
            free = [d for d in range(7) if d not in used]
            if free:
                used.add(free[0])
                sched[free[0]] = [kind]
            elif easy_days:
                sched[easy_days.pop()].append(kind)
        out = {DAYS[d]: sched.get(d, []) for d in range(7)}
        out[DAYS[rest_day]] = ["rest"]
        return out

    def cycling_week(ride_day: int, n_runs: int) -> dict:
        used = {rest_day, ride_day}
        sched: dict[int, list[str]] = {ride_day: ["bike_long"]}
        for kind, off in (("bike_quality", 3), ("bike_quality", 5), ("bike_easy", 6)):
            d = _next_free(ride_day + off, used)
            used.add(d)
            sched[d] = [kind]
        for i, off in enumerate((4, 1, 2)[:n_runs]):   # maintenance runs on the remaining days
            if len(used) == 7:
                break
            d = _next_free(ride_day + off, used)
            used.add(d)
            sched[d] = ["run_strides" if i == 0 else "run_easy"]
        out = {DAYS[d]: sched.get(d, []) for d in range(7)}
        out[DAYS[rest_day]] = ["rest"]
        return out

    has_bike = sport in ("run_bike", "tri", "bike")
    run_anchor = long_day
    if sport == "bike":
        # cyclists: long_day is the long ride; a running block keeps the long run the day after
        run_anchor = _next_free(long_day + 1, {rest_day})
    run_focus = week(
        run_days if sport != "bike" else 3,
        run_anchor,
        [("bike_long", -1), ("bike_easy", 3)] if has_bike else [],
        ["swim_technique"] if sport == "tri" else [],
    )
    templates = {"run_focus": run_focus}
    if sport == "tri":
        tri = week(min(run_days, 3), run_anchor, [("bike_quality", 3), ("bike_long", -1)],
                   ["swim_technique", "swim_endurance"])
        bl = next(day for day, s in tri.items() if "bike_long" in s)
        tri[bl].append("run_brick")
        templates["triathlon"] = tri
    else:
        templates["triathlon"] = dict(run_focus)
    if has_bike:
        ride_day = long_day if sport == "bike" else _next_free(long_day - 1, {rest_day})
        if sport != "bike" and ride_day == long_day:
            ride_day = _next_free(long_day + 1, {rest_day, long_day})
        templates["cycling"] = cycling_week(ride_day, min(run_days, 3) if sport == "bike" else 2)
    return templates


def cron_for(morning: str, tz: str, until: str = "10:00", evening: str | None = "21:00") -> str:
    """GitHub cron (UTC) for the morning check (hourly from `morning` until the
    first run at or after `until`) and the evening plan (at `evening`, plus one
    retry an hour later). GitHub cron has no time zones, so it lists the UTC
    hours for both your winter and summer offsets; the coach then checks your
    local time and only acts from `morning` and from `evening`, so the first
    check is at 08:00 local all year. Runs are on the minute of `morning`."""
    zone = ZoneInfo(tz)
    offsets = {dt.datetime(2026, m, 15, 12, tzinfo=zone).utcoffset() for m in (1, 7)}
    mh, mm = (int(x) for x in morning.split(":"))
    uh, um = (int(x) for x in until.split(":"))
    local = [mh]
    while (local[-1], mm) < (uh, um) and len(local) < 12:
        local.append(local[-1] + 1)
    if evening:
        eh, em = (int(x) for x in evening.split(":"))
        first = eh if em <= mm else eh + 1      # the first run on the minute at or after `evening`
        local += [first, first + 1]
    hours = set()
    for h in local:
        for off in offsets:
            t = dt.datetime(2026, 1, 1, h % 24, mm) - off
            hours.add(t.hour)
    hs = sorted(hours)
    spans, start = [], hs[0]
    for a, b in zip(hs, hs[1:] + [None]):
        if b != a + 1:
            spans.append(f"{start}-{a}" if a != start else f"{a}")
            start = b
    return f"{mm} {','.join(spans)} * * *"


def peaks_for(sport: str, run_days: int) -> dict:
    run = {3: {"short": 32, "half": 42, "tri": 30},
           4: {"short": 40, "half": 52, "tri": 35},
           5: {"short": 48, "half": 62, "tri": 40}}[min(max(run_days, 3), 5)]
    return dict(run, bike=20, bike_hours=12 if sport == "bike" else 9)


def _yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def _yaml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_config(answers: dict) -> str:
    races = []
    for r in answers["races"]:
        lines = [f"  - name: {_yaml_str(r['name'])}", f"    date: {r['date']}", f"    kind: {r['kind']}"]
        if r.get("distance_km"):
            lines.append(f"    distance_km: {r['distance_km']:g}")
        if r.get("elevation_m"):
            lines.append(f"    elevation_m: {r['elevation_m']:g}")
        if r.get("distances"):
            lines.append(f"    distances: {r['distances']}       # swim-bike-run km")
        lines.append(f"    priority: {r['priority']}")
        races.append("\n".join(lines))
    tpl = answers["templates"]
    peaks = answers["peaks"]

    def block(name):
        return "\n".join(f"    {d}: {_yaml_list(tpl[name][d])}" for d in DAYS)

    cycling = ""
    if "cycling" in tpl:
        cycling = f"""
  # Used while the next race is a cycling event
  cycling:
{block('cycling')}"""

    today = dt.date.today()
    monday = today + dt.timedelta(days=(7 - today.weekday()) % 7)
    return f"""# ─────────────────────────────────────────────────────────────
#  Garmin Coach – configuration
#  Generated by `python -m coach setup` on {today.isoformat()}.
#  Re-run the setup or edit this file by hand at any time.
# ─────────────────────────────────────────────────────────────

athlete:
  sports: {answers['sport']}          # run | run_bike | tri | bike
  # Approximate easy pace (min/km). Only used to estimate workout duration
  # in Garmin; intensity targets are heart-rate zones.
  easy_pace_min_per_km: {answers['easy_pace']:g}
  # Pool length for swim workouts on the watch (it counts lengths).
  pool_length_m: {answers['pool']}

# 4-week cycles (3 load weeks + 1 deload week) are counted from this Monday.
plan_start: {monday.isoformat()}

# Target races.
#   kind: 5k | 10k | half | marathon | road (with distance_km)
#         tri_sprint | tri_olympic | tri_half | tri_full
#         tri (any other distance: distances: 1.5-40-10, swim-bike-run km)
#         bike (with distance_km and elevation_m)
#   priority: A = main goal (full taper) | B = tune-up race (3 easy days, quick recovery)
races:
{chr(10).join(races) if races else "  []"}

# Weekly running km: maximum per goal type and minimum. The plan ramps volume
# up gradually (≈ +8 %/week) from what you actually ran over the last 4 weeks.
peak_weekly_run_km:
  short: {peaks['short']}
  half: {peaks['half']}
  tri: {peaks['tri']}
  bike: {peaks['bike']}
min_weekly_run_km: 15
# Return-to-running mode: if your last 4 weeks average less than this many
# km/week of running, the plan starts at this volume, grows ~20 %/week up to
# min_weekly_run_km, and replaces hard sessions with easy runs + strides.
return_to_run_below_km: 10
# The long run is never shorter than ~10 % above your longest run of the last
# 6 weeks (outside taper and race weeks). Set a minimum here if you want one.
min_long_run_km: {answers.get('min_long_run_km', 0):g}

# Weekly riding hours in a cycling block: grows ≈ +8 %/week from your last
# 4 weeks, between these limits. Long rides build towards the event's duration.
min_weekly_bike_hours: 3
peak_weekly_bike_hours: {peaks['bike_hours']}

# Weekly templates. Days: mon tue wed thu fri sat sun
# Available sessions:
#   rest
#   run_easy  run_strides  run_quality  run_long  run_brick
#   bike_easy bike_long    bike_quality
#   swim_technique swim_endurance swim_css
weekly_templates:
  # Used while the next race is a running race
  run_focus:
{block('run_focus')}
  # Used while the next race is a triathlon
  triathlon:
{block('triathlon')}{cycling}

garmin:
  # Where `python -m coach login` saves your session on your computer
  # (outside the repository). On GitHub it's stored encrypted in
  # data/garmin_tokens.enc instead.
  tokenstore: ~/.garminconnect
  upload: true
  workout_prefix: {_yaml_str(answers.get('prefix', 'Coach'))}
  # Every evening the coach plans tomorrow (the real workout in your calendar),
  # and every morning it checks it with last night's sleep, HRV and Body
  # Battery, changing it (and telling you) only if needed. Times are local;
  # `python -m coach setup` sets the GitHub schedule to match.
  evening_plan: "{answers.get('evening', '21:00')}"
  morning_check: "{answers.get('morning', '08:00')}"
  # The morning check waits for last night's sleep to reach Garmin (until your
  # watch syncs). If the watch shows you're up but recorded no sleep, or at
  # this time at the latest, it checks without it.
  wait_for_sleep_until: "{answers.get('until', '10:00')}"
  # How far ahead your Garmin calendar shows the plan: tomorrow is the real
  # workout, the days after are drafts ("Coach draft · …") redrawn every
  # evening from what you did. 0 turns the drafts off.
  preview_days: {answers.get('preview_days', 7)}

# AI coach: each day an AI makes the final call (keep, shorten, make easier
# or rest) within the plan's safety limits, using all your data and notes.
# Needs an OPENAI_API_KEY or ANTHROPIC_API_KEY secret (see README); without
# one the rule-based plan is used. provider: auto | openai | anthropic
ai:
  enabled: {str(answers.get('ai_enabled', True)).lower()}
  provider: {answers.get('ai_provider', 'auto')}
  openai_model: {answers.get('openai_model', 'gpt-5.4-mini')}
  anthropic_model: {answers.get('anthropic_model', 'claude-sonnet-5-5')}

# Your own notes on how you feel, read from Todoist: anything added to this
# project in the last `lookback_hours`, including notes already marked done.
# Notes the AI coach uses get the `diary_label` label and are marked done
# (never deleted); the coach reads the last 6 weeks of them back as your
# training diary. Needs TODOIST_API_TOKEN (see README).
todoist:
  enabled: {str(answers.get('todoist_enabled', True)).lower()}
  project: {_yaml_str(answers.get('todoist_project', 'Inbox'))}
  lookback_hours: {answers.get('todoist_lookback_hours', 14)}
  diary_label: {_yaml_str(answers.get('todoist_diary_label', 'coach'))}
  # Your coming days as tasks in this Todoist project (one per day, due that
  # day), so the week is easy to see. Created if needed; "" turns it off.
  plan_project: {_yaml_str(answers.get('todoist_plan_project', 'Training plan'))}
"""


def update_workflow(path: Path, tz: str, cron: str) -> bool:
    if not path.exists():
        return False
    s = path.read_text(encoding="utf-8")
    s2 = re.sub(r"(\n\s*TZ:\s*)\S+", rf"\g<1>{tz}", s, count=1)
    s2 = re.sub(r'(- cron:\s*)"[^"]*"', rf'\g<1>"{cron}"', s2, count=1)
    path.write_text(s2, encoding="utf-8")
    return s2 != s


# ── suggestions from your Garmin history (pure function, tested) ────────────
def _fmt_pace(min_per_km: float) -> str:
    m = int(min_per_km)
    s = round((min_per_km - m) * 60)
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d}"


def suggest_from_activities(acts: list[dict], tz: str | None = None) -> dict:
    """Proposes setup answers from the lookback window (6 weeks) of normalized Garmin activities."""
    from collections import Counter
    from statistics import median

    sug: dict = {"found": []}
    runs = [a for a in acts if a["sport"] == "run" and a["distance_km"] >= 1]
    rides = [a for a in acts if a["sport"] == "bike"]
    swims = [a for a in acts if a["sport"] == "swim"]
    if not acts:
        return sug

    if len(swims) >= 2:
        sug["sport"] = "tri"
    elif len(rides) >= 6 and len(rides) >= 2 * len(runs):
        sug["sport"] = "bike"
    elif len(rides) >= 2:
        sug["sport"] = "run_bike"
    else:
        sug["sport"] = "run"
    sug["found"].append(f"{len(runs)} runs, {len(rides)} rides, {len(swims)} swims")

    def weekly(items):
        by_week: dict = {}
        for a in items:
            d = dt.date.fromisoformat(a["date"])
            by_week.setdefault(d.isocalendar()[:2], []).append((a, d))
        return by_week

    def usual_longest_day(by_week, key):
        days = Counter(max(v, key=lambda x: x[0][key])[1].weekday() for v in by_week.values() if len(v) >= 2)
        return DAYS[days.most_common(1)[0][0]] if days else None

    run_weeks = weekly(runs)
    if run_weeks:
        per_week = median(len(v) for v in run_weeks.values())
        lo, hi = (0, 3) if sug["sport"] == "bike" else (3, 5)
        sug["run_days"] = min(max(round(per_week), lo), hi)
    if sug["sport"] == "bike":
        sug.setdefault("run_days", 1)
        day = usual_longest_day(weekly(rides), "duration_min")
    else:
        day = usual_longest_day(run_weeks, "distance_km")
    if day:
        sug["long_day"] = day

    if len(acts) >= 8:
        busy = Counter(dt.date.fromisoformat(a["date"]).weekday() for a in acts)
        long_idx = DAYS.index(sug.get("long_day", "sun"))
        quiet = min((d for d in range(7) if d != long_idx), key=lambda d: (busy.get(d, 0), d))
        sug["rest_day"] = DAYS[quiet]

    easy = [a for a in runs if a["distance_km"] >= 3 and a["duration_min"] > 0
            and (a.get("aerobic_te") is None or a["aerobic_te"] < 3.0)]
    pool = easy or [a for a in runs if a["distance_km"] >= 3]
    if pool:
        pace = median(a["duration_min"] / a["distance_km"] for a in pool)
        if not easy:
            pace += 0.25          # all runs were hard-ish: easy pace is a bit slower
        if 3 <= pace <= 12:
            sug["pace"] = _fmt_pace(pace)

    lengths = [round(a["pool_length_m"]) for a in swims if a.get("pool_length_m")]
    if lengths:
        common = Counter(lengths).most_common(1)[0][0]
        sug["pool"] = 50 if common >= 45 else 25

    if tz and tz in available_timezones():
        sug["tz"] = tz
    return sug


def detect_from_garmin(root: Path) -> dict:
    """Reads the last 6 weeks from Garmin (the coach's lookback window), if you've already logged in."""
    try:
        import yaml
        from . import garmin_api as G
        cfg_file = root / "config.yaml"
        cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
        token_dir = Path((cfg.get("garmin") or {}).get("tokenstore", "~/.garminconnect")).expanduser()
        if not (token_dir / "garmin_tokens.json").exists():
            print("Tip: run `python -m coach login` first and the setup will propose settings from your Garmin data.\n")
            return {}
        print("Reading your last 6 weeks from Garmin…")
        api = G.connect(str(token_dir))
        today = dt.date.today()
        from .planner import history_start
        acts = G.fetch_activities(api, history_start(today), today)
        return suggest_from_activities(acts, G.fetch_profile_time_zone(api))
    except (Exception, SystemExit) as e:  # noqa: BLE001 — suggestions are optional
        print(f"Couldn't read Garmin data ({e}); continuing with default settings.\n")
        return {}


# ── interactive part ────────────────────────────────────────────────────────
def _ask(prompt: str, default: str | None = None, valid=None, ask=input) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        raw = ask(f"{prompt}{suffix}: ").strip()
        val = raw or (default or "")
        if not val:
            continue
        err = valid(val) if valid else None
        if err is None:
            return val
        print(f"  {err}")


def _choice(prompt: str, options: dict, default: str, ask=input) -> str:
    print(prompt)
    for k, v in options.items():
        print(f"  {k}) {v[1] if isinstance(v, tuple) else v}")
    return _ask("Choose", default, lambda v: None if v in options else f"Pick one of {', '.join(options)}", ask)


def _valid_date(v: str):
    try:
        d = dt.date.fromisoformat(v)
    except ValueError:
        return "Use the format YYYY-MM-DD"
    return None if d >= dt.date.today() else "That date is in the past"


def _valid_legs(v: str):
    from .planner import parse_legs
    legs = parse_legs(v)
    if not legs:
        return "Three distances in km, swim-bike-run, e.g. 1.5-40-10"
    sw, bk, rn = legs
    if sw > 10 or bk > 400 or rn > 50:
        return "Those look too long: swim, bike and run in km (e.g. 1.9-90-21.1)"
    return None


def _valid_float(lo, hi):
    def f(v):
        try:
            x = float(v.replace(",", "."))
        except ValueError:
            return "Enter a number"
        return None if lo <= x <= hi else f"Between {lo:g} and {hi:g}"
    return f


def _valid_pace(v: str):
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", v)
    return None if m and 3 <= int(m.group(1)) <= 12 else "Use min:sec per km, e.g. 5:45"


def _pace_to_float(v: str) -> float:
    mm, _, ss = v.partition(":")
    return round(int(mm) + int(ss or 0) / 60, 2)


def _valid_day(v: str):
    return None if v.strip().lower()[:3] in DAYS else "Use a day name, e.g. sun"


def _day(v: str) -> str:
    return v.strip().lower()[:3]


def _guess_tz() -> str:
    if os.environ.get("TZ") in available_timezones():
        return os.environ["TZ"]
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            name = target.split("zoneinfo/", 1)[1]
            if name in available_timezones():
                return name
    except OSError:
        pass
    return "UTC"


def existing_setup(cfg: dict, workflow: str) -> dict:
    """Reads the settings and races of an existing config.yaml (and workflow)."""
    out: dict = {"races": [], "extras": {}}
    ath = cfg.get("athlete") or {}
    if ath.get("sports") in SPORT_NAME:
        out["sport"] = ath["sports"]
    if ath.get("easy_pace_min_per_km"):
        out["pace"] = _fmt_pace(float(ath["easy_pace_min_per_km"]))
    tpls = cfg.get("weekly_templates") or {}
    rf = tpls.get("run_focus") or {}
    runs = [d for d in DAYS if any(k.startswith("run_") for k in rf.get(d) or [])]
    if runs:
        out["run_days"] = len(runs)
    long_tpl, long_kind = ((tpls.get("cycling") or {}), "bike_long") if out.get("sport") == "bike" else (rf, "run_long")
    for d in DAYS:
        if long_kind in (long_tpl.get(d) or []):
            out["long_day"] = d
        if (rf.get(d) or []) == ["rest"]:
            out["rest_day"] = d
    m = re.search(r"\n\s*TZ:\s*(\S+)", workflow)
    if m and m.group(1) in available_timezones():
        out["tz"] = m.group(1)
        c = re.search(r'- cron:\s*"(\d+)\s+(\d+)', workflow)
        if c:
            zone = ZoneInfo(out["tz"])
            std = min((dt.datetime(2026, 1, 15, 12, tzinfo=zone), dt.datetime(2026, 7, 15, 12, tzinfo=zone)),
                      key=lambda x: x.utcoffset())
            utc = dt.datetime(2026, std.month, 15, int(c.group(2)), int(c.group(1)), tzinfo=dt.timezone.utc)
            out["wake"] = utc.astimezone(dt.timezone(std.utcoffset())).strftime("%H:%M")
    g0 = cfg.get("garmin") or {}
    if g0.get("morning_check"):
        out["wake"] = str(g0["morning_check"])
    if g0.get("evening_plan"):
        out["evening"] = str(g0["evening_plan"])
    for r in cfg.get("races") or []:
        race = {k: r[k] for k in ("name", "kind", "distance_km", "elevation_m", "distances", "priority")
                if r.get(k) is not None}
        race["date"] = str(r["date"])
        out["races"].append(race)
    g = cfg.get("garmin") or {}
    out["extras"] = {"min_long_run_km": float(cfg.get("min_long_run_km") or 0),
                     "until": str(g.get("wait_for_sleep_until", "10:00")),
                     "prefix": g.get("workout_prefix", "Coach"), "preview_days": g.get("preview_days", 7),
                     "pool": ath.get("pool_length_m"),
                     "ai_enabled": (cfg.get("ai") or {}).get("enabled", True),
                     "ai_provider": (cfg.get("ai") or {}).get("provider", "auto"),
                     "openai_model": (cfg.get("ai") or {}).get("openai_model", "gpt-5.4-mini"),
                     "anthropic_model": (cfg.get("ai") or {}).get("anthropic_model")
                     or (cfg.get("ai") or {}).get("model", "claude-sonnet-5-5"),
                     "todoist_enabled": (cfg.get("todoist") or {}).get("enabled", True),
                     "todoist_project": (cfg.get("todoist") or {}).get("project", "Inbox"),
                     "todoist_lookback_hours": (cfg.get("todoist") or {}).get("lookback_hours", 12),
                     "todoist_diary_label": (cfg.get("todoist") or {}).get("diary_label", "coach"),
                     "todoist_plan_project": (cfg.get("todoist") or {}).get("plan_project", "Training plan")}
    return out


def _settings_lines(st: dict) -> list[tuple[str, str]]:
    bike = st["sport"] == "bike"
    return [
        ("You train", SPORT_NAME[st["sport"]]),
        ("Run days per week", str(st["run_days"])),
        ("Long ride day" if bike else "Long run day", DAY_NAMES[DAYS.index(st["long_day"])]),
        ("Rest day", DAY_NAMES[DAYS.index(st["rest_day"])]),
        ("Easy running pace", f"{st['pace']} /km"),
        ("Time zone", st["tz"]),
        ("Morning check at", st["wake"]),
        ("Evening plan at", st["evening"]),
    ]


def _edit_setting(i: int, st: dict, ask) -> None:
    bike = st["sport"] == "bike"
    if i == 1:
        key = _choice("What do you train?", SPORTS,
                      next(k for k, v in SPORTS.items() if v[0] == st["sport"]), ask)
        st["sport"] = SPORTS[key][0]
    elif i == 2:
        lo, hi = (0, 3) if bike else (3, 5)
        st["run_days"] = int(_ask(f"Run days per week ({lo}-{hi}; 3 is a safe start for runners)",
                                  str(min(max(st["run_days"], lo), hi)),
                                  lambda v: None if v.isdigit() and lo <= int(v) <= hi else f"{lo} to {hi}", ask))
    elif i == 3:
        st["long_day"] = _day(_ask("Long ride day" if bike else "Long run day", st["long_day"], _valid_day, ask))
    elif i == 4:
        st["rest_day"] = _day(_ask("Rest day", st["rest_day"], _valid_day, ask))
    elif i == 5:
        st["pace"] = _ask("Easy running pace, min:sec per km", st["pace"], _valid_pace, ask)
    elif i == 6:
        st["tz"] = _ask("Time zone", st["tz"], lambda v: None if v in available_timezones() else
                        "Use an IANA name like Europe/Madrid or America/New_York", ask)
    elif i == 7:
        st["wake"] = _ask("Morning check of today's plan with your sleep data (HH:MM)", st["wake"],
                          lambda v: None if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v) else "Use HH:MM", ask)
    elif i == 8:
        st["evening"] = _ask("Evening plan for the next day (HH:MM)", st["evening"],
                             lambda v: None if re.fullmatch(r"(1[2-9]|2[0-3]):[0-5]\d", v) else
                             "Use HH:MM, in the afternoon or evening", ask)


def _review_settings(st: dict, title: str, ask, hints: dict | None = None) -> None:
    hints = hints or {}
    while True:
        print(f"{title}:")
        for n, (label, value) in enumerate(_settings_lines(st), 1):
            hint = f"   (Garmin data suggests {hints[n]})" if n in hints and hints[n] != value else ""
            print(f"  {n}. {label:<20} {value}{hint}")
        raw = ask("\nPress Enter to use these, or type the numbers to change (e.g. 3 4): ").strip()
        picks = [int(x) for x in re.findall(r"\d+", raw) if 1 <= int(x) <= 7]
        if not raw:
            # sanity fixes before accepting
            if st["rest_day"] == st["long_day"]:
                print("  The rest day can't be the same as the long day — please pick another.")
                _edit_setting(4, st, ask)
                continue
            lo, hi = (0, 3) if st["sport"] == "bike" else (3, 5)
            st["run_days"] = min(max(st["run_days"], lo), hi)
            return
        if not picks:
            print("  Type numbers from 1 to 7, or just press Enter.")
            continue
        for i in sorted(set(picks)):
            _edit_setting(i, st, ask)
        print()


def _race_line(r: dict) -> str:
    label = next((v[1] for v in RACE_KINDS.values() if v[0] == r["kind"]), r["kind"])
    extra = ""
    if r.get("distances"):
        extra = f", {r['distances']} km"
    elif r["kind"] in ("road", "bike") and r.get("distance_km"):
        extra = f", {r['distance_km']:g} km" + (f", {r['elevation_m']:g} m up" if r.get("elevation_m") else "")
    return f"{r['date']}  {r['name']} ({label}{extra}), priority {r.get('priority', 'A')}"


def _ask_races(sport: str, ask, existing: list[dict] | None = None) -> list[dict]:
    races: list[dict] = []
    upcoming = [r for r in existing or [] if str(r["date"]) >= dt.date.today().isoformat()]
    if upcoming:
        print("\nYour races:")
        for r in upcoming:
            print(f"  • {_race_line(r)}")
        raw = ask("\nPress Enter to keep them, 'a' to add more, or 'n' to start over: ").strip().lower()
        if raw in ("", "k", "keep", "y", "yes"):
            return upcoming
        if raw.startswith("a"):
            races = list(upcoming)
    kinds = {k: RACE_KINDS[k] for k in KINDS_FOR_SPORT[sport]}
    default_kind = "8" if sport == "bike" else "2"
    print("\nYour races. Leave the name empty when you're done.")
    while True:
        name = ask(f"\nRace {len(races) + 1} name (empty to finish): ").strip()
        if not name:
            if races:
                return races
            print("  Add at least one race: the plan is built towards it.")
            continue
        kind = RACE_KINDS[_choice("  Type:", kinds, default_kind, ask)][0]
        race = {"name": name, "kind": kind}
        if kind == "road":
            race["distance_km"] = float(_ask("  Distance (km)", None, _valid_float(1, 100), ask).replace(",", "."))
        if kind == "tri":
            race["distances"] = _ask("  Distances, swim-bike-run in km (e.g. 1.5-40-10)", None, _valid_legs, ask)
        if kind == "bike":
            race["distance_km"] = float(_ask("  Distance (km)", None, _valid_float(10, 400), ask).replace(",", "."))
            race["elevation_m"] = float(_ask("  Elevation gain (m)", "0", _valid_float(0, 10000), ask).replace(",", "."))
        race["date"] = _ask("  Date (YYYY-MM-DD)", None, _valid_date, ask)
        race["priority"] = _ask("  Priority: A = main goal (full taper), B = tune-up (3 easy days)", "A",
                                lambda v: None if v.upper() in ("A", "B") else "A or B", ask).upper()
        races.append(race)


def run(root: Path, ask=input, suggestions: dict | None = None) -> Path:
    print("\nGarmin Coach setup\n")
    sug = detect_from_garmin(root) if suggestions is None else suggestions
    if sug.get("found"):
        print("Found in your last 6 weeks: " + "; ".join(sug["found"]) + ".\n")
    garmin = {
        "sport": sug.get("sport", "run"),
        "run_days": sug.get("run_days", 3),
        "long_day": sug.get("long_day", "sun"),
        "rest_day": sug.get("rest_day", "mon"),
        "pace": sug.get("pace", "6:00"),
        "tz": sug.get("tz") or _guess_tz(),
        "wake": "08:00",
        "evening": "21:00",
    }
    cfg_path = root / "config.yaml"
    wf_path = root / ".github" / "workflows" / "coach.yml"
    current: dict = {"races": [], "extras": {}}
    if cfg_path.exists():
        try:
            import yaml
            current = existing_setup(yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {},
                                     wf_path.read_text(encoding="utf-8") if wf_path.exists() else "")
        except Exception as e:  # noqa: BLE001 — a broken config just means starting fresh
            print(f"(Couldn't read your current config.yaml: {e}. Starting from scratch.)\n")
    st = dict(garmin)
    st.update({k: current[k] for k in garmin if k in current})
    if st["rest_day"] == st["long_day"]:
        st["rest_day"] = "mon" if st["long_day"] != "mon" else "fri"
    if any(k in current for k in garmin):
        hints = {n: v for n, (_, v) in enumerate(_settings_lines(garmin), 1)} if sug.get("found") else {}
        _review_settings(st, "Your current settings", ask, hints)
    else:
        _review_settings(st, "Proposed from your Garmin data" if sug.get("found") else "Proposed settings", ask)
    races = _ask_races(st["sport"], ask, current["races"])
    extras = current["extras"]

    long_idx, rest_idx = DAYS.index(st["long_day"]), DAYS.index(st["rest_day"])
    answers = {"sport": st["sport"], "races": races, "easy_pace": _pace_to_float(st["pace"]),
               "pool": sug.get("pool") or extras.get("pool") or 25,
               "min_long_run_km": extras.get("min_long_run_km", 0),
               "until": extras.get("until", "10:00"), "prefix": extras.get("prefix", "Coach"),
               "preview_days": extras.get("preview_days", 7),
               **{k: extras[k] for k in ("ai_enabled", "ai_provider", "openai_model", "anthropic_model",
                                         "todoist_enabled", "todoist_project",
                                         "todoist_lookback_hours", "todoist_diary_label", "todoist_plan_project") if k in extras},
               "templates": build_templates(st["sport"], st["run_days"], long_idx, rest_idx),
               "peaks": peaks_for(st["sport"], st["run_days"])}

    if cfg_path.exists():
        backup = cfg_path.with_suffix(".yaml.bak")
        backup.write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\nYour previous config was saved as {backup.name}.")
    answers["morning"], answers["evening"] = st["wake"], st["evening"]
    cfg_path.write_text(render_config(answers), encoding="utf-8")
    cron = cron_for(st["wake"], st["tz"], answers["until"], st["evening"])
    wf = update_workflow(root / ".github" / "workflows" / "coach.yml", st["tz"], cron)

    def show(key, title):
        print(f"\n{title}:\n")
        for d, name in zip(DAYS, DAY_NAMES):
            items = answers["templates"][key][d] or ["rest"]
            print(f"  {name:<10} {' + '.join(LABELS.get(k, k) for k in items)}")

    print("\n✅ config.yaml written.")
    kinds = {r["kind"] for r in races}
    if kinds & {"5k", "10k", "half", "marathon", "road"}:
        show("run_focus", "Your typical week before a running race")
    if kinds & {"tri_sprint", "tri_olympic", "tri_half", "tri_full", "tri"}:
        show("triathlon", "Before a triathlon")
    if "bike" in kinds:
        show("cycling", "Before a cycling event")
    print("\nDistances and intensities are set each day from your data and the phase of the plan.")
    if wf:
        print(f"GitHub workflow set to {st['tz']}: tomorrow's plan at {st['evening']}, and the morning check "
              f"from {st['wake']} once your sleep data arrives (at {answers['until']} at the latest; "
              f"cron \"{cron}\" in UTC).")
    print("\nNext: `python -m coach week` to preview it with your Garmin data.")
    print("Then commit and push config.yaml" + (" and .github/workflows/coach.yml" if wf else "")
          + " so the daily run on GitHub uses them.")
    return cfg_path
