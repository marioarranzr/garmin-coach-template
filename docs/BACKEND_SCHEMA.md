# Backend Schema — Garmin Coach Dashboard

The dashboard is stateless. All state lives in Garmin Connect (workouts, activities,
wellness), Todoist (plan tasks, notes, preferences) and Strava (MyWhoosh rides).
`build_data()` reads all of it fresh on every page load.

## `build_data()` return value

```
{
  "generated_at": "2026-10-16T09:30",        # ISO datetime, minutes precision

  "today":        "2026-10-16",              # ISO date

  "race": {                                  # None if no upcoming race
    "name":     "Zürcher Silvesterlauf",
    "date":     "2026-12-13",
    "days":     58,
    "weeks":    8.3,
    "priority": "A",
    "kind":     "run_8k"
  },

  "phase": {
    "name":     "base",                      # phase key from planner
    "describe": "Building aerobic base ...", # one-sentence description
    "focus":    "run"                        # "run" | "bike" | "tri"
  },

  "week_target": {
    "target_km":   48.0,                     # running km target this week
    "chronic_km":  44.0,                     # 4-week rolling average
    "last_week_km": 46.0,                    # previous week's km
    "return_mode": false,                    # true during return-to-running
    "bike_hours":  null                      # float when bike is targeted
  },

  "week_so_far": {                           # from W.summary(led)
    "long_run":   "not yet",                 # or "done Mon, 14 km"
    "hard_runs":  "1 of 1",
    "swims":      "0 of 0"
  },

  "week_current": {                          # computed from this week's activities
    "run_km":    12.5,
    "bike_min":  0,
    "swim_m":    0
  },

  "paces": "Easy 5:18 · Thr 4:30 · ...",    # formatted string from P.summary()

  "history": [                               # last 12 Monday–Sunday weeks
    {
      "week_of":  "2026-08-11",
      "run_km":   42.0,
      "bike_min": 0,
      "swim_m":   0
    },
    ...
  ],

  "this_week": [                             # 7 days, Mon–Sun
    {
      "date":      "2026-10-14",
      "weekday":   "Wednesday",
      "is_today":  false,
      "is_past":   true,
      "planned":   ["Easy run 10 km"],       # session titles from Garmin
      "description": "Readiness 72...",      # full workout description (may be "")
      "done":      ["Easy run — 10 km, 58'"]
    },
    ...
  ],

  "upcoming": [                              # 7 days, next Mon–Sun (drafts)
    # same shape as this_week entries
  ],

  "activities": [                            # all activities in lookback window
    {
      "date":         "2026-10-14",
      "sport":        "run",                 # Garmin sport key (lower-case)
      "name":         "Morning Run",
      "distance_km":  10.2,                  # null for strength/other
      "duration_min": 57.8,
      "aerobic_te":   3.2                    # null if not measured
    },
    ...
  ],

  "decisions": [                             # last 14 days, most recent first
    {
      "date":      "2026-10-16",
      "weekday":   "Thu 16 Oct",
      "is_today":  true,
      "is_past":   false,
      "planned":   ["Easy run 10 km"],
      "description": "Readiness 72/100...", # full Garmin workout description
      "done":      []                        # activities completed
    },
    ...
  ]
}
```

## Data sources per key

| Key | Source |
|-----|--------|
| `race` | `config.yaml` → `races` array |
| `phase` | `planner.phase_for()` |
| `week_target` | `planner.compute_week_target()` |
| `week_so_far` | `week.ledger()` + `week.summary()` |
| `week_current` | computed from `activities` list |
| `paces` | `paces.summary()` ← `G.fetch_race_predictions()` |
| `history` | `G.fetch_activities()` + `S.fetch()` |
| `this_week` | `calendar.Calendar.plan()` × 7 + `G.fetch_activities()` |
| `upcoming` | `calendar.Calendar.plan(draft=True)` × 7 |
| `activities` | `G.fetch_activities()` + `S.fetch()` |
| `decisions.planned` | `calendar.Calendar.plan()` |
| `decisions.description` | `G.api.get_workout_by_id()` |
| `decisions.done` | `G.fetch_activities()` |
