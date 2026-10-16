# App Flow — Garmin Coach Dashboard

## Entry point

```
python -m coach dashboard
  → starts HTTPServer on 127.0.0.1:8765
  → opens http://localhost:8765 in the default browser
```

## Page load

```
GET /
  → build_data(api, cfg)       ~3–8 s (Garmin API calls)
  → render_html(data)          < 1 ms
  → 200 text/html
```

The page opens on the **Status** tab. All five tabs are in the HTML; switching is
instant (no fetch, no reload).

## Tab navigation

```
[📊 Status] [🎯 Why] [🧠 Decisions] [🏃 Training] [📅 Upcoming]
```

Clicking a tab:
1. Removes `active` from all `.tab` buttons and all `.section` elements.
2. Adds `active` to the clicked button and the matching `<section id="...">`.
3. No URL change, no history entry.

## Refresh

The **↻ Refresh** button calls `location.reload()`. The page re-fetches all data
from Garmin, Strava and Todoist, then re-renders the full HTML.

There is no auto-refresh; Mario reloads when he wants the latest data.

## Activity filter (Training tab)

Sport filter pills (All / Running / Cycling / Swimming / Strength) toggle
`display:none` on table rows by matching their `data-sport` attribute.

## Day card expand (Upcoming tab)

Each day card with a workout description has a `<details>` element. Clicking
"Why / details" expands the full workout description from Garmin.

## Decision card expand (Decisions tab)

Same `<details>` pattern: "Coach reasoning" expands the full workout description.

## Debug

`GET /api/data` returns the raw JSON from `build_data()`.

## Error handling

If `build_data()` raises, the server returns HTTP 500 with a `<pre>` showing the
exception. The user can re-try after fixing the underlying issue (e.g. expired
Garmin session → `python -m coach login`).
