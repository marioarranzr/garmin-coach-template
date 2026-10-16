"""A local dashboard showing training status, reasoning and history.

Run `python -m coach dashboard` (after `python -m coach login`). Opens
http://localhost:8765 in your browser. Reads live from Garmin, Todoist and
Strava on every load; nothing is uploaded.

Five tabs:
  Status    — race countdown, phase, weekly target, 12-week volume
  Why       — training logic: phases, progression, key sessions
  Decisions — coach's day-by-day reasoning from Garmin + Todoist
  Training  — all recent activities, filterable by sport
  Upcoming  — this week and next, with full coach reasoning per day
"""
from __future__ import annotations

import datetime as dt
import html
import http.server
import json
import logging
import threading
import webbrowser
from pathlib import Path

from . import garmin_api as G
from . import paces as P
from . import week as W
from .calendar import Calendar
from .planner import (compute_week_target, history_start, learn_long_day,
                      monday_of, phase_for, plan_day, week_parts)

log = logging.getLogger(__name__)
PORT = 8765


# ── data helpers ──────────────────────────────────────────────────────────────

def _race_countdown(cfg: dict, today: dt.date) -> dict | None:
    races = sorted(
        (r for r in cfg.get("races") or []
         if dt.date.fromisoformat(str(r["date"])) >= today),
        key=lambda r: str(r["date"]),
    )
    if not races:
        return None
    r = races[0]
    d = dt.date.fromisoformat(str(r["date"]))
    weeks = (d - today).days / 7
    return {
        "name": r.get("name") or r["kind"].replace("_", " "),
        "date": d.isoformat(),
        "days": (d - today).days,
        "weeks": round(weeks, 1),
        "priority": r.get("priority", "A"),
        "kind": r["kind"],
    }


def _weekly_history(acts: list[dict], today: dt.date, weeks: int = 12) -> list[dict]:
    """[{week_of, run_km, bike_min, swim_m}] for the last `weeks` complete weeks + current."""
    out = []
    mon = monday_of(today)
    for i in range(weeks, -1, -1):  # i=0 is the current (partial) week
        start = mon - dt.timedelta(weeks=i)
        end = start + dt.timedelta(days=7) if i > 0 else today + dt.timedelta(days=1)
        wa = [a for a in acts if start.isoformat() <= a["date"] < end.isoformat()]
        out.append({
            "week_of": start.isoformat(),
            "run_km": round(sum(a["distance_km"] or 0 for a in wa if a["sport"] == "run"), 1),
            "bike_min": round(sum(a.get("duration_min") or 0 for a in wa if a["sport"] == "bike"), 0),
            "swim_m": round(sum((a.get("swum_m") or (a["distance_km"] or 0) * 1000)
                                for a in wa if a["sport"] == "swim")),
        })
    return out


def _full_description(cal: Calendar, entry: dict) -> str:
    try:
        w = cal.api.get_workout_by_id(entry["workoutId"]) or {}
        return str(w.get("description") or "")
    except Exception as e:  # noqa: BLE001
        log.warning("Couldn't read workout %s: %s", entry.get("workoutId"), e)
        return ""


def _day_rows(cal: Calendar, acts: list[dict], start: dt.date, days: int, today: dt.date,
              drafts: bool = False, full_text: bool = False) -> list[dict]:
    out = []
    for i in range(days):
        day = start + dt.timedelta(days=i)
        entries = cal.plan(day) or (cal.plan(day, draft=True) if drafts or day >= today else [])
        done = [a for a in acts if a["date"] == day.isoformat()]
        out.append({
            "date": day.isoformat(),
            "weekday": day.strftime("%A"),
            "is_today": day == today,
            "is_past": day < today,
            "planned": [e["session"]["title"] for e in entries],
            "description": (_full_description(cal, entries[0])
                            if full_text and entries and entries[0].get("workoutId") else ""),
            "done": [f"{a.get('name') or a['sport']} — {a['distance_km']:g} km, {a['duration_min']:.0f}'"
                     for a in done if (a.get("distance_km") or 0) > 0],
        })
    return out


