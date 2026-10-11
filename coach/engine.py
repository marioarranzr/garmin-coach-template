"""Orchestrates the day: data → plan → readiness → adjustment → Garmin → report."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
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


def sleep_deadline(cfg: dict) -> dt.time:
    raw = str((cfg.get("garmin") or {}).get("wait_for_sleep_until", "10:00"))
    h, m = (int(x) for x in raw.split(":"))
    return dt.time(h, m)


def run_day(api, cfg: dict, d: dt.date, upload: bool = True, state_path: Path | None = None,
            wait_for_sleep: bool = False, now: dt.datetime | None = None) -> dict:
    """Plans day `d`. With `wait_for_sleep` (scheduled runs), it does nothing if
    today is already planned, and waits (returns without planning) while last
    night's sleep hasn't reached Garmin yet, until the configured deadline."""
    state = State(state_path or ROOT / "data" / "state.json")
    now = now or dt.datetime.now()
    deadline = sleep_deadline(cfg)
    today_rec = state.data["days"].get(d.isoformat()) or {}
    if wait_for_sleep and today_rec.get("uploads") and today_rec.get("sleep_ready", True):
        msg = f"Today's workout is already in your Garmin calendar ({', '.join(u['name'] for u in today_rec['uploads'])})."
        return {"status": "done", "report": msg + "\n"}

    wellness = G.fetch_wellness(api, d)
    sleep_ready = wellness.get("sleep_hours") is not None or wellness.get("sleep_score") is not None
    if wait_for_sleep and today_rec.get("uploads") and not sleep_ready:
        # planned at the deadline without sleep data, and it still hasn't arrived
        return {"status": "done", "report": "Today's workout is already in your Garmin calendar.\n"}
    # Steps today mean you're up and wearing the watch: if it still has no sleep
    # for last night, it didn't record one (not worn, or another watch) — don't wait.
    awake = (wellness.get("steps_today") or 0) >= 500
    if wait_for_sleep and not sleep_ready and not awake and now.time() < deadline:
        msg = (f"Last night's sleep isn't in Garmin yet (watch not synced, or still asleep). "
               f"Trying again next hour; planning anyway at {deadline:%H:%M} at the latest.")
        return {"status": "waiting", "report": msg + "\n"}

    acts = G.fetch_activities(api, d - dt.timedelta(days=35), d)
    past = [a for a in acts if a["date"] < d.isoformat()]

    wt = week_target(state, past, d, cfg)
    week_km = wt["target_km"]
    ret = wt.get("return_mode", False)
    phase, planned = plan_day(d, cfg, wt)
    _, tomorrow = plan_day(d + dt.timedelta(days=1), cfg, wt)

    # ── yesterday's compliance ──
    yday = d - dt.timedelta(days=1)
    yrec = state.data["days"].get(yday.isoformat())
    if yrec:
        y_planned = yrec["final"]
    else:
        ywt = week_target(state, [a for a in acts if a["date"] < yday.isoformat()], yday, cfg)
        y_planned = [s.to_dict() for s in plan_day(yday, cfg, ywt)[1]]
    y_done = [a for a in acts if a["date"] == yday.isoformat()]
    comp = compliance(y_planned, y_done)

    # ── current week ──
    mon = monday_of(d)
    week_done = weekly_run_km([a for a in past], mon)
    planned_so_far = 0.0
    cur = mon
    while cur < d:
        rec = state.data["days"].get(cur.isoformat())
        sess = [Session.from_dict(x) for x in rec["final"]] if rec else plan_day(cur, cfg, wt)[1]
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
        "compliance_yesterday": comp, "uploads": uploads, "sleep_ready": sleep_ready,
    }
    report = build_report(d, phase, wt, wellness, r, comp, ctx, planned, final, notes, uploads, upload)
    if not sleep_ready:
        why = ("your watch shows you're up but recorded no sleep last night (not worn, or another watch)"
               if awake else "no sleep data from last night reached Garmin")
        report += (f"\n> Planned without sleep data: {why}. Readiness uses Body Battery, resting HR "
                   "and training load instead. If last night's sleep shows up later, press *Run workflow* "
                   "to re-plan with it.\n")
    if upload:  # a dry run only prints the report and leaves no files behind
        state.save()
    if upload and not os.environ.get("GITHUB_ACTIONS"):
        # local runs keep a copy; on GitHub the report is in the run summary
        rep_dir = ROOT / "reports"
        rep_dir.mkdir(exist_ok=True)
        (rep_dir / f"{d.isoformat()}.md").write_text(report, encoding="utf-8")
    return {"status": "planned", "report": report, "final": final, "readiness": r, "notes": notes}


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
         f"**Run so far this week:** {ctx['week_done']} km  "]
    if phase.focus == "bike":
        L.append(f"**Riding this week:** {wt.get('bike_hours')} h target "
                 f"(4-week average {wt.get('bike_chronic_hours')} h, longest recent ride "
                 f"{wt.get('longest_ride_min')}', typical speed {wt.get('ride_speed_kmh')} km/h)  ")
    L.append("")
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
