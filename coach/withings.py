"""Optional: your Withings scale (weight, fat, muscle, water, bone...) read from Withings' API.

`python -m coach withings` on your computer connects it once (a Withings developer app, like Strava).
After that each run reads the last weigh-ins into `data/coach_log/body.json` (see body.py).
Withings replaces the refresh token on every refresh (the old one works 8 more hours), so the latest one is kept
in `data/withings_token.enc`, encrypted with the same key as the Garmin session.
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
ENV = ("WITHINGS_CLIENT_ID", "WITHINGS_CLIENT_SECRET", "WITHINGS_REFRESH_TOKEN")
AUTH_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
TOKEN_FILE = Path(__file__).resolve().parent.parent / "data" / "withings_token.enc"
REDIRECT = "https://localhost/exchange_token"
# Withings measure type -> field in body.json
TYPES = {1: "weight_kg", 8: "fat_kg", 6: "fat_pct", 76: "muscle_kg", 5: "lean_kg", 77: "water_kg", 88: "bone_kg",
         170: "visceral", 226: "bmr_kcal"}


def configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in ENV)


def _http(url: str, data: dict | None = None, token: str | None = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET",
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


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


def _check(out: dict) -> dict:
    if out.get("status") != 0:
        raise RuntimeError(f"Withings answered status {out.get('status')}: {out.get('error', '')}")
    return out.get("body") or {}


def access_token(http=_http) -> str:
    cid, secret, from_secret = (os.environ[k].strip() for k in ENV)
    last = None
    for refresh in dict.fromkeys(x for x in (_saved_refresh(), from_secret) if x):
        try:
            body = _check(http(TOKEN_URL, {"action": "requesttoken", "client_id": cid, "client_secret": secret,
                                           "grant_type": "refresh_token", "refresh_token": refresh}))
            if body.get("refresh_token") and body["refresh_token"] != _saved_refresh():
                _save_refresh(body["refresh_token"])
            return body["access_token"]
        except Exception as e:  # noqa: BLE001 — try the next one
            last = e
            log.info("Withings refresh token didn't work: %s", e)
    raise RuntimeError(f"Withings didn't accept the refresh token: run 'python -m coach withings' again ({last})")


def parse(groups: list[dict]) -> list[dict]:
    """Withings measure groups -> body entries (one per day: the last weigh-in of the day)."""
    by_day: dict[str, tuple[int, dict]] = {}
    for g in groups or []:
        day = dt.datetime.fromtimestamp(g.get("date", 0), dt.timezone.utc).date().isoformat()
        row = {}
        for m in g.get("measures") or []:
            key = TYPES.get(m.get("type"))
            if key:
                row[key] = round(m["value"] * 10 ** m.get("unit", 0), 2)
        if row and (day not in by_day or g["date"] >= by_day[day][0]):
            by_day[day] = (g["date"], row)
    return [{"date": day, **row} for day, (_, row) in sorted(by_day.items())]


def fetch(since: dt.date, http=_http) -> list[dict]:
    """Weigh-ins from `since` on, as body entries; [] if Withings isn't set up or can't be reached."""
    if not configured():
        return []
    try:
        token = access_token(http)
        start = int(dt.datetime.combine(since, dt.time(), dt.timezone.utc).timestamp())
        body = _check(http(MEASURE_URL, {"action": "getmeas", "startdate": start, "enddate": int(dt.datetime.now().timestamp()) + 86400,
                                         "meastypes": ",".join(map(str, TYPES))}, token=token))
        return parse(body.get("measuregrps"))
    except Exception as e:  # noqa: BLE001 — the scale is extra
        log.warning("Couldn't read your Withings scale: %s", e)
        return []


def setup() -> int:
    print("Connect your Withings scale, so the coach sees your weight and body composition.\n")
    print("1. Open https://developer.withings.com/dashboard/ (sign in with your Withings account) and create an")
    print(f"   application (Public API integration). Callback URL: {REDIRECT}\n")
    cid = input("2. Your app's Client ID: ").strip()
    secret = input("   Client Secret: ").strip()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cid, "scope": "user.metrics", "redirect_uri": REDIRECT, "state": "coach"})
    print(f"\n3. Open this link, choose your account and press Allow:\n   {url}\n")
    print("   Your browser then goes to a page that doesn't load (localhost): that's fine.")
    print("   Do it quickly: the code in the address is valid for 30 seconds.")
    back = input("   Paste that page's whole address here: ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(back).query).get("code", [back])[0]
    try:
        body = _check(_http(TOKEN_URL, {"action": "requesttoken", "client_id": cid, "client_secret": secret,
                                        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT}))
    except Exception as e:  # noqa: BLE001
        print(f"Withings didn't accept it ({e}). Try again, pasting the address faster.")
        return 1
    print("\n4. Add these three secrets in GitHub (your repository → Settings → Secrets and variables → Actions):")
    print(f"   WITHINGS_CLIENT_ID      {cid}")
    print(f"   WITHINGS_CLIENT_SECRET  {secret}")
    print(f"   WITHINGS_REFRESH_TOKEN  {body['refresh_token']}")
    print("\nDone: from the next run your weigh-ins are read automatically.")
    return 0