def build_data(api, cfg: dict, today: dt.date | None = None) -> dict:
    today = today or dt.datetime.now().date()
    from . import strava as S
    acts = G.fetch_activities(api, history_start(today), today)
    acts = S.merge(acts, S.fetch(history_start(today), today))[0]
    learned = {s: day for s in ("run", "bike") if (day := learn_long_day(acts, today, s))}
    cfg = dict(cfg, _learned_days=learned)
    wt = compute_week_target(acts, today, cfg)
    phase = phase_for(today, cfg)
    P.set_current(P.estimate(cfg, G.fetch_race_predictions(api), wt.get("chronic_km")))

    cal = Calendar(api, cfg)
    _, tpl, dist, bdist = week_parts(today, cfg, wt)
    led = W.ledger(acts, today, tpl, dist, bdist,
                   quality_ok=not wt.get("return_mode") and phase.name != "transition")

    mon = monday_of(today)
    this_week = _day_rows(cal, acts, mon, 7, today, full_text=True)
    upcoming = _day_rows(cal, acts, mon + dt.timedelta(days=7), 7, today, drafts=True)

    # Full activity list for the Training tab
    activities = []
    for a in acts:
        if (a.get("distance_km") or 0) > 0 or (a.get("duration_min") or 0) > 0:
            activities.append({
                "date": a["date"],
                "sport": a["sport"],
                "name": a.get("name") or a["sport"].replace("_", " ").title(),
                "distance_km": a.get("distance_km"),
                "duration_min": a.get("duration_min"),
                "aerobic_te": a.get("aerobic_te"),
            })
    activities.sort(key=lambda x: x["date"], reverse=True)

    # Decisions log: last 14 days with coach reasoning from Garmin descriptions
    decisions = []
    for i in range(14):
        day = today - dt.timedelta(days=i)
        entries = cal.plan(day) or cal.plan(day, draft=True) or []
        day_acts = [a for a in acts if a["date"] == day.isoformat()
                    and ((a.get("distance_km") or 0) > 0 or (a.get("duration_min") or 0) > 0)]
        planned_titles = [e["session"]["title"] for e in entries] if entries else ["Rest"]
        desc = (_full_description(cal, entries[0])
                if entries and entries[0].get("workoutId") else "")
        decisions.append({
            "date": day.isoformat(),
            "weekday": day.strftime("%a %d %b"),
            "is_today": day == today,
            "is_past": day < today,
            "planned": planned_titles,
            "description": desc,
            "done": [f"{a.get('name') or a['sport']} · {a['distance_km']:g} km · {a['duration_min']:.0f}'"
                     for a in day_acts],
        })

    # Current week aggregates for progress bars
    mon_iso = mon.isoformat()
    tmrw_iso = (today + dt.timedelta(days=1)).isoformat()
    cw = [a for a in acts if mon_iso <= a["date"] < tmrw_iso]
    week_current = {
        "run_km": round(sum(a.get("distance_km") or 0 for a in cw if a["sport"] == "run"), 1),
        "bike_min": round(sum(a.get("duration_min") or 0 for a in cw if a["sport"] == "bike"), 0),
        "swim_m": round(sum((a.get("swum_m") or (a.get("distance_km") or 0) * 1000)
                             for a in cw if a["sport"] == "swim")),
    }

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
        "today": today.isoformat(),
        "race": _race_countdown(cfg, today),
        "phase": {"name": phase.name, "describe": phase.describe(), "focus": phase.focus},
        "week_target": {
            "target_km": wt["target_km"],
            "chronic_km": wt["chronic_km"],
            "last_week_km": wt.get("last_week_km"),
            "return_mode": wt.get("return_mode", False),
            "bike_hours": wt.get("bike_hours"),
        },
        "week_so_far": W.summary(led),
        "week_current": week_current,
        "paces": P.summary(),
        "history": _weekly_history(acts, today, 12),
        "this_week": this_week,
        "upcoming": upcoming,
        "activities": activities,
        "decisions": decisions,
    }


# ── SVG chart ─────────────────────────────────────────────────────────────────

def _svg_history(history: list[dict]) -> str:
    """Inline SVG bar chart for weekly running volume."""
    if not history:
        return "<p style='color:var(--muted);font-size:.83rem'>No history yet.</p>"
    peak = max(h["run_km"] for h in history) or 1
    n = len(history)
    VW, VH = 800, 120
    bar_w = VW / n * 0.62
    spacing = VW / n
    chart_top = 16
    chart_bot = 20
    chart_h = VH - chart_top - chart_bot

    parts: list[str] = []
    for i, h in enumerate(history):
        cx = i * spacing + spacing / 2
        x = cx - bar_w / 2
        is_cur = i == n - 1
        pct = h["run_km"] / peak
        bh = max(pct * chart_h, 2 if h["run_km"] > 0 else 0)
        by = chart_top + chart_h - bh
        opacity = "0.88" if is_cur else "0.42"
        label = dt.date.fromisoformat(h["week_of"]).strftime("%d/%m")

        if h["run_km"] > 0:
            parts.append(
                f'<rect x="{x:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
                f'fill="var(--run)" opacity="{opacity}" rx="2"/>'
            )
            val_y = by - 3
            parts.append(
                f'<text x="{cx:.1f}" y="{val_y:.1f}" text-anchor="middle" '
                f'fill="var(--fg)" font-size="10" opacity="{opacity}">{h["run_km"]:g}</text>'
            )
        else:
            parts.append(
                f'<rect x="{x:.1f}" y="{chart_top + chart_h:.1f}" width="{bar_w:.1f}" height="2" '
                f'fill="var(--border)" rx="1"/>'
            )

        parts.append(
            f'<text x="{cx:.1f}" y="{VH:.1f}" text-anchor="middle" '
            f'fill="var(--muted)" font-size="9.5">{label}</text>'
        )

    # Accent outline around current week
    cur_cx = (n - 1) * spacing + spacing / 2
    cur_x = cur_cx - bar_w / 2 - 2
    parts.append(
        f'<rect x="{cur_x:.1f}" y="{chart_top - 4}" width="{bar_w + 4:.1f}" '
        f'height="{chart_h + 8}" fill="none" stroke="var(--accent)" '
        f'stroke-width="1.5" rx="4" opacity="0.45"/>'
    )

    return (f'<svg viewBox="0 0 {VW} {VH}" width="100%" '
            f'style="display:block;overflow:visible;max-height:130px" aria-hidden="true">'
            + "".join(parts) + "</svg>")


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _e(s: str) -> str:
    return html.escape(str(s))


