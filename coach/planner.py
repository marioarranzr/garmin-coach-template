"""Periodized plan: phases, weekly volume and each day's sessions.

Principles:
  * ~80 % of the time in zone 2 and ~20 % quality (polarized training).
  * 4-week blocks: 3 progressive load weeks + 1 deload week (-30 %).
  * Weekly volume starts from what you ACTUALLY ran in the last 4 weeks and
    grows by ~8-10 % per week at most.
  * Taper before each race and recovery after it.
Intensity targets use Garmin heart-rate zones (Z1-Z5).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Any

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

FAMILY = {"road": "short", "5k": "short", "10k": "short", "half": "half", "marathon": "half",
          "tri_sprint": "tri", "tri_olympic": "tri", "bike": "bike"}
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

    @property
    def hilly(self) -> bool:
        """Cycling course with ≥ 10 m of climbing per km (e.g. 1000 m over 100 km)."""
        return bool(self.elevation_m and self.distance_km and self.elevation_m / self.distance_km >= 10)

    def est_hours(self, speed_kmh: float) -> float:
        """Rough cycling event duration: flat speed plus ~45 min per 1000 m climbed."""
        return (self.distance_km or 100) / speed_kmh + (self.elevation_m or 0) / 1000 * 0.75

    @property
    def family(self) -> str:
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

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Session":
        return Session(**d)


# ── abstract steps (translated to Garmin JSON in workouts.py) ───────────────
def warmup(minutes, zone=2):   return {"type": "warmup", "end": "time", "value": minutes * 60, "zone": zone}
def cooldown(minutes, zone=1): return {"type": "cooldown", "end": "time", "value": minutes * 60, "zone": zone}
def run_t(seconds, zone):      return {"type": "interval", "end": "time", "value": seconds, "zone": zone}
def run_d(meters, zone):       return {"type": "interval", "end": "distance", "value": meters, "zone": zone}
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
        dist = r.get("distance_km") or KIND_DISTANCE_KM.get(r.get("kind", "half"))
        races.append(Race(r["name"], d, r.get("kind", "half"), str(r.get("priority", "A")).upper(),
                          dist, r.get("elevation_m")))
    return sorted(races, key=lambda r: r.date)


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
        recovery = RECOVERY_DAYS[last.family]
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
    if days_out <= 13 and nxt.priority != "B":
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
    # triathlon
    if weeks > 8:
        return Phase(fam, "base", "Triathlon base", nxt, days_out, vol, deload, 14)
    return Phase(fam, "build", "Triathlon build", nxt, days_out, vol, deload, 13)


def _is_deload(d: dt.date, cfg) -> bool:
    week_idx = (d - _plan_start(cfg)).days // 7
    return week_idx >= 0 and week_idx % 4 == 3


def week_index(d: dt.date, cfg) -> int:
    return max(0, (d - _plan_start(cfg)).days // 7)


# ── weekly volume ───────────────────────────────────────────────────────────
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
    out = {"target_km": round(target, 1), "chronic_km": round(chronic, 1),
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


def template_for(phase: Phase, cfg: dict) -> dict:
    tpls = cfg["weekly_templates"]
    if phase.focus == "tri":
        return tpls["triathlon"]
    if phase.focus == "bike":
        return tpls.get("cycling") or tpls["run_focus"]
    return tpls["run_focus"]


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


def run_distribution(template: dict, week_km: float, phase: Phase, return_mode: bool = False) -> dict:
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
    q_km = min(max(_round_half(week_km * 0.28 if n_runs <= 3 else week_km * 0.20), 6), 13) if n_q else 0
    brick_km = 3.0 if has_brick else 0
    rest_km = week_km - long_km - q_km * n_q - brick_km
    easy_km = min(max(_round_half(rest_km / n_easy), 4), 12) if n_easy else 0
    if long_km and easy_km > long_km * 0.85:
        # the long run must be the longest run of the week
        easy_km = max(_round_half(long_km * 0.85), 4)
    return {"long": long_km, "quality": q_km, "easy": easy_km, "brick": brick_km}


QUALITY = {
    ("short", "base"): [
        ("Hills 8×60\"", [warmup(15), repeat(8, [run_t(60, 4), jog(90)]), cooldown(10)]),
        ("Fartlek 10×1'", [warmup(15), repeat(10, [run_t(60, 4), jog(60, 2)]), cooldown(10)]),
        ("Tempo 3×8'", [warmup(15), repeat(3, [run_t(480, 3), jog(120)]), cooldown(10)]),
    ],
    ("short", "specific"): [
        ("Intervals 6×1000 m", [warmup(15), repeat(6, [run_d(1000, 4), jog(90)]), cooldown(10)]),
        ("Intervals 3×2000 m", [warmup(15), repeat(3, [run_d(2000, 4), jog(150)]), cooldown(10)]),
        ("Intervals 10×400 m", [warmup(15), repeat(10, [run_d(400, 5), jog(75)]), cooldown(10)]),
        ("Race pace 2×3 km", [warmup(15), repeat(2, [run_d(3000, 4), jog(180)]), cooldown(10)]),
    ],
    ("short", "taper"): [
        ("Sharpener 4×1000 m", [warmup(15), repeat(4, [run_d(1000, 4), jog(120)]), cooldown(10)]),
    ],
    ("short", "race_week"): [
        ("Openers 4×400 m", [warmup(12), repeat(4, [run_d(400, 4), jog(120)]), cooldown(8)]),
    ],
    ("half", "base"): [
        ("Hills 8×75\"", [warmup(15), repeat(8, [run_t(75, 4), jog(105)]), cooldown(10)]),
        ("Fartlek 8×2'", [warmup(15), repeat(8, [run_t(120, 4), jog(90, 2)]), cooldown(10)]),
        ("Progression 3×10'", [warmup(10), run_t(600, 2), run_t(600, 3), run_t(600, 4), cooldown(10)]),
    ],
    ("half", "build"): [
        ("Threshold 3×10'", [warmup(15), repeat(3, [run_t(600, 4), jog(180)]), cooldown(10)]),
        ("Tempo 2×15'", [warmup(15), repeat(2, [run_t(900, 3), jog(180)]), cooldown(10)]),
        ("Intervals 5×1600 m", [warmup(15), repeat(5, [run_d(1600, 4), jog(120)]), cooldown(10)]),
    ],
    ("half", "peak"): [
        ("HM pace 2×5 km", [warmup(15), repeat(2, [run_d(5000, 4), jog(240)]), cooldown(10)]),
        ("HM pace 3×3 km", [warmup(15), repeat(3, [run_d(3000, 4), jog(180)]), cooldown(10)]),
        ("Threshold 4×10'", [warmup(15), repeat(4, [run_t(600, 4), jog(120)]), cooldown(10)]),
    ],
    ("half", "taper"): [
        ("HM pace 3×2 km", [warmup(15), repeat(3, [run_d(2000, 4), jog(150)]), cooldown(10)]),
    ],
    ("half", "race_week"): [
        ("Openers 3×1 km", [warmup(15), repeat(3, [run_d(1000, 4), jog(120)]), cooldown(8)]),
    ],
    ("tri", "base"): [
        ("Fartlek 6×3'", [warmup(15), repeat(6, [run_t(180, 4), jog(120)]), cooldown(10)]),
        ("Tempo 4×6'", [warmup(15), repeat(4, [run_t(360, 3), jog(90)]), cooldown(10)]),
    ],
    ("tri", "build"): [
        ("Intervals 5×1000 m", [warmup(15), repeat(5, [run_d(1000, 4), jog(120)]), cooldown(10)]),
        ("Threshold 3×10'", [warmup(15), repeat(3, [run_t(600, 4), jog(150)]), cooldown(10)]),
    ],
}
QUALITY[("tri", "taper")] = QUALITY[("short", "taper")]
QUALITY[("tri", "race_week")] = QUALITY[("short", "race_week")]


def make_run(kind: str, km: float, phase: Phase, wk: int = 0) -> Session:
    km = _round_half(km)
    if kind == "run_quality":
        opts = QUALITY.get((phase.focus, phase.name)) or QUALITY[(phase.focus, "base")]
        name, steps = opts[wk % len(opts)]
        return Session(kind, "run", f"Quality · {name}", "hard", km, None,
                       "Easy warm-up, main set in the target zone, jog the recoveries. "
                       "If your heart rate doesn't come down during recovery, drop one rep.",
                       steps)
    if kind == "run_long":
        if phase.name == "peak" and km >= 14:
            steps = [run_d((km - 3) * 1000, 2), run_d(3000, 3)]
            detail = f"Long run {km:g} km: Z2, last 3 km a bit livelier (Z3)."
        else:
            steps = [run_d(km * 1000, 2)]
            detail = f"Long run {km:g} km in Z2, conversational pace. Drink if it goes over 75'."
        return Session(kind, "run", f"Long run {km:g} km", "moderate", km, None, detail, steps)
    if kind == "run_strides":
        steps = [run_d(km * 1000, 2), repeat(6, [run_t(20, None), jog(70)])]
        return Session(kind, "run", f"Easy run {km:g} km + 6 strides", "easy", km, None,
                       f"{km:g} km in Z2, then 6 × 20\" strides (fast but relaxed).", steps)
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
    m = minutes or 60
    return Session("bike_easy", "bike", f"Easy ride {m:g}'", "easy", None, m,
                   f"{m:g}' in Z1-Z2, high cadence (85-95 rpm).", [run_t(m * 60, 2)])


def make_swim(kind: str, phase: Phase, short: bool = False) -> Session:
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


def make_rest(reason: str = "") -> Session:
    return Session("rest", "other", "Rest / mobility 20'", "rest", None, 20,
                   (reason + " " if reason else "") + "Full rest or 20' of gentle mobility and stretching.",
                   [run_t(20 * 60, None)])


def plan_day(d: dt.date, cfg: dict, week: float | dict, return_mode: bool = False) -> tuple[Phase, list[Session]]:
    """`week` is the week target dict from compute_week_target (or just the running km)."""
    wt = week if isinstance(week, dict) else {"target_km": week}
    return_mode = return_mode or wt.get("return_mode", False)
    phase = phase_for(d, cfg)
    tpl = template_for(phase, cfg)
    dist = run_distribution(tpl, wt["target_km"], phase, return_mode)
    bdist = bike_distribution(tpl, wt, phase) if phase.focus == "bike" else None
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
        if kind == "rest":
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
    return phase, sessions or [make_rest()]


def planned_run_km(sessions: list[Session]) -> float:
    return sum(s.distance_km or 0 for s in sessions if s.sport == "run")


def to_jsonable(x: Any) -> Any:
    if isinstance(x, (dt.date,)):
        return x.isoformat()
    return x
