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
          "tri_sprint": "tri", "tri_olympic": "tri"}
RECOVERY_DAYS = {"short": 4, "half": 9, "tri": 6}
KIND_DISTANCE_KM = {"5k": 5, "10k": 10, "half": 21.1, "marathon": 42.2}


# ── models ──────────────────────────────────────────────────────────────────
@dataclass
class Race:
    name: str
    date: dt.date
    kind: str
    priority: str = "A"
    distance_km: float | None = None

    @property
    def family(self) -> str:
        if self.kind == "road" and self.distance_km and self.distance_km > 12:
            return "half"
        return FAMILY.get(self.kind, "half")


@dataclass
class Phase:
    focus: str            # short | half | tri
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
        races.append(Race(r["name"], d, r.get("kind", "half"), r.get("priority", "A"), dist))
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
        if since <= RECOVERY_DAYS[last.family]:
            nxt = future[0] if future else None
            return Phase(nxt.family if nxt else "tri", "transition",
                         f"Recovery after {last.name}", nxt,
                         (nxt.date - d).days if nxt else None, 0.5, False, 8)

    if not future:
        return Phase("tri", "base", "Triathlon maintenance", None, None, 1.0, _is_deload(d, cfg), 14)

    nxt = future[0]
    fam = nxt.family
    days_out = (nxt.date - d).days

    if days_out <= 6:
        vol = {"short": 0.7, "half": 0.55, "tri": 0.6}[fam]
        return Phase(fam, "race_week", "Race week", nxt, days_out, vol, False, 8)
    if days_out <= 13:
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
    return {"target_km": round(target, 1), "chronic_km": round(chronic, 1),
            "last_week_km": last, "phase": phase.name, "deload": phase.deload,
            "return_mode": return_mode}


# ── sessions ────────────────────────────────────────────────────────────────
def _round_half(x: float) -> float:
    return round(x * 2) / 2


def template_for(phase: Phase, cfg: dict) -> dict:
    tpls = cfg["weekly_templates"]
    return tpls["triathlon"] if phase.focus == "tri" else tpls["run_focus"]


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


def make_bike(kind: str, phase: Phase, minutes: float | None = None) -> Session:
    tri = phase.focus == "tri"
    if kind == "bike_quality":
        if phase.name in ("transition", "race_week") or not tri:
            return Session(kind, "bike", "Bike 5×5' tempo", "moderate", None, 60,
                           "Ride with 5×5' in Z3 (3' easy between).",
                           [warmup(15), repeat(5, [run_t(300, 3), jog(180)]), cooldown(10)])
        return Session(kind, "bike", "Bike 4×8' threshold", "hard", None, 75,
                       "4×8' in Z4 with 4' easy between reps.",
                       [warmup(15), repeat(4, [run_t(480, 4), jog(240)]), cooldown(12)])
    if kind == "bike_long":
        m = minutes or (120 if tri and phase.name == "build" else 90)
        return Session(kind, "bike", f"Long ride {m:g}'", "moderate", None, m,
                       f"{m:g}' steady in Z2. Practise eating/drinking every 30'.",
                       [run_t(m * 60, 2)])
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


def plan_day(d: dt.date, cfg: dict, week_km: float, return_mode: bool = False) -> tuple[Phase, list[Session]]:
    phase = phase_for(d, cfg)
    tpl = template_for(phase, cfg)
    dist = run_distribution(tpl, week_km, phase, return_mode)
    wk = week_index(d, cfg)
    sessions: list[Session] = []
    races_today = [r for r in load_races(cfg) if r.date == d]
    if races_today:
        r = races_today[0]
        return phase, [Session("race", "run" if r.family != "tri" else "other", f"RACE: {r.name}", "hard",
                               None, None, "Race day! Warm up 15' with 3 strides. Start controlled.", [])]
    if phase.name == "race_week" and phase.days_out is not None and phase.days_out <= 1:
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
            if phase.name in ("transition", "race_week") and kind != "bike_easy":
                kind = "bike_easy"
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
