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
    _language[0] = str((cfg.get("strength") or {}).get("language") or "en").lower()[:2]
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


# The same cues in Spanish, short enough to fit the watch without scrolling (about 45
# characters). Used when `strength.language: es` in config.yaml.
HOW_ES = {
    "Walking lunge": "Paso largo, rodilla casi al suelo, alterna",
    "Bear crawl": "Manos y pies, rodillas arriba, espalda plana",
    "Leg swings": "Una pierna recta adelante y atrás, suave",
    "Press-up walk-out": "Manos al suelo, camina a flexión, vuelve",
    "Front squat": "Barra al pecho, codos altos, baja recto",
    "Step-up onto box (50 cm)": "Pie entero en el cajón, sube, baja lento",
    "Single-leg RDL with knee drive": "Bisagra a una pierna, sube y rodilla arriba",
    "Single-leg RDL": "Bisagra a una pierna, espalda plana, sube",
    "Raised-leg hip thrust": "Hombros en el cajón, sube cadera, aprieta",
    "Bulgarian split squat (rear foot on box)": "Pie atrás en el cajón, baja recto, sube",
    "Single-leg calf raise": "Un pie en el borde, sube alto, baja lento",
    "Lateral walk": "Goma en los tobillos, pasos laterales",
    "Hanging leg raise": "Colgado, sin balanceo, rodillas a la cadera",
    "Side plank": "Antebrazo bajo el hombro, cuerpo recto",
    "Box jump (50 cm), step down": "Salta al cajón, aterriza suave, baja andando",
    "Broad jump": "Salto largo, aterriza suave en sentadilla",
    "Lateral bound": "Salta de lado de un pie al otro, amortigua",
    "Split squat jump": "Zancada, salta y cambia de pierna en el aire",
    "Pogo hop": "Saltitos rápidos solo con tobillos",
    "Pull-up": "Cuelga, sube la barbilla sobre la barra",
    "Hurdle hops": "Saltos a pies juntos sobre un obstáculo bajo",
    "Bent-over barbell row": "Torso a 45°, tira de la barra a las costillas",
    "Press-up": "Cuerpo recto, pecho casi al suelo, empuja",
    "Single-arm snatch": "Mancuerna del suelo a brazo estirado arriba",
    "Prone Y raise": "Boca abajo, brazos en Y, sube y aprieta",
    "Prone T raise": "Boca abajo, brazos en T, sube y aprieta",
    "Prone W raise": "Boca abajo, codos doblados (W), sube y aprieta",
    "Dead bug": "Boca arriba, baja brazo y pierna contrarios",
    "Seated calf raise": "Sentado, mancuerna en rodillas, sube a puntas",
    "Standing side bend": "Mancuerna en una mano, inclínate de lado",
    "Glute bridge march": "Puente de glúteo, sube una rodilla y la otra",
    "Superman hold": "Boca abajo, sube brazos y piernas y aguanta",
}
# Warm-up moves are plain timed steps (no Garmin exercise), so the watch doesn't count
# reps or ask for them after each one: the step's note is the whole instruction.
WARMUP_MOVES = {"Walking lunge", "Bear crawl", "Leg swings", "Press-up walk-out"}
_language = ["en"]

# Wording of the written description when `strength.language: es`. Exercise names and
# workout titles stay in English (the names identify the exercise; the coach reads titles).
TIP_ES = {
    "Heavier, fewer reps, explosive up and controlled down; jumps fast off the ground.":
        "Más peso y menos repeticiones: sube explosivo, baja controlado; saltos rápidos, con poco contacto con el suelo.",
    "Smooth reps with 2-3 left in the tank; the last rep should still look good.":
        "Repeticiones fluidas, con 2-3 en reserva; la última debe salir con buena técnica.",
    "Controlled reps; shoulders down and back on the rows and Y-T-W (good for swimming).":
        "Repeticiones controladas; hombros atrás y abajo en el remo y en Y-T-W (bueno para nadar).",
}
_PHRASES_ES = (
    ("each leg, left then right", "cada pierna, primero izquierda y luego derecha"),
    ("each side, left then right", "cada lado, primero izquierdo y luego derecho"),
    ("each hand, left then right", "cada mano, primero izquierda y luego derecha"),
    ("alternating hands (switch at the floor)", "alternando manos (cambia en el suelo)"),
    ("alternating sides", "alternando lados"), ("alternating legs", "alternando piernas"),
    (" one way, ", " a un lado, "), (" back", " de vuelta"), (" steps:", " pasos:"), (" reps", " repeticiones"),
)


def es_line(line: str) -> str:
    """One written exercise line ("Front squat · 8 reps @ 30 kg") in Spanish."""
    name, _, rest = line.partition(" · ")
    for en, es in _PHRASES_ES:
        rest = rest.replace(en, es)
    return f"{name} · {rest}" if rest else name


