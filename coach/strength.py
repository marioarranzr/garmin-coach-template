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
    "Leg swings": ("WARM_UP", "FORWARD_AND_BACKWARD_LEG_SWINGS"),
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
    "Broad jump": ("PLYO", ""),
    "Lateral bound": ("PLYO", "LATERAL_LEAP_AND_HOP"),
    "Split squat jump": ("PLYO", "DUMBBELL_SPLIT_JUMP"),
    "Pogo hop": ("PLYO", ""),
    "Pull-up": ("PULL_UP", "PULL_UP"),
    "Hurdle hops": ("PLYO", ""),
    "Bent-over barbell row": ("ROW", "BENT_OVER_ROW_WITH_BARBELL"),
    "Press-up": ("PUSH_UP", "PUSH_UP"),
    "Single-arm snatch": ("OLYMPIC_LIFT", "SINGLE_ARM_DUMBBELL_SNATCH"),
    "Prone Y raise": ("SHOULDER_STABILITY", "FLOOR_Y_RAISE"),
    "Prone T raise": ("SHOULDER_STABILITY", "FLOOR_T_RAISE"),
    "Prone W raise": ("SHOULDER_STABILITY", "INCLINE_W_RAISE"),     # no floor W in Garmin's list
    "Dead bug": ("HIP_STABILITY", "DEAD_BUG"),
    "Seated calf raise": ("CALF_RAISE", "WEIGHTED_SEATED_CALF_RAISE"),
    "Standing side bend": ("CORE", "WEIGHTED_SIDE_BEND"),
    "Glute bridge march": ("HIP_RAISE", "MARCHING_HIP_RAISE"),
    "Superman hold": ("HYPEREXTENSION", "SUPERMAN_FROM_FLOOR"),
}


# How to do each one, in a line: the watch shows it as the step's note (it already shows
# the exercise name, reps and weight), and the Todoist plan lists them under the session.
# Kept under 100 characters, Garmin's limit for a step note.
HOW = {
    "Walking lunge": "Long step forward, back knee almost to the floor, push up and step through to the next lunge.",
    "Bear crawl": "On hands and toes, knees just off the floor, back flat; crawl forward with opposite hand and foot.",
    "Leg swings": "Hold a wall, swing one straight leg forward and back, relaxed and gradually higher.",
    "Press-up walk-out": "From standing, hands to the floor, walk them out to a press-up, one press-up, walk back, stand.",
    "Front squat": "Bar on the front of the shoulders, elbows high; sit down between your heels, chest up, drive up.",
    "Step-up onto box (50 cm)": "Whole foot on the box, push through that heel to stand tall on it; step down slowly.",
    "Single-leg RDL with knee drive": "Hinge on one leg, back flat, dumbbell to mid-shin; stand up and drive the other knee high.",
    "Single-leg RDL": "On one leg, hinge at the hips with a flat back, dumbbell to mid-shin, squeeze the glute to stand.",
    "Raised-leg hip thrust": "Shoulders on the box, one foot down, other leg up; drive hips up until flat, squeeze, lower.",
    "Bulgarian split squat (rear foot on box)": "Rear foot on the box, front foot far ahead; drop the back knee straight down, push up.",
    "Single-leg calf raise": "On one foot on a step edge, heel low; rise as high as you can, 1\" pause, lower slowly.",
    "Lateral walk": "Band around the ankles, half squat, toes forward; side steps without letting the feet touch.",
    "Hanging leg raise": "Hang from the bar, no swinging; lift knees (or straight legs) to hip height, lower slowly.",
    "Side plank": "On one forearm, elbow under the shoulder, body straight from head to feet, hips high.",
    "Box jump (50 cm), step down": "Quick dip and arm swing, jump onto the box, land softly in a half squat; step back down.",
    "Broad jump": "Bend knees, swing arms back; explode forward as far as possible, land softly in a squat.",
    "Lateral bound": "Push off one foot sideways; land softly on the other foot, absorb the landing, then bound back.",
    "Split squat jump": "Start in a lunge, explode straight up, switch feet in the air; land in opposite lunge softly.",
    "Pogo hop": "Ankles stiff, calves only; quick, springy two-footed hops on the spot — ground contact under 0.2 s.",
    "Pull-up": "Full hang, shoulders packed; pull until chin clears the bar, lower slowly to a dead hang.",
    "Hurdle hops": "Hurdle hops: two-footed hops over a low object, quick and springy off the ground.",
    "Bent-over barbell row": "Hinge to ~45°, flat back; pull the bar to your lower ribs, squeeze shoulder blades, lower slowly.",
    "Press-up": "Hands under the shoulders, body straight; chest to just above the floor, push back up.",
    "Single-arm snatch": "Dumbbell between your feet, squat down; jump it up in one move to straight arm overhead.",
    "Prone Y raise": "Face down, forehead on a towel; arms overhead in a Y, thumbs up; lift them, squeeze, lower.",
    "Prone T raise": "Face down, forehead on a towel; arms out to the sides in a T, thumbs up; lift, squeeze, lower.",
    "Prone W raise": "Face down, forehead on a towel; elbows bent, hands by your shoulders (W); lift, squeeze, lower.",
    "Dead bug": "On your back, arms up, knees at 90°; lower opposite arm and leg, lower back stays on the floor.",
    "Seated calf raise": "Sit on the box, both feet on a plate, dumbbell on your knees; rise onto your toes, lower slowly.",
    "Standing side bend": "Dumbbell in one hand, stand tall; bend sideways toward it, come back up using the other side.",
    "Glute bridge march": "Hips up in a bridge; lift one knee, then the other, without letting the hips drop or twist.",
    "Superman hold": "Face down, arms forward; lift arms, chest and legs a few cm off the floor and hold.",
}


