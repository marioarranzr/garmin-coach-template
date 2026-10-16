"""A local dashboard: a page on your own computer showing your training status,
not just what the coach tells your watch.

Run `python -m coach dashboard` (after `python -m coach login`). It starts a
small web server on your computer and opens http://localhost:8765 in your
browser. Nothing leaves your computer except the Garmin/Todoist/Strava calls
the coach already makes; the page itself is never uploaded anywhere.

It shows:
- the next race, how far away, and the training phase that's building towards it;
- the last weeks of running/riding/swimming, so you can see the trend;
- this week's plan, day by day, with the actual workout description the coach
  wrote (the "why", the readiness it used, any changes) — the same reasoning
  that otherwise only shows up on the watch or in a GitHub run summary;
- the days ahead, as drafts;
- what you actually did on days that already have an activity, next to what
  was planned, so you can see compliance at a glance.
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
from .planner import compute_week_target, history_start, learn_long_day, monday_of, phase_for, plan_day, week_parts

log = logging.getLogger(__name__)
PORT = 8765


def _race_countdown(cfg: dict, today: dt.date) -> dict | None:
    races = sorted((r for r in cfg.get("races") or [] if dt.date.fromisoformat(str(r["date"])) >= today),
                   key=lambda r: str(r["date"]))
    if not races:
        return None
    r = races[0]
    d = dt.date.fromisoformat(str(r["date"]))
    weeks = (d - today).days / 7
    return {"name": r.get("name") or r["kind"].replace("_", " "), "date": d.isoformat(),
            "days": (d - today).days, "weeks": round(weeks, 1), "priority": r.get("priority", "A"),
            "kind": r["kind"]}


def _weekly_history(acts: list[dict], today: dt.date, weeks: int = 8) -> list[dict]:
    """[{week_of, run_km, bike_min, swim_m}] for the last `weeks` Monday-to-Sunday weeks."""
    out = []
    mon = monday_of(today)
    for i in range(weeks, 0, -1):
        start = mon - dt.timedelta(weeks=i)
        end = start + dt.timedelta(days=7)
        week_acts = [a for a in acts if start.isoformat() <= a["date"] < end.isoformat()]
        out.append({
            "week_of": start.isoformat(),
            "run_km": round(sum(a["distance_km"] or 0 for a in week_acts if a["sport"] == "run"), 1),
            "bike_min": round(sum(a.get("duration_min") or 0 for a in week_acts if a["sport"] == "bike"), 0),
            "swim_m": round(sum((a.get("swum_m") or (a["distance_km"] or 0) * 1000)
                                for a in week_acts if a["sport"] == "swim")),
        })
    return out


def _full_description(cal: Calendar, entry: dict) -> str:
    try:
        w = cal.api.get_workout_by_id(entry["workoutId"]) or {}
        return str(w.get("description") or "")
    except Exception as e:  # noqa: BLE001 — the dashboard still works without it
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
            "date": day.isoformat(), "weekday": day.strftime("%A"), "is_today": day == today,
            "is_past": day < today,
            "planned": [e["session"]["title"] for e in entries],
            "description": _full_description(cal, entries[0]) if full_text and entries and entries[0].get("workoutId") else "",
            "done": [f"{a.get('name') or a['sport']} — {a['distance_km']:g} km, {a['duration_min']:.0f}'"
                    for a in done],
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
    _, today_sess = plan_day(today, cfg, wt)
    ahead = W.drafts(today, cfg, acts, today_sess, 10)
    upcoming = _day_rows(cal, acts, mon + dt.timedelta(days=7), 7, today, drafts=True)

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
        "today": today.isoformat(),
        "race": _race_countdown(cfg, today),
        "phase": {"name": phase.name, "describe": phase.describe(), "focus": phase.focus},
        "week_target": {"target_km": wt["target_km"], "chronic_km": wt["chronic_km"],
                        "last_week_km": wt.get("last_week_km"), "return_mode": wt.get("return_mode", False),
                        "bike_hours": wt.get("bike_hours")},
        "week_so_far": W.summary(led),
        "paces": P.summary(),
        "history": _weekly_history(acts, today, 8),
        "this_week": this_week,
        "upcoming": upcoming,
    }


# ── rendering ────────────────────────────────────────────────────────────────
def _esc(s: str) -> str:
    return html.escape(str(s))


def _bars(history: list[dict]) -> str:
    peak = max((h["run_km"] for h in history), default=1) or 1
    rows = []
    for h in history:
        pct = round(100 * h["run_km"] / peak)
        label = dt.date.fromisoformat(h["week_of"]).strftime("%d %b")
        rows.append(f'<div class="bar-row"><span class="bar-label">{label}</span>'
                    f'<div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>'
                    f'<span class="bar-value">{h["run_km"]:g} km</span></div>')
    return "\n".join(rows)


def _day_card(d: dict) -> str:
    cls = "today" if d["is_today"] else ("past" if d["is_past"] else "")
    weekday = dt.date.fromisoformat(d["date"]).strftime("%a %d %b")
    planned = " + ".join(_esc(p) for p in d["planned"]) or "Rest"
    done = "".join(f'<div class="done">✓ {_esc(x)}</div>' for x in d["done"])
    desc = (f'<details><summary>Why / details</summary><pre>{_esc(d["description"])}</pre></details>'
           if d.get("description") else "")
    return (f'<div class="day {cls}"><div class="day-head"><strong>{weekday}</strong>'
           f'{" · today" if d["is_today"] else ""}</div>'
           f'<div class="planned">{planned}</div>{done}{desc}</div>')


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Garmin Coach — training status</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 -apple-system, system-ui, sans-serif; max-width: 920px; margin: 0 auto;
         padding: 24px 20px 60px; background: #0b0f14; color: #e6edf3; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
  h2 {{ font-size: 1.05rem; margin: 32px 0 12px; border-bottom: 1px solid #263140; padding-bottom: 6px; }}
  .sub {{ color: #8b98a5; margin-top: 0; }}
  .cards {{ display: flex; gap: 14px; flex-wrap: wrap; margin: 16px 0; }}
  .card {{ background: #121820; border: 1px solid #263140; border-radius: 10px; padding: 14px 16px; flex: 1;
          min-width: 160px; }}
  .card .big {{ font-size: 1.6rem; font-weight: 600; }}
  .card .label {{ color: #8b98a5; font-size: 0.8rem; text-transform: uppercase; letter-spacing: .03em; }}
  .phase-text {{ color: #c3cdd6; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin: 4px 0; font-size: 0.85rem; }}
  .bar-label {{ width: 60px; color: #8b98a5; }}
  .bar-track {{ flex: 1; background: #1a2230; border-radius: 4px; height: 14px; overflow: hidden; }}
  .bar-fill {{ background: #4e8cff; height: 100%; }}
  .bar-value {{ width: 60px; text-align: right; color: #c3cdd6; }}
  .week-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; }}
  .day {{ background: #121820; border: 1px solid #263140; border-radius: 8px; padding: 10px 12px; }}
  .day.today {{ border-color: #4e8cff; }}
  .day.past {{ opacity: 0.75; }}
  .day-head {{ font-size: 0.85rem; color: #8b98a5; margin-bottom: 4px; }}
  .planned {{ font-weight: 500; }}
  .done {{ color: #5ec98f; font-size: 0.85rem; margin-top: 4px; }}
  details {{ margin-top: 6px; font-size: 0.8rem; }}
  summary {{ cursor: pointer; color: #8b98a5; }}
  pre {{ white-space: pre-wrap; background: #0b0f14; border: 1px solid #263140; border-radius: 6px;
        padding: 8px; margin-top: 6px; font-size: 0.78rem; }}
  footer {{ color: #5c6773; font-size: 0.78rem; margin-top: 40px; }}
</style></head>
<body>
<h1>Your training status</h1>
<p class="sub">Generated {generated_at} · reads your Garmin, Todoist and Strava data live, each time this
page loads.</p>

<div class="cards">
  {race_card}
  <div class="card"><div class="label">Phase</div><div class="big">{phase_name}</div>
    <div class="phase-text">{phase_describe}</div></div>
  <div class="card"><div class="label">This week's target</div>
    <div class="big">{target_km:g} km running</div>
    <div class="phase-text">4-week average {chronic_km:g} km{return_note}</div></div>
  <div class="card"><div class="label">Current paces</div><div class="phase-text">{paces}</div></div>
</div>

<h2>Last 8 weeks</h2>
<div>{bars}</div>

<h2>This week</h2>
<div class="week-grid">{this_week}</div>

<h2>Next week (drafts)</h2>
<div class="week-grid">{upcoming}</div>

<footer>Garmin Coach dashboard — runs only on your computer; nothing here is uploaded anywhere.</footer>
</body></html>
"""


