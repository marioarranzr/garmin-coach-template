"""Your training paces, from your current fitness.

Quality sessions target paces (like Runna or a coach would: "400 m at 4:20/km"),
not just heart-rate zones. They're worked out each day from Garmin's race
predictor (your predicted 5K, 10K and half marathon times, which follow your
VO2 max), so they move as your fitness does. Without a prediction they're
estimated from your easy pace in config.yaml.

  5k    5K race pace          fast reps (400-800 m)
  10k   10K race pace         1-2 km reps, the "over" in over-unders
  thr   threshold             ~1 h race pace: tempo, cruise intervals, the "under"
  hm    half marathon pace
  mp    marathon pace         the float in rolling 400s, steady progressions
  easy  easy pace             (easy runs themselves use heart-rate zone 2)
"""
from __future__ import annotations

LABELS = {"5k": "5K pace", "10k": "10K pace", "thr": "threshold", "hm": "half-marathon pace",
          "mp": "marathon pace", "easy": "easy"}
TOLERANCE_S = 5          # the pace target on the watch is ±5 s/km around it

_current: dict = {}


def fmt(sec_per_km: float) -> str:
    s = round(sec_per_km)
    return f"{s // 60}:{s % 60:02d}"


def from_predictions(t5k: float | None, t10k: float | None, thm: float | None, tmar: float | None,
                     easy_s: float) -> dict | None:
    """Paces (s/km) from predicted race times (seconds). Missing ones are filled
    with Riegel's formula from the others."""
    known = {d: t for d, t in ((5, t5k), (10, t10k), (21.0975, thm), (42.195, tmar)) if t}
    if not known:
        return None
    base_d, base_t = min(known.items())

    def time(d):
        return known.get(d) or base_t * (d / base_d) ** 1.06
    p5, p10, phm, pmar = (time(d) / d for d in (5, 10, 21.0975, 42.195))
    return {"5k": p5, "10k": p10, "thr": (p10 + phm) / 2, "hm": phm, "mp": pmar,
            "easy": max(easy_s, pmar * 1.12), "source": "Garmin's race predictor",
            "pred_5k": time(5)}


def from_easy(easy_s: float) -> dict:
    """Rough paces from your easy pace alone (easy ≈ 5K pace × 1.22)."""
    p5 = easy_s / 1.22
    p = from_predictions(p5 * 5, None, None, None, easy_s)
    p["source"] = "your easy pace in config.yaml (no Garmin race prediction)"
    return p


def estimate(cfg: dict, predictions: dict | None, chronic_km: float | None = None) -> dict:
    easy_s = float((cfg.get("athlete") or {}).get("easy_pace_min_per_km", 6.0)) * 60
    p = None
    if predictions:
        p = from_predictions(predictions.get("5k"), predictions.get("10k"), predictions.get("hm"),
                             predictions.get("marathon"), easy_s)
        # a prediction way off your easy pace is more likely stale than real
        if p and not (1.15 <= easy_s / p["5k"] <= 1.75):
            p = None
    p = p or from_easy(easy_s)
    if chronic_km is not None and chronic_km < 15:
        # little running lately: your legs lag behind your VO2 max, so ease the targets
        for k in ("5k", "10k", "thr", "hm", "mp"):
            p[k] *= 1.03
        p["source"] += ", 3 % easier while your running volume is low"
    return p


def set_current(p: dict) -> None:
    _current.clear()
    _current.update(p)


def current() -> dict:
    return _current or from_easy(360.0)


def pace(label: str) -> float | None:
    return current().get(label)


def summary(p: dict | None = None) -> str:
    p = p or current()
    return (f"5K {fmt(p['5k'])} · 10K {fmt(p['10k'])} · threshold {fmt(p['thr'])} · "
            f"HM {fmt(p['hm'])} · marathon {fmt(p['mp'])} /km (from {p['source']})")
