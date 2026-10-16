"""A local dashboard showing training status, reasoning and history.

Run `python -m coach dashboard` (after `python -m coach login`). Opens
http://localhost:8765 in your browser. Reads live from Garmin, Todoist and
Strava on every load; nothing is uploaded.

Three tabs:
  Week   — status strip, this week (full descriptions), next week
  Log    — 12-week volume chart, sport progress, activities table
  Why    — training logic, phase, volume, 14-day decisions
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

# Sport types that count as cycling (Garmin + Strava/MyWhoosh variants)
_BIKE_SPORTS = {"bike", "cycling", "virtual_ride", "indoor_cycling", "e_bike_ride"}


def _is_bike(sport: str) -> bool:
    s = sport.lower()
    return s in _BIKE_SPORTS or "cycling" in s or s.startswith("bike")


# ── data helpers ──────────────────────────────────────────────────────────────

_KIND_LABEL: dict[str, str | None] = {
    "5k": "5 km road run",
    "10k": "10 km road run",
    "half": "Half marathon · 21 km",
    "marathon": "Marathon · 42 km",
    "road": None,        # use distance_km from config
    "tri_sprint": "Sprint triathlon",
    "tri_olympic": "Olympic triathlon",
    "tri_half": "70.3",
    "tri_full": "Ironman",
    "tri": "Triathlon",
    "bike": None,        # use distance_km from config
}


def _race_sub(race: dict) -> str:
    """Short distance/type label for the race status card."""
    kind = race.get("kind", "")
    label = _KIND_LABEL.get(kind)
    if label is None:
        dist = race.get("distance_km")
        label = f"{dist:g} km" if dist else kind.replace("_", " ")
    return label


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
        "distance_km": r.get("distance_km"),
        "distances": r.get("distances"),
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
            "bike_min": round(sum(a.get("duration_min") or 0 for a in wa if _is_bike(a["sport"])), 0),
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

    # Full activity list for the Log tab
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

    # Decisions log: last 14 days with coach reasoning
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

    # Current week aggregates — include all bike-type sports
    mon_iso = mon.isoformat()
    tmrw_iso = (today + dt.timedelta(days=1)).isoformat()
    cw = [a for a in acts if mon_iso <= a["date"] < tmrw_iso]
    week_current = {
        "run_km": round(sum(a.get("distance_km") or 0 for a in cw if a["sport"] == "run"), 1),
        "bike_min": round(sum(a.get("duration_min") or 0 for a in cw if _is_bike(a["sport"])), 0),
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
    """Running km bar chart, last 12 weeks."""
    if not history:
        return "<p style='color:var(--muted);font-size:.83rem'>No history yet.</p>"

    n = len(history)
    VW, VH = 800, 110
    chart_bot = 18
    chart_h = VH - chart_bot
    peak = max(h["run_km"] for h in history) or 1
    spacing = VW / n
    bar_w = spacing * 0.7

    parts: list[str] = []
    for i, h in enumerate(history):
        is_cur = i == n - 1
        bar_op = "0.92" if is_cur else "0.62"
        cx = i * spacing + spacing / 2
        x = cx - bar_w / 2
        label = dt.date.fromisoformat(h["week_of"]).strftime("%d/%m")
        if h["run_km"] > 0:
            bh = max((h["run_km"] / peak) * (chart_h - 2), 2)
            parts.append(f'<rect x="{x:.1f}" y="{chart_h - bh:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="var(--run)" opacity="{bar_op}" rx="2"><title>{h["run_km"]:g} km · {label}</title></rect>')
        else:
            parts.append(f'<rect x="{x:.1f}" y="{chart_h - 1:.1f}" width="{bar_w:.1f}" height="1" fill="var(--border)" opacity="0.35"/>')
        parts.append(f'<text x="{cx:.1f}" y="{VH}" text-anchor="middle" fill="var(--fg)" opacity="0.55" font-size="9.5">{label}</text>')

    return (f'<svg viewBox="0 0 {VW} {VH}" width="100%" '
            f'style="display:block;overflow:visible;max-height:{VH}px" aria-hidden="true">'
            + "".join(parts) + "</svg>")



# ── HTML helpers ──────────────────────────────────────────────────────────────

def _e(s: str) -> str:
    return html.escape(str(s))


def _dot(sport: str) -> str:
    cls = sport.lower().replace(" ", "_")
    return f'<span class="dot dot-{_e(cls)}" aria-hidden="true"></span>'


def _day_card(d: dict, compact: bool = False) -> str:
    """Day card: full-width with inline reasoning (compact=True for next-week grid)."""
    day = dt.date.fromisoformat(d["date"])
    day_name = day.strftime("%a")
    day_date = day.strftime("%-d %b") if hasattr(day, "strftime") else day.strftime("%d %b")
    try:
        day_date = day.strftime("%-d %b")  # "13 Oct" without leading zero
    except ValueError:
        day_date = day.strftime("%d %b").lstrip("0")

    planned = " + ".join(_e(p) for p in d["planned"]) or "Rest"

    if d["is_today"]:
        row_cls = "day-today"
        badge = '<span class="today-badge">today</span>'
    elif d["is_past"]:
        row_cls = "day-past"
        badge = ""
    else:
        row_cls = ""
        badge = ""

    if compact:
        row_cls += " day-compact"

    # Done activities
    done_html = ""
    if d["done"]:
        items = " &nbsp;·&nbsp; ".join(_e(x) for x in d["done"])
        done_html = f'<div class="done-row">&#10003;&nbsp;{items}</div>'
    elif d["is_past"] and not d["planned"]:
        done_html = '<div class="rest-row">Rest</div>'

    # Coach reasoning — strip footer timestamp, preserve newlines
    reason_html = ""
    if d.get("description"):
        import re as _re
        desc = _re.sub(r'\s*(?:Checked \d+:\d+\.|Planned the evening before\.)\s*$', '', d["description"].strip())
        if compact:
            reason_html = (
                '<details class="desc-toggle">'
                '<summary>Why</summary>'
                f'<div class="day-reason">{_e(desc)}</div>'
                '</details>'
            )
        else:
            desc_html = "<br>".join(_e(line) for line in desc.splitlines()) if "\n" in desc else _e(desc)
            reason_html = f'<div class="day-reason">{desc_html}</div>'

    if compact:
        return (
            f'<div class="day-card {row_cls}">'
            f'<div class="day-meta"><span class="day-name">{_e(day_name)}</span>'
            f' <span class="day-date">{_e(day_date)}</span>{badge}</div>'
            f'<div class="day-session">{planned}</div>'
            f'{done_html}'
            f'{reason_html}</div>'
        )

    return (
        f'<div class="day-row {row_cls}">'
        f'<div class="day-label">'
        f'<div class="day-name">{_e(day_name)}</div>'
        f'<div class="day-date">{_e(day_date)}</div>'
        f'{badge}'
        f'</div>'
        f'<div class="day-body">'
        f'<div class="day-session">{planned}</div>'
        f'{done_html}'
        f'{reason_html}'
        f'</div>'
        f'</div>'
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


def _status_strip(data: dict) -> str:
    race = data.get("race")
    wt = data["week_target"]
    phase = data["phase"]

    if race:
        race_html = (
            f'<div class="strip-item">'
            f'<div class="strip-lbl">Next race</div>'
            f'<div class="strip-val" style="color:var(--warn)">{race["days"]}&nbsp;days</div>'
            f'<div class="strip-sub">{_e(race["name"])}</div>'
            f'<div class="strip-sub" style="opacity:.6">{_e(_race_sub(race))}</div>'
            f'</div>'
        )
    else:
        race_html = (
            '<div class="strip-item">'
            '<div class="strip-lbl">Next race</div>'
            '<div class="strip-val" style="color:var(--muted)">&#8212;</div>'
            '</div>'
        )

    phase_name = phase["name"].replace("_", " ").title()
    phase_html = (
        f'<div class="strip-item">'
        f'<div class="strip-lbl">Phase</div>'
        f'<div class="strip-val">{_e(phase_name)}</div>'
        f'<div class="strip-sub">{_e(phase["focus"] or "")}</div>'
        f'</div>'
    )

    chronic = wt.get("chronic_km") or 0
    last = wt.get("last_week_km") or 0
    target_html = (
        f'<div class="strip-item">'
        f'<div class="strip-lbl">Weekly target</div>'
        f'<div class="strip-val">{wt["target_km"]:g}&nbsp;km</div>'
        f'<div class="strip-sub">avg&nbsp;{chronic:g}&nbsp;·&nbsp;last&nbsp;{last:g}&nbsp;km</div>'
        f'</div>'
    )

    pace_lines = "<br>".join(_e(p.strip()) for p in data["paces"].split(" · "))
    paces_html = (
        f'<div class="strip-item">'
        f'<div class="strip-lbl">Paces</div>'
        f'<div class="strip-sub" style="margin-top:4px;line-height:1.85">{pace_lines}</div>'
        f'</div>'
    )

    return race_html + phase_html + target_html + paces_html


def _week_progress(data: dict) -> str:
    wt = data["week_target"]
    wc = data.get("week_current", {})
    parts: list[str] = []

    # Running
    run_done = wc.get("run_km", 0) or 0
    run_target = wt["target_km"] or 1
    pct = min(100, round(100 * run_done / run_target))
    parts.append(
        f'<div class="prog-item">'
        f'<div class="prog-sport run">Running</div>'
        f'<div class="prog-labels"><span>{run_done:g}&nbsp;km</span>'
        f'<span>{run_target:g}&nbsp;km target</span></div>'
        f'<div class="prog-track"><div class="prog-fill" style="width:{pct}%;background:var(--run)"></div></div>'
        f'</div>'
    )

    # Cycling (all bike-type sports)
    bike_min = wc.get("bike_min", 0) or 0
    bike_h = wt.get("bike_hours")
    if bike_h or bike_min > 0:
        bike_target_min = (bike_h or 0) * 60
        bpct = min(100, round(100 * bike_min / bike_target_min)) if bike_target_min else 0
        bike_done_str = f'{bike_min:.0f}&#x2032;'
        bike_tgt_str = f'{bike_target_min:.0f}&#x2032; target' if bike_target_min else ""
        prog_fill = (f'<div class="prog-track"><div class="prog-fill" '
                     f'style="width:{bpct}%;background:var(--bike)"></div></div>'
                     if bike_target_min else "")
        parts.append(
            f'<div class="prog-item">'
            f'<div class="prog-sport bike">Cycling</div>'
            f'<div class="prog-labels"><span>{bike_done_str}</span>'
            f'<span>{bike_tgt_str}</span></div>'
            f'{prog_fill}'
            f'</div>'
        )

    # Swimming
    swim_m = wc.get("swim_m", 0) or 0
    if swim_m > 0:
        parts.append(
            f'<div class="prog-item">'
            f'<div class="prog-sport swim">Swimming</div>'
            f'<div class="prog-labels"><span>{swim_m:.0f}&nbsp;m</span><span></span></div>'
            f'</div>'
        )

    if not parts:
        return "<p class='empty-note'>No activities yet this week.</p>"
    return "\n".join(parts)


def _why_html(data: dict) -> str:
    phase = data["phase"]
    race = data.get("race")
    wt = data["week_target"]
    cards: list[str] = []

    cards.append(
        f'<div class="why-card">'
        f'<div class="why-lbl">Training focus</div>'
        f'<div class="why-text">{_e(phase["describe"])}</div>'
        f'</div>'
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
        pace_lines = "<br>".join(_e(p.strip()) for p in paces.split(" · "))
        cards.append(
            f'<div class="why-card">'
            f'<div class="why-lbl">Training paces</div>'
            f'<div class="why-text" style="line-height:1.7">{pace_lines}</div>'
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


def _week_content(data: dict) -> str:
    """Combined week plan + context for the merged Week tab."""
    this_week = data["this_week"]
    today_d = next((d for d in this_week if d["is_today"]), None)
    past = [d for d in this_week if d["is_past"]]
    future_tw = [d for d in this_week if not d["is_today"] and not d["is_past"]]
    next_week = data["upcoming"]

    parts: list[str] = []

    if today_d:
        parts.append(_day_card(today_d))

    upcoming = future_tw + next_week
    if upcoming:
        parts.append('<h2 class="sh">Upcoming</h2>')
        parts.append('<div class="wk-grid">')
        parts.extend(_day_card(d, compact=True) for d in upcoming)
        parts.append('</div>')
    elif not today_d:
        parts.append("<p class='empty-note'>No plan drafted yet.</p>")

    if past:
        parts.append('<h2 class="sh">Earlier this week</h2>')
        parts.append('<div class="wk-grid wk-past">')
        parts.extend(_day_card(d, compact=True) for d in past)
        parts.append('</div>')

    parts.append('<h2 class="sh">Context</h2>')
    parts.append(f'<div class="why-grid">{_why_html(data)}</div>')

    return "\n".join(parts)


# ── PAGE template (uses __PLACEHOLDER__ substitution to avoid CSS-brace issues) ──

_PAGE = (
    '<!doctype html>\n'
    '<html lang="en">\n'
    '<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
    '<meta name="theme-color" content="#070d14">\n'
    '<meta name="apple-mobile-web-app-capable" content="yes">\n'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">\n'
    '<meta name="apple-mobile-web-app-title" content="Coach">\n'
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
    '  max-width: 860px; margin: 0 auto;\n'
    '  padding-left: max(16px, env(safe-area-inset-left,0px));\n'
    '  padding-right: max(16px, env(safe-area-inset-right,0px));\n'
    '  padding-block: 0 64px;\n'
    '}\n'
    '.hdr {\n'
    '  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;\n'
    '  padding-block: 14px 11px; border-bottom: 1px solid var(--border);\n'
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
    '.strip {\n'
    '  display: grid; grid-template-columns: repeat(auto-fill,minmax(160px,1fr));\n'
    '  gap: 10px; padding: 14px 0 0;\n'
    '}\n'
    '.strip-item {\n'
    '  background: var(--s1); border: 1px solid var(--border);\n'
    '  border-radius: 10px; padding: 11px 14px;\n'
    '}\n'
    '.strip-lbl {\n'
    '  color: var(--muted); font-size: .66rem; font-weight: 600;\n'
    '  text-transform: uppercase; letter-spacing: .07em; margin-bottom: 4px;\n'
    '}\n'
    '.strip-val { font-size: 1.25rem; font-weight: 700; letter-spacing: -.02em; line-height: 1.2; font-variant-numeric: tabular-nums; }\n'
    '.strip-sub { color: var(--muted); font-size: .75rem; margin-top: 3px; }\n'
    '.tabs {\n'
    '  display: flex; gap: 2px; padding-block: 14px 0;\n'
    '  border-bottom: 1px solid var(--border); margin-bottom: 20px;\n'
    '  overflow-x: auto; scrollbar-width: none;\n'
    '}\n'
    '.tabs::-webkit-scrollbar { display: none; }\n'
    '.tab {\n'
    '  background: none; border: none; color: var(--muted); cursor: pointer;\n'
    '  font-size: .82rem; font-weight: 500; padding: 6px 14px; border-radius: 6px;\n'
    '  transition: all .15s; white-space: nowrap;\n'
    '}\n'
    '.tab:hover { color: var(--fg); background: var(--s1); }\n'
    '.tab.active { color: var(--fg); background: var(--s2); }\n'
    '.section { display: none; }\n'
    '.section.active { display: block; }\n'
    '.sh {\n'
    '  font-size: .71rem; font-weight: 600; text-transform: uppercase;\n'
    '  letter-spacing: .08em; color: var(--muted);\n'
    '  margin: 24px 0 12px; padding-bottom: 6px;\n'
    '  border-bottom: 1px solid var(--border);\n'
    '}\n'
    '.week-list { display: flex; flex-direction: column; }\n'
    '.day-row {\n'
    '  display: grid; grid-template-columns: 72px 1fr; gap: 14px;\n'
    '  padding: 13px 0; border-bottom: 1px solid var(--border);\n'
    '}\n'
    '.day-row.day-past { opacity: .72; }\n'
    '.day-row.day-today {\n'
    '  background: color-mix(in srgb,var(--accent) 7%,transparent);\n'
    '  border-radius: 8px; padding: 13px 10px; margin: 0 -10px;\n'
    '  border-bottom-color: transparent;\n'
    '}\n'
    '.day-label { display: flex; flex-direction: column; gap: 2px; padding-top: 1px; }\n'
    '.day-name { font-weight: 600; font-size: .92rem; }\n'
    '.day-date { color: var(--muted); font-size: .73rem; }\n'
    '.today-badge {\n'
    '  background: color-mix(in srgb,var(--accent) 18%,transparent);\n'
    '  color: var(--accent); border-radius: 99px; padding: 1px 6px;\n'
    '  font-size: .62rem; font-weight: 600; text-transform: uppercase;\n'
    '  letter-spacing: .05em; margin-top: 3px; display: inline-block;\n'
    '}\n'
    '.day-body { min-width: 0; }\n'
    '.day-session { font-weight: 500; font-size: .93rem; line-height: 1.35; margin-bottom: 4px; }\n'
    '.done-row { color: var(--ok); font-size: .78rem; margin-bottom: 4px; }\n'
    '.rest-row { color: var(--dim); font-size: .78rem; margin-bottom: 4px; }\n'
    '.day-reason {\n'
    '  color: var(--muted); font-size: .78rem; line-height: 1.6;\n'
    '  margin-top: 2px;\n'
    '}\n'
    '.wk-grid {\n'
    '  display: grid; grid-template-columns: repeat(auto-fill,minmax(112px,1fr));\n'
    '  gap: 8px; margin-bottom: 20px;\n'
    '}\n'
    '.day-card {\n'
    '  background: var(--s1); border: 1px solid var(--border);\n'
    '  border-radius: 10px; padding: 10px 12px; font-size: .8rem;\n'
    '}\n'
    '.day-card.day-today { border-color: var(--accent); }\n'
    '.day-card.day-past  { opacity: .7; }\n'
    '.day-meta {\n'
    '  display: flex; align-items: center; gap: 5px; flex-wrap: wrap;\n'
    '  margin-bottom: 5px;\n'
    '}\n'
    '.day-meta .day-name { font-weight: 600; font-size: .85rem; }\n'
    '.day-meta .day-date { color: var(--muted); font-size: .7rem; }\n'
    '.day-meta .today-badge { font-size: .58rem; }\n'
    '.day-session { font-weight: 500; line-height: 1.35; font-size: .8rem; }\n'
    '.day-card .done-row { font-size: .73rem; margin-top: 4px; }\n'
    '.prog-grid {\n'
    '  display: grid; grid-template-columns: repeat(auto-fill,minmax(200px,1fr));\n'
    '  gap: 10px; margin-bottom: 20px;\n'
    '}\n'
    '.prog-item {\n'
    '  background: var(--s1); border: 1px solid var(--border);\n'
    '  border-radius: 10px; padding: 12px 14px;\n'
    '}\n'
    '.prog-sport {\n'
    '  font-size: .7rem; font-weight: 600; text-transform: uppercase;\n'
    '  letter-spacing: .06em; margin-bottom: 7px;\n'
    '}\n'
    '.prog-sport.run  { color: var(--run); }\n'
    '.prog-sport.bike { color: var(--bike); }\n'
    '.prog-sport.swim { color: var(--swim); }\n'
    '.prog-labels {\n'
    '  display: flex; justify-content: space-between;\n'
    '  font-size: .72rem; color: var(--muted); margin-bottom: 6px;\n'
    '  font-variant-numeric: tabular-nums;\n'
    '}\n'
    '.prog-track { background: var(--s2); border-radius: 99px; height: 5px; overflow: hidden; }\n'
    '.prog-fill { height: 100%; border-radius: 99px; transition: width .4s ease; }\n'
    '.hist-chart {\n'
    '  background: var(--s1); border: 1px solid var(--border);\n'
    '  border-radius: 10px; padding: 14px 16px; margin-bottom: 20px;\n'
    '}\n'
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
    '.done-item { }\n'
    '.desc-toggle { margin-top: 6px; }\n'
    '.desc-toggle summary { color: var(--muted); font-size: .72rem; cursor: pointer; user-select: none; }\n'
    '.desc-toggle summary:hover { color: var(--fg); }\n'
    '.desc-pre {\n'
    '  white-space: pre-wrap; font-size: .72rem; font-family: inherit; color: var(--fg);\n'
    '  background: var(--s2); border: 1px solid var(--border); border-radius: 6px;\n'
    '  padding: 8px 10px; margin-top: 6px; line-height: 1.5;\n'
    '}\n'
    '.empty-note { color: var(--muted); font-size: .83rem; }\n'
    '.ftr {\n'
    '  color: var(--dim); font-size: .71rem; margin-top: 40px;\n'
    '  padding-top: 12px; border-top: 1px solid var(--border);\n'
    '}\n'
    '@media (max-width: 500px) {\n'
    '  .strip { grid-template-columns: repeat(2,1fr); }\n'
    '  .hdr-ts { display: none; }\n'
    '  .strip-val { font-size: 1.1rem; }\n'
    '  .day-row { grid-template-columns: 56px 1fr; gap: 8px; }\n'
    '  .wk-grid { grid-template-columns: repeat(auto-fill,minmax(90px,1fr)); gap: 6px; }\n'
    '  .why-grid { grid-template-columns: 1fr; }\n'
    '  .tab { padding: 8px 12px; font-size: .8rem; }\n'
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
    '<div class="strip">__STATUS_STRIP__</div>\n'
    '\n'
    '<nav class="tabs" role="tablist">\n'
    '  <button class="tab active" data-tab="week" onclick="showTab(this)">&#x1F4C5;&nbsp;Week</button>\n'
    '  <button class="tab"        data-tab="log"  onclick="showTab(this)">&#x1F4CA;&nbsp;Log</button>\n'
    '</nav>\n'
    '\n'
    '<!-- Tab: Week -->\n'
    '<section id="week" class="section active">\n'
    '__WEEK_CONTENT__\n'
    '</section>\n'
    '\n'
    '<!-- Tab: Log -->\n'
    '<section id="log" class="section">\n'
    '  <h2 class="sh">Running volume &mdash; last 12 weeks</h2>\n'
    '  <div class="hist-chart">__HISTORY__</div>\n'
    '  <h2 class="sh">This week</h2>\n'
    '  <div class="prog-grid">__WEEK_PROGRESS__</div>\n'
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
    '        <th class="num">Distance</th><th class="num">Duration</th><th class="num" title="Training Effect (aerobic load, 0–5)">Eff.</th>\n'
    '      </tr></thead>\n'
    '      <tbody>__ACT_ROWS__</tbody>\n'
    '    </table>\n'
    '  </div>\n'
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
    '    const match = sport === "all" || rs === sport || rs.startsWith(sport)\n'
    '      || (sport === "bike" && (rs.includes("cycling") || rs.includes("virtual") || rs.includes("indoor")));\n'
    '    row.style.display = match ? "" : "none";\n'
    '  });\n'
    '}\n'
    '</script>\n'
    '</body>\n'
    '</html>'
)


# ── render ────────────────────────────────────────────────────────────────────

def render_html(data: dict) -> str:
    return (
        _PAGE
        .replace("__PHASE__", _e(data["phase"]["name"].replace("_", " ").title()))
        .replace("__GENERATED_AT__", _e(data["generated_at"]))
        .replace("__STATUS_STRIP__", _status_strip(data))
        .replace("__WEEK_CONTENT__", _week_content(data))
        .replace("__HISTORY__", _svg_history(data["history"]))
        .replace("__WEEK_PROGRESS__", _week_progress(data))
        .replace("__ACT_ROWS__", "\n".join(_act_row(a) for a in data["activities"]))
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
