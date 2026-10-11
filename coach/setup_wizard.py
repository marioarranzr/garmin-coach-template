"""Interactive setup: asks what you train and which races you're preparing,
then writes config.yaml and sets your time zone in the GitHub workflow."""
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
    "2": ("run_bike", "Running + cycling as cross-training"),
    "3": ("tri", "Triathlon (run, bike, swim)"),
}
RACE_KINDS = {
    "1": ("5k", "5K", 5),
    "2": ("10k", "10K", 10),
    "3": ("half", "Half marathon", 21.1),
    "4": ("marathon", "Marathon", 42.2),
    "5": ("road", "Other road distance", None),
    "6": ("tri_sprint", "Sprint triathlon", None),
    "7": ("tri_olympic", "Olympic triathlon", None),
}


# ── schedule building (pure functions, tested) ──────────────────────────────
def _next_free(start: int, used: set[int]) -> int:
    for k in range(7):
        d = (start + k) % 7
        if d not in used:
            return d
    raise ValueError("no free day left")


def build_templates(sport: str, run_days: int, long_day: int, rest_day: int) -> dict:
    """Returns {"run_focus": {...}, "triathlon": {...}} weekly templates.

    Runs are spread around the long run so hard days aren't back to back:
    quality 2 days after the long run, strides 4 days after, extra easy runs
    in between. Bike and swim sessions fill the remaining days.
    """
    run_days = max(2, min(run_days, 6))

    def runs(n: int, used: set[int]) -> dict[int, list[str]]:
        plan: dict[int, list[str]] = {long_day: ["run_long"]}
        used.add(long_day)
        order = [(2, "run_quality"), (4, "run_strides"), (5, "run_easy"), (3, "run_easy"), (1, "run_easy")]
        for offset, kind in order[: n - 1]:
            d = _next_free(long_day + offset, used)
            used.add(d)
            plan[d] = [kind]
        return plan

    def week(n_runs: int, extras: list[tuple[str, int]], swims: list[str]) -> dict:
        used = {rest_day}
        sched = runs(n_runs, used)
        easy_days = [d for d, s in sched.items() if s == ["run_easy"] or s == ["run_strides"]]
        for kind, pref in extras:            # bike sessions, at a preferred offset from the long run
            if len(used) < 7:
                d = _next_free(long_day + pref, used)
                used.add(d)
                sched.setdefault(d, []).append(kind)
            elif easy_days and kind in ("bike_easy", "bike_quality"):
                sched[easy_days.pop()].append("bike_easy")   # no free day: easy spin after an easy run
        for kind in swims:                   # swims on free days, else alongside an easy run
            free = [d for d in range(7) if d not in used]
            if free:
                d = free[0]
                used.add(d)
                sched[d] = [kind]
            elif easy_days:
                sched[easy_days.pop()].append(kind)
        out = {DAYS[d]: sched.get(d, []) for d in range(7)}
        out[DAYS[rest_day]] = ["rest"]
        return out

    bike = sport in ("run_bike", "tri")
    run_focus = week(
        run_days,
        [("bike_long", -1), ("bike_easy", 3)] if bike else [],
        ["swim_technique"] if sport == "tri" else [],
    )
    if sport == "tri":
        tri = week(
            min(run_days, 3),
            [("bike_quality", 3), ("bike_long", -1)],
            ["swim_technique", "swim_endurance"],
        )
        bl = next(day for day, s in tri.items() if "bike_long" in s)
        tri[bl].append("run_brick")
    else:
        tri = dict(run_focus)
    return {"run_focus": run_focus, "triathlon": tri}


def cron_for(wake: str, tz: str) -> str:
    """GitHub cron (UTC) for a local wake time. Uses the winter offset, so in
    summer the run happens one hour later, never before you're up."""
    h, m = (int(x) for x in wake.split(":"))
    winter = dt.datetime(2026, 1, 15, h, m, tzinfo=ZoneInfo(tz))
    utc = winter.astimezone(dt.timezone.utc)
    return f"{utc.minute} {utc.hour} * * *"


