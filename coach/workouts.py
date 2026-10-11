"""Translates a Session into Garmin Connect workout JSON."""
from __future__ import annotations

from .planner import Session

SPORT = {"run": (1, "running"), "bike": (2, "cycling"), "swim": (4, "swimming"), "other": (3, "other")}
STEP = {"warmup": (1, "warmup"), "cooldown": (2, "cooldown"), "interval": (3, "interval"),
        "recovery": (4, "recovery"), "rest": (5, "rest"), "repeat": (6, "repeat")}
COND = {"lap": (1, "lap.button"), "time": (2, "time"), "distance": (3, "distance"), "iterations": (7, "iterations")}


def _target(zone):
    if zone:
        return {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}, zone
    return {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}, None


def _steps(steps: list[dict], counter: list[int], swim: bool) -> list[dict]:
    out = []
    for st in steps:
        counter[0] += 1
        order = counter[0]
        if st["type"] == "repeat":
            sid, skey = STEP["repeat"]
            cid, ckey = COND["iterations"]
            out.append({
                "type": "RepeatGroupDTO", "stepOrder": order,
                "stepType": {"stepTypeId": sid, "stepTypeKey": skey, "displayOrder": sid},
                "numberOfIterations": st["iterations"], "smartRepeat": False,
                "endCondition": {"conditionTypeId": cid, "conditionTypeKey": ckey, "displayOrder": cid, "displayable": False},
                "endConditionValue": float(st["iterations"]),
                "workoutSteps": _steps(st["steps"], counter, swim),
            })
            continue
        sid, skey = STEP[st["type"]]
        cid, ckey = COND[st["end"]]
        zone = None if swim else st.get("zone")
        tgt, zn = _target(zone)
        step = {
            "type": "ExecutableStepDTO", "stepOrder": order,
            "stepType": {"stepTypeId": sid, "stepTypeKey": skey, "displayOrder": sid},
            "endCondition": {"conditionTypeId": cid, "conditionTypeKey": ckey, "displayOrder": cid, "displayable": True},
            "endConditionValue": float(st["value"]),
            "targetType": tgt,
        }
        if zn:
            step["zoneNumber"] = zn
        if st.get("note"):
            step["description"] = st["note"][:100]
        if swim:
            step["strokeType"] = {"strokeTypeId": 0, "displayOrder": 0}
            step["equipmentType"] = {"equipmentTypeId": 0, "displayOrder": 0}
        out.append(step)
    return out


def _estimate_secs(steps, pace_s_per_km: float, swim: bool) -> float:
    tot = 0.0
    for st in steps:
        if st["type"] == "repeat":
            tot += st["iterations"] * _estimate_secs(st["steps"], pace_s_per_km, swim)
        elif st["end"] == "time":
            tot += st["value"]
        elif st["end"] == "distance":
            tot += st["value"] / 100 * 120 if swim else st["value"] / 1000 * pace_s_per_km
    return tot


def to_garmin(s: Session, cfg: dict, description: str) -> dict:
    sid, skey = SPORT[s.sport]
    swim = s.sport == "swim"
    pace = float(cfg.get("athlete", {}).get("easy_pace_min_per_km", 6.0)) * 60
    prefix = cfg.get("garmin", {}).get("workout_prefix", "Coach")
    sport = {"sportTypeId": sid, "sportTypeKey": skey, "displayOrder": sid}
    w = {
        "workoutName": f"{prefix} · {s.title}"[:60],
        "description": description[:1000],
        "sportType": sport,
        "estimatedDurationInSecs": int(_estimate_secs(s.steps, pace, swim)),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": sport,
            "workoutSteps": _steps(s.steps, [0], swim),
        }],
    }
    if s.distance_km and s.sport in ("run", "swim"):
        w["estimatedDistanceInMeters"] = round(s.distance_km * 1000)   # read back by the coach next day
    if swim:
        w["poolLength"] = float(cfg.get("athlete", {}).get("pool_length_m", 25))
        w["poolLengthUnit"] = {"unitId": 1, "unitKey": "meter", "factor": 100.0}
    return w
