"""Activities that reach Strava but never reach Garmin (optional).

Indoor rides in MyWhoosh, Zwift and other apps often sync only to Strava, so
Garmin (and the coach) wouldn't know you trained. With Strava connected, the
coach also reads your Strava activities and matches them to Garmin's: same
sport, same day, starting within 20 minutes of each other is the same activity
(Garmin's version is kept, with its Training Effect and load). What's only in
Strava is added to what you've done: it counts for this week's plan, for
"already done today" and for your training load (by its duration, since
Strava has no Training Effect).

Setup (once): run `python -m coach strava` on your computer. It walks you
through creating a Strava API app and prints the three GitHub secrets:
STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET and STRAVA_REFRESH_TOKEN.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)
ENV = ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN")
TOKEN_URL = "https://www.strava.com/oauth/token"
API = "https://www.strava.com/api/v3"
MATCH_MINUTES = 20

SPORTS = {
    "run": ("Run", "TrailRun", "VirtualRun"),
    "bike": ("Ride", "VirtualRide", "GravelRide", "MountainBikeRide", "EBikeRide", "EMountainBikeRide",
             "Velomobile", "Handcycle"),
    "swim": ("Swim",),
    "strength": ("WeightTraining", "Crossfit", "Workout", "HighIntensityIntervalTraining"),
}


def configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in ENV)


def _http(url: str, data: dict | None = None, token: str | None = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET",
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


# Strava replaces the refresh token now and then (the old one stops working), so the
# latest one is kept next to the Garmin session, encrypted with the same key.
TOKEN_FILE = Path(__file__).resolve().parent.parent / "data" / "strava_token.enc"


def _saved_refresh() -> str | None:
    from .garmin_api import KEY_ENV, _fernet
    key = os.environ.get(KEY_ENV)
    try:
        return _fernet(key).decrypt(TOKEN_FILE.read_bytes()).decode() if key and TOKEN_FILE.exists() else None
    except Exception:  # noqa: BLE001 — e.g. a new key: fall back to the secret
        return None


def _save_refresh(token: str) -> None:
    from .garmin_api import KEY_ENV, _fernet
    key = os.environ.get(KEY_ENV)
    if key:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_bytes(_fernet(key).encrypt(token.encode()))


def access_token(http=_http) -> str:
    cid, secret, from_secret = (os.environ[k].strip() for k in ENV)
    out = None
    for refresh in dict.fromkeys(x for x in (_saved_refresh(), from_secret) if x):
        try:
            out = http(TOKEN_URL, {"client_id": cid, "client_secret": secret, "grant_type": "refresh_token",
                                   "refresh_token": refresh})
            break
        except Exception as e:  # noqa: BLE001 — try the next one
            log.info("Strava refresh token didn't work: %s", e)
    if not out:
        raise RuntimeError("Strava didn't accept the refresh token: run 'python -m coach strava' again")
    if out.get("refresh_token") and out["refresh_token"] != _saved_refresh():
        _save_refresh(out["refresh_token"])
    return out["access_token"]


def sport_of(strava_type: str) -> str:
    return next((s for s, types in SPORTS.items() if strava_type in types), "other")


def normalize(a: dict) -> dict:
    kind = a.get("sport_type") or a.get("type") or ""
    start = str(a.get("start_date_local") or "")[:19]
    return {
        "id": None, "strava_id": a.get("id"), "source": "strava",
        "name": a.get("name"), "date": start[:10], "start": start,
        "type": kind.lower(), "sport": sport_of(kind),
        "distance_km": round((a.get("distance") or 0) / 1000, 2),
        "duration_min": round((a.get("moving_time") or a.get("elapsed_time") or 0) / 60, 1),
        "avg_hr": a.get("average_heartrate"), "avg_power": a.get("average_watts"),
        "aerobic_te": None, "anaerobic_te": None, "load": None,
    }


def fetch(start: dt.date, end: dt.date, http=_http) -> list[dict]:
    """Your Strava activities from `start` to `end` (both included); [] if Strava
    isn't set up or can't be reached."""
    if not configured():
        return []
    try:
        token = access_token(http)
        after = int(dt.datetime.combine(start - dt.timedelta(days=1), dt.time()).timestamp())
        before = int(dt.datetime.combine(end + dt.timedelta(days=2), dt.time()).timestamp())
        out = []
        for page in range(1, 6):
            batch = http(f"{API}/athlete/activities?after={after}&before={before}&per_page=100&page={page}",
                         token=token) or []
            out += [normalize(a) for a in batch]
            if len(batch) < 100:
                break
        return [a for a in out if start.isoformat() <= a["date"] <= end.isoformat()]
    except Exception as e:  # noqa: BLE001 — Strava is extra; Garmin still has the rest
        log.warning("Couldn't read your Strava activities: %s", e)
        return []


def _minutes(a: dict) -> float | None:
    try:
        return dt.datetime.fromisoformat(a["start"]).timestamp() / 60
    except (KeyError, TypeError, ValueError):
        return None


def same(g: dict, s: dict) -> bool:
    """Whether a Garmin and a Strava activity are the same one."""
    if g["date"] != s["date"] or (g["sport"] != s["sport"] and "other" not in (g["sport"], s["sport"])):
        return False
    a, b = _minutes(g), _minutes(s)
    if a is not None and b is not None:
        return abs(a - b) <= MATCH_MINUTES
    # no start time: same sport, same day and about as long
    return abs((g.get("duration_min") or 0) - (s.get("duration_min") or 0)) <= 0.2 * max(s.get("duration_min") or 1, 1)


def merge(garmin: list[dict], strava: list[dict]) -> tuple[list[dict], list[dict]]:
    """Garmin's activities plus the Strava ones that aren't in Garmin.
    Returns (all of them by date, the ones only in Strava)."""
    extra = [s for s in strava if not any(same(g, s) for g in garmin)]
    return sorted(garmin + extra, key=lambda a: (a["date"], a.get("start") or "")), extra


# ── setup: python -m coach strava ──────────────────────────────────────────
def setup() -> int:
    print("Connect Strava, so the coach sees rides that only reach Strava (MyWhoosh, Zwift…).\n")
    print("1. Open https://www.strava.com/settings/api and create an app:")
    print("   any name and website; Authorization Callback Domain: localhost\n")
    cid = input("2. Your app's Client ID: ").strip()
    secret = input("   Client Secret: ").strip()
    url = ("https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": "http://localhost/exchange_token",
        "approval_prompt": "force", "scope": "activity:read_all"}))
    print(f"\n3. Open this link and press Authorize:\n   {url}\n")
    print("   Your browser then goes to a page that doesn't load (localhost): that's fine.")
    back = input("   Paste that page's whole address here: ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(back).query).get("code", [back])[0]
    out = _http(TOKEN_URL, {"client_id": cid, "client_secret": secret, "code": code,
                            "grant_type": "authorization_code"})
    if "activity:read" not in str(out.get("scope", "activity:read_all")) and not out.get("refresh_token"):
        print("Strava didn't give access to your activities. Try again and keep 'View data about your activities' ticked.")
        return 1
    print("\n4. Add these three secrets in GitHub (your repository → Settings → Secrets and variables → Actions):")
    print(f"   STRAVA_CLIENT_ID      {cid}")
    print(f"   STRAVA_CLIENT_SECRET  {secret}")
    print(f"   STRAVA_REFRESH_TOKEN  {out['refresh_token']}")
    print("\nDone: from the next run, rides only in Strava count as training.")
    return 0