def _yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def render_config(answers: dict) -> str:
    races = []
    for r in answers["races"]:
        lines = [f"  - name: {r['name']}", f"    date: {r['date']}", f"    kind: {r['kind']}"]
        if r.get("distance_km"):
            lines.append(f"    distance_km: {r['distance_km']:g}")
        lines.append(f"    priority: {r['priority']}")
        races.append("\n".join(lines))
    tpl = answers["templates"]

    def block(name):
        return "\n".join(f"    {d}: {_yaml_list(tpl[name][d])}" for d in DAYS)

    today = dt.date.today()
    monday = today + dt.timedelta(days=(7 - today.weekday()) % 7)
    return f"""# ─────────────────────────────────────────────────────────────
#  Garmin Coach – configuration
#  Generated by `python -m coach setup` on {today.isoformat()}.
#  Re-run the setup or edit this file by hand at any time.
# ─────────────────────────────────────────────────────────────

athlete:
  sports: {answers['sport']}          # run | run_bike | tri
  # Approximate easy pace (min/km). Only used to estimate workout duration
  # in Garmin; intensity targets are heart-rate zones.
  easy_pace_min_per_km: {answers['easy_pace']:g}
  pool_length_m: {answers['pool']}

# 4-week cycles (3 load weeks + 1 deload week) are counted from this Monday.
plan_start: {monday.isoformat()}

# Target races.
#   kind: road (with distance_km) | 5k | 10k | half | marathon | tri_sprint | tri_olympic
#   priority: A (main goal) | B (secondary)
races:
{chr(10).join(races) if races else "  []"}

# Maximum weekly running km per goal type. The plan ramps volume up gradually
# (≈ +8 %/week) from what you actually ran over the last 4 weeks.
peak_weekly_run_km:
  short: {answers['peaks']['short']}
  half: {answers['peaks']['half']}
  tri: {answers['peaks']['tri']}
min_weekly_run_km: 15
# Return-to-running mode: if your last 4 weeks average less than this many
# km/week of running, the plan starts at this volume, grows ~20 %/week up to
# min_weekly_run_km, and replaces hard sessions with easy runs + strides.
return_to_run_below_km: 10

# Weekly template. Days: mon tue wed thu fri sat sun
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
{block('triathlon')}

garmin:
  # Where `python -m coach login` saves your session on your computer
  # (outside the repository). On GitHub it's stored encrypted in
  # data/garmin_tokens.enc instead.
  tokenstore: ~/.garminconnect
  upload: true
  workout_prefix: "Coach"
"""


def update_workflow(path: Path, tz: str, cron: str) -> bool:
    if not path.exists():
        return False
    s = path.read_text(encoding="utf-8")
    s2 = re.sub(r"(\n\s*TZ:\s*)\S+", rf"\g<1>{tz}", s, count=1)
    s2 = re.sub(r'(- cron:\s*)"[^"]*"', rf'\g<1>"{cron}"', s2, count=1)
    path.write_text(s2, encoding="utf-8")
    return s2 != s


def peaks_for(run_days: int) -> dict:
    # what a sensible ceiling looks like with this many run days
    return {3: {"short": 32, "half": 42, "tri": 30},
            4: {"short": 40, "half": 52, "tri": 35},
            5: {"short": 48, "half": 62, "tri": 40}}.get(min(max(run_days, 3), 5))


# ── interactive part ────────────────────────────────────────────────────────
def _ask(prompt: str, default: str | None = None, valid=None, ask=input) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        raw = ask(f"{prompt}{suffix}: ").strip()
        val = raw or (default or "")
        if not val:
            continue
        if valid is None:
            return val
        err = valid(val)
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
        return None if lo <= x <= hi else f"Between {lo} and {hi}"
    return f


def _valid_pace(v: str):
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", v)
    return None if m and 3 <= int(m.group(1)) <= 12 else "Use min:sec per km, e.g. 5:45"


def _pace_to_float(v: str) -> float:
    mm, _, ss = v.partition(":")
    return round(int(mm) + int(ss or 0) / 60, 2)


def _day_index(v: str) -> int:
    v = v.strip().lower()[:3]
    return DAYS.index(v)


def _valid_day(v: str):
    return None if v.strip().lower()[:3] in DAYS else "Use a day name, e.g. sun"


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
        sug["sport"] = "3"
    elif len(rides) >= 2:
        sug["sport"] = "2"
    else:
        sug["sport"] = "1"
    sug["found"].append(f"{len(runs)} runs, {len(rides)} rides, {len(swims)} swims in the last 12 weeks")

    by_week: dict = {}
    for a in runs:
        d = dt.date.fromisoformat(a["date"])
        by_week.setdefault(d.isocalendar()[:2], []).append((a, d))
    if by_week:
        per_week = median(len(v) for v in by_week.values())
        sug["run_days"] = str(min(max(round(per_week), 3), 5))
        longest = Counter(max(v, key=lambda x: x[0]["distance_km"])[1].weekday()
                          for v in by_week.values() if len(v) >= 2)
        if longest:
            sug["long_day"] = DAYS[longest.most_common(1)[0][0]]
            sug["found"].append(f"your longest run is usually on {DAY_NAMES[DAYS.index(sug['long_day'])]}")

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
            sug["found"].append(f"easy pace around {sug['pace']} /km from {len(pool)} runs")

    lengths = [round(a["pool_length_m"]) for a in swims if a.get("pool_length_m")]
    if lengths:
        common = Counter(lengths).most_common(1)[0][0]
        sug["pool"] = "50" if common >= 45 else "25"
        sug["found"].append(f"you swim in a {sug['pool']} m pool")

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
            print("Tip: run `python -m coach login` first and the setup will suggest answers from your Garmin data.\n")
            return {}
        print("Reading your last 12 weeks from Garmin…")
        api = G.connect(str(token_dir))
        today = dt.date.today()
        acts = G.fetch_activities(api, today - dt.timedelta(weeks=12), today)
        return suggest_from_activities(acts, G.fetch_profile_time_zone(api))
    except (Exception, SystemExit) as e:  # noqa: BLE001 — suggestions are optional
        print(f"Couldn't read Garmin data ({e}); continuing without suggestions.\n")
        return {}