def render_html(data: dict) -> str:
    race = data.get("race")
    if race:
        race_card = (f'<div class="card"><div class="label">Next race</div>'
                    f'<div class="big">{_esc(race["name"])}</div>'
                    f'<div class="phase-text">{race["days"]} days away ({race["weeks"]:g} weeks) '
                    f'· priority {_esc(race["priority"])}</div></div>')
    else:
        race_card = '<div class="card"><div class="label">Next race</div><div class="phase-text">None set</div></div>'
    wt = data["week_target"]
    return_note = " — return-to-running mode" if wt.get("return_mode") else ""
    return PAGE.format(
        generated_at=_esc(data["generated_at"]), race_card=race_card,
        phase_name=_esc(data["phase"]["name"]), phase_describe=_esc(data["phase"]["describe"]),
        target_km=wt["target_km"], chronic_km=wt["chronic_km"], return_note=return_note,
        paces=_esc(data["paces"]), bars=_bars(data["history"]) or "<p class='sub'>No history yet.</p>",
        this_week="\n".join(_day_card(d) for d in data["this_week"]),
        upcoming="\n".join(_day_card(d) for d in data["upcoming"]) or "<p class='sub'>Not drafted yet.</p>",
    )


def serve(root: Path, port: int = PORT, open_browser: bool = True) -> None:
    import yaml
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    tokens = (cfg.get("garmin") or {}).get("tokenstore", "~/.garminconnect")
    api = G.connect(tokens)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            try:
                data = build_data(api, cfg)
                body = render_html(data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:  # noqa: BLE001
                log.exception("Dashboard request failed")
                msg = f"<pre>Couldn't build the dashboard: {html.escape(str(e))}</pre>".encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg)

        def log_message(self, fmt, *args):  # noqa: A002 — quiet
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
