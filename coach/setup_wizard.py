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
    "6": ("tri_sprint", "Sprint triathlon"),
    "7": ("tri_olympic", "Olympic triathlon"),
    "8": ("bike", "Cycling event (distance + elevation)"),
}
KINDS_FOR_SPORT = {"run": "12345", "run_bike": "123458", "tri": "12345678", "bike": "812345"}


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


def cron_for(wake: str, tz: str, until: str = "10:00") -> str:
    """GitHub cron (UTC): hourly from `wake` (local), with the last run at or
    just after `until` so it plans even without sleep data. Uses the winter
    offset, so in summer everything happens one hour later, never too early."""
    # standard (non-daylight-saving) offset, whichever hemisphere you're in
    zone = ZoneInfo(tz)
    std_day = min((dt.datetime(2026, 1, 15, 12, tzinfo=zone), dt.datetime(2026, 7, 15, 12, tzinfo=zone)),
                  key=lambda x: x.utcoffset())

    def utc(hhmm):
        h, m = (int(x) for x in hhmm.split(":"))
        return std_day.replace(hour=h, minute=m).astimezone(dt.timezone.utc)
    start, end = utc(wake), utc(until)
    minute = start.minute
    if end <= start:                       # deadline before wake-up: a single run
        return f"{minute} {start.hour} * * *"
    last = (end.hour if end.minute <= minute else end.hour + 1) % 24

    def span(a, b):
        return f"{a}-{b}" if a != b else f"{a}"
    if last >= start.hour:
        hours = span(start.hour, last)
    else:                                  # the morning crosses midnight UTC (e.g. Asia)
        hours = f"{span(start.hour, 23)},{span(0, last)}"
    return f"{minute} {hours} * * *"


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
#         tri_sprint | tri_olympic | bike (with distance_km and elevation_m)
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
  workout_prefix: "Coach"
  # Morning runs wait for last night's sleep to reach Garmin (until you wake up
  # and your watch syncs). If the watch shows you're up but recorded no sleep,
  # or at this time at the latest, it plans without it.
  wait_for_sleep_until: "{answers.get('until', '10:00')}"
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
    """Proposes setup answers from ~12 weeks of normalized Garmin activities."""
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
    """Reads the last 12 weeks from Garmin, if you've already logged in."""
    try:
        import yaml
        from . import garmin_api as G
        cfg_file = root / "config.yaml"
        cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
        token_dir = Path((cfg.get("garmin") or {}).get("tokenstore", "~/.garminconnect")).expanduser()
        if not (token_dir / "garmin_tokens.json").exists():
            print("Tip: run `python -m coach login` first and the setup will propose settings from your Garmin data.\n")
            return {}
        print("Reading your last 12 weeks from Garmin…")
        api = G.connect(str(token_dir))
        today = dt.date.today()
        acts = G.fetch_activities(api, today - dt.timedelta(weeks=12), today)
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


def _settings_lines(st: dict) -> list[tuple[str, str]]:
    bike = st["sport"] == "bike"
    return [
        ("You train", SPORT_NAME[st["sport"]]),
        ("Run days per week", str(st["run_days"])),
        ("Long ride day" if bike else "Long run day", DAY_NAMES[DAYS.index(st["long_day"])]),
        ("Rest day", DAY_NAMES[DAYS.index(st["rest_day"])]),
        ("Easy running pace", f"{st['pace']} /km"),
        ("Time zone", st["tz"]),
        ("First check at", st["wake"]),
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
        st["wake"] = _ask("First check for your sleep data (HH:MM)", st["wake"],
                          lambda v: None if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v) else "Use HH:MM", ask)


def _review_settings(st: dict, from_garmin: bool, ask) -> None:
    while True:
        title = "Proposed from your Garmin data" if from_garmin else "Proposed settings"
        print(f"{title}:")
        for n, (label, value) in enumerate(_settings_lines(st), 1):
            print(f"  {n}. {label:<20} {value}")
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


def _ask_races(sport: str, ask) -> list[dict]:
    races = []
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
        print("Found in your last 12 weeks: " + "; ".join(sug["found"]) + ".\n")
    st = {
        "sport": sug.get("sport", "run"),
        "run_days": sug.get("run_days", 3),
        "long_day": sug.get("long_day", "sun"),
        "rest_day": sug.get("rest_day", "mon"),
        "pace": sug.get("pace", "6:00"),
        "tz": sug.get("tz") or _guess_tz(),
        "wake": "07:00",
    }
    if st["rest_day"] == st["long_day"]:
        st["rest_day"] = "mon" if st["long_day"] != "mon" else "fri"
    _review_settings(st, bool(sug.get("found")), ask)
    races = _ask_races(st["sport"], ask)

    long_idx, rest_idx = DAYS.index(st["long_day"]), DAYS.index(st["rest_day"])
    answers = {"sport": st["sport"], "races": races, "easy_pace": _pace_to_float(st["pace"]),
               "pool": sug.get("pool", 25),
               "templates": build_templates(st["sport"], st["run_days"], long_idx, rest_idx),
               "peaks": peaks_for(st["sport"], st["run_days"])}

    cfg_path = root / "config.yaml"
    if cfg_path.exists():
        backup = cfg_path.with_suffix(".yaml.bak")
        backup.write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\nYour previous config was saved as {backup.name}.")
    cfg_path.write_text(render_config(answers), encoding="utf-8")

    cron = cron_for(st["wake"], st["tz"])
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
    if kinds & {"tri_sprint", "tri_olympic"}:
        show("triathlon", "Before a triathlon")
    if "bike" in kinds:
        show("cycling", "Before a cycling event")
    print("\nDistances and intensities are set each day from your data and the phase of the plan.")
    if wf:
        print(f"GitHub workflow set to {st['tz']}: hourly from {st['wake']} until your sleep data arrives "
              f"(at 10:00 at the latest; cron \"{cron}\" in UTC).")
    print("\nNext: `python -m coach week` to preview it with your Garmin data.")
    return cfg_path
