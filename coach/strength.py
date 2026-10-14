"""Strength sessions for runners and triathletes, for a small home gym.

Built like a running app's sessions: a dynamic warm-up, then blocks of 2-3
exercises done as a circuit for 2-3 rounds, with a rest after each round. Two
kinds:
  strength       lower-body strength and power (squats, single-leg work, hip
                 thrusts, calves, jumps) plus a core finisher, ~45'
  strength_core  lighter: core, upper body and shoulders for swimming, a bit of
                 single-leg work, ~30'

They follow the phase: strength in base, power (jumps, faster reps) in build,
lighter in the race-specific weeks and taper, none in race week (just core).
Loads come from `strength.loads` in config.yaml and grow 2 kg a week within each
4-week block; the bar never goes above the weight you have (`strength.max_bar_kg`).

On the watch each exercise is a step from Garmin's exercise catalogue (its name,
reps and weight on screen; the watch counts the reps); rests are timed.
"""
from __future__ import annotations

from .planner import Phase, Session

_loads: dict = {}
DEFAULT_LOADS = {"front_squat": 30, "hip_thrust": 30, "dumbbell": 10, "row": 20, "max_bar_kg": 48}


def set_loads(cfg: dict) -> None:
    _loads.clear()
    _loads.update(DEFAULT_LOADS | ((cfg.get("strength") or {}).get("loads") or {}))
    if (cfg.get("strength") or {}).get("max_bar_kg"):
        _loads["max_bar_kg"] = cfg["strength"]["max_bar_kg"]


def _load(key: str, wk: int) -> float:
    """Your load for this lift, +2 kg per load week within the 4-week block (plates go
    on in pairs, so whole kilos are always loadable)."""
    base = float((_loads or DEFAULT_LOADS).get(key, DEFAULT_LOADS.get(key, 10)))
    if key == "dumbbell":
        return base
    step = (wk % 4) if (wk % 4) < 3 else 0          # weeks 1-3 build, week 4 deload back to base
    return min(round(base) + 2 * step, float((_loads or DEFAULT_LOADS).get("max_bar_kg", 48)))


# Garmin's own exercise catalogue (category, exercise), so the watch shows the exercise
# name, counts the reps and shows the weight instead of a plain "Go" step. Checked
# against garminconnect.exercises; exercises it doesn't have show their category.
GARMIN_EXERCISE = {
    "Walking lunge": ("LUNGE", "WALKING_LUNGE"),
    "Bear crawl": ("PLANK", "BEAR_CRAWL"),
    "Heel walks": ("WARM_UP", ""),
    "Press-up walk-out": ("WARM_UP", "WALKOUT_FROM_PUSH_UP_POSITION"),
    "Front squat": ("SQUAT", "BARBELL_FRONT_SQUAT"),
    "Step-up onto box (50 cm)": ("SQUAT", "DUMBBELL_STEP_UP"),
    "Single-leg RDL with knee drive": ("DEADLIFT", "SINGLE_LEG_ROMANIAN_DEADLIFT_WITH_DUMBBELL"),
    "Single-leg RDL": ("DEADLIFT", "SINGLE_LEG_ROMANIAN_DEADLIFT_WITH_DUMBBELL"),
    "Raised-leg hip thrust": ("HIP_RAISE", "WEIGHTED_SINGLE_LEG_HIP_RAISE"),
    "Bulgarian split squat (rear foot on box)": ("LUNGE", "DUMBBELL_BULGARIAN_SPLIT_SQUAT"),
    "Single-leg calf raise": ("CALF_RAISE", "SINGLE_LEG_STANDING_DUMBBELL_CALF_RAISE"),
    "Lateral walk": ("HIP_STABILITY", "LATERAL_WALKS_WITH_BAND_AT_ANKLES"),
    "Hanging leg raise": ("LEG_RAISE", "HANGING_LEG_RAISE"),
    "Side plank": ("PLANK", "SIDE_PLANK"),
    "Box jump (50 cm), step down": ("PLYO", "BOX_JUMP"),
    "Hurdle hops": ("PLYO", ""),
    "Bent-over barbell row": ("ROW", "BENT_OVER_ROW_WITH_BARBELL"),
    "Press-up": ("PUSH_UP", "PUSH_UP"),
    "Single-arm snatch": ("OLYMPIC_LIFT", "SINGLE_ARM_DUMBBELL_SNATCH"),
    "Prone Y-T-W raises (swim shoulders)": ("SHOULDER_STABILITY", "FLOOR_Y_RAISE"),
    "Dead bug": ("HIP_STABILITY", "DEAD_BUG"),
    "Bent-knee calf raise": ("CALF_RAISE", "SINGLE_LEG_BENT_KNEE_CALF_RAISE"),
    "Standing side bend": ("CORE", "WEIGHTED_SIDE_BEND"),
    "Glute bridge march": ("HIP_RAISE", "MARCHING_HIP_RAISE"),
    "Superman hold": ("HYPEREXTENSION", "SUPERMAN_FROM_FLOOR"),
}