def how(name: str) -> str:
    return (HOW_ES if _language[0] == "es" else HOW).get(name, "")


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
    warm = name in WARMUP_MOVES and secs and not reps
    base = {"type": "interval", "zone": None, "exercise": None if warm else GARMIN_EXERCISE.get(name), "kg": kg,
            "how": "" if warm else how(name)}
    end = {"end": "time", "value": secs} if secs else {"end": "reps", "value": reps}
    what = f"{reps} reps" if reps else f"{secs}\""
    if each_side and name in ALTERNATE:
        n = reps * 2
        line = f"{name} · {ALTERNATE[name].format(n=n, r=reps)}{load}"
        return [base | {"end": "reps", "value": n, "note": line, "line": line}]
    if each_side:
        part = SIDE.get(name, "leg")
        line = f"{name} · {what} each {part}, left then right{load}"
        es = warm and _language[0] == "es"
        return [base | end | {"note": (f"{name} {secs}\" · {'izquierda' if lr == 'left' else 'derecha'}"
                                       if es else f"{name} · {what} · {lr} {part}{load}"), "line": line}
                for lr in ("left", "right")]
    line = f"{name} · {what}{load}"
    note = f"{name} {secs}\"" if warm and _language[0] == "es" else line
    return [base | end | {"note": note, "line": line}]


def _rest(secs: int) -> dict:
    return {"type": "rest", "end": "time", "value": secs, "zone": None}


def _block(rounds: int, exercises: list[list[dict]], rest_s: int) -> dict:
    return {"type": "repeat", "iterations": rounds, "steps": [x for e in exercises for x in e] + [_rest(rest_s)]}


def _warmup(spare_shoulders: bool = False) -> list[dict]:   # built per session: the notes follow the language
    moves = (ex("Walking lunge", secs=30), ex("Leg swings", secs=15, each_side=True)) if spare_shoulders else (
        ex("Walking lunge", secs=30), ex("Bear crawl", secs=30), ex("Leg swings", secs=15, each_side=True),
        ex("Press-up walk-out", secs=30))
    return [x for e in moves for x in e]


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


SWIM_DAY_TIP = "Swim day: no shoulders or upper body today, only core and legs, light."
TIP_ES[SWIM_DAY_TIP] = "Día de natación: hoy sin hombros ni tren superior, solo core y piernas, suave."


def _core_blocks(phase: Phase, wk: int, spare_shoulders: bool = False) -> tuple[str, list[dict], str]:
    db, row = _load("dumbbell", wk), _load("row", wk)
    rounds = 2 if phase.name in ("taper", "race_week", "transition") else 3
    if spare_shoulders:                      # swim day: nothing that loads the shoulders
        blocks = [
            _block(rounds, [ex("Dead bug", 10, each_side=True), ex("Glute bridge march", 10, each_side=True),
                            ex("Seated calf raise", 15, kg=db)], 45),
            _block(2, [ex("Lateral walk", 10, each_side=True), ex("Single-leg calf raise", 12, kg=db, each_side=True)], 45),
        ]
        return "Strength & core", blocks, SWIM_DAY_TIP
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


def spare_shoulders(sessions: list[Session], phase: Phase, wk: int = 0, swam: bool = False,
                    notes: list[str] | None = None) -> list[Session]:
    """On a swim day (a swim planned, or one already done today) the upper-body strength
    session becomes core and legs only: your shoulders are spent after the pool."""
    # an optional swim doesn't count until you've actually swum (then `swam` is set)
    if not (swam or any(s.sport == "swim" and "(optional)" not in s.title for s in sessions)):
        return sessions
    out = []
    for s in sessions:
        if s.sport == "strength" and s.kind == "strength_core" and SWIM_DAY_TIP not in (s.detail or "") \
                and "no shoulders" not in (s.detail or "") and "sin hombros" not in (s.detail or ""):
            out.append(make_strength("strength_core", phase, wk, spare_shoulders=True))
            if notes is not None:
                notes.append("Swim day: the strength session leaves out shoulders and upper body.")
        else:
            out.append(s)
    return out


def make_strength(kind: str, phase: Phase, wk: int = 0, spare_shoulders: bool = False) -> Session:
    """`strength` or `strength_core`. Race week and the days around a race: core only.
    `spare_shoulders`: core and legs only (swim day)."""
    if kind == "strength" and phase.name in ("race_week", "taper", "transition"):
        kind = "strength_core"
    title, blocks, tip = (_strength_blocks(phase, wk) if kind == "strength"
                          else _core_blocks(phase, wk, spare_shoulders))
    steps = [{"type": "repeat", "iterations": 2, "steps": _warmup(spare_shoulders and kind == "strength_core")}] + blocks
    m = _minutes(steps)

    es = _language[0] == "es"
    spare = spare_shoulders and kind == "strength_core"
    moves = "walking lunge, leg swings" if spare else "walking lunge, bear crawl, leg swings, press-up walk-out"
    lines = [f"Calentamiento, 2 rondas de 30\" cada uno: {moves}" if es
             else f"Warm-up, 2 rounds of 30\" each: {moves}"]
    for b in blocks:
        rest_s = next((x["value"] for x in b["steps"] if x["type"] == "rest"), 0)
        lines.append(f"{b['iterations']} rondas ({rest_s}\" de descanso tras cada una):" if es
                     else f"{b['iterations']} rounds ({rest_s}\" rest after each):")
        lines += [f"• {es_line(x) if es else x}"
                  for x in dict.fromkeys(x["line"] for x in b["steps"] if x["type"] == "interval")]
    detail = "\n".join(lines + [TIP_ES.get(tip, tip) if es else tip])
    names = dict.fromkeys(x["line"].split(" · ")[0] for b in [steps[0]] + blocks for x in b["steps"]
                          if x["type"] == "interval")
    head = "Cómo hacerlo:\n" if _language[0] == "es" else "How to:\n"
    how_text = head + "\n".join(f"• {n}: {how(n).split(': ', 1)[-1]}"
                                for n in names if how(n))
    return Session(kind, "strength", f"{title} {m}'", "moderate" if kind == "strength" else "easy",
                   None, m, detail, steps, how=how_text)


