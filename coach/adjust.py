"""Adjusts the planned sessions to recovery and plan compliance."""
from __future__ import annotations

from .planner import Phase, Session, make_bike, make_rest, make_run, make_swim, run_t, _round_half


def adjust(planned: list[Session], r: dict, ctx: dict, phase: Phase, tomorrow: list[Session]) -> tuple[list[Session], list[str]]:
    level = r["level"]
    comp = ctx.get("compliance") or {}
    notes: list[str] = []
    out: list[Session] = []

    if any(s.kind == "race" for s in planned):
        return planned, ["Race day: the plan stays as it is. Good luck!"]

    # ── 1. Move a quality session you skipped (yesterday or earlier this week)? ──
    tomorrow_hard = any(s.kind in ("run_quality", "run_long") for s in tomorrow)
    owed = comp.get("missed_quality") or ctx.get("quality_owed")
    if (owed and level == "green" and not tomorrow_hard and not ctx.get("hard_yesterday")
            and any(s.kind in ("run_easy", "run_strides") for s in planned)):
        q_km = ctx.get("dist", {}).get("quality", 8)
        planned = [make_run("run_quality", q_km, phase, ctx.get("week_idx", 0))
                   if s.kind in ("run_easy", "run_strides") else s for s in planned]
        when = "yesterday's" if comp.get("missed_quality") else "this week's"
        notes.append(f"You skipped {when} quality session and you're recovered today: "
                     "it moves to today (the easy run is dropped).")
    elif comp.get("missed_long"):
        notes.append("You skipped yesterday's long run. Not making it up: better to lose one than pile up fatigue.")

    # ── 2. Adjust by recovery level ─────────────────────────────────────────
    for s in planned:
        if s.kind == "rest":
            out.append(s)
            continue
        if level == "green":
            out.append(s)
        elif level == "amber":
            if s.kind == "run_quality":
                km = _round_half((s.distance_km or 8) * 0.85)
                out.append(make_run("run_easy", km, phase))
                notes.append(f"Quality swapped for a {km:g} km easy run in Z2: pushing today isn't worth it.")
            elif s.kind == "run_long":
                km = _round_half((s.distance_km or 12) * 0.8)
                out.append(make_run("run_long", km, phase))
                notes.append(f"Long run cut to {km:g} km, all in Z2.")
            elif s.kind == "bike_quality":
                out.append(make_bike("bike_easy", phase, 60))
                notes.append("Intensity ride swapped for an easy ride.")
            elif s.kind == "bike_long" and (s.duration_min or 0) > 90:
                m = max(round((s.duration_min or 90) * 0.75 / 15) * 15, 60)
                ride = make_bike("bike_long", phase, m, ctx.get("week_idx", 0))
                ride.steps, ride.detail = [run_t(m * 60, 2)], f"{m}' steady in Z2: no climbing efforts today."
                out.append(ride)
                notes.append(f"Long ride cut to {m}' and kept in Z2.")
            elif s.kind == "swim_css":
                out.append(make_swim("swim_technique", phase))
                notes.append("Hard swim swapped for technique.")
            else:
                out.append(s)
        elif level == "orange":
            if s.sport == "swim":
                out.append(make_swim("swim_technique", phase, short=True))
                notes.append("Short technique swim: the water helps recovery.")
            elif s.sport == "run":
                if any(x.sport == "swim" for x in planned):
                    notes.append(f"Run removed ({s.title}): the swim is enough today.")
                    continue
                km = min(_round_half((s.distance_km or 6) * 0.6), 6)
                out.append(make_run("run_easy", max(km, 4), phase))
                notes.append(f"{s.title} → very easy {max(km, 4):g} km run (Z1-Z2).")
            elif s.kind == "bike_spin":
                out.append(s)                      # already a recovery session
            elif s.sport == "bike":
                out.append(make_bike("bike_easy", phase, 45))
                notes.append(f"{s.title} → 45' very easy ride.")
        else:  # red
            pass

    if level == "red":
        out = [make_rest("Your body is asking for rest today.")]
        notes.append("Today's training is replaced with rest or mobility.")

    # ── 2b. Heavy last few days (any sport) but green numbers: still go easier ──
    if r.get("flags", {}).get("heavy_block") and level == "green":
        load = ctx.get("recent_load") or {}
        new = []
        for s in out:
            if s.kind == "run_long":
                km = max(_round_half((s.distance_km or 8) * 0.7), 4)
                notes.append(f"Long run cut to {km:g} km, easy: {load.get('hours', 0):g} h of training "
                             "in the last 4 days is a lot for your legs, whatever the morning numbers say.")
                s = make_run("run_long", km, phase)
                s.steps = [st for st in s.steps if st.get("zone") != 3] or s.steps
            elif s.kind in ("run_quality", "bike_quality", "swim_css"):
                notes.append(f"{s.title} swapped for an easy session after a heavy few days.")
                s = (make_run("run_easy", (s.distance_km or 8) * 0.8, phase) if s.sport == "run"
                     else make_bike("bike_easy", phase, 60) if s.sport == "bike" else make_swim("swim_technique", phase))
            new.append(s)
        out = new

    # ── 3. Overdid it yesterday → shorten today's run ───────────────────────
    excess = comp.get("excess_km", 0)
    cut_applied = False
    if excess >= 2 and level in ("green", "amber"):
        cut = min(excess * 0.5, 0.4 * sum(s.distance_km or 0 for s in out if s.sport == "run"))
        new = []
        for s in out:
            if s.sport == "run" and s.kind in ("run_easy", "run_strides", "run_long") and cut > 0:
                km = max(_round_half((s.distance_km or 0) - cut), 4)
                if km < (s.distance_km or 0):
                    notes.append(f"You ran {excess:g} km more than planned yesterday: today's {s.title.lower()} becomes {km:g} km.")
                    s = make_run(s.kind, km, phase, ctx.get("week_idx", 0))
                    cut_applied = True
                cut = 0
            elif s.kind == "run_quality" and comp.get("extra_intensity"):
                s = make_run("run_easy", (s.distance_km or 8) * 0.8, phase)
                notes.append("You already added extra intensity yesterday, so today's quality becomes an easy run.")
            new.append(s)
        out = new

    # ── 4. Week ahead of plan → 15 % cut ────────────────────────────────────
    if ctx.get("week_over") and level != "red" and not cut_applied:
        new = []
        for s in out:
            if s.sport == "run" and s.kind != "run_quality" and s.distance_km:
                km = max(_round_half(s.distance_km * 0.85), 4)
                if km < s.distance_km:
                    s = make_run(s.kind, km, phase, ctx.get("week_idx", 0))
            new.append(s)
        out = new
        notes.append(f"You've run {ctx['week_done']:g} km this week vs {ctx['week_planned_so_far']:g} planned so far: "
                     "today's run is cut by 15 % to stay within the weekly volume.")

    # ── 5. Swimming gets priority if you've been skipping it ────────────────
    target = ctx.get("swim_target_week", 1) if phase.focus == "tri" else 0   # optional swims never nag
    done = ctx.get("swims_14d", 0)
    if target and done < 2 * target - 1:
        for s in out:
            if s.sport == "swim":
                s.priority = True
        if any(s.sport == "swim" for s in out):
            notes.append(f"Only {done} swim session(s) in the last 14 days: today's swim is a priority.")

    return out or [make_rest()], notes
