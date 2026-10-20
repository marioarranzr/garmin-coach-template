"""Optional push notification with today's plan, so you can see it on your watch.

Your watch mirrors your phone's notifications, so a push from the free ntfy app
(https://ntfy.sh, no account) lands on your wrist as soon as the coach plans the
day, and stays in the watch's notification list. Set the NTFY_TOPIC secret to a
long random name (it's the only thing protecting the topic) and subscribe to it
in the app. NTFY_SERVER points to your own ntfy server if you run one.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger(__name__)
TOPIC_ENV = "NTFY_TOPIC"
SERVER_ENV = "NTFY_SERVER"


def message(final, why: str, coach_note: str, r: dict, label: str = "Today", ahead=(),
            done=()) -> tuple[str, str]:
    """`label`: Tomorrow (evening plan), Today, Changed (the morning check changed
    last night's plan) or Updated (a later re-plan)."""
    sessions = list(done) + [s.title for s in final] or ["Nothing left: already done 🎉"]
    title = f"{label}: " + " + ".join(sessions)
    lines = [x for x in (why, coach_note) if x]
    lines.append("It's in your Garmin calendar; the morning check adjusts it to your sleep." if r["score"] is None
                 else f"Readiness {r['score']}/100. The workout is in your Garmin calendar.")
    if ahead:
        # the coming days, one per line (no "Next:", no semicolons): easy to scan on a watch
        lines.append("\n".join(f"{day:%a}: " + " + ".join(s.title for s in sess) for day, _, sess, _ in ahead))
    return title[:120], "\n\n".join(lines)


def send(title: str, body: str, poster=None) -> bool:
    topic = os.environ.get(TOPIC_ENV, "").strip()
    if not topic:
        return False
    server = (os.environ.get(SERVER_ENV) or "https://ntfy.sh").rstrip("/")
    payload = {"topic": topic, "title": title, "message": body, "tags": ["runner"]}
    try:
        if poster:
            poster(server, payload)
        else:
            req = urllib.request.Request(server, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception as e:  # noqa: BLE001 — a missed notification never fails the run
        log.warning("Couldn't send the notification: %s", e)
        return False
