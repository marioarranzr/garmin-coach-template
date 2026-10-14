"""Daily readiness score (0-100) from Garmin data and what you've done
over the last few days."""
from __future__ import annotations

import datetime as dt

LEVELS = [(70, "green"), (50, "amber"), (35, "orange"), (0, "red")]
LEVEL_TXT = {
    "green": "🟢 Ready for the plan",
    "amber": "🟡 Some fatigue: intensity goes down",
    "orange": "🟠 Clear fatigue: short and easy session",
    "red": "🔴 Time to rest",
}


def _lin(x, x0, y0, x1, y1):
    """Clamped linear interpolation."""
    if x <= x0:
        return y0
    if x >= x1:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def compute(w: dict, ctx: dict) -> dict:
    """w = Garmin wellness data; ctx = load/compliance context."""
    comps: dict[str, tuple] = {}
    reasons: list[str] = []
    flags: dict[str, bool] = {}

    # 1) Garmin Training Readiness (already blends sleep, HRV, load, recovery)
    if w.get("training_readiness") is not None:
        tr = w["training_readiness"]
        comps["Training Readiness now" if w.get("tr_is_now") else "Training Readiness"] = (tr, tr, 0.30)
        if tr < 40:
            reasons.append(f"Low Garmin Training Readiness ({tr}).")

    # 2) HRV
    hrv, wk = w.get("hrv_last_night"), w.get("hrv_weekly_avg")
    status = (w.get("hrv_status") or "").upper()
    if hrv or status:
        s = {"BALANCED": 90, "UNBALANCED": 60, "LOW": 30, "POOR": 15}.get(status, 70)
        lo = w.get("hrv_baseline_low")
        if hrv and lo and hrv < lo:
            s -= 20
            reasons.append(f"Last night's HRV ({hrv} ms) is below your normal range (≥{lo} ms).")
        elif hrv and wk and hrv < 0.88 * wk:
            s -= 15
            reasons.append(f"Last night's HRV ({hrv} ms) is well below your weekly average ({wk} ms).")
        elif status in ("LOW", "POOR"):
            reasons.append(f"HRV status: {status.lower()}.")
        comps["HRV"] = (f"{hrv} ms / {status.lower() or '—'}", max(s, 0), 0.20)
        flags["hrv_low"] = status in ("LOW", "POOR") or bool(hrv and lo and hrv < lo)

    # 3) Sleep
    ss, sh = w.get("sleep_score"), w.get("sleep_hours")
    if ss is not None or sh is not None:
        s = ss if ss is not None else _lin(sh, 5, 30, 8, 90)
        if sh is not None and sh < 6:
            s = min(s, _lin(sh, 4, 15, 6, 50))
            reasons.append(f"Short sleep ({sh} h).")
        elif ss is not None and ss < 60:
            reasons.append(f"Poor sleep quality (score {ss}).")
        comps["Sleep"] = (f"{ss if ss is not None else '—'} pts / {sh if sh is not None else '—'} h", s, 0.15)
        flags["short_sleep"] = sh is not None and sh < 5.5

    # 4) Body Battery on waking
    bb = w.get("body_battery_wake")
    if bb is not None:
        label = "Body Battery now" if w.get("bb_is_now") else "Body Battery"
        comps[label] = (bb, _lin(bb, 20, 10, 85, 100), 0.15)
        if bb < 45:
            reasons.append(f"Low Body Battery right now ({bb}), after today's training." if w.get("after_training")
                           else f"Low Body Battery right now ({bb})." if w.get("bb_is_now")
                           else f"Low Body Battery on waking ({bb}).")

    # 5) Resting heart rate vs 7-day average
    rhr, rhr7 = w.get("rhr"), w.get("rhr_7d")
    if rhr and rhr7:
        delta = rhr - rhr7
        comps["Resting HR"] = (f"{rhr} bpm ({delta:+.0f})", _lin(delta, 0, 100, 8, 15), 0.12)
        if delta >= 4:
            reasons.append(f"Resting HR {delta:+.0f} bpm above your average: possible fatigue, stress or a cold coming on.")
        flags["rhr_high"] = delta >= 7

    # 6) Yesterday's stress
    st = w.get("stress_yesterday")
    if st is not None:
        comps["Yesterday's stress"] = (st, _lin(st, 25, 100, 55, 30), 0.08)
        if st >= 45:
            reasons.append(f"Very stressful day yesterday (average {st}).")

    if comps:
        tot_w = sum(c[2] for c in comps.values())
        score = sum(c[1] * c[2] for c in comps.values()) / tot_w
    else:
        score = 70.0
        reasons.append("No Garmin recovery data today: assuming a normal day (has the watch synced?).")

    # ── load and compliance modifiers ──
    acwr = w.get("acwr") or ctx.get("acwr_own")
    if acwr:
        if acwr > 1.5:
            score -= 15
            reasons.append(f"Very high acute/chronic load ratio ({acwr:.2f}): injury risk.")
        elif acwr > 1.3:
            score -= 7
            reasons.append(f"High acute/chronic load ratio ({acwr:.2f}).")

    c = ctx.get("compliance") or {}
    if c.get("excess_km", 0) >= 2:
        score -= min(4 * c["excess_km"], 15)
        reasons.append(f"Yesterday you ran {c['run_done']:.1f} km vs {c['run_planned']:.1f} planned (+{c['excess_km']:.1f} km).")
    if c.get("extra_intensity"):
        score -= 8
        reasons.append("Yesterday's session was harder than planned (high Training Effect on an easy day).")
    load = ctx.get("recent_load") or {}
    if load.get("ratio") and load["ratio"] >= 1.5 and load["hours"] >= 4:
        # a heavy block of the last few days, any sport: legs are tired even if HRV/sleep look fine
        score -= 16 if load["ratio"] >= 2 else 10
        flags["heavy_block"] = True
        reasons.append(f"You've trained {load['hours']:g} h in the last 4 days, about {load['ratio']:g}× your usual.")
    if ctx.get("consecutive_days", 0) >= 6:
        score -= 8
        reasons.append(f"You've trained {ctx['consecutive_days']} days in a row.")

    score = max(0, min(100, round(score)))
    level = next(lvl for thr, lvl in LEVELS if score >= thr)

    # hard rules
    if flags.get("hrv_low") and flags.get("rhr_high"):
        level, score = "red", min(score, 30)
        reasons.insert(0, "Low HRV + high resting HR: a typical sign of illness or overload. Rest.")
    elif flags.get("short_sleep") and level == "green":
        level = "amber"
        reasons.append("With this little sleep, quality work isn't a good idea.")

    return {"score": score, "level": level, "label": LEVEL_TXT[level],
            "components": {k: {"value": v[0], "score": round(v[1])} for k, v in comps.items()},
            "reasons": reasons, "flags": flags}


