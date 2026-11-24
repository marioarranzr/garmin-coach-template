"""Training load, measured the same way for every sport.

Each activity gets a stress score (TSS-like: 100 = one hour at threshold). From it:
- fitness (CTL, 42-day average), fatigue (ATL, 7-day average) and form (TSB = CTL - ATL), overall and per sport;
- a leg-fatigue index: how much leg-heavy work (runs, skiing, lower-body strength, rides) came in the
  last 3 days compared with your usual, because tired legs decide where runs and leg strength fit.
Everything is computed here (deterministic); the AI coach only reads the numbers.
"""
from __future__ import annotations

import datetime as dt
import math

CTL_DAYS, ATL_DAYS = 42, 7
LTHR_DEFAULT = 165                       # lactate-threshold heart rate (bpm) when config has none
DEFAULT_IF = {"run": 0.75, "bike": 0.65, "swim": 0.7, "strength": 0.55, "other": 0.5}
# how much of a sport's stress lands on the legs (running 1.0 is the reference)
LEG_FACTOR = {"run": 1.0, "bike": 0.6, "swim": 0.15, "strength": 0.3, "other": 0.3}
SNOW = ("ski", "snowboard")
LEG_STRENGTH = ("lower", "leg", "squat", "power")


def _kind(a: dict) -> str:
    t = f"{a.get('type') or ''} {a.get('name') or ''}".lower()
    if any(k in t for k in SNOW):
        return "snow"
    return a.get("sport") or "other"


def stress(a: dict, cfg: dict | None = None) -> float:
    """Stress score of one activity (TSS-like)."""
    hours = (a.get("duration_min") or 0) / 60
    if hours <= 0:
        return 0.0
    ath = (cfg or {}).get("athlete") or {}
    sport = a.get("sport") or "other"
    ftp = ath.get("ftp_w")
    if sport == "bike" and a.get("avg_power") and ftp:
        intensity = a["avg_power"] / ftp
    elif a.get("avg_hr"):
        intensity = a["avg_hr"] / float(ath.get("lthr") or LTHR_DEFAULT)
    else:
        intensity = DEFAULT_IF.get(sport, 0.5)
    intensity = min(max(intensity, 0.4), 1.15)
    return round(hours * intensity ** 2 * 100, 1)


def leg_factor(a: dict) -> float:
    kind = _kind(a)
    if kind == "snow":
        return 0.9                      # quads, back and core all day; arms barely
    if kind == "strength":
        name = f"{a.get('name') or ''}".lower()
        return 0.8 if any(k in name for k in LEG_STRENGTH) else LEG_FACTOR["strength"]
    return LEG_FACTOR.get(kind, 0.3)


def _daily(acts: list[dict], cfg: dict | None, sport: str | None = None, legs: bool = False) -> dict[str, float]:
    out: dict[str, float] = {}
    for a in acts:
        if sport and a.get("sport") != sport:
            continue
        v = stress(a, cfg) * (leg_factor(a) if legs else 1.0)
        out[a["date"]] = out.get(a["date"], 0.0) + v
    return out


def _ewma(daily: dict[str, float], d: dt.date, days: int) -> float:
    """Exponentially weighted average of the daily stress up to the day before `d`."""
    if not daily:
        return 0.0
    first = min(dt.date.fromisoformat(k) for k in daily)
    k = 1 - math.exp(-1 / days)
    value, day = 0.0, first
    while day < d:
        value += (daily.get(day.isoformat(), 0.0) - value) * k
        day += dt.timedelta(days=1)
    return value


def fitness(acts: list[dict], d: dt.date, cfg: dict | None = None, sport: str | None = None) -> dict:
    daily = _daily(acts, cfg, sport)
    ctl, atl = _ewma(daily, d, CTL_DAYS), _ewma(daily, d, ATL_DAYS)
    return {"ctl": round(ctl, 1), "atl": round(atl, 1), "tsb": round(ctl - atl, 1)}


def leg_fatigue(acts: list[dict], d: dt.date, cfg: dict | None = None) -> dict:
    """Leg-heavy stress of the last 3 days (weighted: yesterday counts most) against your usual 3 days."""
    legs = _daily(acts, cfg, legs=True)
    recent = sum(legs.get((d - dt.timedelta(days=n)).isoformat(), 0.0) * w
                 for n, w in ((1, 1.0), (2, 0.6), (3, 0.3)))
    base_days = [(d - dt.timedelta(days=n)).isoformat() for n in range(4, 32)]
    usual = sum(legs.get(x, 0.0) for x in base_days) / len(base_days) * 1.9     # the 3 weights add up to 1.9
    ratio = round(recent / usual, 2) if usual > 0 else None
    level = ("unknown" if ratio is None else "high" if ratio >= 1.5 else "raised" if ratio >= 1.15
             else "normal" if ratio >= 0.6 else "low")
    return {"index": ratio, "level": level, "last_3_days_leg_stress": round(recent, 1)}


def summary(acts: list[dict], d: dt.date, cfg: dict | None = None) -> dict:
    """What the AI coach reads: overall and per-sport fitness/fatigue/form and leg fatigue as of the morning of `d`."""
    out = {"overall": fitness(acts, d, cfg), "leg_fatigue": leg_fatigue(acts, d, cfg), "by_sport": {}}
    for sport in ("run", "bike", "swim", "strength", "other"):
        if any(a.get("sport") == sport for a in acts):
            out["by_sport"][sport] = fitness(acts, d, cfg, sport)
    out["note"] = ("stress score: 100 = one hour at threshold; ctl = fitness (42 d), atl = fatigue (7 d), "
                   "tsb = form (ctl - atl; below -20 deep fatigue, above +10 fresh); leg_fatigue.index compares "
                   "the last 3 days of leg-heavy work (running, skiing, leg strength, rides) with your usual")
    return out