# one side at a time: what changes sides ("left leg", then "right leg")
SIDE = {"Side plank": "side", "Standing side bend": "hand"}
# both sides, alternating within one set: what the total counts
ALTERNATE = {"Single-arm snatch": "{n} reps, alternating hands (switch at the floor)",
             "Dead bug": "{n} reps, alternating sides", "Glute bridge march": "{n} reps, alternating legs",
             "Lateral walk": "{n} steps: {r} one way, {r} back"}


def ex(name: str, reps: int | None = None, secs: int | None = None, kg: float | None = None,
       each_side: bool = False) -> list[dict]:
    """The exercise's steps on the watch. `each_side`: `reps`/`secs` per side, as two
    steps (left, then right), or as one set alternating sides (ALTERNATE: 2× the reps)."""
    load = f" @ {kg:g} kg" if kg else ""
    base = {"type": "interval", "zone": None, "exercise": GARMIN_EXERCISE.get(name), "kg": kg,
            "how": HOW.get(name, "")}
    end = {"end": "time", "value": secs} if secs else {"end": "reps", "value": reps}
    what = f"{reps} reps" if reps else f"{secs}\""
    if each_side and name in ALTERNATE:
        n = reps * 2
        line = f"{name} · {ALTERNATE[name].format(n=n, r=reps)}{load}"
        return [base | {"end": "reps", "value": n, "note": line, "line": line}]
    if each_side:
        part = SIDE.get(name, "leg")
        line = f"{name} · {what} each {part}, left then right{load}"
        return [base | end | {"note": f"{name} · {what} · {lr} {part}{load}", "line": line}
                for lr in ("left", "right")]
    line = f"{name} · {what}{load}"
    return [base | end | {"note": line, "line": line}]


def _rest(secs: int) -> dict:
    return {"type": "rest", "end": "time", "value": secs, "zone": None}


def _block(rounds: int, exercises: list[list[dict]], rest_s: int) -> dict:
    return {"type": "repeat", "iterations": rounds, "steps": [x for e in exercises for x in e] + [_rest(rest_s)]}


WARMUP = [x for e in (ex("Walking lunge", secs=30), ex("Bear crawl", secs=30),
                       ex("Leg swings", secs=15, each_side=True), ex("Press-up walk-out", secs=30)) for x in e]