def _dot(sport: str) -> str:
    cls = sport.lower().replace(" ", "_")
    return f'<span class="dot dot-{_e(cls)}" aria-hidden="true"></span>'


def _day_card(d: dict) -> str:
    cls = "day-today" if d["is_today"] else ("day-past" if d["is_past"] else "")
    badge = " <span class='today-badge'>today</span>" if d["is_today"] else ""
    date_str = dt.date.fromisoformat(d["date"]).strftime("%a %d %b")
    planned = " + ".join(_e(p) for p in d["planned"]) or "Rest"
    done_html = "".join(f'<div class="done-row">&#10003; {_e(x)}</div>' for x in d["done"])
    desc_html = ""
    if d.get("description"):
        desc_html = (
            '<details class="desc-toggle">'
            '<summary>Why / details</summary>'
            f'<pre class="desc-pre">{_e(d["description"])}</pre>'
            '</details>'
        )
    return (
        f'<div class="day-card {cls}">'
        f'<div class="day-meta">{_e(date_str)}{badge}</div>'
        f'<div class="day-session">{planned}</div>'
        f'{done_html}{desc_html}</div>'
    )


def _decision_card(d: dict) -> str:
    cls = "dec-today" if d["is_today"] else ""
    planned = " + ".join(_e(p) for p in d["planned"])
    done_html = ""
    if d["done"]:
        items = "".join(f'<span class="done-item">{_e(x)}</span>' for x in d["done"])
        done_html = f'<div class="dec-done">&#10003; {items}</div>'
    elif d["is_past"]:
        done_html = '<div class="dec-rest">&#8212;</div>'
    desc_html = ""
    if d.get("description"):
        desc_html = (
            '<details class="desc-toggle">'
            '<summary>Coach reasoning</summary>'
            f'<pre class="desc-pre">{_e(d["description"])}</pre>'
            '</details>'
        )
    return (
        f'<div class="dec-card {cls}">'
        f'<div class="dec-hd">'
        f'<span class="dec-date">{_e(d["weekday"])}</span>'
        f'<span class="dec-plan">{planned}</span>'
        f'</div>'
        f'{done_html}{desc_html}</div>'
    )


def _act_row(a: dict) -> str:
    sport = a["sport"].lower()
    dist = f'{a["distance_km"]:g}&nbsp;km' if a.get("distance_km") else "&#8212;"
    dur = f'{a["duration_min"]:.0f}&#x2032;' if a.get("duration_min") else "&#8212;"
    te = f'{a["aerobic_te"]:.1f}' if a.get("aerobic_te") else "&#8212;"
    date_str = dt.date.fromisoformat(a["date"]).strftime("%d %b")
    return (
        f'<tr class="act-row" data-sport="{_e(sport)}">'
        f'<td class="act-date">{_e(date_str)}</td>'
        f'<td>{_dot(sport)}{_e(a["name"])}</td>'
        f'<td class="num">{dist}</td>'
        f'<td class="num">{dur}</td>'
        f'<td class="num">{te}</td>'
        f'</tr>'
    )


def _stat_cards(data: dict) -> str:
    race = data.get("race")
    wt = data["week_target"]
    phase = data["phase"]

    if race:
        race_card = (
            f'<div class="stat-card">'
            f'<div class="lbl">Next race</div>'
            f'<div class="big" style="color:var(--warn)">{race["days"]}</div>'
            f'<div class="sub">{_e(race["name"])}</div>'
            f'<div class="sub">{race["weeks"]:g}&nbsp;weeks away</div>'
            f'</div>'
        )
    else:
        race_card = (
            '<div class="stat-card"><div class="lbl">Next race</div>'
            '<div class="big" style="color:var(--muted)">&#8212;</div>'
            '<div class="sub">None configured</div></div>'
        )

    phase_name_fmt = phase["name"].replace("_", " ").title()
    phase_card = (
        f'<div class="stat-card">'
        f'<div class="lbl">Phase</div>'
        f'<div class="big" style="font-size:1.1rem;line-height:1.35">{_e(phase_name_fmt)}</div>'
        f'<div class="sub">{_e(phase["describe"][:80])}</div>'
        f'</div>'
    )

    chronic = wt.get("chronic_km") or 0
    last = wt.get("last_week_km") or 0
    target_card = (
        f'<div class="stat-card">'
        f'<div class="lbl">Weekly target</div>'
        f'<div class="big">{wt["target_km"]:g}'
        f'<span style="font-size:.9rem;font-weight:500;color:var(--muted)"> km</span></div>'
        f'<div class="sub">avg&nbsp;{chronic:g}&nbsp;km &middot; last&nbsp;{last:g}&nbsp;km</div>'
        f'</div>'
    )

    pace_card = (
        f'<div class="stat-card">'
        f'<div class="lbl">Training paces</div>'
        f'<div class="sub" style="margin-top:8px;line-height:1.8">{_e(data["paces"])}</div>'
        f'</div>'
    )

    return race_card + phase_card + target_card + pace_card


