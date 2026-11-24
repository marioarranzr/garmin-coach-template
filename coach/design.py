"""Week design: the AI coach plans the whole week, code checks it.

Once a week (the first run of a week that has no design yet, normally Sunday evening for the coming week)
the AI picks which sessions go on which day, from the same session kinds the weekly template uses.
`validate` enforces the hard limits; a design that breaks them is repaired once by the AI, otherwise the
configured template is used. Distances and minutes still come from the usual planner (week target, paces,
phase), and notes (`no_run_until`, `day_plan`...) apply on top, as they do for the template.
A design is saved in `data/coach_log/week-<monday>.json` so the daily check follows it.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from . import ai
from . import load as load_model
from .planner import WEEKDAYS, monday_of, phase_for, template_for

log = logging.getLogger(__name__)
LOG_DIR = "data/coach_log"
KINDS = ["run_easy", "run_strides", "run_quality", "run_long", "run_brick", "bike_easy", "bike_spin",
         "bike_quality", "bike_long", "swim_technique", "swim_css", "swim_endurance", "strength",
         "strength_core", "rest"]
KEY_KINDS = ("run_long", "run_quality", "bike_long", "bike_quality", "run_brick")

SCHEMA = {
    "type": "object",
    "properties": {
        "days": {"type": "array", "items": {
            "type": "object",
            "properties": {"day": {"type": "string", "enum": WEEKDAYS},
                           "sessions": {"type": "array", "items": {"type": "string", "enum": KINDS}},
                           "purpose": {"type": "string"}},
            "required": ["day", "sessions", "purpose"], "additionalProperties": False}},
        "intent": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "changes_vs_template": {"type": "string"},
    },
    "required": ["days", "intent", "risks", "changes_vs_template"],
    "additionalProperties": False,
}

SYSTEM = """You are an experienced triathlon and running coach designing one week for an amateur athlete. \
The goal is the best possible form for the A races, not just staying healthy: keep the key sessions that \
build towards them, put the right work on the right day, and protect the athlete from injury.

You receive the athlete's phase, races, measured training load (fitness ctl, fatigue atl, form tsb, \
leg fatigue), the last weeks, preferences, notes and the usual weekly template. Return the week as the \
sessions on each day, using only the session kinds listed. Distances, minutes and paces are set later by \
the planner from the weekly target, so choose WHICH sessions go on WHICH day.

Rules (checked by code; a design that breaks them is thrown away):
- Keep the template's rest days as rest (a day with "rest" has nothing else). At least one rest day.
- Never more key sessions than the template has: run_long, run_quality, bike_long, bike_quality, run_brick. \
Keep the key session of the phase (usually run_long).
- Never more runs (any run_*) than the template; no runs on consecutive days when "no_runs_on_consecutive_days" \
is true; no run_quality when "return_mode" or "after_gap" is true; run_quality and run_long at least 2 days apart.
- No heavy leg strength ("strength") the day before a run_long or run_quality.
- Strength and swim sessions: no more than the template.
- Days before "today" are over: leave them as in the template, they're ignored.

