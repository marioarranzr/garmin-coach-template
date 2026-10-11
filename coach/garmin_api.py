"""Garmin Connect access and normalization of the data the coach uses."""
from __future__ import annotations

import datetime as dt
import getpass
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RUN_TYPES = {"running", "treadmill_running", "trail_running", "track_running",
             "street_running", "indoor_running", "virtual_run", "ultra_run"}
BIKE_TYPES = {"cycling", "road_biking", "indoor_cycling", "virtual_ride",
              "mountain_biking", "gravel_cycling", "e_bike_fitness", "cyclocross"}
SWIM_TYPES = {"lap_swimming", "open_water_swimming", "swimming"}


# ── helpers ─────────────────────────────────────────────────────────────────
def find_key(obj: Any, key: str) -> Any:
    """Recursively finds the first occurrence of `key` (Garmin often changes the
    shape of its responses, so we don't rely on exact paths)."""
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def _safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        log.warning("Garmin: %s%s failed: %s", getattr(fn, "__name__", fn), args, e)
        return default


def sport_of(type_key: str) -> str:
    if type_key in RUN_TYPES or "run" in type_key:
        return "run"
    if type_key in BIKE_TYPES or "cycl" in type_key or "bik" in type_key:
        return "bike"
    if type_key in SWIM_TYPES or "swim" in type_key:
        return "swim"
    return "other"


# ── encrypted tokens (for GitHub Actions) ───────────────────────────────────
TOKENS_ENC = Path(__file__).resolve().parent.parent / "data" / "garmin_tokens.enc"
KEY_ENV = "GARMIN_TOKENS_KEY"


def _fernet(key: str):
    from cryptography.fernet import Fernet
    return Fernet(key.strip().encode())


def cloud_mode() -> bool:
    return bool(os.environ.get(KEY_ENV))


def save_cloud_tokens(api) -> None:
    """Saves the (refreshed) tokens encrypted with the key from the GitHub secret."""
    key = os.environ.get(KEY_ENV)
    if not key:
        return
    TOKENS_ENC.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_ENC.write_bytes(_fernet(key).encrypt(api.client.dumps().encode()))


def export_tokens(tokenstore: str) -> str:
    """Encrypts the tokens from the local login and returns the key for GitHub."""
    from cryptography.fernet import Fernet

    api = connect(tokenstore)  # same checks and messages as a normal run
    key = Fernet.generate_key().decode()
    TOKENS_ENC.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_ENC.write_bytes(_fernet(key).encrypt(api.client.dumps().encode()))
    return key


