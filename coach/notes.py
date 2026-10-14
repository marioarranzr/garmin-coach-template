"""Your own notes on how you feel, read from your Todoist Inbox.

Notes added to your Inbox in the last 14 hours (since the evening before) (by voice, from a gadget, or by
hand) are passed to the AI coach, including ones already marked done. The
notes the coach uses are then labelled `coach` and marked done (completed, never
deleted). Those labelled, completed notes are your training diary: the coach
reads the last weeks of them back from Todoist to learn how you respond.
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
    due = getattr(t, "due", None)
    return {"id": str(t.id), "time": when, "created": created.isoformat(timespec="minutes"),
            "text": text[:500], "done": done, "recurring": bool(due and getattr(due, "is_recurring", False)),
            "labels": list(getattr(t, "labels", None) or [])}


def diary_label(cfg: dict) -> str:
    return str((cfg.get("todoist") or {}).get("diary_label", "coach"))


def _client(api):
    if api is not None:
        return api
    token = os.environ.get(TOKEN_ENV)
    if not token:
        return None
    from todoist_api_python.api import TodoistAPI
    return TodoistAPI(token)


def diary(cfg: dict, start: dt.date, d: dt.date, now: dt.datetime, api=None) -> list[dict]:
    """Notes the coach used from `start` until the day before d (the same window
    the coach uses for your activities): completed tasks in the notes project
    that carry the diary label."""
    settings = cfg.get("todoist") or {}
    if not settings.get("enabled", True):
        return []
    try:
        api = _client(api)
        if api is None:
            return []
        project_id = _find_project(api, str(settings.get("project", "Inbox")))
        if not project_id:
            return []
        since = dt.datetime.combine(start, dt.time())
        aware = lambda x: x.astimezone().astimezone(dt.timezone.utc)
        label = diary_label(cfg)
        out = []
        for page in api.get_completed_tasks_by_completion_date(since=aware(since), until=aware(now)):
            for t in page:
                if str(getattr(t, "project_id", "")) != str(project_id) or label not in (getattr(t, "labels", None) or []):
                    continue
                n = _note(t, now, done=True)
                if n and n["created"][:10] < d.isoformat():
                    out.append({"date": n["created"][:10], "time": n["created"][11:16], "text": n["text"]})
        return sorted(out, key=lambda e: (e["date"], e["time"]))
    except Exception as e:  # noqa: BLE001 — the diary is optional
        log.warning("Couldn't read your training diary from Todoist: %s", e)
        return []


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
        since = now - dt.timedelta(hours=float(settings.get("lookback_hours", 14)))
        until = now + dt.timedelta(minutes=5)

        def recent(n):
            return n and since <= dt.datetime.fromisoformat(n["created"]) <= until

        notes = [n for page in api.get_tasks(project_id=project_id) for t in page
                 if PLAN_LABEL not in (getattr(t, "labels", None) or []) and recent(n := _note(t, now, done=False))]
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


def complete_notes(ids: list[str], api=None, recurring: set[str] | frozenset = frozenset(),
                   labels: dict[str, list[str]] | None = None, label: str = "coach") -> list[str]:
    """Completes (not deletes) the notes the coach used, so they leave your Inbox
    but stay in Todoist's completed history. Returns the ids completed.

    Completing a *recurring* task in Todoist only moves it to its next date, so
    for those the date is removed first. That's safe because only tasks created
    in the last few hours are ever read as notes: a real routine created long
    ago is never touched.

    Each one gets the diary `label` first, so it can be found later as part
    of your training diary."""
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
                current = list((labels or {}).get(task_id, []))
                changes = {} if label in current else {"labels": current + [label]}
                if task_id in recurring:
                    changes["due_string"] = "no date"
                if changes:
                    try:
                        api.update_task(task_id, **changes)
                    except Exception as e:  # noqa: BLE001 — still complete it
                        log.warning("Couldn't label Todoist note %s: %s", task_id, e)
                        if task_id in recurring:
                            api.update_task(task_id, due_string="no date")
                api.complete_task(task_id)
                done.append(task_id)
            except Exception as e:  # noqa: BLE001
                log.warning("Couldn't complete Todoist note %s: %s", task_id, e)
    except Exception as e:  # noqa: BLE001
        log.warning("Couldn't complete Todoist notes: %s", e)
    return done


PLAN_LABEL = "coach-plan"


PLAN_MARK = "(Garmin Coach plan)"


def _due(t) -> dt.date | None:
    due = getattr(t, "due", None)
    v = getattr(due, "date", None) if due is not None else getattr(t, "due_date", None)
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def sync_plan(cfg: dict, days: list[tuple], api=None) -> int:
    """Your coming days as Todoist tasks, so the week is easy to see: one task per
    day ("Quality · Rolling 400s, 8 km"), due that day, in the `plan_project`
    project (created if needed). `days`: [(date, sessions, why)], from the first
    day being planned.

    Days before that are over: their tasks are completed (so the evening plan
    ticks off today, and in the morning today's task is on top, not yesterday's).
    The coach's tasks from that day on are replaced. Only the coach's own tasks
    are touched (coach-plan label, and a note at the end of the description).
    Returns the number of tasks written."""
    settings = cfg.get("todoist") or {}
    name = settings.get("plan_project", "Training plan")
    if not settings.get("enabled", True) or not name or not days:
        return 0
    try:
        api = _client(api)
        if api is None:
            return 0
        pid = _find_project(api, str(name))
        if not pid:
            pid = api.add_project(name=str(name)).id
        days = [tuple(x) + ((),) * (4 - len(x)) for x in days]       # (day, sessions, why, done)
        first = min(x[0] for x in days)
        for page in api.get_tasks(project_id=pid):
            for t in page:
                ours = PLAN_LABEL in (getattr(t, "labels", None) or []) or \
                    (getattr(t, "description", "") or "").rstrip().endswith(PLAN_MARK)
                if not ours:
                    continue
                due = _due(t)
                if due is not None and due < first:
                    api.complete_task(t.id)          # that day is over
                else:
                    api.delete_task(t.id)            # replaced below
        n = 0
        for day, sessions, why, done in sorted(days, key=lambda x: x[0]):
            titles = " + ".join(list(done) + [x.title for x in sessions]) or "Rest"
            details = "\n\n".join(x for x in [why] + list(done)
                                    + [f"{x.title}\n{x.detail}" for x in sessions] if x)
            api.add_task(content=titles[:480], project_id=pid, due_date=day, labels=[PLAN_LABEL],
                         description=(details[:3900] + "\n\n" + PLAN_MARK).strip())
            n += 1
        return n
    except Exception as e:  # noqa: BLE001 — the calendar has the plan anyway
        log.warning("Couldn't update your training plan in Todoist: %s", e)
        return 0


PREFS_TITLE = "📌 Coach preferences"
PREFS_LABEL = "coach-prefs"


def _plan_project(api, cfg: dict, create: bool = False):
    name = (cfg.get("todoist") or {}).get("plan_project", "Training plan")
    if not name:
        return None
    pid = _find_project(api, str(name))
    if not pid and create:
        pid = api.add_project(name=str(name)).id
    return pid


def read_preferences(cfg: dict, api=None) -> tuple[str | None, list[str]]:
    """Your lasting preferences ("1' walk before strides", "long runs on Saturday"),
    kept in one Todoist task in the plan project. You can edit it there: one per line."""
    if not (cfg.get("todoist") or {}).get("enabled", True):
        return None, []
    try:
        api = _client(api)
        pid = _plan_project(api, cfg) if api is not None else None
        if not pid:
            return None, []
        for page in api.get_tasks(project_id=pid):
            for t in page:
                if PREFS_LABEL in (getattr(t, "labels", None) or []) or t.content == PREFS_TITLE:
                    lines = [x.strip().lstrip("•-* ").strip() for x in (t.description or "").splitlines()]
                    return str(t.id), [x for x in lines if x]
    except Exception as e:  # noqa: BLE001
        log.warning("Couldn't read your coach preferences from Todoist: %s", e)
    return None, []


def save_preferences(cfg: dict, prefs: list[str], task_id: str | None, api=None) -> bool:
    try:
        api = _client(api)
        if api is None:
            return False
        desc = "\n".join(f"• {x}" for x in prefs)[:4000]
        if task_id:
            api.update_task(task_id, description=desc)
        else:
            pid = _plan_project(api, cfg, create=True)
            if not pid:
                return False
            api.add_task(content=PREFS_TITLE, project_id=pid, labels=[PREFS_LABEL], description=desc)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Couldn't save your coach preferences in Todoist: %s", e)
        return False