def run(root: Path, ask=input, suggestions: dict | None = None) -> Path:
    print("\nGarmin Coach setup. Press Enter to accept the [suggested] answer.\n")
    sug = detect_from_garmin(root) if suggestions is None else suggestions
    if sug.get("found"):
        print("From your Garmin data: " + "; ".join(sug["found"]) + ".\n")
    sport = SPORTS[_choice("What do you train?", SPORTS, sug.get("sport", "1"), ask)][0]

    races = []
    print("\nYour races, in any order. Leave the name empty when you're done.")
    while True:
        name = ask(f"\nRace {len(races) + 1} name (empty to finish): ").strip()
        if not name:
            if races:
                break
            print("  Add at least one race: the plan is built towards it.")
            continue
        date = _ask("  Date (YYYY-MM-DD)", None, _valid_date, ask)
        kinds = RACE_KINDS if sport == "tri" else {k: v for k, v in RACE_KINDS.items() if not v[0].startswith("tri")}
        kind, label, dist = RACE_KINDS[_choice("  Type:", kinds, "2", ask)]
        if kind == "road":
            dist = float(_ask("  Distance (km)", None, _valid_float(1, 100), ask).replace(",", "."))
        prio = _ask("  Priority: A = main goal, B = secondary", "A",
                    lambda v: None if v.upper() in ("A", "B") else "A or B", ask).upper()
        races.append({"name": name.replace(":", " -"), "date": date, "kind": kind,
                      "distance_km": dist if kind == "road" else None, "priority": prio})

    print()
    run_days = int(_ask("How many days a week do you want to run? (3 is a safe start)", sug.get("run_days", "3"),
                        lambda v: None if v in ("3", "4", "5") else "3, 4 or 5", ask))
    long_day = _day_index(_ask("Long run day", sug.get("long_day", "sun"), _valid_day, ask))
    rest_default = sug.get("rest_day", "mon")
    if _day_index(rest_default) == long_day:
        rest_default = "mon" if long_day != 0 else "fri"
    rest_day = _day_index(_ask("Full rest day", rest_default,
                               lambda v: _valid_day(v) or (None if _day_index(v) != long_day
                                                           else "Can't be the long run day"), ask))
    pace = _pace_to_float(_ask("Easy pace, min:sec per km", sug.get("pace", "6:00"), _valid_pace, ask))
    pool = int(_ask("Pool length (m)", sug.get("pool", "25"), lambda v: None if v in ("25", "50") else "25 or 50", ask)) \
        if sport == "tri" else 25
    tz = _ask("Time zone", sug.get("tz") or _guess_tz(), lambda v: None if v in available_timezones() else
              "Use an IANA name like Europe/Madrid or America/New_York", ask)
    wake = _ask("When should the daily workout be ready (HH:MM)?", "07:00",
                lambda v: None if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v) else "Use HH:MM", ask)

    answers = {"sport": sport, "races": races, "easy_pace": pace, "pool": pool,
               "templates": build_templates(sport, run_days, long_day, rest_day),
               "peaks": peaks_for(run_days)}

    cfg_path = root / "config.yaml"
    if cfg_path.exists():
        backup = cfg_path.with_suffix(".yaml.bak")
        backup.write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\nYour previous config was saved as {backup.name}.")
    cfg_path.write_text(render_config(answers), encoding="utf-8")

    cron = cron_for(wake, tz)
    wf = update_workflow(root / ".github" / "workflows" / "coach.yml", tz, cron)

    def show(key):
        for d, name in zip(DAYS, DAY_NAMES):
            items = answers["templates"][key][d] or ["rest"]
            print(f"  {name:<10} {' + '.join(LABELS.get(k, k) for k in items)}")

    print("\n✅ config.yaml written. Your typical week before a running race:\n")
    show("run_focus")
    if sport == "tri":
        print("\nBefore a triathlon:\n")
        show("triathlon")
    print("\nDistances and intensities are set each day from your data and the phase of the plan.")
    if wf:
        print(f"\nGitHub workflow set to {tz}, running daily around {wake} (cron \"{cron}\" in UTC).")
    print("\nNext: `python -m coach week` to preview it with your Garmin data.")
    return cfg_path