def _week_progress(data: dict) -> str:
    wt = data["week_target"]
    wc = data.get("week_current", {})
    cards: list[str] = []

    run_done = wc.get("run_km", 0) or 0
    run_target = wt["target_km"] or 1
    pct = min(100, round(100 * run_done / run_target))
    cards.append(
        f'<div class="wk-prog-card">'
        f'<div class="sport-lbl run">Running</div>'
        f'<div class="prog">'
        f'<div class="prog-labels"><span>{run_done:g}&nbsp;km</span><span>{run_target:g}&nbsp;km target</span></div>'
        f'<div class="prog-track"><div class="prog-fill" style="width:{pct}%;background:var(--run)"></div></div>'
        f'</div>'
        f'<div class="wpc-nums">{pct}% of target</div>'
        f'</div>'
    )

    bike_min = wc.get("bike_min", 0) or 0
    bike_h = wt.get("bike_hours")
    if bike_h or bike_min > 0:
        bike_target_min = (bike_h or 0) * 60
        bpct = min(100, round(100 * bike_min / bike_target_min)) if bike_target_min else 0
        if bike_target_min:
            bike_str = f'{bike_min:.0f}&#x2032; of {bike_target_min:.0f}&#x2032; target ({bpct}%)'
        else:
            bike_str = f'{bike_min:.0f}&#x2032; this week'
        prog_fill = (f'<div class="prog-track"><div class="prog-fill" '
                     f'style="width:{bpct}%;background:var(--bike)"></div></div>')
        cards.append(
            f'<div class="wk-prog-card">'
            f'<div class="sport-lbl bike">Cycling</div>'
            f'<div class="prog">'
            f'<div class="prog-labels"><span>{bike_min:.0f}&#x2032;</span>'
            f'{"<span>" + f"{bike_target_min:.0f}&#x2032; target</span>" if bike_target_min else "<span></span>"}'
            f'</div>{prog_fill}</div>'
            f'<div class="wpc-nums">{bike_str}</div>'
            f'</div>'
        )

    swim_m = wc.get("swim_m", 0) or 0
    if swim_m > 0:
        cards.append(
            f'<div class="wk-prog-card">'
            f'<div class="sport-lbl swim">Swimming</div>'
            f'<div class="wpc-nums" style="margin-top:8px">{swim_m:.0f}&nbsp;m this week</div>'
            f'</div>'
        )

    if not cards:
        return "<p style='color:var(--muted);font-size:.83rem'>No activities yet this week.</p>"
    return "\n".join(cards)


def _why_html(data: dict) -> str:
    phase = data["phase"]
    race = data.get("race")
    wt = data["week_target"]
    cards: list[str] = []

    phase_name_fmt = phase["name"].replace("_", " ").title()
    cards.append(
        f'<div class="why-card">'
        f'<div class="why-lbl">Current phase</div>'
        f'<div class="why-title">{_e(phase_name_fmt)}</div>'
        f'<div class="why-text">{_e(phase["describe"])}</div>'
        f'</div>'
    )

    if race:
        date_fmt = dt.date.fromisoformat(race["date"]).strftime("%d %B %Y")
        cards.append(
            f'<div class="why-card">'
            f'<div class="why-lbl">Goal race</div>'
            f'<div class="why-title">{_e(race["name"])}</div>'
            f'<div class="why-text">'
            f'{race["days"]}&nbsp;days away ({race["weeks"]:g}&nbsp;wk)'
            f' &middot; {_e(date_fmt)}'
            f'</div></div>'
        )

    chronic = wt.get("chronic_km") or 0
    target = wt["target_km"]
    last = wt.get("last_week_km") or 0
    ramp = round(target - chronic, 1) if chronic else 0
    if ramp > 0.5:
        ramp_note = f" (+{ramp:g}&nbsp;km from avg)"
    elif ramp < -0.5:
        ramp_note = f" ({abs(ramp):g}&nbsp;km below avg &mdash; recovery)"
    else:
        ramp_note = " (at average)"
    lines = [
        f'Target: <strong>{target}&nbsp;km</strong>{ramp_note}',
        f'4-week average: {chronic}&nbsp;km',
        f'Last week: {last}&nbsp;km',
    ]
    if wt.get("return_mode"):
        lines.append("Return-to-running: volume builds ~20%/week from a 10&nbsp;km base, no hard sessions yet.")
    cards.append(
        f'<div class="why-card">'
        f'<div class="why-lbl">Volume logic</div>'
        f'<div class="why-text">{"<br>".join(lines)}</div>'
        f'</div>'
    )

    paces = data.get("paces", "")
    if paces:
        cards.append(
            f'<div class="why-card">'
            f'<div class="why-lbl">Training paces</div>'
            f'<div class="why-text">{_e(paces)}</div>'
            f'</div>'
        )

    ws = data.get("week_so_far") or {}
    if ws:
        items = "; ".join(f'{k.replace("_", " ")}: {v}' for k, v in ws.items())
        cards.append(
            f'<div class="why-card">'
            f'<div class="why-lbl">This week so far</div>'
            f'<div class="why-text">{_e(items)}</div>'
            f'</div>'
        )

    return "\n".join(cards)


