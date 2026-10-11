"""AI coach: an LLM (OpenAI or Claude) reviews the day and makes the final call,
within safety limits.

The rule-based plan is the proposal and the ceiling. The AI sees everything
(Garmin data, the last two weeks of all sports, the plan, your own notes) and
may keep the proposal, shorten it, make it easier, swap it for an easy
session, or choose rest. It can't add volume or intensity: those limits are
enforced here in code, and if the AI fails or breaks them, the rule-based plan
is used and the report says so.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

from .planner import Phase, Session, make_bike, make_rest, make_run, make_swim

log = logging.getLogger(__name__)
KEY_ENV = "ANTHROPIC_API_KEY"          # Claude
OPENAI_KEY_ENV = "OPENAI_API_KEY"       # OpenAI
DEFAULT_MODELS = {"openai": "gpt-5.4-mini", "anthropic": "claude-sonnet-5-5"}
DEFAULT_MODEL = DEFAULT_MODELS["anthropic"]

KINDS = ["rest", "run_easy", "run_strides", "run_quality", "run_long", "run_brick",
         "bike_easy", "bike_long", "bike_quality", "swim_technique", "swim_endurance", "swim_css"]
ALWAYS_ALLOWED = {"rest", "run_easy", "bike_easy", "swim_technique"}   # the easy options

_num_or_null = {"anyOf": [{"type": "number"}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["keep", "adjust", "rest"]},
        "sessions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": KINDS},
                               "distance_km": _num_or_null, "minutes": _num_or_null},
                "required": ["kind", "distance_km", "minutes"],
                "additionalProperties": False,
            },
        },
        "why": {"type": "string"},
        "coach_note": {"type": "string"},
        "notes_used": {"type": "array", "items": {"type": "string"}},   # ids of the notes you used
    },
    "required": ["decision", "sessions", "why", "coach_note", "notes_used"],
    "additionalProperties": False,
}

SYSTEM = """You are an experienced running, cycling and triathlon coach. Each morning you get one \
athlete's data and the plan a rule-based system proposes for today, and you make the final call.

How to decide:
- Protect long-term consistency over any single session. Injuries and illness cost far more than \
one missed workout.
- Look at the whole picture across sports: several big days in a row (e.g. long rides) leave the \
legs tired even when HRV, sleep and Body Battery look fine. Running on tired legs is the classic \
injury pattern, especially for someone returning to running.
- Take the athlete's own notes seriously (pain, illness, heavy legs, poor sleep, stress, motivation). \
Pain in a joint or tendon means no running that day. Feeling great is not a reason to add volume.
- Notes may include unrelated things (shopping, work); ignore those.
- You may keep the proposal, shorten it, make it easier, swap it for an easy alternative (an easy \
swim or easy spin is often a good recovery choice), or choose rest. You must not add volume or \
intensity beyond the proposal.
- If a session has already been done today, don't repeat it.

