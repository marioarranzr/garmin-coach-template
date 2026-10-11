"""The coach's memory lives in your Garmin calendar, not in files.

Every workout the coach uploads ends its description with one short line,
`coach:{...}`, that records what it planned (kind, sport, distance or minutes,
intensity), the readiness score, whether last night's sleep was in Garmin, and
which of your notes it saw and used. Reading the calendar back gives the coach
everything it needs from earlier runs: yesterday's plan (to check compliance),
this week's plan so far, what to replace when it re-plans today, and what it
planned on the days of your older notes. Nothing is stored in the repository.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

from .garmin_api import find_key

log = logging.getLogger(__name__)
TAG = "coach:"
MAX_DESCRIPTION = 1000


def marker(s, extra: dict) -> str:
    data = {"k": s.kind, "s": s.sport, "i": s.intensity}
    if s.distance_km:
        data["km"] = round(s.distance_km, 2)
    if s.duration_min:
        data["min"] = round(s.duration_min)
    data.update({k: v for k, v in extra.items() if v not in (None, [], "")})
    return TAG + json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def with_marker(text: str, tag: str) -> str:
    """The human description, shortened if needed so the marker always fits."""
    room = MAX_DESCRIPTION - len(tag) - 2
    if len(text) > room:
        text = text[:room - 1].rstrip() + "…"
    return f"{text}\n\n{tag}"


def parse(description: str | None) -> dict | None:
    for line in reversed((description or "").splitlines()):
        line = line.strip()
        if line.startswith(TAG):
            try:
                return json.loads(line[len(TAG):])
            except ValueError:
                return None
    return None


def _session_from(meta: dict | None, title: str, sport_key: str | None) -> dict:
    """A planned session (as a dict) from the marker, or guessed from the title
    for workouts uploaded before markers existed."""
    if meta:
        return {"kind": meta.get("k", ""), "sport": meta.get("s", "other"), "title": title,
                "intensity": meta.get("i", "easy"), "distance_km": meta.get("km"), "duration_min": meta.get("min")}
    sport = {"running": "run", "cycling": "bike", "swimming": "swim"}.get((sport_key or "").lower(), "other")
    low = title.lower()
    if sport == "other":
        sport = "bike" if ("bike" in low or "ride" in low) else "swim" if "swim" in low else \
                "run" if ("run" in low or "quality" in low) else "other"
    km = re.search(r"(\d+(?:\.\d+)?)\s*km", title)
    kind = ("run_long" if "long run" in low else "run_quality" if "quality" in low else
            f"{sport}_easy" if sport in ("run", "bike") else "swim_technique" if sport == "swim" else "rest")
    return {"kind": kind, "sport": sport, "title": title, "intensity": "hard" if "quality" in low else "easy",
            "distance_km": float(km.group(1)) if km and sport == "run" else None, "duration_min": None}


class Calendar:
    """The coach's workouts in your Garmin calendar, read on demand and cached."""

    def __init__(self, api, cfg: dict):
        self.api = api
        self.prefix = (cfg.get("garmin") or {}).get("workout_prefix", "Coach") + " ·"
        self._months: dict[tuple[int, int], list[dict]] = {}
        self._details: dict[str, dict] = {}

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

    def _meta(self, workout_id) -> dict | None:
        key = str(workout_id)
        if key not in self._details:
            try:
                self._details[key] = parse((self.api.get_workout_by_id(workout_id) or {}).get("description")) or {}
            except Exception as e:  # noqa: BLE001
                log.warning("Couldn't read Garmin workout %s: %s", workout_id, e)
                self._details[key] = {}
        return self._details[key] or None

    def plan(self, day: dt.date) -> list[dict]:
        """[{name, workoutId, scheduleId, meta, session}] for the coach's workouts on `day`
        (the month listing is cached; each workout's marker is read once)."""
        out = []
        for item in self._month(day.year, day.month):
            title = str(item.get("title") or "")
            if str(item.get("date") or "")[:10] != day.isoformat() or not title.startswith(self.prefix):
                continue
            wid = item.get("workoutId") or find_key(item, "workoutId")
            meta = self._meta(wid) if wid else None
            out.append({
                "name": title, "workoutId": wid,
                "scheduleId": item.get("id") or find_key(item, "workoutScheduleId"),
                "meta": meta or {},
                "session": _session_from(meta, title[len(self.prefix):].strip(), item.get("sportTypeKey")),
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
