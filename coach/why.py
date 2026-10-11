"""One short sentence explaining today's workout, for the top of the report
and of the workout description on the watch."""
from __future__ import annotations

SPORT_WORD = {"run": "run", "bike": "ride", "swim": "swim"}


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def recovery_causes(w: dict, r: dict, ctx: dict) -> list[str]:
    """What's dragging recovery down, most important first, in a few words each."""
    out = []
    flags = r.get("flags", {})
    if flags.get("hrv_low") and flags.get("rhr_high"):
        out.append("low HRV with a raised resting HR (often a cold coming on)")
    tr = w.get("training_readiness")
    if tr is not None and tr < 50:
        out.append(f"your Training Readiness is low ({tr})")
    sh, ss = w.get("sleep_hours"), w.get("sleep_score")
    if sh is not None and sh < 6:
        out.append(f"you slept {sh:g} h")
    elif ss is not None and ss < 60:
        out.append(f"you slept poorly (score {ss})")
    if flags.get("hrv_low") and not flags.get("rhr_high"):
        out.append("your HRV is below normal")
    if w.get("rhr") and w.get("rhr_7d") and w["rhr"] - w["rhr_7d"] >= 4 and not flags.get("rhr_high"):
        out.append(f"your resting HR is up {w['rhr'] - w['rhr_7d']:+.0f} bpm")
    bb = w.get("body_battery_wake")
    if bb is not None and bb < 45:
        out.append(f"your Body Battery is low ({bb})")
    acwr = w.get("acwr") or ctx.get("acwr_own")
    if acwr and acwr > 1.3:
        out.append("your training load has jumped recently")
    comp = ctx.get("compliance") or {}
    if comp.get("excess_km", 0) >= 2:
        out.append(f"you ran {comp['excess_km']:g} km more than planned yesterday")
    if comp.get("extra_intensity"):
        out.append("yesterday was harder than planned")
    return out


def substantial(a: dict) -> bool:
    """A session that matters for the rest of the day: 45'+, a decent distance, or a hard effort."""
    if a["sport"] not in SPORT_WORD:
        return False
    far = {"bike": 25, "run": 8, "swim": 1.5}[a["sport"]]
    return (a["duration_min"] >= 45 or (a.get("distance_km") or 0) >= far
            or (a.get("aerobic_te") or 0) >= 2.5)


def _biggest_substantial(acts: list[dict]) -> dict | None:
    done = [a for a in acts if substantial(a)]
    return max(done, key=lambda a: a["duration_min"]) if done else None


def _big(acts: list[dict]) -> dict | None:
    big = [a for a in acts if a["sport"] in SPORT_WORD and (a["duration_min"] >= 90 or (a.get("aerobic_te") or 0) >= 3.5)]
    return max(big, key=lambda a: a["duration_min"]) if big else None


def _act_phrase(a: dict) -> str:
    km = a.get("distance_km") or 0
    dist = f"{round(km)} km " if km >= 10 else (f"{km:g} km " if km else "")
    return f"{dist}{SPORT_WORD[a['sport']]}".strip()


def why_line(final: list, planned: list, r: dict, w: dict, ctx: dict, wt: dict, phase) -> str:
    if any(s.kind == "race" for s in final):
        return "Race day: trust the work you've done."
    causes = recovery_causes(w, r, ctx)
    level, score = r["level"], r["score"]
    today = _biggest_substantial(ctx.get("today_acts") or [])
    yday = _big(ctx.get("yesterday_acts") or [])

    if not final and planned:
        return "Nothing left for today: you've already done it."
    if level == "red":
        return f"Rest today: {_join(causes[:2]) or 'your recovery is very low'}."
    if level in ("amber", "orange"):
        lead = "Much easier than planned" if level == "orange" else "Easier than planned"
        return f"{lead}: {_join(causes[:2]) or 'your recovery is below normal'} (readiness {score})."

    # green: say why the session looks the way it does
    if today and any(s.sport == "run" for s in final):
        return f"Keep it easy: you already did a {_act_phrase(today)} today."
    comp = ctx.get("compliance") or {}
    if comp.get("excess_km", 0) >= 2:
        return f"Shorter run: you ran {comp['excess_km']:g} km more than planned yesterday."
    if ctx.get("week_over"):
        return "Shorter run: you're already ahead of this week's running volume."
    if phase.name == "race_week":
        if phase.race and phase.race.priority == "B":
            reason = f"a few easy days before {phase.race.name}"
        else:
            reason = f"light week to arrive fresh at {phase.race.name}" if phase.race else "light week before your race"
    elif phase.name == "taper":
        reason = f"tapering for {phase.race.name} (less volume, keeping some speed)" if phase.race else "tapering"
    elif phase.name == "transition":
        reason = "easy days to recover from your race"
    elif wt.get("return_mode"):
        reason = "easy and short while you rebuild running after weeks of little running"
    elif phase.deload:
        reason = "deload week: lighter so the last weeks' work sinks in"
    elif phase.race:
        reason = {"base": f"building your base for {phase.race.name}",
                  "build": f"building towards {phase.race.name}",
                  "specific": f"race-specific work for {phase.race.name}",
                  "peak": f"race-specific work for {phase.race.name}"}.get(phase.name, "as planned")
    else:
        reason = "as planned"
    extra = f"; yesterday's {_act_phrase(yday)} is absorbed" if yday else ""
    return f"{reason[0].upper()}{reason[1:]}: you're well recovered (readiness {score}){extra}."
