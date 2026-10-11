"""Orchestrates the day: data → plan → readiness → adjustment → Garmin → report."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from . import garmin_api as G
from .adjust import adjust
from .planner import (Session, compute_week_target, monday_of, phase_for, plan_day,
                      planned_run_km, run_distribution, template_for, week_index, weekly_run_km)
from .readiness import compliance, compute, consecutive_days, own_acwr
from .workouts import to_garmin

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {"days": {}, "weeks": {}}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False, default=str))


def _week_key(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_target(state: State, acts, d, cfg) -> dict:
    key = _week_key(d)
    if key not in state.data["weeks"]:
        state.data["weeks"][key] = compute_week_target(acts, d, cfg)
    return state.data["weeks"][key]


def run_day(api, cfg: dict, d: dt.date, upload: bool = True, state_path: Path | None = None) -> dict:
    state = State(state_path or ROOT / "data" / "state.json")
    acts = G.fetch_activities(api, d - dt.timedelta(days=35), d)
    past = [a for a in acts if a["date"] < d.isoformat()]
    wellness = G.fetch_wellness(api, d)

    wt = week_target(state, past, d, cfg)
    week_km = wt["target_km"]
    ret = wt.get("return_mode", False)
    phase, planned = plan_day(d, cfg, week_km, ret)
    _, tomorrow = plan_day(d + dt.timedelta(days=1), cfg, week_km, ret)

    # ── yesterday's compliance ──
    yday = d - dt.timedelta(days=1)
    yrec = state.data["days"].get(yday.isoformat())
    if yrec:
        y_planned = yrec["final"]
    else:
        ywt = week_target(state, [a for a in acts if a["date"] < yday.isoformat()], yday, cfg)
        y_planned = [s.to_dict() for s in plan_day(yday, cfg, ywt["target_km"], ywt.get("return_mode", False))[1]]
    y_done = [a for a in acts if a["date"] == yday.isoformat()]
    comp = compliance(y_planned, y_done)

    # ── current week ──
    mon = monday_of(d)
    week_done = weekly_run_km([a for a in past], mon)
    planned_so_far = 0.0
    cur = mon
    while cur < d:
        rec = state.data["days"].get(cur.isoformat())
        sess = [Session.from_dict(x) for x in rec["final"]] if rec else plan_day(cur, cfg, week_km, ret)[1]
        planned_so_far += planned_run_km(sess)
        cur += dt.timedelta(days=1)
    week_over = planned_so_far > 0 and week_done > planned_so_far * 1.15 and week_done - planned_so_far >= 4

    swims_14d = sum(1 for a in past if a["sport"] == "swim" and a["date"] >= (d - dt.timedelta(days=14)).isoformat())

    ctx = {
        "compliance": comp,
        "acwr_own": own_acwr(past, d),
        "consecutive_days": consecutive_days(past, d),
        "week_over": week_over, "week_done": week_done, "week_planned_so_far": round(planned_so_far, 1),
        "swims_14d": swims_14d,
        "swim_target_week": sum(k.startswith("swim_") for day in template_for(phase, cfg).values() for k in day),
        "dist": run_distribution(template_for(phase, cfg), week_km, phase, ret),
        "week_idx": week_index(d, cfg),
    }
    r = compute(wellness, ctx)
    final, notes = adjust(planned, r, ctx, phase, tomorrow)

    # ── Garmin ──
    prev = (state.data["days"].get(d.isoformat()) or {}).get("uploads", [])
    uploads = []
    if upload:
        G.remove_uploaded(api, prev)
        for s in final:
            if not s.steps:
                continue
            desc = _workout_description(s, r, notes, phase)
            try:
                uploads.append(G.upload_and_schedule(api, to_garmin(s, cfg, desc), d.isoformat()))
            except Exception as e:  # noqa: BLE001
                log.error("Could not upload '%s' to Garmin: %s", s.title, e)
    else:
        uploads = prev

    state.data["days"][d.isoformat()] = {
        "phase": phase.describe(), "week_target_km": week_km,
        "planned": [s.to_dict() for s in planned], "final": [s.to_dict() for s in final],
        "readiness": {k: r[k] for k in ("score", "level", "reasons")},
        "compliance_yesterday": comp, "uploads": uploads,
    }
    report = build_report(d, phase, wt, wellness, r, comp, ctx, planned, final, notes, uploads, upload)
    if upload:  # a dry run only prints the report and leaves no files behind
        state.save()
        rep_dir = ROOT / "reports"
        rep_dir.mkdir(exist_ok=True)
        (rep_dir / f"{d.isoformat()}.md").write_text(report, encoding="utf-8")
    return {"report": report, "final": final, "readiness": r, "notes": notes}


def _workout_description(s: Session, r: dict, notes: list[str], phase) -> str:
    parts = [s.detail, f"Readiness today: {r['score']}/100 – {r['label'][2:]}."]
    if r["reasons"]:
        parts.append("Why: " + " ".join(r["reasons"][:3]))
    if notes:
        parts.append("Changes: " + " ".join(notes[:3]))
    parts.append(f"Phase: {phase.describe()}.")
    return "\n".join(parts)


def build_report(d, phase, wt, w, r, comp, ctx, planned, final, notes, uploads, upload) -> str:
    L = [f"# Training for {d.strftime('%A %d %b %Y')}", "",
         f"**Phase:** {phase.describe()}  ",
         f"**Weekly running target:** {wt['target_km']} km "
         f"(last 4 weeks average {wt['chronic_km']} km, last week {wt['last_week_km']} km)  ",
         f"**Run so far this week:** {ctx['week_done']} km", ""]
    if wt.get("return_mode"):
        L.insert(-1, "**Return-to-running mode:** volume grows ~20 %/week from 10 km and there are "
                     "no hard sessions until you average 10+ km/week.  ")
    L += ["",
         f"## Readiness: {r['score']}/100 — {r['label']}", ""]
    for k, v in r["components"].items():
        L.append(f"- {k}: {v['value']} → {v['score']}/100")
    if ctx.get("acwr_own"):
        L.append(f"- Acute/chronic load (computed): {ctx['acwr_own']}")
    if w.get("training_status"):
        L.append(f"- Garmin training status: {w['training_status']}")
    if r["reasons"]:
        L += ["", "**Why:**"] + [f"- {x}" for x in r["reasons"]]

    L += ["", "## Yesterday", f"- Planned: {comp['run_planned']} km running",
          f"- Done: {', '.join(comp['done_titles']) or 'nothing recorded'}"]
    if comp["missed"]:
        L.append(f"- Skipped: {', '.join(comp['missed'])}")

    L += ["", "## Today"]
    if [s.title for s in planned] != [s.title for s in final]:
        L.append(f"- Original plan: {' + '.join(s.title for s in planned)}")
    for s in final:
        star = " ⭐" if s.priority else ""
        L.append(f"- **{s.title}**{star}: {s.detail}")
    if notes:
        L += ["", "**Coach adjustments:**"] + [f"- {n}" for n in notes]
    L += ["", "## Garmin"]
    if upload:
        L.append(f"- Added to calendar: {', '.join(u['name'] for u in uploads) or 'nothing'}")
    else:
        L.append("- Dry run: nothing was uploaded.")
    return "\n".join(L) + "\n"