def _strength_blocks(phase: Phase, wk: int) -> tuple[str, list[dict], str]:
    db = _load("dumbbell", wk)
    squat, thrust = _load("front_squat", wk), _load("hip_thrust", wk)
    power = phase.name in ("build", "specific", "peak")
    rounds = 2 if phase.name in ("specific", "peak", "taper", "transition") else 3
    if power:
        # power phase: heavier, fewer reps, plyometrics and jumps
        blocks = [
            _block(rounds, [ex("Front squat", 5, kg=squat + 2), ex("Box jump (50 cm), step down", 5),
                            ex("Broad jump", 5)], 120),
            _block(rounds, [ex("Single-leg RDL", 6, kg=db, each_side=True),
                            ex("Split squat jump", 6, each_side=True)], 90),
            _block(rounds, [ex("Bulgarian split squat (rear foot on box)", 6, kg=db, each_side=True),
                            ex("Lateral bound", 6, each_side=True),
                            ex("Single-leg calf raise", 12, kg=db, each_side=True)], 75),
            _block(2, [ex("Pogo hop", 20), ex("Hanging leg raise", 10),
                       ex("Side plank", secs=40, each_side=True)], 45),
        ]
        return "Strength · power", blocks, "Heavier, fewer reps, explosive up and controlled down; jumps fast off the ground."
    blocks = [
        _block(rounds, [ex("Front squat", 8, kg=squat), ex("Step-up onto box (50 cm)", 8, kg=db, each_side=True),
                        ex("Pogo hop", 15)], 90),
        _block(rounds, [ex("Single-leg RDL with knee drive", 10, kg=db, each_side=True),
                        ex("Raised-leg hip thrust", 8, kg=thrust, each_side=True)], 75),
        _block(rounds, [ex("Bulgarian split squat (rear foot on box)", 8, kg=db, each_side=True),
                        ex("Single-leg calf raise", 15, kg=db, each_side=True), ex("Lateral walk", 10, each_side=True)], 60),
        _block(2, [ex("Hanging leg raise", 10), ex("Side plank", secs=40, each_side=True)], 45),
    ]
    return "Strength · lower body", blocks, "Smooth reps with 2-3 left in the tank; the last rep should still look good."


def _core_blocks(phase: Phase, wk: int) -> tuple[str, list[dict], str]:
    db, row = _load("dumbbell", wk), _load("row", wk)
    rounds = 2 if phase.name in ("taper", "race_week", "transition") else 3
    blocks = [
        _block(rounds, [ex("Pull-up", 5), ex("Bent-over barbell row", 10, kg=row),
                        ex("Press-up", 10), ex("Single-arm snatch", 10, kg=db, each_side=True)], 60),
        # Y, T and W one after the other (swimming shoulders), then dead bugs
        _block(rounds, [ex("Prone Y raise", 8), ex("Prone T raise", 8), ex("Prone W raise", 8),
                        ex("Dead bug", 10, each_side=True)], 45),
        _block(rounds, [ex("Seated calf raise", 15, kg=db), ex("Standing side bend", 10, kg=db, each_side=True),
                        ex("Glute bridge march", 10, each_side=True)], 45),
        _block(2, [ex("Side plank", secs=40, each_side=True), ex("Superman hold", secs=30)], 30),
    ]
    return "Strength & core", blocks, "Controlled reps; shoulders down and back on the rows and Y-T-W (good for swimming)."


def _minutes(steps: list[dict]) -> int:
    def secs(st):
        if st["type"] == "repeat":
            return st["iterations"] * sum(secs(x) for x in st["steps"])
        return st["value"] if st["end"] == "time" else 15 + 3 * st["value"]   # ~3" a rep, plus setting up
    return round(sum(secs(st) for st in steps) / 60)


def make_strength(kind: str, phase: Phase, wk: int = 0) -> Session:
    """`strength` or `strength_core`. Race week and the days around a race: core only."""
    if kind == "strength" and phase.name in ("race_week", "taper", "transition"):
        kind = "strength_core"
    title, blocks, tip = (_strength_blocks if kind == "strength" else _core_blocks)(phase, wk)
    steps = [{"type": "repeat", "iterations": 2, "steps": WARMUP}] + blocks
    m = _minutes(steps)

    lines = ["Warm-up, 2 rounds of 30\" each: walking lunge, bear crawl, leg swings, press-up walk-out"]
    for b in blocks:
        rest_s = next((x["value"] for x in b["steps"] if x["type"] == "rest"), 0)
        lines.append(f"{b['iterations']} rounds ({rest_s}\" rest after each):")
        lines += [f"• {x}" for x in dict.fromkeys(x["line"] for x in b["steps"] if x["type"] == "interval")]
    detail = "\n".join(lines + [tip])
    names = dict.fromkeys(x["line"].split(" · ")[0] for b in [steps[0]] + blocks for x in b["steps"]
                          if x["type"] == "interval")
    how = "How to:\n" + "\n".join(f"• {n}: {HOW[n].split(': ', 1)[-1]}" for n in names if n in HOW)
    return Session(kind, "strength", f"{title} {m}'", "moderate" if kind == "strength" else "easy",
                   None, m, detail, steps, how=how)