# ── context: compliance and own load ───────────────────────────────────────
def own_acwr(activities: list[dict], d: dt.date) -> float | None:
    def load_between(a_days, b_days):
        lo = (d - dt.timedelta(days=a_days)).isoformat()
        hi = (d - dt.timedelta(days=b_days)).isoformat()
        tot = 0.0
        for a in activities:
            if lo <= a["date"] < hi:
                tot += a["load"] if a.get("load") else a["duration_min"] * 1.0
        return tot
    acute = load_between(7, 0) / 7
    chronic = load_between(28, 0) / 28
    return round(acute / chronic, 2) if chronic > 0 else None


def recent_load(past: list[dict], today: list[dict], d: dt.date) -> dict:
    """Training hours (all sports) over the last 4 days, including today, vs your usual 4 days."""
    lo = (d - dt.timedelta(days=3)).isoformat()
    base_lo = (d - dt.timedelta(days=28)).isoformat()
    recent = sum(a["duration_min"] for a in past if a["date"] >= lo) + sum(a["duration_min"] for a in today)
    base = sum(a["duration_min"] for a in past if base_lo <= a["date"] < lo)
    usual = base / 25 * 4
    return {"hours": round(recent / 60, 1), "ratio": round(recent / usual, 1) if usual > 0 else None}