# ── login ───────────────────────────────────────────────────────────────────
def connect(tokenstore: str, interactive: bool = False):
    from garminconnect import Garmin

    if cloud_mode() and not interactive:
        if not TOKENS_ENC.exists():
            raise SystemExit("data/garmin_tokens.enc is missing: run 'python -m coach export-tokens' on your computer.")
        tokens = _fernet(os.environ[KEY_ENV]).decrypt(TOKENS_ENC.read_bytes()).decode()
        api = Garmin()
        api.login(tokens)
        return api

    path = str(Path(tokenstore).expanduser())
    token_file = Path(path) / "garmin_tokens.json"
    if not interactive:
        if not token_file.exists():
            raise SystemExit(
                f"No saved Garmin session ({token_file} not found).\n"
                "Run 'python -m coach login' first and complete it (email, password, MFA code)."
            )
        api = Garmin()
        try:
            api.login(path)  # uses the saved tokens
        except Exception as e:  # noqa: BLE001
            raise SystemExit(
                f"The saved Garmin session could not be used ({e}).\n"
                "Run 'python -m coach login' again."
            ) from None
        return api

    email = input("Garmin Connect email: ").strip()
    password = getpass.getpass("Password (not stored, nothing shows while typing): ")
    api = Garmin(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    Path(path).mkdir(parents=True, exist_ok=True)
    api.login(path)
    try:
        api.client.dump(path)
    except Exception:  # noqa: BLE001
        pass
    if not _session_reusable(path):
        raise SystemExit(
            "Logged in, but Garmin didn't return a session that can be saved and reused, "
            "so the daily run wouldn't work.\nUpdate the library and try again: "
            "pip install -U garminconnect"
        )
    return api


def _session_reusable(path: str) -> bool:
    """Checks the saved session file holds reusable tokens (never the password)."""
    import json
    f = Path(path) / "garmin_tokens.json"
    try:
        data = json.loads(f.read_text())
        return bool(data.get("di_token") or data.get("di_refresh_token"))
    except Exception:  # noqa: BLE001
        return False


# ── data ────────────────────────────────────────────────────────────────────
def normalize_activity(a: dict) -> dict:
    start = a.get("startTimeLocal") or a.get("startTimeGMT") or ""
    type_key = (a.get("activityType") or {}).get("typeKey", "")
    return {
        "id": a.get("activityId"),
        "name": a.get("activityName"),
        "date": start[:10],
        "type": type_key,
        "sport": sport_of(type_key),
        "distance_km": round((a.get("distance") or 0) / 1000, 2),
        "duration_min": round((a.get("duration") or 0) / 60, 1),
        "avg_hr": a.get("averageHR"),
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "anaerobic_te": a.get("anaerobicTrainingEffect"),
        "load": a.get("activityTrainingLoad"),
        "pool_length_m": _pool_length_m(a),
    }


def _pool_length_m(a: dict) -> float | None:
    """Pool length of a pool swim in metres (Garmin stores it in cm or yard-hundredths)."""
    length = a.get("poolLength")
    if not length:
        return None
    unit = a.get("unitOfPoolLength") or {}
    value = length / (unit.get("factor") or 100.0)
    if "yard" in (unit.get("unitKey") or ""):
        value *= 0.9144
    return round(value, 1)


def fetch_profile_time_zone(api) -> str | None:
    settings = _safe(api.get_userprofile_settings, default={}) or {}
    tz = find_key(settings, "timeZone") or find_key(settings, "timezone")
    return tz if isinstance(tz, str) and "/" in tz else None


def fetch_activities(api, start: dt.date, end: dt.date) -> list[dict]:
    raw = _safe(api.get_activities_by_date, start.isoformat(), end.isoformat(), default=[]) or []
    acts = [normalize_activity(a) for a in raw]
    acts.sort(key=lambda a: a["date"])
    return acts


def _body_battery_wake(bb_raw) -> float | None:
    best = None
    for day in bb_raw or []:
        for entry in (day.get("bodyBatteryValuesArray") or []):
            nums = [x for x in entry[1:] if isinstance(x, (int, float)) and 0 <= x <= 100]
            if nums:
                best = nums[0] if best is None else max(best, nums[0])
    return best


def fetch_wellness(api, d: dt.date) -> dict:
    ds = d.isoformat()
    yday = (d - dt.timedelta(days=1)).isoformat()
    w: dict[str, Any] = {}

    sleep = _safe(api.get_sleep_data, ds, default={}) or {}
    dto = sleep.get("dailySleepDTO") or {}
    w["sleep_score"] = find_key(dto.get("sleepScores") or {}, "value")
    secs = dto.get("sleepTimeSeconds")
    w["sleep_hours"] = round(secs / 3600, 1) if secs else None

    hrv = _safe(api.get_hrv_data, ds, default={}) or {}
    s = hrv.get("hrvSummary") or {}
    w["hrv_last_night"] = s.get("lastNightAvg")
    w["hrv_weekly_avg"] = s.get("weeklyAvg")
    w["hrv_status"] = s.get("status")
    base = s.get("baseline") or {}
    w["hrv_baseline_low"] = base.get("balancedLow")
    w["hrv_baseline_high"] = base.get("balancedUpper")

    stats = _safe(api.get_stats, ds, default={}) or {}
    w["rhr"] = stats.get("restingHeartRate")
    w["rhr_7d"] = stats.get("lastSevenDaysAvgRestingHeartRate")
    bb = _safe(api.get_body_battery, ds, default=[])
    w["body_battery_wake"] = _body_battery_wake(bb) or stats.get("bodyBatteryHighestValue")

    stats_y = _safe(api.get_stats, yday, default={}) or {}
    w["stress_yesterday"] = stats_y.get("averageStressLevel")

    tr = _safe(api.get_training_readiness, ds, default=[]) or []
    if isinstance(tr, dict):
        tr = [tr]
    tr = [t for t in tr if isinstance(t, dict) and t.get("score") is not None]
    w["training_readiness"] = tr[0].get("score") if tr else None
    w["training_readiness_level"] = tr[0].get("level") if tr else None

    ts = _safe(api.get_training_status, ds, default={}) or {}
    w["acwr"] = find_key(ts, "dailyAcuteChronicWorkloadRatio")
    w["acute_load"] = find_key(ts, "dailyTrainingLoadAcute")
    w["chronic_load"] = find_key(ts, "dailyTrainingLoadChronic")
    w["training_status"] = find_key(ts, "trainingStatusFeedbackPhrase")
    return w


# ── calendar ────────────────────────────────────────────────────────────────
def upload_and_schedule(api, workout_json: dict, date_str: str) -> dict:
    res = api.upload_workout(workout_json)
    wid = find_key(res, "workoutId")
    sched = api.schedule_workout(wid, date_str)
    sid = find_key(sched, "workoutScheduleId") or find_key(sched, "scheduledWorkoutId")
    return {"workoutId": wid, "scheduleId": sid, "name": workout_json.get("workoutName")}


def remove_uploaded(api, uploads: list[dict]) -> None:
    for u in uploads or []:
        if u.get("scheduleId"):
            _safe(api.unschedule_workout, u["scheduleId"])
        if u.get("workoutId"):
            _safe(api.delete_workout, u["workoutId"])