Write for the athlete, in English, plainly:
- "why": ONE short sentence (max ~150 characters) giving the main reason for today's call. It is \
shown first on their watch.
- "coach_note": 1-3 short sentences of practical advice for today (how it should feel, what to \
watch for). No generic motivation.
- "notes_used": the ids of the athlete notes that are about how they feel or their training \
and that you took into account, or []. Never include unrelated notes (they stay in the athlete's \
to-do list). Notes listed under "earlier_notes_today" were already used; don't list them again.
- "notes_history" holds the athlete's notes from the last 6 weeks, each with what they trained that day \
and the day before, their readiness and what the coach had planned. "activities_last_6_weeks" and \
"weekly_summary" show the training trend over the same weeks. Use them to learn how this athlete responds (which loads leave \
them heavy or sore, what they recover well from) and to spot recurring niggles. It describes the \
past, not how they feel today; never list those notes in "notes_used".
- Distances in km for runs, minutes for rides; null where not applicable."""


def provider(cfg: dict) -> str | None:
    """Which AI to use: `ai.provider` (openai | anthropic | auto); auto picks whichever key is set."""
    settings = cfg.get("ai") or {}
    if not settings.get("enabled", True):
        return None
    wanted = str(settings.get("provider", "auto")).lower()
    have = {"openai": bool(os.environ.get(OPENAI_KEY_ENV)), "anthropic": bool(os.environ.get(KEY_ENV))}
    if wanted in have:
        return wanted if have[wanted] else None
    return next((p for p in ("openai", "anthropic") if have[p]), None)


def enabled(cfg: dict) -> bool:
    return provider(cfg) is not None


def model_for(cfg: dict, prov: str) -> str:
    settings = cfg.get("ai") or {}
    return settings.get(f"{prov}_model") or DEFAULT_MODELS[prov]


def _session_json(s: Session) -> dict:
    return {"kind": s.kind, "title": s.title, "distance_km": s.distance_km, "minutes": s.duration_min,
            "intensity": s.intensity}


def weekly_summary(acts: list[dict], d) -> list[dict]:
    """Per week (Monday first) and sport: sessions, km and hours, over the lookback window."""
    from .planner import LOOKBACK_WEEKS, monday_of
    mon = monday_of(d)
    out = []
    for k in range(LOOKBACK_WEEKS, -1, -1):
        lo = mon - dt.timedelta(weeks=k)
        hi = lo + dt.timedelta(weeks=1)
        week = [a for a in acts if lo.isoformat() <= a["date"] < min(hi, d).isoformat()]
        row = {"week_of": lo.isoformat(), "so_far" if k == 0 else "complete": True}
        for sport in ("run", "bike", "swim", "other"):
            items = [a for a in week if a["sport"] == sport]
            if items:
                row[sport] = {"sessions": len(items), "km": round(sum(a["distance_km"] for a in items), 1),
                              "hours": round(sum(a["duration_min"] for a in items) / 60, 1)}
        out.append(row)
    return out


def build_context(d, phase: Phase, wt: dict, wellness: dict, r: dict, ctx: dict, proposal: list[Session],
                  planned: list[Session], notes: list[dict], acts: list[dict], rule_notes: list[str],
                  rule_why: str, earlier_notes: list[dict] | None = None,
                  notes_history: list[dict] | None = None) -> dict:
    keep_w = ("sleep_hours", "sleep_score", "hrv_last_night", "hrv_weekly_avg", "hrv_status", "hrv_baseline_low",
              "hrv_baseline_high", "body_battery_wake", "rhr", "rhr_7d", "stress_yesterday", "training_readiness",
              "training_status", "acwr", "steps_today", "after_training", "last_sync")
    recent = [{k: a.get(k) for k in ("date", "sport", "name", "distance_km", "duration_min", "avg_hr",
                                     "aerobic_te", "anaerobic_te")}
              for a in acts if a["date"] < d.isoformat()]
    return {
        "today": d.isoformat(), "weekday": d.strftime("%A"),
        "phase": phase.describe(),
        "next_race": ({"name": phase.race.name, "date": phase.race.date.isoformat(), "kind": phase.race.kind,
                       "priority": phase.race.priority, "days_out": phase.days_out} if phase.race else None),
        "week": {k: wt.get(k) for k in ("target_km", "chronic_km", "last_week_km", "return_mode", "longest_run_km",
                                        "bike_hours")} | {"run_km_done_this_week": ctx.get("week_done")},
        "garmin_today": {k: wellness.get(k) for k in keep_w},
        "rule_readiness": {"score": r["score"], "level": r["level"], "reasons": r["reasons"]},
        "last_4_days_load": ctx.get("recent_load"),
        "consecutive_training_days": ctx.get("consecutive_days"),
        "this_week_key_sessions": ctx.get("this_week"),
        "yesterday_vs_plan": {k: (ctx.get("compliance") or {}).get(k)
                              for k in ("run_planned", "run_done", "missed", "done_titles", "extra_intensity")},
        "done_today_so_far": ctx.get("today_acts"),
        "activities_last_6_weeks": recent,
        "weekly_summary": weekly_summary(acts, d),
        "originally_planned_today": [_session_json(s) for s in planned],
        "rule_proposal_today": [_session_json(s) for s in proposal],
        "rule_adjustments": rule_notes,
        "rule_one_line_why": rule_why,
        "athlete_notes_today": notes,
        "earlier_notes_today": earlier_notes or [],
        "notes_history": notes_history or [],
    }


def apply_limits(raw: list[dict], proposal: list[Session], phase: Phase, wk: int) -> tuple[list[Session], list[str]]:
    """Builds the AI's sessions, never exceeding the rule proposal's volume or intensity."""
    has_training = any(s.sport in ("run", "bike", "swim") for s in proposal)
    # easy alternatives only replace training that was planned: never added to a rest day
    allowed = (ALWAYS_ALLOWED if has_training else {"rest"}) | {s.kind for s in proposal}
    run_budget = sum(s.distance_km or 0 for s in proposal if s.sport == "run")
    long_cap = max((s.distance_km or 0 for s in proposal if s.kind == "run_long"), default=0)
    bike_budget = sum(s.duration_min or 0 for s in proposal if s.sport == "bike")
    out: list[Session] = []
    limited: list[str] = []
    swims = bikes_added = 0
    for item in raw[:3]:
        kind = item.get("kind")
        if kind not in KINDS:
            raise ValueError(f"unknown session kind {kind!r}")
        if kind not in allowed:
            limited.append(f"{kind} isn't in today's plan, so it wasn't added")
            continue
        if kind == "rest":
            return [make_rest("Your coach chose rest today.")], limited
        if kind.startswith("run_"):
            km = float(item.get("distance_km") or 0)
            if kind == "run_long" and km > long_cap:
                limited.append(f"long run limited to {long_cap:g} km")
                km = long_cap
            if km > run_budget:
                limited.append(f"running limited to {run_budget:g} km")
                km = run_budget
            if km < 3:
                continue
            run_budget -= km
            out.append(make_run(kind, km, phase, wk))
        elif kind.startswith("bike_"):
            minutes = float(item.get("minutes") or 45)
            swap_for_run = kind == "bike_easy" and not bikes_added and any(s.sport == "run" for s in proposal)
            cap = bike_budget if bike_budget else (45 if swap_for_run else 0)
            if minutes > cap:
                limited.append(f"ride limited to {cap:g}'")
                minutes = cap
            if minutes < 20:
                continue
            bike_budget = max(bike_budget - minutes, 0)
            bikes_added += 1
            out.append(make_bike(kind, phase, minutes, wk))
        else:  # swim
            if swims:
                continue
            swims += 1
            out.append(make_swim(kind, phase, short=kind == "swim_technique" and kind not in {s.kind for s in proposal}))
    return out or [make_rest("Your coach chose rest today.")], limited


def _ask_openai(client, model: str, context: dict) -> str:
    resp = client.chat.completions.create(
        model=model,
        max_completion_tokens=4000,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": "Today's data (JSON):\n" + json.dumps(context, default=str)}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "coach_decision", "schema": SCHEMA, "strict": True}},
    )
    msg = resp.choices[0].message
    if getattr(msg, "refusal", None):
        raise RuntimeError(f"refused: {msg.refusal}")
    return msg.content or ""


def _ask_anthropic(client, model: str, context: dict) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=1500,
        system=SYSTEM,
        messages=[{"role": "user", "content": "Today's data (JSON):\n" + json.dumps(context, default=str)}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    return "".join(getattr(b, "text", "") for b in resp.content)


def decide(cfg: dict, context: dict, proposal: list[Session], phase: Phase, wk: int, client=None,
           prov: str | None = None) -> dict:
    """Returns {"used": bool, "sessions", "why", "coach_note", "notes_used", "limited", "error", "model"}."""
    prov = prov or provider(cfg)
    if prov is None:
        return {"used": False}
    model = model_for(cfg, prov)
    try:
        if client is None:
            if prov == "openai":
                import openai
                client = openai.OpenAI()
            else:
                import anthropic
                client = anthropic.Anthropic()
        text = _ask_openai(client, model, context) if prov == "openai" else _ask_anthropic(client, model, context)
        data = json.loads(text)
        sessions, limited = apply_limits(data.get("sessions") or [], proposal, phase, wk)
        why = (data.get("why") or "").strip().split("\n")[0][:200]
        known = {str(n["id"]) for n in context.get("athlete_notes_today") or []}
        return {"used": True, "model": model, "decision": data.get("decision"), "sessions": sessions, "why": why,
                "coach_note": (data.get("coach_note") or "").strip()[:600],
                "notes_used": [str(n) for n in data.get("notes_used") or [] if str(n) in known][:10],
                "limited": limited}
    except Exception as e:  # noqa: BLE001 — never leave the athlete without a plan
        log.warning("AI coach unavailable, using the rule-based plan: %s", e)
        return {"used": False, "model": model, "error": str(e)[:200]}
