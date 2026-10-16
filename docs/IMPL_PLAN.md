# Implementation Plan — Garmin Coach Dashboard

## Build sequence

Steps are in dependency order. Each step is independently testable.

### Step 1 — Extend `build_data()` ✅
Add three new keys to the return dict:
- `activities` — all activities in the lookback window, sorted recent-first.
- `decisions` — last 14 days with planned titles and Garmin descriptions.
- `week_current` — `{run_km, bike_min, swim_m}` from the current week's activities.

Test: `build_data()` with a fixed `today` returns the new keys and correct shapes.

### Step 2 — SVG bar chart ✅
`_svg_history(history)` → inline SVG string.
- Bars scaled to peak week.
- Current week highlighted with accent outline.
- Labels: week-start date.

Test: returns valid SVG for empty list and for a list of 12 weeks.

### Step 3 — HTML component functions ✅
- `_day_card(d)` — for Upcoming and This Week.
- `_decision_card(d)` — for Decisions tab.
- `_act_row(a)` — for Training tab.
- `_why_html(data)` — for Why tab.
- `_week_progress_html(data)` — progress bars for Status tab.

Test: each function returns a non-empty string for typical inputs.

### Step 4 — PAGE template + `render_html()` ✅
Replace the single `PAGE` string and `render_html()` with the tabbed design.
Use `str.replace("__PLACEHOLDER__", value)` substitution to avoid CSS/JS brace
escaping issues.

Test: `render_html(mock_data)` returns a string containing all five tab IDs.

### Step 5 — Update `serve()` ✅
Add `/api/data` route returning JSON.

Test: `GET /api/data` returns valid JSON with the expected keys.

### Step 6 — Update CLAUDE.md ✅
Add Working Style section and reference to docs/ folder.

### Step 7 — Run tests ✅
`python tests/test_scenarios.py` — must pass.

### Step 8 — Push to template
`git push template main` (or push to the template repo).

### Step 9 — Merge into private repo
`git fetch template && git merge template/main`
Check for conflicts: `git diff --name-only --diff-filter=U`
Keep Mario's `config.yaml`.

### Step 10 — Push + confirm CI
`git push origin main`
Check GitHub Actions run.

### Step 11 — (Optional) Live test
`python -m coach dashboard` — verify the page loads and all five tabs render.

---

## Open work (not in this iteration)

- Auto-refresh on a timer (e.g. every 10 min) without full page reload.
- HRV / sleep trend chart in the Status tab (requires wellness endpoint in the dashboard).
- Long-run progression chart in the Training tab.
- Withings scale integration (weight, muscle, fat %) — see CLAUDE.md open ideas.
