"""Your own notes on how you feel, read from your Todoist Inbox.

Anything you add to the Inbox in the morning (by voice, from a gadget, or by
hand) is passed to the AI coach. Only notes created since early this morning
are read, and nothing in Todoist is changed.
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


def todays_notes(cfg: dict, now: dt.datetime, api=None) -> list[dict]:
    """Notes created today since `from_hour` (default 03:00) in the configured project."""
    settings = cfg.get("todoist") or {}
    token = os.environ.get(TOKEN_ENV)
    if not settings.get("enabled", True) or (api is None and not token):
        return []
    try:
        if api is None:
            from todoist_api_python.api import TodoistAPI
            api = TodoistAPI(token)
        wanted = str(settings.get("project", "Inbox")).lower()
        project_id = None
        for page in api.get_projects():
            for p in page:
                if (wanted == "inbox" and getattr(p, "is_inbox_project", False)) or p.name.lower() == wanted:
                    project_id = p.id
                    break
            if project_id:
                break
        if not project_id:
            log.warning("Todoist project %r not found", settings.get("project", "Inbox"))
            return []
        since = now.replace(hour=int(settings.get("from_hour", 3)), minute=0, second=0, microsecond=0)
        notes = []
        for page in api.get_tasks(project_id=project_id):
            for t in page:
                created = _as_local(getattr(t, "created_at", None))
                if created and since <= created <= now + dt.timedelta(minutes=5):
                    text = (t.content or "").strip()
                    if t.description:
                        text += " — " + t.description.strip()
                    notes.append({"id": str(t.id), "time": created.strftime("%H:%M"), "text": text[:500]})
        notes.sort(key=lambda n: n["time"])
        return notes[-10:]
    except Exception as e:  # noqa: BLE001 — notes are optional
        log.warning("Couldn't read Todoist notes: %s", e)
        return []
