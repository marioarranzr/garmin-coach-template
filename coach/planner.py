"""Periodized plan: phases, weekly volume and each day's sessions.

Principles:
  * ~80 % of the time in zone 2 and ~20 % quality (polarized training).
  * 4-week blocks: 3 progressive load weeks + 1 deload week (-30 %).
  * Weekly volume starts from what you ACTUALLY ran in the last 4 weeks and
    grows by ~8-10 % per week at most.
  * Taper before each race and recovery after it.
Easy, long and recovery running use Garmin heart-rate zones (Z1-Z5); quality
reps target paces from your current fitness (paces.py).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Any

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

FAMILY = {"road": "short", "5k": "short", "10k": "short", "half": "half", "marathon": "half",
          "tri_sprint": "tri", "tri_olympic": "tri", "tri_half": "tri", "tri_full": "tri", "tri": "tri",
          "bike": "bike"}
# triathlon legs in km: swim, bike, run ("tri" is any other distance, set with `distances: 1.5-40-10`)
TRI_LEGS = {"tri_sprint": (0.75, 20, 5), "tri_olympic": (1.5, 40, 10), "tri_half": (1.9, 90, 21.1),
            "tri_full": (3.8, 180, 42.2)}
RECOVERY_DAYS = {"short": 4, "half": 9, "tri": 6, "bike": 4}
KIND_DISTANCE_KM = {"5k": 5, "10k": 10, "half": 21.1, "marathon": 42.2}


# ── models ──────────────────────────────────────────────────────────────────
@dataclass
class Race:
    name: str
    date: dt.date
    kind: str
    priority: str = "A"
    distance_km: float | None = None
    elevation_m: float | None = None
    legs: tuple[float, float, float] | None = None     # triathlon: swim, bike, run km

    @property
    def run_leg(self) -> float:
        return self.legs[2] if self.legs else (self.distance_km or 0)

    @property
    def hilly(self) -> bool:
        """Cycling course with ≥ 10 m of climbing per km (e.g. 1000 m over 100 km)."""
        return bool(self.elevation_m and self.distance_km and self.elevation_m / self.distance_km >= 10)

    def est_hours(self, speed_kmh: float) -> float:
        """Rough cycling event duration: flat speed plus ~45 min per 1000 m climbed."""
        return (self.distance_km or 100) / speed_kmh + (self.elevation_m or 0) / 1000 * 0.75

    @property
    def family(self) -> str:  # noqa: D401
        if self.kind == "road" and self.distance_km and self.distance_km > 12:
            return "half"
        return FAMILY.get(self.kind, "half")


@dataclass
class Phase:
    focus: str            # short | half | tri | bike
    name: str             # base | specific | build | peak | taper | race_week | transition
    label: str
    race: Race | None
    days_out: int | None
    volume_factor: float
    deload: bool
    long_cap_km: float

    def describe(self) -> str:
        r = f" — {self.race.name} in {self.days_out} days" if self.race and self.days_out is not None else ""
        return f"{self.label}{' (deload week)' if self.deload else ''}{r}"


@dataclass
class Session:
    kind: str
    sport: str                       # run | bike | swim | other
    title: str
    intensity: str = "easy"          # rest | easy | moderate | hard
    distance_km: float | None = None
    duration_min: float | None = None
    detail: str = ""
    steps: list[dict] = field(default_factory=list)
    priority: bool = False           # e.g. swimming when you've been skipping it
    optional: bool = False           # e.g. swims while the next goals are running races

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Session":
        return Session(**d)


# ── abstract steps (translated to Garmin JSON in workouts.py) ───────────────
def warmup(minutes, zone=2):   return {"type": "warmup", "end": "time", "value": minutes * 60, "zone": zone}
def cooldown(minutes, zone=1): return {"type": "cooldown", "end": "time", "value": minutes * 60, "zone": zone}
def run_t(seconds, zone, pace=None):
    return {"type": "interval", "end": "time", "value": seconds, "zone": zone, "pace": pace}
def run_d(meters, zone, pace=None):
    return {"type": "interval", "end": "distance", "value": meters, "zone": zone, "pace": pace}
def at(meters, pace):          return run_d(meters, None, pace)        # e.g. at(400, "5k"): 400 m at 5K pace
def for_(seconds, pace):       return run_t(seconds, None, pace)
def jog(seconds, zone=1):      return {"type": "recovery", "end": "time", "value": seconds, "zone": zone}
def rest(seconds):             return {"type": "rest", "end": "time", "value": seconds, "zone": None}
def swim(meters, typ="interval", note=""):
    return {"type": typ, "end": "distance", "value": meters, "zone": None, "note": note}
def repeat(n, steps):          return {"type": "repeat", "iterations": n, "steps": steps}


# ── phases ──────────────────────────────────────────────────────────────────
def load_races(cfg: dict) -> list[Race]:
    races = []
    for r in cfg.get("races", []):
        d = r["date"] if isinstance(r["date"], dt.date) else dt.date.fromisoformat(str(r["date"]))
        kind = r.get("kind", "half")
        dist = r.get("distance_km") or KIND_DISTANCE_KM.get(kind)
        legs = None
        if FAMILY.get(kind) == "tri":
            legs = parse_legs(r.get("distances")) or TRI_LEGS.get(kind) or TRI_LEGS["tri_olympic"]
            dist = None
        races.append(Race(r["name"], d, kind, str(r.get("priority", "A")).upper(),
                          dist, r.get("elevation_m"), legs))
    return sorted(races, key=lambda r: r.date)


def parse_legs(value) -> tuple[float, float, float] | None:
    """'1.5-40-10' (swim-bike-run km) → (1.5, 40.0, 10.0)."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 3:
        parts = list(value)
    else:
        parts = str(value).replace(",", ".").replace("/", "-").split("-")
    try:
        legs = tuple(float(x) for x in parts)
    except ValueError:
        return None
    return legs if len(legs) == 3 and all(x > 0 for x in legs) else None


def _tri_recovery(r: Race) -> int:
    return 21 if r.run_leg >= 40 else 10 if r.run_leg >= 20 else 6


def _plan_start(cfg) -> dt.date:
    p = cfg.get("plan_start", "2026-10-12")
    return p if isinstance(p, dt.date) else dt.date.fromisoformat(str(p))


