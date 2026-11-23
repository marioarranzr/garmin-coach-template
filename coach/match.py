"""Did you follow the planned workout? An activity that reached Garmin or Strava without
"Follow workout" still counts as that day's session when it's close enough to the plan."""
from __future__ import annotations

import datetime as dt

THRESHOLD = 0.85        # share of the plan: ride 55' of a 60' tempo = 0.92, 40' = 0.67


def _ratio(done: float | None, planned: float | None) -> float | None:
    if not done or not planned:
        return None
    return min(done, planned) / max(done, planned)


COOLDOWN_SLACK_MIN = 10     # a cool-down cut short doesn't matter: the main set is the session


def similarity(plan: dict, a: dict) -> float:
    """0..1: same sport, then how close the duration (and the distance, for runs and swims) is.
    A session with a main set (quality, long) can end up to 10' early: the cool-down is optional."""
    sport = lambda x: "strength" if x == "strength_training" else x
    if sport(plan.get("sport")) != sport(a.get("sport")):
        return 0.0
    want = plan.get("duration_min")
    if want and plan.get("intensity") in ("hard", "moderate") and (a.get("duration_min") or 0) < want:
        want = max(want - COOLDOWN_SLACK_MIN, want * 0.5)
        if a["duration_min"] >= want:
            want = a["duration_min"]            # ended inside the cool-down: nothing missing
    if plan.get("sport") == "swim" and plan.get("distance_km") and a.get("distance_km"):
        return round(_ratio(a["distance_km"], plan["distance_km"]) or 0, 2)    # rests make the planned time meaningless
    parts = [r for r in (_ratio(a.get("duration_min"), want),
                         _ratio(a.get("distance_km"), plan.get("distance_km"))
                         if plan.get("sport") in ("run", "swim") else None) if r is not None]
    return round(sum(parts) / len(parts), 2) if parts else 0.0


def tag_followed(acts: list[dict], cal, days: list[dt.date], threshold: float = THRESHOLD) -> int:
    """Marks the activities that match a session the coach planned that day: `followed` (its
    title), `match` (0..1) and, for a hard session, `plan_hard` (counts as the week's hard
    session). One activity per planned session, the closest first. Returns how many."""
    n = 0
    for day in days:
        entries = cal.plan(day)
        if not entries:
            continue
        today_acts = [a for a in acts if a["date"] == day.isoformat() and not a.get("followed")]
        for e in entries:
            plan = e["session"]
            best = max(((similarity(plan, a), i) for i, a in enumerate(today_acts)), default=(0, -1))
            if best[1] >= 0 and best[0] > 0 and not today_acts[best[1]].get("plan_score"):
                today_acts[best[1]]["plan_score"] = best[0]          # for "52 % of the plan" on the Week tab
            if best[0] >= threshold:
                a = today_acts.pop(best[1])
                a["followed"], a["match"] = plan["title"], best[0]
                if plan.get("intensity") == "hard":
                    a["plan_hard"] = True
                n += 1
    return n
