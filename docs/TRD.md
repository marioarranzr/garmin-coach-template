# Technical Requirements Document — Garmin Coach Dashboard

## Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Server | Python `http.server.HTTPServer` | already in stdlib, no deps |
| Template | Python string with `str.replace()` | no brace-escaping issues vs `.format()` |
| CSS/JS | Vanilla, inline in the HTML | no CDN deps, works offline |
| Charts | Inline SVG generated in Python | no chart library needed |
| Data | Same `build_data()` call as before | reuses all existing logic |

## Endpoints

| Path | Response | Notes |
|------|----------|-------|
| `GET /` | Full HTML page | builds and renders on every request |
| `GET /api/data` | JSON (`build_data()` output) | for debugging / future use |

## Data flow

```
browser → GET / → Python handler
  → build_data(api, cfg)
      → G.fetch_activities (Garmin)
      → S.fetch (Strava)
      → G.fetch_race_predictions (Garmin)
      → G.fetch_wellness (Garmin) — not in dashboard, only in engine
      → cal.plan(day) × 14   for decisions log
      → cal.api.get_workout_by_id() × ≤14   for descriptions
  → render_html(data)
  → HTML response
```

## `build_data()` output keys

See `docs/BACKEND_SCHEMA.md` for the full schema.

New keys added for the dashboard:
- `activities` — list of all activities in the lookback window
- `decisions` — list of last 14 days with planned sessions + descriptions
- `week_current` — `{run_km, bike_min, swim_m}` for the current week

## HTML design system

Defined as CSS custom properties on `:root` (dark theme only — the dashboard is
a personal localhost tool, always dark):

```css
--bg, --s1, --s2, --border   /* background layers */
--fg, --muted, --dim          /* text hierarchy */
--accent                      /* interactive elements */
--run, --bike, --swim, --str  /* sport colours */
--warn, --ok                  /* status colours */
```

## Performance notes

- Each `cal.api.get_workout_by_id()` call is a Garmin API request. The decisions
  log fetches up to 14 descriptions per load. This is acceptable on localhost but
  slow on a poor connection. No caching is implemented (stateless design).
- The SVG chart is generated server-side; no JS rendering.

## File layout

```
coach/dashboard.py    — all server, data and rendering logic
docs/PRD.md           — what the dashboard does
docs/TRD.md           — this file
docs/APP_FLOW.md      — navigation and user journey
docs/DESIGN_BRIEF.md  — colours, typography, components
docs/BACKEND_SCHEMA.md— data structures
docs/IMPL_PLAN.md     — build sequence
```
