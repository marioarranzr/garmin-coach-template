"""The coach's memory lives in your Garmin calendar, not in files.

Everything it needs from an earlier run is in the workouts it uploaded, in text
you can read anyway:
- the workout name ("Coach · Long run 12 km") gives the kind of session, its
  sport and distance (also stored as the workout's estimated distance);
- "Readiness 63/100" gives that day's readiness;
- the footer says whether it was "Planned the evening before" (not checked
  yet) or "Checked 08:03" that morning, so a note added later is spotted as new;
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
    room = MAX_DESCRIPTION - (len(footer) + 2 if footer else 0)
    if len(body) > room:
        body = body[:room - 1].rstrip() + "…"
    return f"{body}\n\n{footer}" if footer else body


EVENING = "Planned the evening before"


def footer(phase_text: str, planned_at: dt.datetime, sleep_ready: bool, evening: bool = False,
           seen: int | None = None) -> str:
    """The last lines of a workout's description, which the coach reads back:
    the evening plan says so; the morning check says when it checked, and how
    many of that day's activities it had seen (a new one means: check again)."""
    if evening:
        return f"{phase_text}\n{EVENING} ({planned_at:%a %H:%M}); checked again in the morning."
    lines = [] if sleep_ready else [NO_SLEEP]
    lines += [f"{phase_text}", f"Checked {planned_at:%H:%M}."]
    if seen is not None:
        lines.append(f"Activities seen today: {seen}.")
    return "\n".join(lines)


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
    at = re.findall(r"^(?:Planned|Checked) (\d\d:\d\d)\.$", text, flags=re.M)
    if at:
        meta["at"] = at[-1]              # checked that morning (or later that day)
    if EVENING in text:
        meta["eve"] = 1                  # planned the evening before, not checked yet
        when = re.search(re.escape(EVENING) + r" \(\w{3} (\d\d:\d\d)\)", text)
        if when:
            meta["eve_at"] = when.group(1)   # when, the evening before (to spot notes written later)
    seen = re.findall(r"^Activities seen today: (\d+)\.$", text, flags=re.M)
    if seen:
        meta["seen"] = int(seen[-1])
    return meta


# workout name (without the prefix) → (kind, sport, intensity)
_KINDS = [
    (r"^Quality", ("run_quality", "run", "hard")),
    (r"^Long run", ("run_long", "run", "moderate")),
    (r"^Easy run .*strides", ("run_strides", "run", "easy")),
    (r"^Brick run", ("run_brick", "run", "moderate")),
    (r"^Easy run", ("run_easy", "run", "easy")),
    (r"^Long ride", ("bike_long", "bike", "moderate")),
    (r"^Recovery spin", ("bike_spin", "bike", "easy")),
    (r"^(Easy|Openers) ride", ("bike_easy", "bike", "easy")),
    (r"^Bike .*tempo", ("bike_quality", "bike", "moderate")),
    (r"^Bike", ("bike_quality", "bike", "hard")),
    (r"^Swim .*hard", ("swim_css", "swim", "hard")),
    (r"^Endurance swim", ("swim_endurance", "swim", "moderate")),
    (r"^Technique swim", ("swim_technique", "swim", "easy")),
    (r"^Strength & core", ("strength_core", "strength", "easy")),
    (r"^Strength", ("strength", "strength", "moderate")),
    (r"^Rest", ("rest", "other", "rest")),
]


def session_from(title: str, meta: dict | None = None, km: float | None = None) -> dict:
    """A planned session (as a dict) from the workout's name and details."""
    meta = meta or {}
    optional = title.startswith("Optional · ") or title.endswith(" (optional)")
    bare = title[len("Optional · "):] if title.startswith("Optional · ") else title
    bare = bare.removesuffix(" (optional)")
    kind, sport, intensity = next((v for pat, v in _KINDS if re.search(pat, bare)), ("other", "other", "easy"))
    if meta.get("k"):            # older workouts with the coach:{…} line
        kind, sport, intensity = meta["k"], meta.get("s", sport), meta.get("i", intensity)
    if km is None:
        km = meta.get("km")
    if km is None and sport == "run":
        found = re.findall(r"(\d+(?:\.\d+)?)\s*km", title)   # the last one: "Quality · 5×1 km, 8 km"
        km = float(found[-1]) if found else None
    mins = re.search(r"(\d+)'", title)
    return {"kind": kind, "sport": sport, "title": title, "intensity": "easy" if optional else intensity,
            "optional": optional,
            "distance_km": km if sport in ("run", "swim") else None,
            "duration_min": meta.get("min") or (int(mins.group(1)) if mins else None)}


class Calendar:
    """The coach's workouts in your Garmin calendar, read on demand and cached."""

    def __init__(self, api, cfg: dict):
        self.api = api
        base = (cfg.get("garmin") or {}).get("workout_prefix", "Coach")
        self.prefix = base + " ·"
        self.draft_prefix = base + " draft ·"
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

    def plan(self, day: dt.date, draft: bool = False) -> list[dict]:
        """[{name, workoutId, scheduleId, meta, session}] for the coach's workouts on `day`
        (its drafts for the days ahead with `draft`)."""
        prefix = self.draft_prefix if draft else self.prefix
        out = []
        for item in self._month(day.year, day.month):
            title = str(item.get("title") or "")
            if str(item.get("date") or "")[:10] != day.isoformat() or not title.startswith(prefix):
                continue
            wid = item.get("workoutId") or find_key(item, "workoutId")
            meta, km = self._detail(wid) if (wid and not draft) else ({}, None)
            out.append({
                "name": title, "workoutId": wid,
                "scheduleId": item.get("id") or find_key(item, "workoutScheduleId"),
                "meta": meta,
                "session": session_from(title[len(prefix):].strip(), meta, km),
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