# ── PAGE template (uses __PLACEHOLDER__ substitution to avoid CSS-brace issues) ──

_PAGE = (
    '<!doctype html>\n'
    '<html lang="en">\n'
    '<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
    '<title>Garmin Coach</title>\n'
    '<style>\n'
    ':root {\n'
    '  --bg:     #070d14;\n'
    '  --s1:     #0d1820;\n'
    '  --s2:     #132030;\n'
    '  --border: #18293c;\n'
    '  --fg:     #ddeaf8;\n'
    '  --muted:  #4f6880;\n'
    '  --dim:    #243344;\n'
    '  --accent: #4d8ef0;\n'
    '  --run:    #1cc47a;\n'
    '  --bike:   #f09530;\n'
    '  --swim:   #38b5e5;\n'
    '  --str:    #9b72e8;\n'
    '  --warn:   #f0a030;\n'
    '  --ok:     #1cc47a;\n'
    '  color-scheme: dark;\n'
    '}\n'
    '*,*::before,*::after { box-sizing: border-box; margin: 0; padding: 0; }\n'
    'html,body {\n'
    '  background: var(--bg); color: var(--fg);\n'
    '  font-family: -apple-system,"SF Pro Text","Segoe UI",system-ui,sans-serif;\n'
    '  font-size: 14px; line-height: 1.55; min-height: 100%;\n'
    '}\n'
    '.app {\n'
    '  max-width: 980px; margin: 0 auto;\n'
    '  padding-left: max(16px, env(safe-area-inset-left,0px));\n'
    '  padding-right: max(16px, env(safe-area-inset-right,0px));\n'
    '  padding-block: 0 64px;\n'
    '}\n'
    '.hdr {\n'
    '  display: flex; align-items: center; gap: 10px;\n'
    '  padding-block: 15px 11px; border-bottom: 1px solid var(--border);\n'
    '}\n'
    '.hdr-title { font-size: 1rem; font-weight: 700; letter-spacing: -.01em; }\n'
    '.hdr-pill {\n'
    '  font-size: .67rem; font-weight: 600; text-transform: uppercase;\n'
    '  letter-spacing: .06em; padding: 2px 8px; border-radius: 99px;\n'
    '  background: color-mix(in srgb,var(--accent) 14%,transparent);\n'
    '  color: var(--accent);\n'
    '  border: 1px solid color-mix(in srgb,var(--accent) 28%,transparent);\n'
    '}\n'
    '.hdr-meta { margin-left: auto; display: flex; align-items: center; gap: 10px; }\n'
    '.hdr-ts { color: var(--muted); font-size: .74rem; }\n'
    '.btn {\n'
    '  background: var(--s1); border: 1px solid var(--border); color: var(--fg);\n'
    '  border-radius: 6px; padding: 4px 10px; font-size: .77rem;\n'
    '  cursor: pointer; transition: background .15s;\n'
    '}\n'
    '.btn:hover { background: var(--s2); }\n'
    '.tabs {\n'
    '  display: flex; gap: 2px; padding-block: 10px;\n'
    '  border-bottom: 1px solid var(--border); margin-bottom: 20px;\n'
    '  overflow-x: auto; scrollbar-width: none;\n'
    '}\n'
    '.tabs::-webkit-scrollbar { display: none; }\n'
    '.tab {\n'
    '  background: none; border: none; color: var(--muted); cursor: pointer;\n'
    '  font-size: .82rem; font-weight: 500; padding: 5px 13px; border-radius: 6px;\n'
    '  transition: all .15s; white-space: nowrap;\n'
    '}\n'
    '.tab:hover { color: var(--fg); background: var(--s1); }\n'
    '.tab.active { color: var(--fg); background: var(--s2); }\n'
    '.section { display: none; }\n'
    '.section.active { display: block; }\n'
    '.stat-grid {\n'
    '  display: grid; grid-template-columns: repeat(auto-fill,minmax(175px,1fr));\n'
    '  gap: 10px; margin-bottom: 20px;\n'
    '}\n'
    '.stat-card {\n'
    '  background: var(--s1); border: 1px solid var(--border);\n'
    '  border-radius: 10px; padding: 13px 15px;\n'
    '}\n'
    '.lbl {\n'
    '  color: var(--muted); font-size: .67rem; font-weight: 600;\n'
    '  text-transform: uppercase; letter-spacing: .07em; margin-bottom: 5px;\n'
    '}\n'
    '.big {\n'
    '  font-size: 1.65rem; font-weight: 700; letter-spacing: -.03em;\n'
    '  line-height: 1.1; font-variant-numeric: tabular-nums;\n'
    '}\n'
    '.sub { color: var(--muted); font-size: .77rem; margin-top: 4px; }\n'
    '.prog { margin-top: 8px; }\n'
    '.prog-labels {\n'
    '  display: flex; justify-content: space-between; font-size: .72rem;\n'
    '  color: var(--muted); margin-bottom: 4px; font-variant-numeric: tabular-nums;\n'
    '}\n'
    '.prog-track { background: var(--s2); border-radius: 99px; height: 5px; overflow: hidden; }\n'
    '.prog-fill { height: 100%; border-radius: 99px; transition: width .4s ease; }\n'
    '.sh {\n'
    '  font-size: .71rem; font-weight: 600; text-transform: uppercase;\n'
    '  letter-spacing: .08em; color: var(--muted);\n'
    '  margin: 24px 0 10px; padding-bottom: 6px;\n'
    '  border-bottom: 1px solid var(--border);\n'
    '}\n'
    '.wk-prog-grid {\n'
    '  display: grid; grid-template-columns: repeat(auto-fill,minmax(200px,1fr));\n'
    '  gap: 10px; margin-bottom: 20px;\n'
    '}\n'
    '.wk-prog-card {\n'
    '  background: var(--s1); border: 1px solid var(--border);\n'
    '  border-radius: 10px; padding: 12px 14px;\n'
    '}\n'
    '.sport-lbl {\n'
    '  font-size: .71rem; font-weight: 600; text-transform: uppercase;\n'
    '  letter-spacing: .06em; margin-bottom: 8px;\n'
    '}\n'
    '.sport-lbl.run  { color: var(--run); }\n'
    '.sport-lbl.bike { color: var(--bike); }\n'
    '.sport-lbl.swim { color: var(--swim); }\n'
    '.wpc-nums { color: var(--muted); font-size: .8rem; margin-top: 5px; font-variant-numeric: tabular-nums; }\n'
    '.hist-chart {\n'
    '  background: var(--s1); border: 1px solid var(--border);\n'
    '  border-radius: 10px; padding: 14px 16px; margin-bottom: 20px;\n'
    '}\n'
    '.wk-grid {\n'
    '  display: grid; grid-template-columns: repeat(auto-fill,minmax(194px,1fr));\n'
    '  gap: 10px; margin-bottom: 20px;\n'
    '}\n'
    '.day-card {\n'
    '  background: var(--s1); border: 1px solid var(--border);\n'
    '  border-radius: 10px; padding: 10px 12px; font-size: .81rem;\n'
    '}\n'
    '.day-card.day-today { border-color: var(--accent); }\n'
    '.day-card.day-past  { opacity: .7; }\n'
    '.day-meta {\n'
    '  color: var(--muted); font-size: .71rem; margin-bottom: 4px;\n'
    '  display: flex; align-items: center; gap: 6px;\n'
    '}\n'
    '.today-badge {\n'
    '  background: color-mix(in srgb,var(--accent) 18%,transparent);\n'
    '  color: var(--accent); border-radius: 99px; padding: 1px 6px;\n'
    '  font-size: .64rem; font-weight: 600; text-transform: uppercase; letter-spacing: .05em;\n'
    '}\n'
    '.day-session { font-weight: 500; line-height: 1.35; }\n'
    '.done-row { color: var(--ok); font-size: .76rem; margin-top: 3px; }\n'
    '.desc-toggle { margin-top: 6px; }\n'
    '.desc-toggle summary { color: var(--muted); font-size: .72rem; cursor: pointer; user-select: none; }\n'
    '.desc-toggle summary:hover { color: var(--fg); }\n'
    '.desc-pre {\n'
    '  white-space: pre-wrap; font-size: .72rem; font-family: inherit; color: var(--fg);\n'
    '  background: var(--s2); border: 1px solid var(--border); border-radius: 6px;\n'
    '  padding: 8px 10px; margin-top: 6px; line-height: 1.5;\n'
    '}\n'
    '.dec-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 20px; }\n'
    '.dec-card {\n'
    '  background: var(--s1); border: 1px solid var(--border);\n'
    '  border-radius: 10px; padding: 11px 14px;\n'
    '}\n'
    '.dec-card.dec-today { border-color: var(--accent); }\n'
    '.dec-hd { display: flex; align-items: baseline; gap: 10px; margin-bottom: 4px; }\n'
    '.dec-date { color: var(--muted); font-size: .72rem; min-width: 78px; flex-shrink: 0; font-variant-numeric: tabular-nums; }\n'
    '.dec-plan { font-weight: 500; font-size: .85rem; min-width: 0; }\n'
    '.dec-done { font-size: .77rem; color: var(--ok); margin-top: 3px; display: flex; flex-wrap: wrap; gap: 6px; }\n'
    '.dec-rest { color: var(--dim); font-size: .77rem; margin-top: 3px; }\n'
    '.act-filters { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }\n'
    '.filter-btn {\n'
    '  background: var(--s1); border: 1px solid var(--border); border-radius: 99px;\n'
    '  color: var(--muted); font-size: .75rem; padding: 3px 10px;\n'
    '  cursor: pointer; transition: all .15s;\n'
    '}\n'
    '.filter-btn:hover { color: var(--fg); }\n'
    '.filter-btn.active                     { background: var(--s2); color: var(--fg); }\n'
    '.filter-btn.active[data-sport=all]     { color: var(--accent); border-color: var(--accent); }\n'
    '.filter-btn.active[data-sport=run]     { color: var(--run);    border-color: var(--run); }\n'
    '.filter-btn.active[data-sport=bike]    { color: var(--bike);   border-color: var(--bike); }\n'
    '.filter-btn.active[data-sport=swim]    { color: var(--swim);   border-color: var(--swim); }\n'
    '.filter-btn.active[data-sport=strength]{ color: var(--str);    border-color: var(--str); }\n'
    '.act-wrap { overflow-x: auto; }\n'
    '.act-table { width: 100%; border-collapse: collapse; font-size: .81rem; }\n'
    '.act-table th {\n'
    '  color: var(--muted); font-weight: 500; font-size: .67rem; text-transform: uppercase;\n'
    '  letter-spacing: .05em; padding: 0 10px 8px; text-align: left;\n'
    '}\n'
    '.act-table th.num,td.num { text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); }\n'
    '.act-table td { padding: 7px 10px; border-top: 1px solid var(--border); vertical-align: middle; }\n'
    '.act-date { color: var(--muted); font-size: .75rem; white-space: nowrap; font-variant-numeric: tabular-nums; }\n'
    '.dot {\n'
    '  display: inline-block; width: 7px; height: 7px; border-radius: 50%;\n'
    '  margin-right: 6px; flex-shrink: 0;\n'
    '}\n'
    '.dot-run    { background: var(--run); }\n'
    '.dot-bike,.dot-cycling,.dot-virtual_ride,.dot-indoor_cycling { background: var(--bike); }\n'
    '.dot-swim,.dot-swimming,.dot-open_water_swimming             { background: var(--swim); }\n'
    '.dot-strength,.dot-strength_training                         { background: var(--str); }\n'
    '.why-grid {\n'
    '  display: grid; grid-template-columns: repeat(auto-fill,minmax(235px,1fr));\n'
    '  gap: 12px; margin-bottom: 20px;\n'
    '}\n'
    '.why-card {\n'
    '  background: var(--s1); border: 1px solid var(--border);\n'
    '  border-radius: 10px; padding: 14px 16px;\n'
    '}\n'
    '.why-lbl {\n'
    '  color: var(--muted); font-size: .67rem; font-weight: 600;\n'
    '  text-transform: uppercase; letter-spacing: .07em; margin-bottom: 5px;\n'
    '}\n'
    '.why-title { font-size: .97rem; font-weight: 600; margin-bottom: 5px; }\n'
    '.why-text { color: var(--muted); font-size: .82rem; line-height: 1.65; }\n'
    '.why-text strong { color: var(--fg); font-weight: 600; }\n'
    '.ftr {\n'
    '  color: var(--dim); font-size: .71rem; margin-top: 40px;\n'
    '  padding-top: 12px; border-top: 1px solid var(--border);\n'
    '}\n'
    '@media (max-width: 500px) {\n'
    '  .stat-grid { grid-template-columns: repeat(2,1fr); }\n'
    '  .hdr-ts { display: none; }\n'
    '  .big { font-size: 1.4rem; }\n'
    '}\n'
    '</style>\n'
    '</head>\n'
    '<body>\n'
    '<div class="app">\n'
    '\n'
    '<header class="hdr">\n'
    '  <span class="hdr-title">Garmin Coach</span>\n'
    '  <span class="hdr-pill">__PHASE__</span>\n'
    '  <div class="hdr-meta">\n'
    '    <span class="hdr-ts">__GENERATED_AT__</span>\n'
    '    <button class="btn" onclick="location.reload()">&#8635;&nbsp;Refresh</button>\n'
    '  </div>\n'
    '</header>\n'
    '\n'
    '<nav class="tabs" role="tablist">\n'
    '  <button class="tab active" data-tab="status"   onclick="showTab(this)">&#x1F4CA;&nbsp;Status</button>\n'
    '  <button class="tab"        data-tab="why"       onclick="showTab(this)">&#x1F3AF;&nbsp;Why</button>\n'
    '  <button class="tab"        data-tab="decisions" onclick="showTab(this)">&#x1F9E0;&nbsp;Decisions</button>\n'
    '  <button class="tab"        data-tab="training"  onclick="showTab(this)">&#x1F3C3;&nbsp;Training</button>\n'
    '  <button class="tab"        data-tab="upcoming"  onclick="showTab(this)">&#x1F4C5;&nbsp;Upcoming</button>\n'
    '</nav>\n'
    '\n'
    '<section id="status" class="section active">\n'
    '  <div class="stat-grid">__STAT_CARDS__</div>\n'
    '  <h2 class="sh">This week</h2>\n'
    '  <div class="wk-prog-grid">__WEEK_PROGRESS__</div>\n'
    '  <h2 class="sh">Volume &mdash; last 12 weeks</h2>\n'
    '  <div class="hist-chart">__HISTORY__</div>\n'
    '</section>\n'
    '\n'
    '<section id="why" class="section">\n'
    '  <div class="why-grid">__WHY__</div>\n'
    '</section>\n'
    '\n'
    '<section id="decisions" class="section">\n'
    '  <h2 class="sh">Coach decisions &mdash; last 14 days</h2>\n'
    '  <div class="dec-list">__DECISIONS__</div>\n'
    '</section>\n'
    '\n'
    '<section id="training" class="section">\n'
    '  <div class="act-filters">\n'
    '    <button class="filter-btn active" data-sport="all"      onclick="filterActs(this)">All</button>\n'
    '    <button class="filter-btn"        data-sport="run"      onclick="filterActs(this)">Running</button>\n'
    '    <button class="filter-btn"        data-sport="bike"     onclick="filterActs(this)">Cycling</button>\n'
    '    <button class="filter-btn"        data-sport="swim"     onclick="filterActs(this)">Swimming</button>\n'
    '    <button class="filter-btn"        data-sport="strength" onclick="filterActs(this)">Strength</button>\n'
    '  </div>\n'
    '  <div class="act-wrap">\n'
    '    <table class="act-table">\n'
    '      <thead><tr>\n'
    '        <th>Date</th><th>Activity</th>\n'
    '        <th class="num">Distance</th><th class="num">Duration</th><th class="num">TE</th>\n'
    '      </tr></thead>\n'
    '      <tbody>__ACT_ROWS__</tbody>\n'
    '    </table>\n'
    '  </div>\n'
    '</section>\n'
    '\n'
    '<section id="upcoming" class="section">\n'
    '  <h2 class="sh">This week</h2>\n'
    '  <div class="wk-grid">__THIS_WEEK__</div>\n'
    '  <h2 class="sh">Next week (drafts)</h2>\n'
    '  <div class="wk-grid">__NEXT_WEEK__</div>\n'
    '</section>\n'
    '\n'
    '<footer class="ftr">\n'
    '  Garmin Coach &middot; updated a few times a day via GitHub Actions\n'
    '</footer>\n'
    '</div>\n'
    '\n'
    '<script>\n'
    'function showTab(btn) {\n'
    '  document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));\n'
    '  document.querySelectorAll(".section").forEach(s => s.classList.remove("active"));\n'
    '  btn.classList.add("active");\n'
    '  document.getElementById(btn.dataset.tab).classList.add("active");\n'
    '}\n'
    'function filterActs(btn) {\n'
    '  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));\n'
    '  btn.classList.add("active");\n'
    '  const sport = btn.dataset.sport;\n'
    '  document.querySelectorAll("tr.act-row").forEach(row => {\n'
    '    const rs = (row.dataset.sport || "").toLowerCase();\n'
    '    row.style.display = (sport === "all" || rs === sport || rs.startsWith(sport)) ? "" : "none";\n'
    '  });\n'
    '}\n'
    '</script>\n'
    '</body>\n'
    '</html>'
)


