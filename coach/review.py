"""Weekly review: the AI coach looks back at a finished week and says what worked.

The first run of a new week reviews the week before (planned against done, load change, notes). The review is
saved in `data/coach_log/review-<monday>.json`; the next week's design and the daily check read it, so the
coach learns from what happened. Facts and numbers are computed here; the AI interprets them.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from . import ai
from . import load as load_model
from .planner import monday_of

log = logging.getLogger(__name__)
LOG_DIR = "data/coach_log"

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["on_track", "mixed", "off_track"]},
        "summary": {"type": "string"},
        "went_well": {"type": "array", "items": {"type": "string"}},
        "concerns": {"type": "array", "items": {"type": "string"}},
        "next_week": {"type": "array", "items": {"type": "string"}},
        "load_comment": {"type": "string"},
    },
    "required": ["verdict", "summary", "went_well", "concerns", "next_week", "load_comment"],
    "additionalProperties": False,
}

SYSTEM = """You are an experienced triathlon and running coach reviewing the week that just ended for an \
amateur athlete who is aiming for the best form at his A races.

You get, for each day, what was planned and what was done (with a stress score: 100 = one hour at \
threshold), the totals per sport, how fitness (ctl), fatigue (atl) and form (tsb) changed over the week, \
the week's design and intent, the athlete's notes and the season (phase, races, injuries). Be honest and \
specific, like a coach talking to the athlete: what the week achieved for the next A race, what went \
wrong and why (injury, fatigue, missed key session, too much or too little), and what to change next week.

- "verdict": on_track (the week did its job), mixed, or off_track (e.g. an injury or a key block missed).
- "summary": two sentences at most. "went_well" and "concerns": at most 3 short items each, concrete.
- "next_week": at most 3 concrete adjustments the next week's design must respect (e.g. "first run back \
4 km, no strides", "keep the leg strength off the day after skiing").
- "load_comment": one line about fitness, fatigue and form.
- Don't invent data. If a number isn't given, don't state it. A skipped session because of a note \
(pain, travel) is explained, not a failure. Skiing, hikes and other sports count as training."""


def path(root: Path, monday: dt.date) -> Path:
    return Path(root) / LOG_DIR / f"review-{monday.isoformat()}.json"


def read(root: Path, monday: dt.date) -> dict | None:
    try:
        return json.loads(path(root, monday).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("ai") or {}).get("weekly_review")) and ai.enabled(cfg)


def facts(cfg: dict, mon: dt.date, acts: list[dict], planned: dict[str, list[dict]], notes: list[dict],
          design: dict | None) -> dict:
    """What happened in the week starting `mon` (all numbers computed here)."""
    days, sports = [], {}
    for i in range(7):
        day = mon + dt.timedelta(days=i)
        iso = day.isoformat()
        done = [a for a in acts if a["date"] == iso]
        for a in done:
            s = sports.setdefault(a["sport"], {"sessions": 0, "km": 0.0, "hours": 0.0, "stress": 0.0})
            s["sessions"] += 1
            s["km"] += a.get("distance_km") or 0
            s["hours"] += (a.get("duration_min") or 0) / 60
            s["stress"] += load_model.stress(a, cfg)
        days.append({"date": iso, "weekday": day.strftime("%a"),
                     "planned": [p.get("title") or p.get("kind") for p in planned.get(iso, [])],
                     "done": [{"sport": a["sport"], "name": a.get("name"), "km": a.get("distance_km"),
                               "min": a.get("duration_min"), "avg_hr": a.get("avg_hr"),
                               "stress": load_model.stress(a, cfg), "followed_plan": a.get("followed")}
                              for a in done]})
    end = mon + dt.timedelta(days=7)
    start_f, end_f = load_model.fitness(acts, mon, cfg), load_model.fitness(acts, end, cfg)
    return {
        "week_of": mon.isoformat(), "days": days,
        "totals": {k: {"sessions": v["sessions"], "km": round(v["km"], 1), "hours": round(v["hours"], 1),
                       "stress": round(v["stress"])} for k, v in sports.items()},
        "fitness_start": start_f, "fitness_end": end_f,
        "design": {k: design.get(k) for k in ("intent", "risks", "changes_vs_template")} if design else None,
        "notes": [{"date": n.get("date") or n.get("created", "")[:10], "text": n.get("text")} for n in notes][:20],
    }


def review_week(cfg: dict, context: dict, client=None, prov: str | None = None) -> dict:
    prov = prov or ai.provider(cfg)
    if prov is None:
        return {"used": False}
    model = ai.model_for(cfg, prov)
    try:
        cl = ai._client(prov, client)
        ask = ai._ask_openai if prov == "openai" else ai._ask_anthropic
        data = json.loads(ask(cl, model, context, SYSTEM, SCHEMA, "week_review"))
        return {"used": True, "model": model, "verdict": data["verdict"], "summary": data["summary"][:400],
                "went_well": [str(x)[:200] for x in data["went_well"]][:3],
                "concerns": [str(x)[:200] for x in data["concerns"]][:3],
                "next_week": [str(x)[:200] for x in data["next_week"]][:3],
                "load_comment": data["load_comment"][:200]}
    except Exception as e:  # noqa: BLE001 — a review is a bonus
        log.warning("Week review unavailable: %s", e)
        return {"used": False, "model": model, "error": str(e)[:200]}


def ensure(cfg: dict, root: Path, d: dt.date, acts: list[dict], planned_fn, notes: list[dict], design_fn,
           season: dict | None = None, save: bool = True, client=None, prov: str | None = None) -> dict | None:
    """Reviews the week before `d`'s week once. `planned_fn(day)` -> what was planned that day;
    `design_fn(monday)` -> that week's saved design. Returns the new review, or None."""
    if not enabled(cfg):
        return None
    mon = monday_of(d) - dt.timedelta(weeks=1)
    if read(root, mon):
        return None
    last = mon + dt.timedelta(days=6)
    if not any(mon.isoformat() <= a["date"] <= last.isoformat() for a in acts):
        return None
    planned = {(mon + dt.timedelta(days=i)).isoformat(): planned_fn(mon + dt.timedelta(days=i)) for i in range(7)}
    ctx = facts(cfg, mon, acts, planned, notes, design_fn(mon))
    ctx["season"] = season or ai.season_context(last, cfg)
    res = review_week(cfg, ctx, client=client, prov=prov)
    if not res.get("used"):
        return res
    record = {"week_of": mon.isoformat(), "created": dt.datetime.now().isoformat(timespec="minutes"), **{
        k: res[k] for k in ("model", "verdict", "summary", "went_well", "concerns", "next_week", "load_comment")}}
    if save:
        p = path(root, mon)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record