def ex(name: str, reps: int | None = None, secs: int | None = None, kg: float | None = None,
       each_side: bool = False) -> dict:
    what = f"{reps} reps" if reps else f"{secs}\""
    if each_side:
        what += " each side"
    note = f"{name} · {what}" + (f" @ {kg:g} kg" if kg else "")
    step = {"type": "interval", "zone": None, "note": note, "exercise": GARMIN_EXERCISE.get(name), "kg": kg}
    if secs:
        return step | {"end": "time", "value": secs * (2 if each_side else 1)}
    # the watch counts the reps (both sides together when it's each side)
    return step | {"end": "reps", "value": reps * (2 if each_side else 1)}


def _rest(secs: int) -> dict:
    return {"type": "rest", "end": "time", "value": secs, "zone": None}


def _block(rounds: int, exercises: list[dict], rest_s: int) -> dict:
    return {"type": "repeat", "iterations": rounds, "steps": exercises + [_rest(rest_s)]}


WARMUP = [ex("Walking lunge", secs=20), ex("Bear crawl", secs=20), ex("Heel walks", secs=20),
          ex("Press-up walk-out", secs=20)]


def _strength_blocks(phase: Phase, wk: int) -> tuple[str, list[dict], str]:
    db = _load("dumbbell", wk)
    squat, thrust = _load("front_squat", wk), _load("hip_thrust", wk)
    power = phase.name in ("build", "specific", "peak")
    rounds = 2 if phase.name in ("specific", "peak", "taper", "transition") else 3
    if power:
        # power phase: heavier, fewer reps, plus jumps
        blocks = [
            _block(rounds, [ex("Front squat", 5, kg=squat + 2), ex("Box jump (50 cm), step down", 5)], 120),
            _block(rounds, [ex("Single-leg RDL", 6, kg=db, each_side=True), ex("Hurdle hops", 10)], 90),
            _block(rounds, [ex("Bulgarian split squat (rear foot on box)", 6, kg=db, each_side=True),
                            ex("Single-leg calf raise", 12, kg=db, each_side=True)], 75),
            _block(2, [ex("Hanging leg raise", 10), ex("Side plank", secs=40, each_side=True)], 45),
        ]
        return "Strength · power", blocks, "Heavier, fewer reps, explosive up and controlled down; jumps fast off the ground."
    blocks = [
        _block(rounds, [ex("Front squat", 8, kg=squat), ex("Step-up onto box (50 cm)", 8, kg=db, each_side=True)], 90),
        _block(rounds, [ex("Single-leg RDL with knee drive", 10, kg=db, each_side=True),
                        ex("Raised-leg hip thrust", 10, kg=thrust)], 75),
        _block(rounds, [ex("Bulgarian split squat (rear foot on box)", 8, kg=db, each_side=True),
                        ex("Single-leg calf raise", 15, kg=db, each_side=True), ex("Lateral walk", 10, each_side=True)], 60),
        _block(2, [ex("Hanging leg raise", 10), ex("Side plank", secs=40, each_side=True)], 45),
    ]
    return "Strength · lower body", blocks, "Smooth reps with 2-3 left in the tank; the last rep should still look good."


def _core_blocks(phase: Phase, wk: int) -> tuple[str, list[dict], str]:
    db, row = _load("dumbbell", wk), _load("row", wk)
    rounds = 2 if phase.name in ("taper", "race_week", "transition") else 3
    blocks = [
        _block(rounds, [ex("Bent-over barbell row", 10, kg=row), ex("Press-up", 10),
                        ex("Single-arm snatch", 8, kg=db, each_side=True)], 60),
        _block(rounds, [ex("Prone Y-T-W raises (swim shoulders)", 8), ex("Dead bug", 10, each_side=True)], 45),
        _block(rounds, [ex("Bent-knee calf raise", 12, kg=db), ex("Standing side bend", 10, kg=db, each_side=True),
                        ex("Glute bridge march", 10, each_side=True)], 45),
        _block(2, [ex("Side plank", secs=40, each_side=True), ex("Superman hold", secs=30)], 30),
    ]
    return "Strength & core", blocks, "Controlled reps; shoulders down and back on the rows and Y-T-W (good for swimming)."


def _minutes(steps: list[dict]) -> int:
    def secs(st):
        if st["type"] == "repeat":
            return st["iterations"] * sum(secs(x) for x in st["steps"])
        return st["value"] if st["end"] == "time" else 45          # ~45" per set of reps
    return round(sum(secs(st) for st in steps) / 60)


def make_strength(kind: str, phase: Phase, wk: int = 0) -> Session:
    """`strength` or `strength_core`. Race week and the days around a race: core only."""
    if kind == "strength" and phase.name in ("race_week", "taper", "transition"):
        kind = "strength_core"
    title, blocks, tip = (_strength_blocks if kind == "strength" else _core_blocks)(phase, wk)
    steps = [{"type": "repeat", "iterations": 2, "steps": WARMUP}] + blocks
    m = _minutes(steps)

    lines = ["Warm-up, 2 rounds of 20\" each: walking lunge, bear crawl, heel walks, press-up walk-out"]
    for b in blocks:
        rest_s = next((x["value"] for x in b["steps"] if x["type"] == "rest"), 0)
        lines.append(f"{b['iterations']} rounds ({rest_s}\" rest after each):")
        lines += [f"• {x['note']}" for x in b["steps"] if x["type"] == "interval"]
    detail = "\n".join(lines + [tip])
    return Session(kind, "strength", f"{title} {m}'", "moderate" if kind == "strength" else "easy",
                   None, m, detail, steps)
