"""Your own notes on how you feel, read from your Todoist Inbox.

Notes added to your Inbox in the last 12 hours (by voice, from a gadget, or by
hand) are passed to the AI coach, including ones already marked done. The
notes the coach uses are then marked done (completed, never deleted).
"""
from __future__ import annotations

import datetime as dt
import logging
import os

log = logging.getLogger(__name__)
TOKEN_ENV = "TODOIST_API_TOKEN"


def _as_local(value) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)   # to local time (TZ of the run)
    return value


def _find_project(api, wanted: str):
    wanted = wanted.lower()
    for page in api.get_projects():
        for p in page:
            if (wanted == "inbox" and getattr(p, "is_inbox_project", False)) or p.name.lower() == wanted:
                return p.id
    return None


def _note(t, now: dt.datetime, done: bool) -> dict | None:
    created = _as_local(getattr(t, "created_at", None))
    if created is None:
        return None
    text = (t.content or "").strip()
    if getattr(t, "description", None):
        text += " — " + t.description.strip()
    when = created.strftime("%H:%M") if created.date() == now.date() else created.strftime("%a %H:%M")
    return {"id": str(t.id), "time": when, "created": created.isoformat(timespec="minutes"),
            "text": text[:500], "done": done}


def recent_notes(cfg: dict, now: dt.datetime, api=None) -> list[dict]:
    """Notes created in the last `lookback_hours` (default 12) in the configured
    project, both open ones and ones already marked done (by the coach or you)."""
    settings = cfg.get("todoist") or {}
    token = os.environ.get(TOKEN_ENV)
    if not settings.get("enabled", True) or (api is None and not token):
        return []
    try:
        if api is None:
            from todoist_api_python.api import TodoistAPI
            api = TodoistAPI(token)
        project_id = _find_project(api, str(settings.get("project", "Inbox")))
        if not project_id:
            log.warning("Todoist project %r not found", settings.get("project", "Inbox"))
            return []
        since = now - dt.timedelta(hours=float(settings.get("lookback_hours", 12)))
        until = now + dt.timedelta(minutes=5)

        def recent(n):
            return n and since <= dt.datetime.fromisoformat(n["created"]) <= until

        notes = [n for page in api.get_tasks(project_id=project_id) for t in page
                 if recent(n := _note(t, now, done=False))]
        aware = lambda x: x.astimezone().astimezone(dt.timezone.utc)   # local → UTC for the API
        try:
            for page in api.get_completed_tasks_by_completion_date(since=aware(since), until=aware(until)):
                for t in page:
                    if str(getattr(t, "project_id", "")) == str(project_id) and recent(n := _note(t, now, done=True)):
                        notes.append(n)
        except Exception as e:  # noqa: BLE001 — open notes are still useful on their own
            log.warning("Couldn't read completed Todoist notes: %s", e)
        notes.sort(key=lambda n: n["created"])
        return notes[-15:]
    except Exception as e:  # noqa: BLE001 — notes are optional
        log.warning("Couldn't read Todoist notes: %s", e)
        return []


todays_notes = recent_notes   # old name


def complete_notes(ids: list[str], api=None) -> list[str]:
    """Completes (not deletes) the notes the coach used, so they leave your Inbox
    but stay in Todoist's completed history. Returns the ids completed."""
    if not ids:
        return []
    token = os.environ.get(TOKEN_ENV)
    if api is None and not token:
        return []
    done = []
    try:
        if api is None:
            from todoist_api_python.api import TodoistAPI
            api = TodoistAPI(token)
        for task_id in ids:
            try:
                api.complete_task(task_id)
                done.append(task_id)
            except Exception as e:  # noqa: BLE001
                log.warning("Couldn't complete Todoist note %s: %s", task_id, e)
    except Exception as e:  # noqa: BLE001
        log.warning("Couldn't complete Todoist notes: %s", e)
    return done