# A picture per exercise for the dashboard: the free-exercise-db collection (public domain),
# loaded from GitHub. Exercises with no good match have no picture.
_IMG = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/{}/0.jpg"
IMAGE = {
    "Walking lunge": "Bodyweight_Walking_Lunge", "Press-up walk-out": "Inchworm",
    "Front squat": "Front_Squat_Clean_Grip", "Step-up onto box (50 cm)": "Dumbbell_Step_Ups",
    "Raised-leg hip thrust": "Single_Leg_Glute_Bridge", "Bulgarian split squat (rear foot on box)": "Split_Squats",
    "Single-leg calf raise": "Calf_Raise_On_A_Dumbbell", "Hanging leg raise": "Hanging_Leg_Raise",
    "Side plank": "Side_Bridge", "Box jump (50 cm), step down": "Front_Box_Jump", "Lateral bound": "Lateral_Bound",
    "Split squat jump": "Split_Jump", "Pogo hop": "Rope_Jumping", "Pull-up": "Pullups", "Hurdle hops": "Hurdle_Hops",
    "Bent-over barbell row": "Bent_Over_Barbell_Row", "Press-up": "Pushups", "Dead bug": "Dead_Bug",
    "Single-arm snatch": "One-Arm_Kettlebell_Snatch", "Standing side bend": "Dumbbell_Side_Bend",
    "Glute bridge march": "Butt_Lift_Bridge", "Superman hold": "Superman",
}


def image_url(name: str) -> str:
    return _IMG.format(IMAGE[name]) if name in IMAGE else ""


def name_from_step(step: dict) -> str:
    """The exercise's name for a step of a workout read back from Garmin: its cue
    (either language) or Garmin's exercise name; warm-up steps carry it in their note."""
    desc = str(step.get("description") or "")
    for table in (HOW_ES, HOW):
        for n, cue in table.items():
            if cue and cue[:100] == desc:
                return n
    key = step.get("exerciseName")
    if key:
        for n, (_, k) in GARMIN_EXERCISE.items():
            if k == key:
                return n
    return desc.split(' ·')[0].rsplit(' ', 1)[0].strip() if desc else ""


def read_workout(w: dict) -> list[dict]:
    """A strength workout from Garmin as blocks for the dashboard:
    [{rounds, rest_s, items: [{name, what, kg, cue, img}]}]. Left/right steps become one item."""
    def items(steps: list[dict]) -> list[dict]:
        out: list[dict] = []
        for s in steps:
            if s.get("type") != "ExecutableStepDTO" or (s.get("stepType") or {}).get("stepTypeKey") == "rest":
                continue
            name = name_from_step(s)
            cond = (s.get("endCondition") or {}).get("conditionTypeKey")
            v = int(s.get("endConditionValue") or 0)
            what = f'{v}"' if cond == "time" else f"{v} reps" if cond == "reps" else ""
            kg = round(float(s.get("weightValue") or 0) / 1000, 1)
            cue = "" if (s.get("description") or "").startswith(name) else str(s.get("description") or "")
            if out and out[-1]["name"] == name and out[-1]["what"] == what and not out[-1]["both"]:
                out[-1]["both"] = True
                continue
            out.append({"name": name, "what": what, "kg": kg or None, "cue": cue, "img": image_url(name), "both": False})
        return out

    blocks, loose = [], []
    for s in (w.get("workoutSegments") or [{}])[0].get("workoutSteps") or []:
        if s.get("type") == "RepeatGroupDTO":
            inner = s.get("workoutSteps") or []
            rest = next((int(x.get("endConditionValue") or 0) for x in inner
                         if (x.get("stepType") or {}).get("stepTypeKey") == "rest"), 0)
            blocks.append({"rounds": int(s.get("numberOfIterations") or 1), "rest_s": rest, "items": items(inner)})
        else:
            loose.append(s)
    if loose:
        blocks.insert(0, {"rounds": 1, "rest_s": 0, "items": items(loose), "warmup": True})
    return blocks
