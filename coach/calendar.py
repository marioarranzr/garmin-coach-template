"""The coach's memory lives in your Garmin calendar, not in files.

Everything it needs from an earlier run is in the workouts it uploaded, in text
you can read anyway:
- the workout name ("Coach · Long run 12 km") gives the kind of session, its
  sport and distance (also stored as the workout's estimated distance);
- "Readiness 63/100" gives that day's readiness;
- the footer "Planned 07:42" says when it was planned, so a note you add
  later is spotted as new;
- "Planned without last night's sleep data" says the sleep hadn't arrived.

That covers yesterday's plan (compliance), this week's plan so far, what to
replace on a re-run, and what was planned on the days of your older notes.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

from .garmin_api import find_key

log = logging.getLogger(__name__)
MAX_DESCRIPTION = 1000
NO_SLEEP = "Planned without last night's sleep data."


def fit(body: str, footer: str) -> str:
    """The description, with the body shortened if needed so the footer (which
    the coach reads back) always fits Garmin's limit."""
    room = MAX_DESCRIPTION - len(footer) - 2
    if len(body) > room:
        body = body[:room - 1].rstrip() + "…"
    return f"{body}\n\n{footer}"


def footer(phase_text: str, planned_at: dt.datetime, sleep_ready: bool) -> str:
    lines = [] if sleep_ready else [NO_SLEEP]
    return "\n".join(lines + [f"{phase_text}", f"Planned {planned_at:%H:%M}."])


def read_description(description: str | None) -> dict:
    """{r, sr, at} from a description the coach wrote (and from the older
    `coach:{…}` line, for workouts uploaded before)."""
    text = description or ""
    meta: dict = {}
    for line in reversed(text.splitlines()):
        if line.strip().startswith("coach:"):
            try:
                meta = json.loads(line.strip()[len("coach:"):])
            except ValueError:
                pass
            break
    r = re.search(r"Readiness(?: today:)? (\d+)/100", text)
    if r:
        meta["r"] = int(r.group(1))
    if NO_SLEEP in text:
        meta["sr"] = 0
    at = re.findall(r"^Planned (\d\d:\d\d)\.$", text, flags=re.M)
    if at:
        meta["at"] = at[-1]
    return meta


# workout name (without the prefix) → (kind, sport, intensity)
_KINDS = [
    (r"^Quality", ("run_quality", "run", "hard")),
    (r"^Long run", ("run_long", "run", "moderate")),
    (r"^Easy run .*strides", ("run_strides", "run", "easy")),
    (r"^Brick run", ("run_brick", "run", "moderate")),
    (r"^Easy run", ("run_easy", "run", "easy")),
    (r"^Long ride", ("bike_long", "bike", "moderate")),
    (r"^(Easy|Openers) ride", ("bike_easy", "bike", "easy")),
    (r"^Bike .*tempo", ("bike_quality", "bike", "moderate")),
    (r"^Bike", ("bike_quality", "bike", "hard")),
    (r"^Swim .*hard", ("swim_css", "swim", "hard")),
    (r"^Endurance swim", ("swim_endurance", "swim", "moderate")),
    (r"^Technique swim", ("swim_technique", "swim", "easy")),
    (r"^Rest", ("rest", "other", "rest")),
]


def session_from(title: str, meta: dict | None = None, km: float | None = None) -> dict:
    """A planned session (as a dict) from the workout's name and details."""
    meta = meta or {}
    kind, sport, intensity = next((v for pat, v in _KINDS if re.search(pat, title)), ("other", "other", "easy"))
    if meta.get("k"):            # older workouts with the coach:{…} line
        kind, sport, intensity = meta["k"], meta.get("s", sport), meta.get("i", intensity)
    if km is None:
        km = meta.get("km")
    if km is None and sport == "run":
        found = re.findall(r"(\d+(?:\.\d+)?)\s*km", title)   # the last one: "Quality · 5×1 km, 8 km"
        km = float(found[-1]) if found else None
    mins = re.search(r"(\d+)'", title)
    return {"kind": kind, "sport": sport, "title": title, "intensity": intensity,
            "distance_km": km if sport in ("run", "swim") else None,
            "duration_min": meta.get("min") or (int(mins.group(1)) if mins else None)}


class Calendar:
    """The coach's workouts in your Garmin calendar, read on demand and cached."""

    def __init__(self, api, cfg: dict):
        self.api = api
        self.prefix = (cfg.get("garmin") or {}).get("workout_prefix", "Coach") + " ·"
        self._months: dict[tuple[int, int], list[dict]] = {}
        self._details: dict[str, tuple[dict, float | None]] = {}

    def _month(self, y: int, m: int) -> list[dict]:
        if (y, m) not in self._months:
            try:
                raw = self.api.get_scheduled_workouts(y, m) or {}
                items = raw.get("calendarItems") or []
            except Exception as e:  # noqa: BLE001
                log.warning("Couldn't read the Garmin calendar for %d-%02d: %s", y, m, e)
                items = []
            self._months[(y, m)] = [i for i in items if i.get("itemType") == "workout"]
        return self._months[(y, m)]

    def _detail(self, workout_id) -> tuple[dict, float | None]:
        key = str(workout_id)
        if key not in self._details:
            try:
                w = self.api.get_workout_by_id(workout_id) or {}
                dist = w.get("estimatedDistanceInMeters")
                self._details[key] = (read_description(w.get("description")),
                                      round(dist / 1000, 2) if dist else None)
            except Exception as e:  # noqa: BLE001
                log.warning("Couldn't read Garmin workout %s: %s", workout_id, e)
                self._details[key] = ({}, None)
        return self._details[key]

    def plan(self, day: dt.date) -> list[dict]:
        """[{name, workoutId, scheduleId, meta, session}] for the coach's workouts on `day`."""
        out = []
        for item in self._month(day.year, day.month):
            title = str(item.get("title") or "")
            if str(item.get("date") or "")[:10] != day.isoformat() or not title.startswith(self.prefix):
                continue
            wid = item.get("workoutId") or find_key(item, "workoutId")
            meta, km = self._detail(wid) if wid else ({}, None)
            out.append({
                "name": title, "workoutId": wid,
                "scheduleId": item.get("id") or find_key(item, "workoutScheduleId"),
                "meta": meta,
                "session": session_from(title[len(self.prefix):].strip(), meta, km),
            })
        return out

    def active_since(self, start: dt.date, end: dt.date) -> bool:
        """Whether the coach put any workout in the calendar between start and end."""
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            if any(start.isoformat() <= str(i.get("date") or "")[:10] <= end.isoformat()
                   and str(i.get("title") or "").startswith(self.prefix) for i in self._month(y, m)):
                return True
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return False

    day = plan