How to decide:
- Follow the template unless there is a reason to change it: leg fatigue after skiing or hikes, a break \
or injury in the notes, a trip, deep fatigue (tsb below -20), a very light or heavy last week.
- Hard sessions need easy days around them. Put leg-heavy work (long run, quality run, heavy leg strength) \
on fresh legs; after a big leg day put easy rides, swims, core or rest.
- "purpose" is one short line per day; "intent" is the week in one or two sentences; "changes_vs_template" \
says what you changed and why, or "none"."""


def path(root: Path, monday: dt.date) -> Path:
    return Path(root) / LOG_DIR / f"week-{monday.isoformat()}.json"


def read(root: Path, monday: dt.date) -> dict | None:
    try:
        return json.loads(path(root, monday).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write(root: Path, monday: dt.date, record: dict) -> None:
    p = path(root, monday)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("ai") or {}).get("week_design")) and ai.enabled(cfg)


def attach(cfg: dict, root: Path, d: dt.date) -> dict:
    """cfg with the saved designs of this week and the next one (`_designs`: monday -> {weekday: kinds})."""
    designs = {}
    for k in (0, 1):
        mon = monday_of(d) + dt.timedelta(weeks=k)
        rec = read(root, mon)
        if rec and rec.get("days"):
            designs[mon.isoformat()] = rec["days"]
    return dict(cfg, _designs=designs) if designs else cfg


def validate(days: dict, base: dict, cfg: dict, wt: dict, phase, d: dt.date) -> list[str]:
    """Problems with a designed week (empty = fine). `days`/`base`: weekday -> list of kinds."""
    bad: list[str] = []
    if set(days) != set(WEEKDAYS):
        return ["all seven days are needed"]
    for w in WEEKDAYS:
        if not days[w] or any(k not in KINDS for k in days[w]):
            bad.append(f"{w}: unknown or empty sessions")
        elif "rest" in days[w] and len(days[w]) > 1:
            bad.append(f"{w}: rest can't be combined with other sessions")
    if bad:
        return bad
    flat = lambda t: [k for w in WEEKDAYS for k in t[w]]          # noqa: E731
    mine, theirs = flat(days), flat(base)
    for w in WEEKDAYS:
        if base[w] == ["rest"] and days[w] != ["rest"]:
            bad.append(f"{w} is a rest day in the template")
    if "rest" not in mine:
        bad.append("at least one rest day is needed")
    for k in KEY_KINDS:
        if mine.count(k) > theirs.count(k):
            bad.append(f"more {k} than the template ({mine.count(k)} > {theirs.count(k)})")
    for prefix in ("run_", "swim_", "strength"):
        a = sum(k.startswith(prefix) for k in mine)
        b = sum(k.startswith(prefix) for k in theirs)
        if a > b:
            bad.append(f"more {prefix.strip('_')} sessions than the template ({a} > {b})")
    key_kind = "bike_long" if phase.focus == "bike" else "run_long"
    if phase.name not in ("race_week", "transition") and key_kind in theirs and key_kind not in mine:
        bad.append(f"the phase's key session {key_kind} is missing")
    runs = [i for i, w in enumerate(WEEKDAYS) if any(k.startswith("run_") for k in days[w])]
    if (cfg.get("athlete") or {}).get("no_runs_on_consecutive_days") and any(b - a == 1 for a, b in zip(runs, runs[1:])):
        bad.append("runs on consecutive days")
    if (wt.get("return_mode") or wt.get("after_gap")) and "run_quality" in mine:
        bad.append("no quality run while returning to running")
    pos = {k: [i for i, w in enumerate(WEEKDAYS) if k in days[w]] for k in ("run_quality", "run_long", "strength")}
    if any(abs(a - b) < 2 for a in pos["run_quality"] for b in pos["run_long"]):
        bad.append("run_quality and run_long need 2 days between them")
    if any(i + 1 in pos["run_long"] + pos["run_quality"] for i in pos["strength"]):
        bad.append("heavy leg strength the day before a hard run")
    return bad


def _context(cfg: dict, d: dt.date, wt: dict, phase, acts: list[dict], base: dict, notes: list[str],
             prefs: list[str], constraints: dict, problems: list[str] | None = None) -> dict:
    ath = cfg.get("athlete") or {}
    recent = [{k: a.get(k) for k in ("date", "sport", "name", "distance_km", "duration_min", "avg_hr", "type")
               if a.get(k) is not None} for a in acts if a["date"] >= (d - dt.timedelta(days=14)).isoformat()]
    ctx = {
        "today": d.isoformat(), "weekday": d.strftime("%A"), "week_of": monday_of(d).isoformat(),
        "phase": phase.describe(), "season": ai.season_context(d, cfg),
        "template_week": base,
        "week_target": {k: wt.get(k) for k in ("target_km", "chronic_km", "last_week_km", "return_mode", "after_gap",
                                              "days_since_run", "longest_run_km", "bike_hours")},
        "return_mode": bool(wt.get("return_mode")), "after_gap": bool(wt.get("after_gap")),
        "no_runs_on_consecutive_days": bool(ath.get("no_runs_on_consecutive_days")),
        "load": load_model.summary(acts, d, cfg),
        "weeks": ai.weekly_summary(acts, d),
        "last_14_days": recent,
        "athlete_preferences": prefs,
        "athlete_notes": notes,
        "schedule_notes": {k: v for k, v in (constraints or {}).items() if v and k in
                           ("unavailable", "moves", "no_swim_until", "no_run_until", "day_plan")},
    }
    if problems:
        ctx["your_last_design_broke_these_rules_fix_them"] = problems
    return ctx


def design_week(cfg: dict, d: dt.date, wt: dict, acts: list[dict], notes: list[str], prefs: list[str],
                constraints: dict, client=None, prov: str | None = None) -> dict:
    """Asks the AI for the week of `d`. Returns {"used", "days", "intent", "risks", "changes", "problems", "model"}."""
    prov = prov or ai.provider(cfg)
    if prov is None:
        return {"used": False}
    phase = phase_for(d, cfg)
    base = {w: list(v) for w, v in template_for(phase, cfg).items()}
    model = ai.model_for(cfg, prov)
    problems: list[str] = []
    try:
        cl = ai._client(prov, client)
        ask = ai._ask_openai if prov == "openai" else ai._ask_anthropic
        for attempt in (1, 2):
            ctx = _context(cfg, d, wt, phase, acts, base, notes, prefs, constraints, problems or None)
            data = json.loads(ask(cl, model, ctx, SYSTEM, SCHEMA, "week_design"))
            days = {e["day"]: list(e["sessions"]) for e in data.get("days") or []}
            problems = validate(days, base, cfg, wt, phase, d)
            if not problems:
                return {"used": True, "model": model, "days": days, "intent": (data.get("intent") or "").strip()[:400],
                        "risks": [str(x)[:200] for x in data.get("risks") or []][:5],
                        "changes": (data.get("changes_vs_template") or "").strip()[:500],
                        "purposes": {e["day"]: (e.get("purpose") or "")[:160] for e in data.get("days") or []},
                        "retried": attempt == 2}
            log.warning("Week design broke the rules (attempt %d): %s", attempt, "; ".join(problems))
        return {"used": False, "model": model, "problems": problems}
    except Exception as e:  # noqa: BLE001 — the template week is always there
        log.warning("Week design unavailable, using the template: %s", e)
        return {"used": False, "model": model, "error": str(e)[:200]}


def ensure(cfg: dict, root: Path, d: dt.date, wt: dict, acts: list[dict], notes: list[str], prefs: list[str],
           constraints: dict, save: bool = True, client=None, prov: str | None = None) -> tuple[dict, dict | None]:
    """cfg with this week's design attached; creates the design if the week has none yet.
    Returns (cfg, info) where info is the new design (None if nothing new)."""
    cfg = attach(cfg, root, d)
    mon = monday_of(d)
    if not enabled(cfg) or mon.isoformat() in (cfg.get("_designs") or {}):
        return cfg, None
    res = design_week(cfg, d, wt, acts, notes, prefs, constraints, client=client, prov=prov)
    if not res.get("used"):
        return cfg, res
    record = {"week_of": mon.isoformat(), "created": dt.datetime.now().isoformat(timespec="minutes"),
              "model": res["model"], "days": res["days"], "intent": res["intent"], "risks": res["risks"],
              "changes_vs_template": res["changes"], "purposes": res["purposes"]}
    if save:
        write(root, mon, record)
    return dict(cfg, _designs=dict(cfg.get("_designs") or {}, **{mon.isoformat(): res["days"]})), record
