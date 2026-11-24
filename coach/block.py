"""Block review: every ~4 weeks (or the week after a race) the coach steps back and sets the next block.

It reads the weekly reviews since the last block review, the trend of fitness (ctl) by week, the weekly totals,
the body data and the season, and writes: an assessment, priorities and things to avoid for the next block,
and tests worth doing. The week designer and the daily check read the latest block. Saved in
`data/coach_log/block-<monday>.json`.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from . import ai
from . import load as load_model
from .planner import load_races, monday_of

log = logging.getLogger(__name__)
LOG_DIR = "data/coach_log"
EVERY_WEEKS = 4

SCHEMA = {
    "type": "object",
    "properties": {
        "assessment": {"type": "string"},
        "fitness_trend": {"type": "string"},
        "priorities": {"type": "array", "items": {"type": "string"}},
        "avoid": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "array", "items": {"type": "string"}},
        "message": {"type": "string"},
    },
    "required": ["assessment", "fitness_trend", "priorities", "avoid", "tests", "message"],
    "additionalProperties": False,
}

SYSTEM = """You are an experienced triathlon and running coach doing the review at the end of a training \
block (about four weeks, or the week after a race) for an amateur athlete who wants his best form at his A races.

You get the weekly reviews of the block, the weekly fitness (ctl) per sport, weekly totals, body data, the \
races (A races matter most; a B race is a tune-up) and the previous block's plan. Decide what the next block \
must do for the next A race.

- "assessment": how the block went against what it was meant to do, honestly, two or three sentences.
- "fitness_trend": one line on ctl/form and what drove it (use only the numbers given).
- "priorities": at most 4 concrete aims for the next block (e.g. "rebuild running to 3 runs/week without \
pain", "raise ctl by 5 with bike volume", "two leg-strength sessions a week").
- "avoid": at most 3 things not to do (e.g. "no quality runs until a week of 15 km is pain free").
- "tests": at most 2 tests or checkpoints worth doing (e.g. a 20-minute power test on the bike, a 5 km time \
trial) or an empty list; only when the athlete is healthy enough.
- "message": a short note to the athlete (2-3 sentences), the way a coach would say it.
Don't invent numbers. Injuries and illness come first."""


def path(root: Path, monday: dt.date) -> Path:
    return Path(root) / LOG_DIR / f"block-{monday.isoformat()}.json"


def _files(root: Path, kind: str) -> list[Path]:
    return sorted((Path(root) / LOG_DIR).glob(f"{kind}-*.json"))


def _load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def latest(root: Path, d: dt.date) -> dict | None:
    """The most recent block review made on or before `d`."""
    for p in reversed(_files(root, "block")):
        rec = _load(p)
        if rec and str(rec.get("created", ""))[:10] <= d.isoformat():
            return rec
    return None


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("ai") or {}).get("block_review")) and ai.enabled(cfg)


def due(cfg: dict, root: Path, d: dt.date) -> list[dict]:
    """The weekly reviews to cover now (empty = not due): 4 new ones, or 1+ the week after a race."""
    prev = latest(root, d)
    since = (prev or {}).get("covers_to", "")
    new = [r for r in (_load(p) for p in _files(root, "review")) if r and r.get("week_of", "") > since]
    new = [r for r in new if r["week_of"] < monday_of(d).isoformat()]
    if len(new) >= EVERY_WEEKS:
        return new[-EVERY_WEEKS:] if len(new) > 8 else new
    if new:
        last_week = monday_of(d) - dt.timedelta(weeks=1)
        raced = any(last_week <= r.date < monday_of(d) for r in load_races(cfg))
        if raced:
            return new
    return []


def ensure(cfg: dict, root: Path, d: dt.date, acts: list[dict], save: bool = True, client=None,
           prov: str | None = None) -> dict | None:
    if not enabled(cfg):
        return None
    reviews = due(cfg, root, d)
    if not reviews:
        return None
    prov = prov or ai.provider(cfg)
    if prov is None:
        return None
    from . import body as body_log
    mon = monday_of(d)
    weeks = []
    for k in range(len(reviews), 0, -1):
        w = mon - dt.timedelta(weeks=k)
        weeks.append({"week_of": w.isoformat(), "fitness_at_start": {
            "overall": load_model.fitness(acts, w, cfg),
            **{s: load_model.fitness(acts, w, cfg, s) for s in ("run", "bike", "swim")}}})
    ctx = {
        "today": d.isoformat(), "weekly_reviews": [{k: r.get(k) for k in (
            "week_of", "verdict", "summary", "went_well", "concerns", "next_week", "load_comment")} for r in reviews],
        "fitness_by_week": weeks, "fitness_now": load_model.summary(acts, d, cfg),
        "weekly_totals": ai.weekly_summary(acts, d), "season": ai.season_context(d, cfg),
        "body": body_log.summary(root, d), "previous_block": _prev(root, d),
    }
    model = ai.model_for(cfg, prov)
    try:
        cl = ai._client(prov, client)
        ask = ai._ask_openai if prov == "openai" else ai._ask_anthropic
        data = json.loads(ask(cl, model, ctx, SYSTEM, SCHEMA, "block_review"))
    except Exception as e:  # noqa: BLE001 — a review is a bonus
        log.warning("Block review unavailable: %s", e)
        return {"error": str(e)[:200]}
    rec = {"created": dt.datetime.now().isoformat(timespec="minutes"), "model": model,
           "covers_from": reviews[0]["week_of"], "covers_to": reviews[-1]["week_of"],
           "assessment": data["assessment"][:500], "fitness_trend": data["fitness_trend"][:250],
           "priorities": [str(x)[:200] for x in data["priorities"]][:4], "avoid": [str(x)[:200] for x in data["avoid"]][:3],
           "tests": [str(x)[:200] for x in data["tests"]][:2], "message": data["message"][:500]}
    if save:
        p = path(root, mon)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return rec


def _prev(root: Path, d: dt.date) -> dict | None:
    rec = latest(root, d)
    return {k: rec.get(k) for k in ("assessment", "priorities", "avoid", "tests")} if rec else None