def phase_for(d: dt.date, cfg: dict) -> Phase:
    races = load_races(cfg)
    past = [r for r in races if r.date < d]
    future = [r for r in races if r.date >= d]

    if past:
        last = past[-1]
        since = (d - last.date).days
        recovery = _tri_recovery(last) if last.family == "tri" else RECOVERY_DAYS[last.family]
        if last.priority == "B":
            recovery = max(2, recovery // 2)   # tune-up race: shorter recovery
        if since <= recovery:
            nxt = future[0] if future else None
            return Phase(nxt.family if nxt else "tri", "transition",
                         f"Recovery after {last.name}", nxt,
                         (nxt.date - d).days if nxt else None, 0.5, False, 8)

    if not future:
        return Phase("tri", "base", "Triathlon maintenance", None, None, 1.0, _is_deload(d, cfg), 14)

    nxt = future[0]
    fam = nxt.family
    days_out = (nxt.date - d).days

    if nxt.priority == "B" and days_out <= 3:
        # tune-up race: a few easy days before it, no full taper
        return Phase(fam, "race_week", "Mini taper (B race)", nxt, days_out, 0.8, False, 8)
    if days_out <= 6 and nxt.priority != "B":
        vol = {"short": 0.7, "half": 0.55, "tri": 0.6, "bike": 0.6}[fam]
        return Phase(fam, "race_week", "Race week", nxt, days_out, vol, False, 8)
    taper_days = 20 if fam == "tri" and nxt.run_leg >= 40 else 13      # Ironman: 3-week taper
    if days_out <= taper_days and nxt.priority != "B":
        vol = 0.85 if fam == "short" else 0.7
        cap = 11 if fam == "short" else 13
        return Phase(fam, "taper", "Taper", nxt, days_out, vol, False, cap)

    weeks = days_out // 7
    deload = _is_deload(d, cfg)
    vol = 0.7 if deload else 1.0
    if fam == "short":
        # even when the next target is a short race, the long run keeps
        # growing for any longer race later in the season
        dist = f"{nxt.distance_km:g} km" if nxt.distance_km else "race"
        if weeks > 5:
            return Phase(fam, "base", "Aerobic base + speed", nxt, days_out, vol, deload, 14)
        return Phase(fam, "specific", f"{dist} specific", nxt, days_out, vol, deload, 14)
    if fam == "half":
        if weeks > 10:
            return Phase(fam, "base", "Half marathon base", nxt, days_out, vol, deload, 15)
        if weeks > 4:
            return Phase(fam, "build", "Half marathon build", nxt, days_out, vol, deload, 18)
        return Phase(fam, "peak", "Half marathon specific", nxt, days_out, vol, deload, 20)
    if fam == "bike":
        terrain = "hilly " if nxt.hilly else ""
        if weeks > 10:
            return Phase(fam, "base", f"Cycling base ({terrain}event)", nxt, days_out, vol, deload, 10)
        if weeks > 4:
            return Phase(fam, "build", f"Cycling build ({terrain}event)", nxt, days_out, vol, deload, 10)
        return Phase(fam, "peak", f"Cycling specific ({terrain}event)", nxt, days_out, vol, deload, 10)
    # triathlon: the long run grows towards the run leg (≈ 85 % of it, at most 30 km)
    run_cap = 13 if nxt.run_leg <= 10 else min(round(nxt.run_leg * 0.85), 30)
    long_course = nxt.run_leg >= 20
    if weeks > (12 if long_course else 8):
        return Phase(fam, "base", "Triathlon base", nxt, days_out, vol, deload, max(14, round(run_cap * 0.85)))
    return Phase(fam, "build", "Triathlon build", nxt, days_out, vol, deload, run_cap)


def _is_deload(d: dt.date, cfg) -> bool:
    week_idx = (d - _plan_start(cfg)).days // 7
    return week_idx >= 0 and week_idx % 4 == 3


def week_index(d: dt.date, cfg) -> int:
    return max(0, (d - _plan_start(cfg)).days // 7)


# ── weekly volume ───────────────────────────────────────────────────────────
# How far back the coach looks, for everything: Garmin activities, its own plans
# in your Garmin calendar, your Todoist diary and what the AI coach sees.
# Six full weeks before this week's Monday covers the longest window any rule
# needs (your longest recent run, below) plus the 4-week load average, and is
# short enough that it reflects your current fitness rather than an old block.
LOOKBACK_WEEKS = 6


def history_start(d: dt.date) -> dt.date:
    return monday_of(d) - dt.timedelta(weeks=LOOKBACK_WEEKS)


def monday_of(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def weekly_run_km(activities: list[dict], monday: dt.date) -> float:
    end = monday + dt.timedelta(days=7)
    return round(sum(a["distance_km"] for a in activities
                     if a["sport"] == "run" and monday.isoformat() <= a["date"] < end.isoformat()), 1)


def compute_week_target(activities: list[dict], d: dt.date, cfg: dict) -> dict:
    mon = monday_of(d)
    weeks = [weekly_run_km(activities, mon - dt.timedelta(weeks=k)) for k in range(1, 5)]
    chronic = sum(weeks) / 4
    last = weeks[0]
    phase = phase_for(mon, cfg)
    peak = cfg.get("peak_weekly_run_km", {}).get(phase.focus, 45)
    if phase.focus == "tri" and phase.race and phase.race.run_leg >= 20:
        peak = max(peak, round(phase.race.run_leg * (1.8 if phase.race.run_leg < 40 else 1.3)))   # 70.3 / Ironman
    floor = cfg.get("min_weekly_run_km", 15)

    return_km = cfg.get("return_to_run_below_km", 10)
    return_mode = chronic < return_km
    if return_mode:
        # Coming back to running (e.g. mostly cycling lately): aerobic fitness is
        # there but tendons and bones need time. Start at ~10 km and grow ~20 %
        # per week until the normal minimum is reached; no hard sessions yet.
        target = min(max(return_km, last * 1.2, chronic * 1.5), floor)
    else:
        target = min(chronic * 1.08, last * 1.10 + 3)   # cautious progression
        target = max(target, chronic * 0.75, floor)     # after a light week, don't drop off a cliff
    target = min(target, peak)
    target *= phase.volume_factor
    recent_runs = sorted(a["distance_km"] for a in activities if a["sport"] == "run"
                         and a["date"] >= (mon - dt.timedelta(weeks=LOOKBACK_WEEKS)).isoformat())
    longest = recent_runs[-1] if recent_runs else 0
    usual = [x for x in recent_runs if x >= 3]
    typical = usual[len(usual) // 2] if usual else 0          # your usual run (median)
    out = {"target_km": round(target, 1), "chronic_km": round(chronic, 1), "longest_run_km": round(longest, 1),
           "typical_run_km": round(typical, 1),
           "last_week_km": last, "phase": phase.name, "deload": phase.deload,
           "return_mode": return_mode}
    out.update(_bike_week(activities, mon, cfg, phase))
    return out


def _bike_week(activities: list[dict], mon: dt.date, cfg: dict, phase: Phase) -> dict:
    """Weekly riding hours, current longest ride and typical speed from the last weeks."""
    def hours(k):
        lo, hi = (mon - dt.timedelta(weeks=k)).isoformat(), (mon - dt.timedelta(weeks=k - 1)).isoformat()
        return sum(a["duration_min"] for a in activities if a["sport"] == "bike" and lo <= a["date"] < hi) / 60
    weeks = [hours(k) for k in range(1, 5)]
    chronic, last = sum(weeks) / 4, weeks[0]
    recent = [a for a in activities if a["sport"] == "bike"
              and a["date"] >= (mon - dt.timedelta(weeks=4)).isoformat()]
    longest = max((a["duration_min"] for a in recent), default=0)
    long_rides = [a for a in activities if a["sport"] == "bike" and a["duration_min"] >= 60 and a["distance_km"] > 0]
    speeds = sorted(a["distance_km"] / (a["duration_min"] / 60) for a in long_rides)
    speed = speeds[len(speeds) // 2] if speeds else 25.0
    floor = cfg.get("min_weekly_bike_hours", 3)
    peak = cfg.get("peak_weekly_bike_hours", 10)
    target = min(chronic * 1.08, last * 1.10 + 0.5)
    target = min(max(target, chronic * 0.75, floor), peak) * phase.volume_factor
    return {"bike_hours": round(target, 1), "bike_chronic_hours": round(chronic, 1),
            "longest_ride_min": round(longest), "ride_speed_kmh": round(min(max(speed, 15), 40), 1)}


# ── sessions ────────────────────────────────────────────────────────────────
def _round_half(x: float) -> float:
    return round(x * 2) / 2


def _configured_template(phase: Phase, cfg: dict) -> dict:
    tpls = cfg["weekly_templates"]
    if phase.focus == "tri":
        return tpls["triathlon"]
    if phase.focus == "bike":
        return tpls.get("cycling") or tpls["run_focus"]
    return tpls["run_focus"]


def key_session(phase: Phase) -> tuple[str, str]:
    """The session the week is built around: (template kind, sport learned from)."""
    return ("bike_long", "bike") if phase.focus == "bike" else ("run_long", "run")


def key_day(template: dict, kind: str) -> int | None:
    for i, day in enumerate(WEEKDAYS):
        if kind in (template.get(day) or []):
            return i
    return None


def _rotate(template: dict, frm: int, to: int) -> dict:
    """Shifts the whole week so the session on day `frm` lands on day `to`,
    keeping the spacing between hard days, the long day and the rest day."""
    off = (to - frm) % 7
    return {WEEKDAYS[(i + off) % 7]: template.get(WEEKDAYS[i], []) for i in range(7)}


def template_for(phase: Phase, cfg: dict) -> dict:
    tpl = _configured_template(phase, cfg)
    kind, sport = key_session(phase)
    learned = (cfg.get("_learned_days") or {}).get(sport)
    cur = key_day(tpl, kind)
    if learned in WEEKDAYS and cur is not None and WEEKDAYS.index(learned) != cur:
        tpl = _rotate(tpl, cur, WEEKDAYS.index(learned))
    return tpl


def learn_long_day(activities: list[dict], d: dt.date, sport: str = "run",
                   weeks: int = 4, need: int = 3) -> str | None:
    """Weekday of your longest run (or ride) if it was the same in at least
    `need` of the last `weeks` complete weeks; otherwise None."""
    from collections import Counter
    mon = monday_of(d)
    days = []
    for k in range(1, weeks + 1):
        lo, hi = mon - dt.timedelta(weeks=k), mon - dt.timedelta(weeks=k - 1)
        items = [a for a in activities if a["sport"] == sport and lo.isoformat() <= a["date"] < hi.isoformat()]
        if len(items) < 2:
            continue
        longest = max(items, key=lambda a: a["distance_km"] if sport == "run" else a["duration_min"])
        days.append(dt.date.fromisoformat(longest["date"]).weekday())
    if not days:
        return None
    day, n = Counter(days).most_common(1)[0]
    return WEEKDAYS[day] if n >= need else None


LONG_RIDE_SHARE = {"base": 0.55, "build": 0.7, "peak": 0.85, "taper": 0.5, "race_week": 0.3, "transition": 0.3}


def bike_distribution(template: dict, wt: dict, phase: Phase) -> dict:
    """Minutes for long, quality and easy rides in a cycling-focused week."""
    kinds = [k for day in WEEKDAYS for k in template.get(day, [])]
    n_q, n_easy = kinds.count("bike_quality"), kinds.count("bike_easy")
    week_min = wt.get("bike_hours", 6) * 60
    speed = wt.get("ride_speed_kmh", 25)
    event_min = phase.race.est_hours(speed) * 60 if phase.race and phase.race.family == "bike" else 240
    current = max(wt.get("longest_ride_min", 0), 60)
    long_min = min(event_min * LONG_RIDE_SHARE.get(phase.name, 0.6),   # what the phase calls for
                   current * 1.15,                                     # +15 % over your longest recent ride
                   360)
    long_min = max(round(long_min / 15) * 15, 60) if "bike_long" in kinds else 0
    q_min = 60 if phase.name in ("taper", "race_week") else 75
    rest = week_min - long_min - q_min * n_q
    easy_cap = {"race_week": 60, "taper": 75}.get(phase.name, 90)
    easy_min = min(max(round(rest / n_easy / 15) * 15, 45), easy_cap) if n_easy else 0
    return {"long": long_min, "quality": q_min, "easy": easy_min, "event_min": round(event_min)}


def run_distribution(template: dict, week_km: float, phase: Phase, return_mode: bool = False,
                     long_floor: float = 0, easy_floor: float = 4) -> dict:
    kinds = [k for day in WEEKDAYS for k in template.get(day, [])]
    if return_mode:  # quality sessions become easy runs with strides
        kinds = ["run_strides" if k == "run_quality" else k for k in kinds]
    n_easy = sum(k in ("run_easy", "run_strides") for k in kinds)
    n_q = kinds.count("run_quality")
    has_long = "run_long" in kinds
    has_brick = "run_brick" in kinds

    n_runs = n_easy + n_q + int(has_long)
    long_share = 0.40 if n_runs <= 3 else 0.30
    long_km = min(_round_half(week_km * long_share), phase.long_cap_km) if has_long else 0
    if has_long and phase.name not in ("taper", "race_week", "transition"):
        # never shorter than what your legs already handle (or your configured minimum)
        long_km = max(long_km, min(long_floor, phase.long_cap_km))
    q_km = min(max(_round_half(week_km * 0.28 if n_runs <= 3 else week_km * 0.20), 6), 13) if n_q else 0
    brick_km = 3.0 if has_brick else 0
    rest_km = week_km - long_km - q_km * n_q - brick_km
    easy_km = min(max(_round_half(rest_km / n_easy), easy_floor), 12) if n_easy else 0
    if long_km and easy_km > long_km * 0.85:
        # the long run must be the longest run of the week
        easy_km = max(_round_half(long_km * 0.85), min(easy_floor, long_km), 4)
    return {"long": long_km, "quality": q_km, "easy": easy_km, "brick": brick_km}


# Quality runs: Runna-style sessions with pace targets from your current
# fitness (see paces.py). Each entry is (name, builder(n), (min n, max n)):
# the number of reps n is chosen so the whole session is close to the week's
# quality distance. Rotated week by week.
def _rolling_400s(n):  return [warmup(15), repeat(n, [at(400, "5k"), at(400, "mp")]), cooldown(10)]
def _over_unders(n):   return [warmup(15), repeat(n, [at(1000, "thr"), at(1000, "5k"), jog(90)]), cooldown(10)]
def _hills(n):         return [warmup(15), repeat(n, [run_t(60, 4), jog(90)]), cooldown(10)]
def _fartlek(n):       return [warmup(15), repeat(n, [for_(60, "5k"), jog(60, 2)]), cooldown(10)]
def _km_reps(n):       return [warmup(15), repeat(n, [at(1000, "10k"), jog(90)]), cooldown(10)]
def _400s(n):          return [warmup(15), repeat(n, [at(400, "5k"), jog(75)]), cooldown(10)]
def _800s(n):          return [warmup(15), repeat(n, [at(800, "5k"), jog(120)]), cooldown(10)]
def _race_2k(n):       return [warmup(15), repeat(n, [at(2000, "10k"), jog(150)]), cooldown(10)]
def _pyramid(n):       return [warmup(15), at(400, "5k"), jog(90), at(800, "5k"), jog(90), at(1200, "10k"),
                               jog(120), at(800, "5k"), jog(90), at(400, "5k"), cooldown(10)]
def _tempo(n):         return [warmup(15), for_(n * 300, "thr"), cooldown(10)]
def _cruise(n):        return [warmup(15), repeat(n, [at(1600, "thr"), jog(60)]), cooldown(10)]
def _thr_10(n):        return [warmup(15), repeat(n, [for_(600, "thr"), jog(120)]), cooldown(10)]
def _progression(n):   return [warmup(10), for_(n * 300, "mp"), for_(n * 240, "hm"), cooldown(10)]
def _hm_3k(n):         return [warmup(15), repeat(n, [at(3000, "hm"), jog(120)]), cooldown(10)]
def _hm_5k(n):         return [warmup(15), repeat(n, [at(5000, "hm"), jog(180)]), cooldown(10)]
def _hm_2k(n):         return [warmup(15), repeat(n, [at(2000, "hm"), jog(120)]), cooldown(10)]
def _long_tempo(n):    return [warmup(15), at(n * 1000, "hm"), cooldown(10)]
def _over_unders_2k(n): return [warmup(15), repeat(n, [at(1000, "hm"), at(1000, "thr")]), cooldown(10)]
def _openers_400(n):   return [warmup(12), repeat(n, [at(400, "5k"), jog(120)]), cooldown(8)]
def _openers_1k(n):    return [warmup(15), repeat(n, [at(1000, "hm"), jog(120)]), cooldown(8)]
def _sharpener(n):     return [warmup(15), repeat(n, [at(1000, "10k"), jog(120)]), cooldown(10)]


QUALITY = {
    ("short", "base"): [("Rolling 400s", _rolling_400s, (3, 6)), ("Over-unders 1K", _over_unders, (2, 4)),
                        ("Hill reps 60\"", _hills, (6, 10)), ("Fartlek 1'/1'", _fartlek, (8, 12))],
    ("short", "specific"): [("1 km repeats", _km_reps, (4, 7)), ("Pyramid", _pyramid, (1, 1)),
                            ("Rolling 400s", _rolling_400s, (4, 8)), ("2 km at 10K pace", _race_2k, (2, 4)),
                            ("800s", _800s, (4, 7))],
    ("short", "taper"): [("Sharpener 1 km", _sharpener, (3, 4))],
    ("short", "race_week"): [("Openers 400 m", _openers_400, (4, 4))],
    ("half", "base"): [("Over-unders 1K", _over_unders, (2, 4)), ("Tempo", _tempo, (3, 5)),
                       ("Rolling 400s", _rolling_400s, (3, 6)), ("Hill reps 60\"", _hills, (6, 10))],
    ("half", "build"): [("Cruise intervals 1.6 km", _cruise, (3, 5)), ("Threshold 10'", _thr_10, (2, 4)),
                        ("Over-unders 2K", _over_unders_2k, (3, 5)), ("Progression", _progression, (4, 7))],
    ("half", "peak"): [("HM pace 3 km", _hm_3k, (2, 4)), ("HM pace 5 km", _hm_5k, (1, 3)),
                       ("Long tempo", _long_tempo, (6, 10)), ("Threshold 10'", _thr_10, (3, 4))],
    ("half", "taper"): [("HM pace 2 km", _hm_2k, (2, 3))],
    ("half", "race_week"): [("Openers 1 km", _openers_1k, (3, 3))],
    ("tri", "base"): [("Fartlek 1'/1'", _fartlek, (8, 12)), ("Tempo", _tempo, (3, 5))],
    ("tri", "build"): [("1 km repeats", _km_reps, (4, 6)), ("Threshold 10'", _thr_10, (2, 3))],
}
QUALITY[("tri", "taper")] = QUALITY[("short", "taper")]
QUALITY[("tri", "race_week")] = QUALITY[("short", "race_week")]

ZONE_PACE = {1: "easy", 2: "easy", 3: "mp", 4: "thr", 5: "5k"}   # to estimate distances of time-based steps


def steps_km(steps: list[dict]) -> float:
    """Approximate distance of a run's steps, using your current paces."""
    from . import paces
    tot = 0.0
    for st in steps:
        if st["type"] == "repeat":
            tot += st["iterations"] * steps_km(st["steps"])
        elif st["end"] == "distance":
            tot += st["value"] / 1000
        else:
            label = st.get("pace") or ZONE_PACE.get(st.get("zone") or 1, "easy")
            if st["type"] in ("recovery", "rest"):
                label = "easy"
            tot += st["value"] / (paces.pace(label) or 360) * (0.85 if st["type"] == "rest" else 1)
    return tot


def describe_steps(steps: list[dict]) -> str:
    """'15' easy · 4 × (400 m at 4:20/km, 400 m at 5:00/km) · 10' easy'."""
    from . import paces

    def amount(st):
        if st["end"] == "distance":
            v = st["value"]
            return f"{v / 1000:g} km" if v >= 1000 else f"{v:g} m"
        sec = st["value"]
        return f"{sec // 60:g}'" if sec >= 60 and sec % 60 == 0 else (f"{sec / 60:.1f}'" if sec >= 60 else f"{sec:g}\"")

    def one(st):
        if st["type"] == "repeat":
            return f"{st['iterations']} × (" + ", ".join(one(x) for x in st["steps"]) + ")"
        if st["type"] in ("warmup", "cooldown"):
            return f"{amount(st)} easy"
        if st["type"] == "recovery":
            return f"{amount(st)} jog"
        if st.get("pace"):
            return f"{amount(st)} at {paces.fmt(paces.pace(st['pace']))}/km"
        if st.get("zone"):
            return f"{amount(st)} hard (Z{st['zone']}, uphill if you can)" if st["zone"] >= 4 else f"{amount(st)} Z{st['zone']}"
        return amount(st)
    return " · ".join(one(st) for st in steps)


def make_run(kind: str, km: float, phase: Phase, wk: int = 0) -> Session:
    km = _round_half(km)
    if kind == "run_quality":
        opts = QUALITY.get((phase.focus, phase.name)) or QUALITY[(phase.focus, "base")]
        name, build, (lo, hi) = opts[wk % len(opts)]
        # as many reps as fit the week's quality distance
        n = min(range(lo, hi + 1), key=lambda k: abs(steps_km(build(k)) - km))
        steps = build(n)
        total = _round_half(steps_km(steps))
        return Session(kind, "run", f"Quality · {name}, {total:g} km", "hard", total, None,
                       describe_steps(steps) + ". Paces are set from your current fitness: if a rep feels "
                       "much harder than it should, run it by feel, and drop the last rep if your heart "
                       "rate doesn't come down in the recoveries.", steps)
    if kind == "run_long":
        from . import paces
        finish = 0.0
        if phase.name in ("build", "specific", "peak") and km >= 10 and not phase.deload:
            # race-specific long run: the last part at goal pace, on tired legs
            share = {"build": 0.15, "specific": 0.2, "peak": 0.3}[phase.name]
            finish = min(_round_half(km * share), 6 if phase.focus == "half" else 3)
        if finish >= 1:
            label = "hm" if phase.focus in ("half", "tri") else "10k" if phase.focus == "short" else "mp"
            steps = [run_d((km - finish) * 1000, 2), at(finish * 1000, label)]
            detail = (f"Long run {km:g} km: {km - finish:g} km easy in Z2, then the last {finish:g} km at "
                      f"{paces.LABELS[label]} ({paces.fmt(paces.pace(label))}/km). Drink if it goes over 75'.")
        else:
            steps = [run_d(km * 1000, 2)]
            detail = f"Long run {km:g} km in Z2, conversational pace. Drink if it goes over 75'."
        return Session(kind, "run", f"Long run {km:g} km", "moderate", km, None, detail, steps)
    if kind == "run_strides":
        steps = [run_d(km * 1000, 2), jog(60), repeat(6, [run_t(20, None), jog(70)])]
        return Session(kind, "run", f"Easy run {km:g} km + 6 strides", "easy", km, None,
                       f"{km:g} km in Z2, 1' walk or easy jog, then 6 × 20\" strides (fast but relaxed) "
                       "with 70\" easy jog between.", steps)
    if kind == "run_brick":
        return Session(kind, "run", f"Brick run {km:g} km", "moderate", km, None,
                       "Straight off the bike: 1 km easy, the rest in Z3 at triathlon pace.",
                       [run_d(1000, 2), run_d(max(km - 1, 1) * 1000, 3)])
    # easy run
    return Session("run_easy", "run", f"Easy run {km:g} km", "easy", km, None,
                   f"{km:g} km in Z2. If you can't hold Z2, walk the hills.",
                   [run_d(km * 1000, 2)])


BIKE_QUALITY = {
    # (phase, hilly course?) → options rotated week by week
    ("base", False): [
        ("Tempo 3×12'", [warmup(15), repeat(3, [run_t(720, 3), jog(300, 2)]), cooldown(10)]),
        ("Cadence + tempo 4×8'", [warmup(15), repeat(4, [run_t(480, 3), jog(240, 2)]), cooldown(10)]),
    ],
    ("build", False): [
        ("Sweet spot 3×15'", [warmup(15), repeat(3, [run_t(900, 3), jog(300, 2)]), cooldown(10)]),
        ("Threshold 4×8'", [warmup(15), repeat(4, [run_t(480, 4), jog(240, 2)]), cooldown(10)]),
    ],
    ("build", True): [
        ("Hill repeats 6×5'", [warmup(15), repeat(6, [run_t(300, 4), jog(180, 2)]), cooldown(10)]),
        ("Sweet spot 3×15'", [warmup(15), repeat(3, [run_t(900, 3), jog(300, 2)]), cooldown(10)]),
    ],
    ("peak", False): [
        ("Threshold 2×20'", [warmup(15), repeat(2, [run_t(1200, 4), jog(360, 2)]), cooldown(10)]),
        ("Race pace 60'", [warmup(15), run_t(3600, 3), cooldown(10)]),
    ],
    ("peak", True): [
        ("Long climbs 3×12'", [warmup(15), repeat(3, [run_t(720, 4), jog(360, 2)]), cooldown(10)]),
        ("Threshold 2×20'", [warmup(15), repeat(2, [run_t(1200, 4), jog(360, 2)]), cooldown(10)]),
    ],
    ("taper", False): [("Openers 4×4'", [warmup(15), repeat(4, [run_t(240, 4), jog(240, 2)]), cooldown(10)])],
    ("race_week", False): [("Openers 3×3'", [warmup(15), repeat(3, [run_t(180, 4), jog(240, 2)]), cooldown(10)])],
}


def make_bike(kind: str, phase: Phase, minutes: float | None = None, wk: int = 0) -> Session:
    tri = phase.focus == "tri"
    cycling = phase.focus == "bike"
    hilly = bool(cycling and phase.race and phase.race.hilly)
    if kind == "bike_quality":
        if cycling:
            opts = (BIKE_QUALITY.get((phase.name, hilly)) or BIKE_QUALITY.get((phase.name, False))
                    or BIKE_QUALITY[("base", False)])
            name, steps = opts[wk % len(opts)]
            m = round(sum(st["value"] * (st.get("iterations", 1)) if st["type"] != "repeat" else
                          st["iterations"] * sum(x["value"] for x in st["steps"]) for st in steps) / 60)
            return Session(kind, "bike", f"Bike · {name}", "hard", None, m,
                           "Warm up easy, main set in the target zone, spin easy between reps"
                           + (" — do the reps on a climb if you can." if "Hill" in name or "climb" in name else "."),
                           steps)
        if phase.name in ("transition", "race_week") or not tri:
            return Session(kind, "bike", "Bike 5×5' tempo", "moderate", None, 60,
                           "Ride with 5×5' in Z3 (3' easy between).",
                           [warmup(15), repeat(5, [run_t(300, 3), jog(180)]), cooldown(10)])
        return Session(kind, "bike", "Bike 4×8' threshold", "hard", None, 75,
                       "4×8' in Z4 with 4' easy between reps.",
                       [warmup(15), repeat(4, [run_t(480, 4), jog(240)]), cooldown(12)])
    if kind == "bike_long":
        m = minutes or (120 if tri and phase.name == "build" else 90)
        if tri and phase.race and phase.race.legs and phase.race.legs[1] > 40 and not minutes:
            # long-course triathlon: the long ride builds towards the bike leg (≈ 28 km/h, up to 5 h)
            share = {"base": 0.5, "build": 0.7}.get(phase.name, 0.4)
            m = min(max(round(phase.race.legs[1] / 28 * 60 * share / 15) * 15, 90), 300)
        if cycling and hilly and phase.name in ("build", "peak") and m >= 120:
            # rehearse the climbing: two 20' blocks in Z3 in the middle of the ride
            z2 = (m - 50) / 2
            steps = [run_t(z2 * 60, 2), repeat(2, [run_t(1200, 3), jog(300, 2)]), run_t(z2 * 60, 2)]
            detail = (f"{m:g}' mostly in Z2 with 2×20' steady climbing in Z3 in the middle. "
                      "Eat every 30' and drink regularly, as on race day.")
        else:
            steps = [run_t(m * 60, 2)]
            detail = f"{m:g}' steady in Z2. Practise eating/drinking every 30'."
        return Session(kind, "bike", f"Long ride {m:g}'", "moderate", None, m, detail, steps)
    if kind == "bike_spin":
        m = minutes or 35
        return Session("bike_spin", "bike", f"Recovery spin {m:g}'", "easy", None, m,
                       f"{m:g}' very easy (Z1), light gear, 90+ rpm, a few hours after your run: it flushes "
                       "the legs and adds aerobic time without any impact.", [run_t(m * 60, 1)])
    m = minutes or 60
    return Session("bike_easy", "bike", f"Easy ride {m:g}'", "easy", None, m,
                   f"{m:g}' in Z1-Z2, high cadence (85-95 rpm).", [run_t(m * 60, 2)])


# What you can swim right now, from your recent swims (set each run, see swim_capacity)
_swim_cap: dict = {}


def swim_capacity(acts: list[dict], d: dt.date) -> dict:
    """Your current swim capacity from the last 4 weeks of swims, with their intervals:
    the next swim is at most ~25 % more than the most you've swum lately, in reps no
    longer than what you swam without stopping. No recent swims: a 600 m restart."""
    since = (d - dt.timedelta(days=28)).isoformat()
    recent = [a for a in acts if a["sport"] == "swim" and since <= a["date"] < d.isoformat()]
    if not recent:
        return {"cap_m": 600, "rep_m": 50, "why": "no swims in the last 4 weeks: a short restart"}
    best = max(a.get("swum_m") or a["distance_km"] * 1000 for a in recent)
    longest = max(a.get("longest_m") or 0 for a in recent) or best
    cap = max(400, int(round(best * 1.25 / 100)) * 100)
    rep = 50 if longest < 200 else 100 if longest < 400 else 200
    last = max(recent, key=lambda a: a["date"])
    detail = f"last swim {last.get('swum_m') or round(last['distance_km'] * 1000)} m"
    if last.get("stops"):
        detail += f" with {last['stops']} stops (longest stretch {last.get('longest_m')} m)"
    return {"cap_m": cap, "rep_m": rep, "best_m": round(best), "longest_m": round(longest),
            "why": f"{detail}: building up from what you can swim now"}


def set_swim_capacity(c: dict | None) -> None:
    _swim_cap.clear()
    _swim_cap.update(c or {})


def _build_up_swim(kind: str, cap: int, rep: int, why: str) -> Session:
    """A shorter swim sized to your current capacity: drills, then reps you can hold."""
    drills = max(4, min(8, (cap - 200) // 100))
    main_m = max(cap - 200 - drills * 50, rep)
    n = max(1, main_m // rep)
    steps = [swim(100, "warmup", "easy, any stroke"),
             repeat(drills, [swim(50, "interval", "technique: alternate drills"), rest(20)]),
             repeat(n, [swim(rep, "interval", "easy and smooth, stop if form goes"), rest(30)]),
             swim(100, "cooldown", "easy")]
    dist = 200 + drills * 50 + n * rep
    return Session(kind if kind == "swim_technique" else "swim_technique", "swim", f"Build-up swim {dist} m",
                   "easy", dist / 1000, None,
                   f"100 easy, {drills}×50 drills (20\" rest), {n}×{rep} easy (30\" rest), 100 easy. Sized to "
                   f"what you can swim now ({why}); it grows ~25 % as you do.", steps)


def make_swim(kind: str, phase: Phase, short: bool = False) -> Session:
    s = _make_swim(kind, phase, short)
    cap = _swim_cap.get("cap_m")
    if cap and (s.distance_km or 0) * 1000 > cap:
        s = _build_up_swim(kind, int(cap), int(_swim_cap.get("rep_m", 50)), _swim_cap.get("why", ""))
    return s if phase.focus == "tri" else optional_swim(s, phase)


def _make_swim(kind: str, phase: Phase, short: bool = False) -> Session:
    tri = phase.focus == "tri" and phase.name == "build"
    if kind == "swim_css" and not short:
        n = 12 if tri else 10
        steps = [swim(400, "warmup", "easy, mixed strokes"), repeat(n, [swim(100, "interval", "strong and even"), rest(15)]),
                 swim(200, "cooldown", "easy")]
        dist = 600 + n * 100
        return Session(kind, "swim", f"Swim {n}×100 hard", "hard", dist / 1000, None,
                       f"400 warm-up, {n}×100 at threshold pace (15\" rest), 200 easy.", steps)
    if kind == "swim_endurance" and not short:
        n = 5 if tri else 4
        if phase.race and phase.race.legs and phase.race.legs[0] >= 1.9:
            n = 10 if phase.race.legs[0] >= 3.8 else 7         # 70.3 / Ironman swim
        steps = [swim(300, "warmup", "easy"), repeat(n, [swim(300, "interval", "steady, comfortable breathing"), rest(30)]),
                 repeat(4, [swim(50, "interval", "fast"), rest(20)]), swim(200, "cooldown", "easy")]
        dist = 300 + n * 300 + 200 + 200
        return Session(kind, "swim", f"Endurance swim {dist} m", "moderate", dist / 1000, None,
                       f"300 warm-up, {n}×300 steady (30\" rest), 4×50 fast, 200 easy.", steps)
    # technique (also the short version for tired days)
    reps = 4 if short else 6
    steps = [swim(300, "warmup", "easy"), repeat(8, [swim(50, "interval", "technique: alternate drills"), rest(20)]),
             repeat(reps, [swim(100, "interval", "Z2, long strokes"), rest(20)]), swim(200, "cooldown", "easy")]
    dist = 300 + 400 + reps * 100 + 200
    return Session("swim_technique", "swim", f"Technique swim {dist} m", "easy", dist / 1000, None,
                   "300 warm-up, 8×50 technique drills (catch-up, 3-3, fists), "
                   f"{reps}×100 aerobic, 200 easy.", steps)


def optional_swim(s: Session, phase: Phase) -> Session:
    """While the next goal isn't a triathlon, swims are optional extras: easy
    aerobic work that keeps your feel for the water, never a priority."""
    until = f" until {phase.race.name}" if phase.race else ""
    return Session(s.kind, s.sport, f"Optional · {s.title}", "easy", s.distance_km, s.duration_min,
                   f"Optional: running is the focus{until}. Swim if you feel like it; skipping it is fine. "
                   + s.detail, s.steps, False, True)


def make_rest(reason: str = "") -> Session:
    return Session("rest", "other", "Rest / mobility 20'", "rest", None, 20,
                   (reason + " " if reason else "") + "Full rest or 20' of gentle mobility and stretching.",
                   [run_t(20 * 60, None)])


KEY_KINDS = ("run_long", "run_quality", "bike_long", "bike_quality")


def apply_constraints(tpl: dict, mon: dt.date, c: dict, today: dt.date | None = None) -> dict:
    """Your notes about the week, applied to its template (only from `today` on):
    - moves: [(kind, date)]: that session goes to that day, swapping days;
    - unavailable: {dates}: no training; its long run/quality session moves to
      the nearest free day (preferably earlier, not next to another hard day);
    - no_swim_until: date: swims are dropped until then."""
    days = {wd: list(tpl.get(wd) or []) for wd in WEEKDAYS}
    first = max(0, ((today or mon) - mon).days)
    if first > 6:
        return days

    def idx(date):
        i = (date - mon).days
        return i if first <= i <= 6 else None

    def has(wd, kind):
        return any(k == kind or (kind == "swim" and k.startswith("swim_")) for k in days[wd])

    for kind, date in c.get("moves") or []:
        t = idx(date)
        if t is None or has(WEEKDAYS[t], kind):
            continue
        src = next((WEEKDAYS[i] for i in range(first, 7) if i != t and has(WEEKDAYS[i], kind)), None)
        if src:
            days[src], days[WEEKDAYS[t]] = days[WEEKDAYS[t]], days[src]
    off = sorted(i for i in (idx(x) for x in c.get("unavailable") or []) if i is not None)

    def place(k: str, t: int, depth: int = 0) -> None:
        """Puts key session k on the best day near day t: not next to another hard session
        of the same sport, not on top of another sport's session, then the nearest one
        (a rest day only if it's as near, earlier first).
        A key session of another sport already there moves on the same way."""
        sport = k.split("_")[0]

        def same_hard(i):
            return 0 <= i <= 6 and any(x in KEY_KINDS and x.startswith(sport + "_") for x in days[WEEKDAYS[i]])
        cands = [i for i in range(first, 7) if i not in off and i != t and not same_hard(i)]
        if not cands:
            return
        def doubles(i):   # would leave two sports on that day
            return any(x not in ("rest", "off") and not x.startswith(sport + "_") for x in days[WEEKDAYS[i]])
        best = min(cands, key=lambda i: (same_hard(i - 1) or same_hard(i + 1), doubles(i), abs(i - t),
                                         days[WEEKDAYS[i]] == ["rest"], i > t))
        wd = WEEKDAYS[best]
        displaced = [x for x in days[wd] if x in KEY_KINDS]
        days[wd] = [x for x in days[wd] if not x.startswith(sport + "_") and x != "rest" and x not in KEY_KINDS] + [k]
        for x in displaced:
            if depth < 2:
                place(x, best, depth + 1)

    for t in off:
        wd = WEEKDAYS[t]
        moved = [k for k in days[wd] if k in KEY_KINDS]
        days[wd] = ["off"]
        for k in moved:
            place(k, t)
    until = c.get("no_swim_until")
    if until:
        for i in range(first, 7):
            if mon + dt.timedelta(days=i) <= until:
                wd = WEEKDAYS[i]
                days[wd] = [k for k in days[wd] if not k.startswith("swim_")] or ["rest"]
    return days


def week_parts(d: dt.date, cfg: dict, week: float | dict, return_mode: bool = False):
    """(phase, template, run distances, ride minutes or None) for the week of day d."""
    wt = week if isinstance(week, dict) else {"target_km": week}
    return_mode = return_mode or wt.get("return_mode", False)
    phase = phase_for(d, cfg)
    tpl = template_for(phase, cfg)
    if cfg.get("_constraints"):
        tpl = apply_constraints(tpl, monday_of(d), cfg["_constraints"], cfg.get("_today"))
    long_floor = max(float(cfg.get("min_long_run_km") or 0), _round_half(wt.get("longest_run_km", 0) * 1.1))
    # no easy run shorter than the runs you've been doing lately (a 4 km run after
    # regular 6 km ones is too short to be worth it), between 4 and 8 km
    easy_floor = min(max(_round_half(wt.get("typical_run_km") or 0), 4), 8)
    if phase.name in ("taper", "race_week", "transition"):
        easy_floor = 4
    dist = run_distribution(tpl, wt["target_km"], phase, return_mode, long_floor, easy_floor)
    kinds = [k for day in WEEKDAYS for k in tpl.get(day, [])]
    if return_mode:
        kinds = ["run_strides" if k == "run_quality" else k for k in kinds]
    planned = (dist["long"] * ("run_long" in kinds) + dist["quality"] * kinds.count("run_quality")
               + dist["easy"] * sum(k in ("run_easy", "run_strides") for k in kinds) + dist["brick"])
    if isinstance(week, dict) and planned > wt["target_km"] + 0.5:
        # the floors make the week a bit bigger than the volume ramp alone: say so
        week["ramp_km"] = week.get("ramp_km", wt["target_km"])
        week["target_km"] = round(planned, 1)
    bdist = bike_distribution(tpl, wt, phase) if phase.focus == "bike" else None
    return phase, tpl, dist, bdist


def plan_day(d: dt.date, cfg: dict, week: float | dict, return_mode: bool = False) -> tuple[Phase, list[Session]]:
    """`week` is the week target dict from compute_week_target (or just the running km)."""
    wt = week if isinstance(week, dict) else {"target_km": week}
    return_mode = return_mode or wt.get("return_mode", False)
    phase, tpl, dist, bdist = week_parts(d, cfg, wt, return_mode)
    wk = week_index(d, cfg)
    sessions: list[Session] = []
    races_today = [r for r in load_races(cfg) if r.date == d]
    if races_today:
        r = races_today[0]
        sport = {"tri": "other", "bike": "bike"}.get(r.family, "run")
        tip = ("Race day! Start conservatively, eat every 30' and drink regularly." if r.family == "bike"
               else "Race day! Warm up 15' with 3 strides. Start controlled.")
        return phase, [Session("race", sport, f"RACE: {r.name}", "hard", None, None, tip, [])]
    if phase.name == "race_week" and phase.days_out is not None and phase.days_out <= 1:
        if phase.days_out == 1 and phase.focus == "bike":
            return phase, [Session("bike_easy", "bike", "Openers ride 45'", "easy", None, 45,
                                   "45' easy with 3×1' brisk (Z4) to wake the legs up.",
                                   [warmup(20), repeat(3, [run_t(60, 4), jog(180, 2)]), cooldown(13)])]
        return phase, [make_run("run_strides", 4, phase)] if phase.days_out == 1 else [make_rest()]

    for kind in tpl.get(WEEKDAYS[d.weekday()], []):
        if kind == "off":
            sessions.append(make_rest("You told the coach you can't train today."))
        elif kind == "rest":
            sessions.append(make_rest())
        elif kind.startswith("run_"):
            if phase.name == "transition" and kind in ("run_quality", "run_long"):
                kind = "run_easy"
            if return_mode and kind == "run_quality":
                kind = "run_strides"
            key = {"run_long": "long", "run_quality": "quality", "run_brick": "brick"}.get(kind, "easy")
            sessions.append(make_run(kind, dist[key], phase, wk))
        elif kind.startswith("bike_"):
            if phase.name == "transition" and kind != "bike_easy":
                kind = "bike_easy"
            if phase.name == "race_week" and kind == "bike_long":
                kind = "bike_easy"
            if phase.name == "race_week" and kind == "bike_quality" and phase.focus != "bike":
                kind = "bike_easy"
            if bdist:
                minutes = {"bike_long": bdist["long"], "bike_easy": bdist["easy"]}.get(kind)
                # alternate the interval sessions within the week and from week to week
                nth = sum(k == "bike_quality" for day in WEEKDAYS[:d.weekday()] for k in tpl.get(day, []))
                sessions.append(make_bike(kind, phase, minutes or None, wk * 2 + nth))
            else:
                sessions.append(make_bike(kind, phase))
        elif kind.startswith("swim_"):
            sessions.append(make_swim(kind, phase, short=phase.name == "transition"))
        elif kind in ("strength", "strength_core"):
            if phase.name == "race_week" and phase.days_out is not None and phase.days_out <= 3:
                continue                                  # nothing in the last days before a race
            from .strength import make_strength
            sessions.append(make_strength(kind, phase, wk))
    return phase, sessions or [make_rest()]


def planned_run_km(sessions: list[Session]) -> float:
    return sum(s.distance_km or 0 for s in sessions if s.sport == "run")


def to_jsonable(x: Any) -> Any:
    if isinstance(x, (dt.date,)):
        return x.isoformat()
    return x