def consecutive_days(activities: list[dict], d: dt.date) -> int:
    days = {a["date"] for a in activities}
    n, cur = 0, d - dt.timedelta(days=1)
    while cur.isoformat() in days:
        n += 1
        cur -= dt.timedelta(days=1)
    return n


def _km(x: float) -> str:
    return f"{round(x)}" if x >= 10 else f"{x:g}"


COMMUTE_MAX_MIN = 25   # a recorded ride shorter than this is getting around (a commute), not training


def is_training(a: dict) -> bool:
    return not (a.get("sport") == "bike" and (a.get("duration_min") or 0) < COMMUTE_MAX_MIN)


def compliance(planned: list[dict], done: list[dict]) -> dict:
    """Compares yesterday's plan with what was actually done."""
    run_planned = sum(s.get("distance_km") or 0 for s in planned if s["sport"] == "run")
    run_done = sum(a["distance_km"] for a in done if a["sport"] == "run")
    planned_kinds = {s["kind"] for s in planned}
    planned_easy = all(s["intensity"] in ("easy", "rest") for s in planned)
    hard_done = any((a.get("aerobic_te") or 0) >= 3.8 or (a.get("anaerobic_te") or 0) >= 2.5 for a in done)
    sports_done = {a["sport"] for a in done if is_training(a)}
    missed = [s["title"] for s in planned
              if s["sport"] in ("run", "bike", "swim") and s["sport"] not in sports_done and not s.get("optional")]
    return {
        "run_planned": round(run_planned, 1),
        "run_done": round(run_done, 1),
        "excess_km": round(run_done - run_planned, 1) if run_planned and run_done > run_planned else
                     (round(run_done, 1) if not run_planned and run_done else 0),
        "missed": missed,
        "missed_quality": "run_quality" in planned_kinds and "run" not in sports_done,
        "missed_long": "run_long" in planned_kinds and "run" not in sports_done,
        "extra_intensity": planned_easy and hard_done,
        "done_titles": [f"{a['name']} ({_km(a['distance_km'])} km, {a['duration_min']:.0f}')" for a in done],
    }


def evening(ctx: dict) -> dict:
    """The evening before there's no sleep data yet: plan from training load and
    compliance only. The morning check redoes this with sleep, HRV and Body Battery."""
    reasons: list[str] = []
    flags: dict[str, bool] = {}
    level = "green"
    c = ctx.get("compliance") or {}
    if c.get("excess_km", 0) >= 2:
        reasons.append(f"Today you ran {c['run_done']:.1f} km vs {c['run_planned']:.1f} planned (+{c['excess_km']:.1f} km).")
    if c.get("extra_intensity"):
        reasons.append("Today's session was harder than planned.")
    load = ctx.get("recent_load") or {}
    if load.get("ratio") and load["ratio"] >= 1.5 and load["hours"] >= 4:
        flags["heavy_block"] = True
        reasons.append(f"You've trained {load['hours']:g} h in the last 4 days, about {load['ratio']:g}× your usual.")
    acwr = ctx.get("acwr_own")
    if (acwr and acwr > 1.5) or ctx.get("consecutive_days", 0) >= 6:
        level = "amber"
        reasons.append(f"Very high acute/chronic load ratio ({acwr:.2f})." if acwr and acwr > 1.5
                       else f"You've trained {ctx['consecutive_days']} days in a row.")
    return {"score": None, "level": level, "label": "🌙 Planned the evening before",
            "components": {}, "reasons": reasons, "flags": flags}