# ── render ────────────────────────────────────────────────────────────────────

def render_html(data: dict) -> str:
    next_week_html = "\n".join(_day_card(d) for d in data["upcoming"])
    if not next_week_html:
        next_week_html = "<p style='color:var(--muted);font-size:.83rem'>Not drafted yet.</p>"

    return (
        _PAGE
        .replace("__PHASE__", _e(data["phase"]["name"].replace("_", " ").title()))
        .replace("__GENERATED_AT__", _e(data["generated_at"]))
        .replace("__STAT_CARDS__", _stat_cards(data))
        .replace("__WEEK_PROGRESS__", _week_progress(data))
        .replace("__HISTORY__", _svg_history(data["history"]))
        .replace("__WHY__", _why_html(data))
        .replace("__DECISIONS__", "\n".join(_decision_card(d) for d in data["decisions"]))
        .replace("__ACT_ROWS__", "\n".join(_act_row(a) for a in data["activities"]))
        .replace("__THIS_WEEK__", "\n".join(_day_card(d) for d in data["this_week"]))
        .replace("__NEXT_WEEK__", next_week_html)
    )


# ── server ────────────────────────────────────────────────────────────────────

def serve(root: Path, port: int = PORT, open_browser: bool = True) -> None:
    import yaml
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    tokens = (cfg.get("garmin") or {}).get("tokenstore", "~/.garminconnect")
    api = G.connect(tokens)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            try:
                data = build_data(api, cfg)
                if path == "/api/data":
                    body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
                else:
                    body = render_html(data).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:  # noqa: BLE001
                log.exception("Dashboard request failed")
                msg = f"<pre>Error: {html.escape(str(e))}</pre>".encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg)

        def log_message(self, fmt, *args):  # noqa: A002
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Dashboard running at {url} (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
