"""Offline tests: a fake Garmin with different scenarios.
Run:  python -m pytest -q tests   (or  python tests/test_scenarios.py)"""
import contextlib
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yaml  # noqa: E402

from coach import engine  # noqa: E402
from coach.planner import plan_day, phase_for  # noqa: E402
from coach.workouts import to_garmin  # noqa: E402

# the tests never call the real Claude or Todoist APIs
for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TODOIST_API_TOKEN"):
    os.environ.pop(_k, None)

CFG = yaml.safe_load((Path(__file__).resolve().parent / "test_config.yaml").read_text(encoding="utf-8"))


def act(date, typ, km, mins, aero=2.5, anae=0.5, load=None):
    return {"activityId": hash((date, typ, km)) % 10**8, "activityName": typ, "startTimeLocal": f"{date} 07:00:00",
            "activityType": {"typeKey": typ}, "distance": km * 1000, "duration": mins * 60,
            "averageHR": 140, "aerobicTrainingEffect": aero, "anaerobicTrainingEffect": anae,
            "activityTrainingLoad": load or mins * 1.2}


class FakeGarmin:
    """Garmin, including a calendar. Instances created inside the same
    `fresh()` block share one calendar, like hourly runs on the same account."""
    shared = None

    def __init__(self, today, good=True, extra_yesterday=0.0, swims=True, synced=True, steps=0, today_acts=(),
                 bb_now=None, tr_now=None, last_sync=None):
        self.today = today
        self.good = good
        self.synced = synced          # False: last night's sleep hasn't reached Garmin yet
        self.steps = steps            # steps so far today (you're up and wearing the watch)
        self.bb_now, self.tr_now = bb_now, tr_now   # later in the day, e.g. after a workout
        self.last_sync = last_sync                  # datetime of the watch's last upload
        self.uploaded, self.scheduled, self.deleted = [], [], []
        self.cal = FakeGarmin.shared if FakeGarmin.shared is not None else {"items": [], "workouts": {}}
        acts = []
        for k in range(1, 36):
            d = today - dt.timedelta(days=k)
            wd = d.weekday()
            if wd in (1, 3, 5):
                acts.append(act(d.isoformat(), "running", 7, 42))
            if wd == 6:
                acts.append(act(d.isoformat(), "running", 12, 72))
            if wd == 2:
                acts.append(act(d.isoformat(), "road_biking", 25, 60))
            if wd == 0 and swims and k > 14:
                acts.append(act(d.isoformat(), "lap_swimming", 1.5, 40))
        y = (today - dt.timedelta(days=1)).isoformat()
        if extra_yesterday:
            acts = [a for a in acts if not a["startTimeLocal"].startswith(y)]
            acts.append(act(y, "running", 6 + extra_yesterday, 70, aero=4.2, anae=2.8))
        self.acts = acts + list(today_acts)

    def get_activities_by_date(self, s, e):
        return [a for a in self.acts if s <= a["startTimeLocal"][:10] <= e]

    def get_activity_splits(self, activity_id):
        return (getattr(self, "splits", {}) or {}).get(activity_id)

    def get_sleep_data(self, d):
        if not self.synced:
            return {"dailySleepDTO": {"sleepTimeSeconds": None, "sleepScores": None}}
        return {"dailySleepDTO": {"sleepTimeSeconds": (7.8 if self.good else 5.2) * 3600,
                                  "sleepScores": {"overall": {"value": 85 if self.good else 48}}}}

    def get_hrv_data(self, d):
        return {"hrvSummary": {"lastNightAvg": 62 if self.good else 41, "weeklyAvg": 60,
                               "status": "BALANCED" if self.good else "LOW",
                               "baseline": {"balancedLow": 52, "balancedUpper": 70}}}

    def get_stats(self, d):
        return {"totalSteps": self.steps, "restingHeartRate": 48 if self.good else 57, "lastSevenDaysAvgRestingHeartRate": 49,
                "averageStressLevel": 24 if self.good else 48, "bodyBatteryHighestValue": 88 if self.good else 35}

    def get_body_battery(self, d):
        v = 88 if self.good else 35
        values = [[1, v - 20], [2, v]] + ([[3, self.bb_now]] if self.bb_now is not None else [])
        return [{"bodyBatteryValuesArray": values}]

    def get_device_last_used(self):
        if not self.last_sync:
            return {}
        return {"lastUsedDeviceUploadTime": int(self.last_sync.timestamp() * 1000)}

    def get_training_readiness(self, d):
        morning = {"score": 82 if self.good else 22, "level": "HIGH" if self.good else "LOW",
                   "timestampLocal": f"{d}T07:10:00"}
        later = [{"score": self.tr_now, "level": "LOW", "timestampLocal": f"{d}T12:30:00"}] if self.tr_now else []
        return later + [morning]          # Garmin doesn't promise any order

    def get_training_status(self, d):
        return {"mostRecentTrainingStatus": {"latestTrainingStatusData": {"123": {
            "trainingStatusFeedbackPhrase": "PRODUCTIVE",
            "acuteTrainingLoadDTO": {"dailyAcuteChronicWorkloadRatio": 1.1}}}}}

    def upload_workout(self, w):
        json.dumps(w)  # must be serializable
        assert len(w["description"]) <= 1000
        self.uploaded.append(w)
        wid = len(self.cal["workouts"]) + 1
        self.cal["workouts"][wid] = w
        return {"workoutId": wid}

    def schedule_workout(self, wid, date):
        self.scheduled.append((wid, date))
        w = self.cal["workouts"][wid]
        self.cal["items"].append({"id": 1000 + wid, "itemType": "workout", "workoutId": wid, "date": date,
                                  "title": w["workoutName"], "sportTypeKey": w["sportType"]["sportTypeKey"]})
        return {"workoutScheduleId": 1000 + wid}

    def get_scheduled_workouts(self, year, month):
        return {"calendarItems": [i for i in self.cal["items"] if i["date"].startswith(f"{year}-{month:02d}")]}

    def get_workout_by_id(self, wid):
        return self.cal["workouts"][int(wid)]

    def unschedule_workout(self, sid):
        self.deleted.append(("sched", sid))
        self.cal["items"] = [i for i in self.cal["items"] if i["id"] != sid]

    def delete_workout(self, wid):
        self.deleted.append(("w", wid))
        self.cal["items"] = [i for i in self.cal["items"] if i["workoutId"] != wid]


@contextlib.contextmanager
def fresh():
    """A new Garmin account: every FakeGarmin made inside shares one calendar."""
    FakeGarmin.shared = {"items": [], "workouts": {}}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            yield tmp
    finally:
        FakeGarmin.shared = None


def _run(day, **kw):
    api = FakeGarmin(day, **kw)
    with fresh() as tmp:
        res = engine.run_day(api, CFG, day, upload=True)
    return api, res


def test_good_day_follows_plan():
    day = dt.date(2026, 10, 13)  # Tuesday → quality
    api, res = _run(day)
    assert res["readiness"]["level"] == "green"
    assert any(s.kind == "run_quality" for s in res["final"])
    assert api.uploaded and api.scheduled[0][1] == "2026-10-13"


def test_bad_day_goes_to_rest():
    day = dt.date(2026, 10, 13)
    api, res = _run(day, good=False)
    assert res["readiness"]["level"] == "red"
    assert [s.kind for s in res["final"]] == ["rest"]


def test_overdid_yesterday_cuts_today():
    day = dt.date(2026, 10, 15)  # Thursday (Wednesday: bike, no run planned)
    api, res = _run(day, extra_yesterday=4)
    titles = " ".join(res["notes"])
    assert "more than planned" in titles or "intens" in titles.lower() or res["readiness"]["score"] < 80


def test_swims_are_optional_until_the_triathlon_block():
    day = dt.date(2026, 10, 16)  # Friday → swim, but the next goals are running races
    api, res = _run(day, swims=False)
    swims = [s for s in res["final"] if s.sport == "swim"]
    assert swims and all(s.optional and not s.priority for s in swims)
    assert swims[0].title.startswith("Optional · ") and "running is the focus" in swims[0].detail
    assert "priority" not in res["report"].lower()
    # skipping an optional swim isn't "missed"
    from coach.readiness import compliance
    assert compliance([s.to_dict() for s in swims], [])["missed"] == []


def test_no_swims_marks_priority_in_the_triathlon_block():
    day = dt.date(2027, 5, 13)   # Thursday, 6 weeks before the sprint triathlon → endurance swim
    api, res = _run(day, swims=False)
    assert any(s.priority and not s.optional for s in res["final"] if s.sport == "swim")


def test_phases():
    assert phase_for(dt.date(2026, 10, 12), CFG).name == "base"
    assert phase_for(dt.date(2026, 11, 20), CFG).name == "specific"
    assert phase_for(dt.date(2026, 12, 10), CFG).name == "race_week"
    # the 8.5 km race is priority B: 3-day mini taper and 2 days of recovery
    assert phase_for(dt.date(2026, 12, 8), CFG).name != "taper"
    assert phase_for(dt.date(2026, 12, 10), CFG).label == "Mini taper (B race)"
    assert phase_for(dt.date(2026, 12, 15), CFG).name == "transition"
    assert phase_for(dt.date(2026, 12, 16), CFG).name == "base"
    assert phase_for(dt.date(2027, 3, 20), CFG).name == "peak"
    assert phase_for(dt.date(2027, 4, 15), CFG).name == "transition"
    assert phase_for(dt.date(2027, 5, 10), CFG).focus == "tri"
    assert phase_for(dt.date(2026, 11, 20), CFG).label == "8.5 km specific"


def test_three_runs_and_one_swim_per_week():
    kinds = []
    for i in range(7):
        kinds += [s.kind for s in plan_day(dt.date(2026, 10, 12) + dt.timedelta(days=i), CFG, 25)[1]]
    assert sum(k.startswith("run_") for k in kinds) == 3
    assert sum(k.startswith("swim_") for k in kinds) == 1


def test_cloud_tokens_roundtrip(monkeypatch=None):
    import os
    from cryptography.fernet import Fernet
    from coach import garmin_api as G
    key = Fernet.generate_key().decode()
    os.environ[G.KEY_ENV] = key
    old = G.TOKENS_ENC
    with fresh() as tmp:
        G.TOKENS_ENC = Path(tmp) / "t.enc"
        class C:  # fake client
            def dumps(self): return '{"di_token": "a", "di_refresh_token": "b", "di_client_id": "c"}'
        class A: client = C()
        G.save_cloud_tokens(A())
        assert b"di_token" not in G.TOKENS_ENC.read_bytes()
        assert "di_refresh_token" in G._fernet(key).decrypt(G.TOKENS_ENC.read_bytes()).decode()
    G.TOKENS_ENC = old
    del os.environ[G.KEY_ENV]


def test_all_sessions_build_json():
    for i in range(0, 300, 3):
        d = dt.date(2026, 10, 12) + dt.timedelta(days=i)
        _, sess = plan_day(d, CFG, 35)
        for s in sess:
            if s.steps:
                w = to_garmin(s, CFG, "x")
                assert w["workoutSegments"][0]["workoutSteps"]



def test_return_to_running_mode():
    """Mostly cycling, ~4 km/week of running: start at 10 km, no hard sessions."""
    from coach.planner import compute_week_target
    mon = dt.date(2026, 10, 12)
    acts = [{"date": (mon - dt.timedelta(days=d)).isoformat(), "sport": "run", "distance_km": 4.2,
             "duration_min": 25} for d in (8, 15, 22)] + \
           [{"date": (mon - dt.timedelta(days=d)).isoformat(), "sport": "run", "distance_km": 4.2,
             "duration_min": 25} for d in (25,)]
    wt = compute_week_target(acts, mon, CFG)
    assert wt["return_mode"] and wt["target_km"] == 10
    kinds, km = [], 0
    for i in range(7):
        sess = plan_day(mon + dt.timedelta(days=i), CFG, wt["target_km"], True)[1]
        kinds += [s.kind for s in sess]
        km += sum(s.distance_km or 0 for s in sess if s.sport == "run")
    assert "run_quality" not in kinds
    assert km <= 12.5
    # next week, after running 10 km: ~20 % more
    acts2 = acts + [{"date": (mon + dt.timedelta(days=d)).isoformat(), "sport": "run",
                     "distance_km": 10 / 3, "duration_min": 20} for d in (1, 3, 6)]
    wt2 = compute_week_target(acts2, mon + dt.timedelta(days=7), CFG)
    assert wt2["return_mode"] and 11.5 <= wt2["target_km"] <= 12.5


def test_setup_wizard_writes_a_working_config():
    """Scripted answers → config.yaml + workflow time zone, usable by the planner."""
    import shutil
    from coach import setup_wizard
    d1 = (dt.date.today() + dt.timedelta(days=120)).isoformat()
    d2 = (dt.date.today() + dt.timedelta(days=200)).isoformat()
    d3 = (dt.date.today() + dt.timedelta(days=250)).isoformat()
    answers = iter([
        "1 2 3 4 5 6 7",                       # change every proposed setting
        "3", "4", "sun", "mon", "5:30", "America/New_York", "06:30",
        "",                                    # accept the reviewed settings
        "City half", "3", d1, "A",             # name → type → date → priority
        "Lake sprint", "6", d2, "",            # default priority A
        "Alpine fondo", "8", "120", "2400", d3, "b",
        "",                                    # done with races
    ])
    with fresh() as tmp:
        root = Path(tmp)
        wf = root / ".github" / "workflows"
        wf.mkdir(parents=True)
        shutil.copy(Path(__file__).resolve().parent.parent / ".github" / "workflows" / "coach.yml", wf)
        setup_wizard.run(root, ask=lambda prompt: next(answers), suggestions={"pool": 50})
        cfg = yaml.safe_load((root / "config.yaml").read_text())
        flow = (wf / "coach.yml").read_text()
    assert [r["kind"] for r in cfg["races"]] == ["half", "tri_sprint", "bike"]
    assert cfg["races"][2]["elevation_m"] == 2400 and cfg["races"][2]["priority"] == "B"
    assert cfg["athlete"]["easy_pace_min_per_km"] == 5.5 and cfg["athlete"]["pool_length_m"] == 50
    runs = [k for day in cfg["weekly_templates"]["run_focus"].values() for k in day if k.startswith("run_")]
    assert len(runs) == 4
    assert "cycling" in cfg["weekly_templates"]
    assert "TZ: America/New_York" in flow and '"17,47 1-3,10-15 * * *"' in flow     # 06:30 morning, 21:00 evening
    assert cfg["garmin"]["morning_check"] == "06:30" and cfg["garmin"]["evening_plan"] == "21:00"
    for i in range(0, 260, 3):   # the planner accepts it for the whole season
        plan_day(dt.date.today() + dt.timedelta(days=i), cfg, 30)


def test_setup_accepts_proposed_settings_with_enter():
    """With suggestions from Garmin, pressing Enter keeps them; only races are typed."""
    from coach import setup_wizard
    d1 = (dt.date.today() + dt.timedelta(days=90)).isoformat()
    answers = iter(["", "Gran fondo", "", "150", "2000", d1, "", ""])
    sug = {"found": ["x"], "sport": "bike", "run_days": 1, "long_day": "sat", "rest_day": "mon",
           "pace": "5:20", "tz": "Europe/Zurich", "pool": 25}
    with fresh() as tmp:
        setup_wizard.run(Path(tmp), ask=lambda prompt: next(answers), suggestions=sug)
        cfg = yaml.safe_load((Path(tmp) / "config.yaml").read_text())
    assert cfg["athlete"]["sports"] == "bike" and cfg["races"][0]["kind"] == "bike"
    cyc = cfg["weekly_templates"]["cycling"]
    assert cyc["sat"] == ["bike_long"] and cyc["mon"] == ["rest"]
    assert sum(k.startswith("run_") for day in cyc.values() for k in day) == 1


def test_setup_suggestions_from_garmin_history():
    """12 weeks of runs, rides and pool swims → sensible suggested answers."""
    from coach.garmin_api import normalize_activity
    from coach.setup_wizard import suggest_from_activities
    raw, start = [], dt.date(2026, 7, 20)  # a Monday
    for w in range(12):
        mon = start + dt.timedelta(weeks=w)
        raw.append(act((mon + dt.timedelta(days=1)).isoformat(), "running", 8, 44, aero=2.6))    # Tue easy 5:30/km
        raw.append(act((mon + dt.timedelta(days=3)).isoformat(), "running", 6, 27, aero=4.1))    # Thu hard
        raw.append(act((mon + dt.timedelta(days=6)).isoformat(), "running", 14, 79, aero=2.9))   # Sun long
        raw.append(act((mon + dt.timedelta(days=5)).isoformat(), "road_biking", 60, 150))
        swim = act((mon + dt.timedelta(days=2)).isoformat(), "lap_swimming", 2, 45)
        swim.update(poolLength=5000.0, unitOfPoolLength={"unitKey": "meter", "factor": 100.0})
        raw.append(swim)
    sug = suggest_from_activities([normalize_activity(a) for a in raw], "Europe/Zurich")
    assert sug["sport"] == "tri"          # swims + rides → triathlon
    assert sug["run_days"] == 3
    assert sug["long_day"] == "sun"
    assert sug["rest_day"] == "mon"       # the only day with nothing logged
    assert sug["pace"] == "5:34"          # median of the easy runs (5:30 and 5:39/km); the hard run is excluded
    assert sug["pool"] == 50
    assert sug["tz"] == "Europe/Zurich"
    assert suggest_from_activities([], None) == {"found": []}
    # mostly riding → cyclist, long-ride day from the longest rides
    rides = [act((start + dt.timedelta(weeks=w, days=d)).isoformat(), "road_biking", km, mins)
             for w in range(8) for d, km, mins in ((1, 30, 70), (3, 35, 80), (5, 90, 220))]
    sug = suggest_from_activities([normalize_activity(a) for a in rides])
    assert sug["sport"] == "bike" and sug["long_day"] == "sat"


def _cycling_cfg():
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["races"] = [{"name": "Alpine gran fondo", "date": dt.date(2027, 6, 27), "kind": "bike",
                     "distance_km": 120, "elevation_m": 2400, "priority": "A"}]
    cfg["weekly_templates"]["cycling"] = {
        "mon": ["rest"], "tue": ["bike_quality"], "wed": ["run_easy"], "thu": ["bike_quality"],
        "fri": ["bike_easy"], "sat": ["bike_long"], "sun": ["run_strides"]}
    return cfg


def test_cycling_event_plan():
    """Hilly 120 km / 2400 m event: long rides grow from your current longest ride, with climbing work."""
    from coach.planner import compute_week_target, phase_for
    cfg = _cycling_cfg()
    mon = dt.date(2027, 3, 1)
    rides = [{"date": (mon - dt.timedelta(days=d)).isoformat(), "sport": "bike", "distance_km": 50,
              "duration_min": 120} for d in (2, 9, 16, 23)] + \
            [{"date": (mon - dt.timedelta(days=d)).isoformat(), "sport": "bike", "distance_km": 25,
              "duration_min": 60} for d in (5, 12, 19, 26)]
    wt = compute_week_target(rides, mon, cfg)
    assert wt["bike_chronic_hours"] == 3.0 and wt["longest_ride_min"] == 120 and wt["ride_speed_kmh"] == 25.0
    assert phase_for(mon, cfg).focus == "bike" and phase_for(mon, cfg).race.hilly
    week = [s for i in range(7) for s in plan_day(mon + dt.timedelta(days=i), cfg, wt)[1]]
    long_ride = next(s for s in week if s.kind == "bike_long")
    assert long_ride.duration_min <= 120 * 1.15          # never more than +15 % over your longest recent ride
    assert sum(s.kind == "bike_quality" for s in week) == 2
    assert sum(s.sport == "run" for s in week) == 2       # running kept as maintenance
    # peak phase on a hilly course: climbing reps and a long ride with climbing blocks
    peak_mon = dt.date(2027, 6, 7)
    wt_peak = dict(wt, longest_ride_min=300, bike_hours=10)
    peak = [s for i in range(7) for s in plan_day(peak_mon + dt.timedelta(days=i), cfg, wt_peak)[1]]
    assert any("climb" in s.title.lower() for s in peak if s.kind == "bike_quality")
    lr = next(s for s in peak if s.kind == "bike_long")
    assert lr.duration_min >= 240 and any(st["type"] == "repeat" for st in lr.steps)
    # race day and the day before
    assert plan_day(dt.date(2027, 6, 26), cfg, wt)[1][0].title == "Openers ride 45'"
    assert plan_day(dt.date(2027, 6, 27), cfg, wt)[1][0].sport == "bike"
    # every session of the season converts to a Garmin workout
    for i in range(0, 130, 2):
        for s in plan_day(mon + dt.timedelta(days=i), cfg, wt)[1]:
            if s.steps:
                assert to_garmin(s, cfg, "x")["workoutSegments"][0]["workoutSteps"]


def test_scheduled_runs_wait_for_sleep_data():
    """Hourly morning runs: wait while the watch hasn't synced, plan once, then stop."""
    day = dt.date(2026, 10, 13)
    at = lambda h, m=0: dt.datetime.combine(day, dt.time(h, m))
    with fresh() as tmp:
        asleep = FakeGarmin(day, synced=False)
        res = engine.run_day(asleep, CFG, day, wait_for_sleep=True, now=at(7))
        assert res["status"] == "waiting" and not asleep.uploaded

        awake = FakeGarmin(day)                                   # synced after waking up
        res = engine.run_day(awake, CFG, day, wait_for_sleep=True, now=at(9))
        assert res["status"] == "planned" and awake.uploaded

        later = FakeGarmin(day)
        res = engine.run_day(later, CFG, day, wait_for_sleep=True, now=at(10))
        assert res["status"] == "done" and not later.uploaded and not later.deleted

        manual = FakeGarmin(day)                                  # "Run workflow" by hand: re-plans
        res = engine.run_day(manual, CFG, day, now=at(10, 30))
        assert res["status"] == "planned" and manual.uploaded

    with fresh() as tmp:                    # never synced: plan at the deadline
        api = FakeGarmin(day, synced=False)
        res = engine.run_day(api, CFG, day, wait_for_sleep=True, now=at(10, 3))
        assert res["status"] == "planned" and api.uploaded and "no sleep data from last night" in res["report"]
        res = engine.run_day(FakeGarmin(day, synced=False), CFG, day, wait_for_sleep=True, now=at(11))
        assert res["status"] == "done"        # still no sleep data: don't re-upload the same thing
        res = engine.run_day(FakeGarmin(day), CFG, day, wait_for_sleep=True, now=at(12))
        assert res["status"] == "planned"     # sleep arrived late: re-plan once with it
        res = engine.run_day(FakeGarmin(day), CFG, day, wait_for_sleep=True, now=at(13))
        assert res["status"] == "done"

    with fresh() as tmp:                    # up and walking, but no sleep recorded
        api = FakeGarmin(day, synced=False, steps=1800)
        res = engine.run_day(api, CFG, day, wait_for_sleep=True, now=at(8))
        assert res["status"] == "planned" and api.uploaded and "recorded no sleep" in res["report"]


def test_long_run_never_below_recent_longest_run():
    """Return-to-running from a cycling block: a recent 6 km run sets the long-run floor."""
    sunday = dt.date(2026, 10, 18)                       # base phase
    wt = {"target_km": 10, "return_mode": True, "longest_run_km": 6.0}
    long_run = next(s for s in plan_day(sunday, CFG, wt)[1] if s.kind == "run_long")
    assert long_run.distance_km == 6.5                   # +10 % over 6 km, not 4 km
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["min_long_run_km"] = 8                           # athlete's own minimum
    long_run = next(s for s in plan_day(sunday, cfg, wt)[1] if s.kind == "run_long")
    assert long_run.distance_km == 8
    # tapering still wins over the floor
    taper_sunday = dt.date(2027, 4, 4)
    taper_long = next(s for s in plan_day(taper_sunday, cfg, dict(wt, target_km=15))[1] if s.kind == "run_long")
    assert taper_long.distance_km < 8


def test_planning_later_in_the_day_counts_what_you_already_did():
    """Sunday afternoon re-plan after a 30 km morning ride: the run stays, easy; a done run isn't re-added."""
    sunday = dt.date(2026, 10, 18)
    ride = act(sunday.isoformat(), "road_biking", 30, 75)
    with fresh() as tmp:
        api = FakeGarmin(sunday, today_acts=[ride])
        res = engine.run_day(api, CFG, sunday)
        runs = [s for s in res["final"] if s.sport == "run"]
        assert runs and any("keep this run easy" in n for n in res["notes"])
    run_done = act(sunday.isoformat(), "running", 9, 50)
    with fresh() as tmp:
        api = FakeGarmin(sunday, today_acts=[run_done])
        res = engine.run_day(api, CFG, sunday)
        assert not [s for s in res["final"] if s.sport == "run"] and not api.uploaded
        assert "Already done today" in " ".join(res["notes"]) and "Nothing left for today" in res["report"]


def test_learns_long_run_day_from_recent_weeks():
    """Longest run on Wednesday in 3 of 4 weeks → the week rotates so the long run is on Wednesday."""
    from coach.planner import learn_long_day, template_for, phase_for, key_day
    mon = dt.date(2026, 10, 26)
    def runs(long_offsets):
        out = []
        for k, off in enumerate(long_offsets, 1):
            wk = mon - dt.timedelta(weeks=k)
            out.append({"date": (wk + dt.timedelta(days=off)).isoformat(), "sport": "run", "distance_km": 9, "duration_min": 50})
            out.append({"date": (wk + dt.timedelta(days=(off + 2) % 7)).isoformat(), "sport": "run", "distance_km": 5, "duration_min": 28})
        return out
    assert learn_long_day(runs([2, 2, 6, 2]), mon) == "wed"          # 3 of 4 on Wednesday
    assert learn_long_day(runs([2, 6, 6, 2]), mon) is None           # no clear pattern: keep the plan
    cfg = dict(CFG, _learned_days={"run": "wed"})
    phase = phase_for(mon, cfg)
    tpl, base = template_for(phase, cfg), template_for(phase, CFG)
    assert key_day(tpl, "run_long") == 2 and key_day(base, "run_long") == 6
    # spacing is preserved: quality is still 2 days after the long run
    assert key_day(tpl, "run_quality") == (key_day(tpl, "run_long") + 2) % 7
    # the daily report explains the move
    with fresh() as tmp:
        api = FakeGarmin(mon)
        api.acts = [a for a in api.acts if a["activityType"]["typeKey"] != "running"]
        for a in runs([2, 2, 2, 6]):
            api.acts.append(act(a["date"], "running", a["distance_km"], a["duration_min"]))
        res = engine.run_day(api, CFG, mon)
        assert any("instead of Sunday" in n for n in res["notes"])


def test_setup_rerun_keeps_current_settings_and_races():
    """Re-running setup starts from config.yaml: Enter + Enter keeps everything, including tweaks."""
    import shutil
    from coach import setup_wizard
    future = (dt.date.today() + dt.timedelta(days=60)).isoformat()
    with fresh() as tmp:
        root = Path(tmp)
        wf = root / ".github" / "workflows"
        wf.mkdir(parents=True)
        shutil.copy(Path(__file__).resolve().parent.parent / ".github" / "workflows" / "coach.yml", wf)
        first = iter(["3", "4", "sun", "mon", "", "Zürcher Silvesterlauf", "5", "8.5", future, "B", ""])
        setup_wizard.run(root, ask=lambda p: next(first), suggestions={})
        cfg_file = root / "config.yaml"
        cfg_file.write_text(cfg_file.read_text().replace("min_long_run_km: 0", "min_long_run_km: 8"))
        prompts = []
        again = iter(["", ""])
        sug = {"found": ["x"], "sport": "tri", "run_days": 3, "long_day": "wed", "rest_day": "thu", "pace": "5:18"}
        setup_wizard.run(root, ask=lambda p: prompts.append(p) or next(again), suggestions=sug)
        cfg = yaml.safe_load(cfg_file.read_text())
    assert cfg["races"][0]["name"] == "Zürcher Silvesterlauf" and cfg["races"][0]["priority"] == "B"
    assert cfg["min_long_run_km"] == 8
    assert cfg["weekly_templates"]["run_focus"]["sun"] == ["run_long"]     # kept Sunday, not Garmin's Wednesday
    assert cfg["weekly_templates"]["run_focus"]["mon"] == ["rest"]
    assert any("keep them" in p for p in prompts) and not any("Race 1 name" in p for p in prompts)


def test_one_line_why():
    """The report and the watch description start with one short reason for today's workout."""
    day = dt.date(2026, 10, 13)
    cases = [
        (FakeGarmin(day), "Building your base for"),
        (FakeGarmin(day, good=False), "Rest today: low HRV with a raised resting HR"),
    ]
    sunday = dt.date(2026, 10, 18)
    cases.append((FakeGarmin(sunday, today_acts=[act(sunday.isoformat(), "road_biking", 30, 95)]),
                  "Keep it easy: you already did a 30 km ride today."))
    for api, expected in cases:
        with fresh() as tmp:
            res = engine.run_day(api, CFG, api.today)
        line = res["report"].splitlines()[2]
        assert expected in line, line
        assert line.count(".") <= 2 and len(line) < 180          # one short sentence
        assert api.uploaded[0]["description"].startswith(line.strip("*"))
    with fresh() as tmp:                   # B race: mini taper wording
        res = engine.run_day(FakeGarmin(dt.date(2026, 12, 11)), CFG, dt.date(2026, 12, 11))
    assert "A few easy days before Autumn 8.5 km race" in res["report"]


def test_moderate_ride_today_is_counted():
    """A 30 km / 55' easy ride this morning: listed in the report, and today's run stays easy."""
    sunday = dt.date(2026, 10, 18)
    ride = act(sunday.isoformat(), "road_biking", 30, 55, aero=2.2)
    with fresh() as tmp:
        api = FakeGarmin(sunday, today_acts=[ride])
        res = engine.run_day(api, CFG, sunday)
    assert "Done today so far: road_biking (30 km, 55')" in res["report"]
    assert "Keep it easy: you already did a 30 km ride today." in res["report"]
    assert "(scores " in res["report"] and "→" not in res["report"].split("## Yesterday")[0]


def test_after_training_uses_current_body_battery_and_readiness():
    """Woke at Body Battery 88, rode in the morning, now 40 and Training Readiness down to 35."""
    sunday = dt.date(2026, 10, 18)
    ride = act(sunday.isoformat(), "road_biking", 30, 58, aero=2.3)
    with fresh() as tmp:
        res = engine.run_day(FakeGarmin(sunday, today_acts=[ride], bb_now=40, tr_now=35), CFG, sunday)
    rep = res["report"]
    assert "Body Battery now: 40" in rep and "Training Readiness now: 35" in rep
    assert res["readiness"]["level"] != "green"
    assert "Body Battery" in rep.splitlines()[2] or "Training Readiness" in rep.splitlines()[2]
    # first thing in the morning the latest values are the waking ones
    with fresh() as tmp:
        res = engine.run_day(FakeGarmin(sunday), CFG, sunday)
    assert "- Body Battery: 88" in res["report"] and "- Training Readiness: 82" in res["report"]
    # later in the day without training: still the latest values
    with fresh() as tmp:
        res = engine.run_day(FakeGarmin(sunday, bb_now=60, tr_now=70), CFG, sunday)
    assert "- Body Battery now: 60" in res["report"] and "- Training Readiness now: 70" in res["report"]


def test_report_shows_watch_sync_and_warns_when_stale():
    day = dt.date(2026, 10, 13)
    now = dt.datetime.combine(day, dt.time(16, 0))
    with fresh() as tmp:
        res = engine.run_day(FakeGarmin(day, last_sync=now - dt.timedelta(minutes=20)), CFG, day, now=now)
    assert "last sync: Tue 15:40" in res["report"] and "⚠️" not in res["report"]
    with fresh() as tmp:
        res = engine.run_day(FakeGarmin(day, last_sync=now - dt.timedelta(hours=9)), CFG, day, now=now)
    assert "Your watch last synced 9 h ago" in res["report"]


def test_heavy_multisport_block_shortens_the_long_run():
    """4 days of big rides: even with green morning numbers, the long run is cut (min_long_run_km included)."""
    import copy
    sunday = dt.date(2026, 10, 18)
    cfg = copy.deepcopy(CFG)
    cfg["min_long_run_km"] = 8
    api = FakeGarmin(sunday, today_acts=[act(sunday.isoformat(), "road_biking", 30, 57, aero=2.4)])
    recent = {(sunday - dt.timedelta(days=k)).isoformat() for k in (1, 2, 3)}
    api.acts = [a for a in api.acts if a["startTimeLocal"][:10] not in recent]
    for k, km, mins in ((3, 50, 120), (2, 40, 90), (1, 92, 207)):
        api.acts.append(act((sunday - dt.timedelta(days=k)).isoformat(), "road_biking", km, mins, aero=3.0))
    with fresh() as tmp:
        res = engine.run_day(api, cfg, sunday)
    long_run = next(s for s in res["final"] if s.kind == "run_long")
    assert long_run.distance_km <= 13 * 0.7 and any("Long run cut to" in n for n in res["notes"])
    # a returning runner whose long run comes only from min_long_run_km (8 km) ends up below it
    from coach.adjust import adjust
    from coach.planner import make_run, phase_for
    r = {"level": "green", "score": 74, "flags": {"heavy_block": True}}
    out, _ = adjust([make_run("run_long", 8, phase_for(sunday, cfg))], r,
                    {"recent_load": {"hours": 7.9, "ratio": 2.9}}, phase_for(sunday, cfg), [])
    assert out[0].distance_km == 5.5
    why = res["report"].splitlines()[2]
    assert why.startswith("**Shorter and easier: you've trained") and "your usual" in why, why


# ── AI coach and Todoist notes (fakes, no network) ─────────────────────────
class FakeTodoist:
    """Inbox notes: (time "hh:mm" today or a datetime, text[, already done[, recurring]])."""
    def __init__(self, notes):
        from types import SimpleNamespace as NS
        self.day = None
        self.notes = notes
        self.NS = NS
        self.completed = []
        self.updated = []
        self.labels = {}
        self.projects, self.plan = [], []

    def update_task(self, task_id, **kw):
        self.updated.append((task_id, kw))
        if "labels" in kw:
            self.labels[task_id] = kw["labels"]
        for t in getattr(self, "plan", []):
            if t.id == task_id and "description" in kw:
                t.description = kw["description"]
        return True

    def _items(self):
        for i, n in enumerate(self.notes):
            when, text = n[0], n[1]
            if isinstance(when, str):
                when = dt.datetime.combine(self.day, dt.time(*map(int, when.split(":"))))
            done = (len(n) > 2 and n[2]) or f"t{i}" in self.completed
            due = self.NS(is_recurring=True, string="every thu") if len(n) > 3 and n[3] else None
            labels = self.labels.get(f"t{i}", list(n[4]) if len(n) > 4 else [])
            yield self.NS(id=f"t{i}", content=text, description="", project_id="inbox", created_at=when,
                          due=due, labels=labels), done

    def get_projects(self):
        yield [self.NS(id="p1", name="Work", is_inbox_project=False),
               self.NS(id="inbox", name="Inbox", is_inbox_project=True)] + self.projects

    def get_tasks(self, project_id=None, label=None):
        if project_id != "inbox":
            yield [t for t in self.plan if t.project_id == project_id and (not label or label in t.labels)
                   and t.id not in self.completed]
            return
        yield [t for t, done in self._items() if not done]

    def add_project(self, name):
        self.projects.append(self.NS(id=f"p{len(self.projects) + 2}", name=name, is_inbox_project=False))
        return self.projects[-1]

    def add_task(self, content, project_id=None, due_date=None, labels=None, description=None):
        self.plan.append(self.NS(id=f"plan{len(self.plan)}", content=content, project_id=project_id,
                                 due_date=due_date, labels=labels or [], description=description))
        return self.plan[-1]

    def delete_task(self, task_id):
        self.plan = [t for t in self.plan if t.id != task_id]

    def get_completed_tasks_by_completion_date(self, since, until):
        assert since.tzinfo is not None and until.tzinfo is not None
        work = self.NS(id="w1", content="finish report", description="", project_id="p1",
                       created_at=dt.datetime.combine(self.day, dt.time(7)), labels=[])
        yield [t for t, done in self._items() if done] + [work]

    def complete_task(self, task_id):
        self.completed.append(task_id)
        return True


NO_SCHEDULE = {"unavailable": [], "moves": [], "no_swim_until": "", "summary": "", "preferences": [],
               "notes_used": []}


class FakeClaude:
    """The day's decision answers `answer`; the schedule question answers `schedule`."""
    provider = "anthropic"

    def __init__(self, answer=None, fail=False, schedule=None):
        from types import SimpleNamespace as NS
        self.answer, self.fail, self.requests = answer, fail, []
        self.schedule, self.schedule_requests = schedule or NO_SCHEDULE, []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        from types import SimpleNamespace as NS
        from coach.ai import SCHEDULE_SYSTEM
        if kw.get("system") == SCHEDULE_SYSTEM:
            self.schedule_requests.append(kw)
            return NS(content=[NS(type="text", text=json.dumps(self.schedule))])
        self.requests.append(kw)
        if self.fail:
            raise RuntimeError("overloaded")
        return NS(content=[NS(type="text", text=json.dumps(self.answer))])


class FakeOpenAI:
    provider = "openai"

    def __init__(self, answer=None, fail=False, schedule=None):
        from types import SimpleNamespace as NS
        self.answer, self.fail, self.requests = answer, fail, []
        self.schedule, self.schedule_requests = schedule or NO_SCHEDULE, []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        from types import SimpleNamespace as NS
        if kw["response_format"]["json_schema"]["name"] == "schedule_changes":
            self.schedule_requests.append(kw)
            return NS(choices=[NS(message=NS(content=json.dumps(self.schedule), refusal=None))])
        self.requests.append(kw)
        if self.fail:
            raise RuntimeError("rate limited")
        return NS(choices=[NS(message=NS(content=json.dumps(self.answer), refusal=None))])


def _ai_day(answer=None, notes=(), fail=False, now=None, wait=False, api=None,
            client_cls=None, upload=True, todo=None):
    day = dt.date(2026, 10, 18)                     # Sunday: long run planned
    todo = todo or FakeTodoist(list(notes))
    todo.day = day
    claude = (client_cls or FakeClaude)(answer, fail)
    api = api or FakeGarmin(day)
    res = engine.run_day(api, CFG, day, notes_api=todo, ai_client=claude,
                         now=now or dt.datetime.combine(day, dt.time(9)), wait_for_sleep=wait, upload=upload)
    res["todo"] = todo
    return res, claude, api


def test_ai_coach_can_choose_rest_from_your_note():
    answer = {"decision": "rest", "sessions": [{"kind": "rest", "distance_km": None, "minutes": None}],
              "why": "Rest today: you mention knee pain, and running on it risks a longer layoff.",
              "coach_note": "Ice it and see how it feels tomorrow; an easy swim is fine if it doesn't hurt.",
              "notes_used": ["t0"]}
    with fresh() as tmp:
        res, claude, api = _ai_day(answer, notes=[("07:40", "knee hurts a bit"), ("08:05", "buy milk")])
    assert [s.kind for s in res["final"]] == ["rest"]
    rep = res["report"]
    assert rep.splitlines()[2] == "**Rest today: you mention knee pain, and running on it risks a longer layoff.**"
    assert "Decided by the AI coach" in rep and "“knee hurts a bit” (07:40)" in rep and "Taken into account" in rep
    sent = json.loads(claude.requests[0]["messages"][0]["content"].split("\n", 1)[1])
    assert sent["athlete_notes_today"][0]["text"] == "knee hurts a bit"
    assert sent["rule_proposal_today"] and sent["activities_last_6_weeks"] and sent["weekly_summary"]
    assert api.uploaded[0]["description"].startswith("Rest today")
    assert res["todo"].completed == ["t0"]                  # the knee note, not "buy milk"


def test_ai_coach_cannot_exceed_the_plan():
    answer = {"decision": "adjust", "why": "Feeling strong, go long.", "coach_note": "", "notes_used": [],
              "sessions": [{"kind": "run_long", "distance_km": 25, "minutes": None},
                           {"kind": "bike_quality", "distance_km": None, "minutes": 90}]}
    with fresh() as tmp:
        res, _, _ = _ai_day(answer)
    long_run = next(s for s in res["final"] if s.kind == "run_long")
    assert long_run.distance_km <= 13                       # the rule proposal's long run
    assert not any(s.kind == "bike_quality" for s in res["final"])
    assert any("Safety limit" in n for n in res["notes"])


def test_ai_failure_falls_back_to_rules():
    with fresh() as tmp:
        res, _, api = _ai_day(fail=True)
    assert any(s.kind == "run_long" for s in res["final"]) and api.uploaded
    assert "AI coach unavailable today (overloaded): rule-based plan used." in res["report"]


def test_new_note_after_planning_triggers_replan():
    answer = {"decision": "keep", "sessions": [{"kind": "run_long", "distance_km": 13, "minutes": None}],
              "why": "As planned.", "coach_note": "", "notes_used": []}
    with fresh() as tmp:
        day = dt.date(2026, 10, 18)
        at = lambda h: dt.datetime.combine(day, dt.time(h))
        res, _, _ = _ai_day(answer, notes=[("07:40", "slept ok")], wait=True, now=at(8))
        assert res["status"] == "planned"
        res, _, _ = _ai_day(answer, notes=[("07:40", "slept ok")], wait=True, now=at(9))
        assert res["status"] == "done"                      # nothing new
        res, claude, _ = _ai_day(answer, notes=[("07:40", "slept ok"), ("09:20", "legs very heavy")], wait=True, now=at(10))
        assert res["status"] == "planned"                   # new note → re-planned with it
        assert "legs very heavy" in claude.requests[0]["messages"][0]["content"]


def test_ai_never_adds_training_to_a_rest_day():
    from coach import ai
    from coach.planner import make_rest, phase_for
    phase = phase_for(dt.date(2026, 10, 19), CFG)
    out, _ = ai.apply_limits([{"kind": "bike_easy", "distance_km": None, "minutes": 45},
                              {"kind": "swim_technique", "distance_km": None, "minutes": None}],
                             [make_rest()], phase, 0)
    assert [s.kind for s in out] == ["rest"]


def test_openai_provider_and_note_completion():
    """OpenAI decides; only the training note is completed, and only on real runs."""
    answer = {"decision": "adjust", "sessions": [{"kind": "run_easy", "distance_km": 5, "minutes": None}],
              "why": "Easy 5 km: you say your legs feel heavy after the rides.", "coach_note": "Keep it chatty.",
              "notes_used": ["t1", "nonexistent"]}
    notes = [("07:10", "call the bank"), ("07:45", "legs heavy")]
    with fresh() as tmp:
        res, oai, _ = _ai_day(answer, notes=notes, client_cls=FakeOpenAI)
        assert [s.kind for s in res["final"]] == ["run_easy"] and res["final"][0].distance_km == 5
        assert oai.requests[0]["response_format"]["json_schema"]["strict"] is True
        assert res["todo"].completed == ["t1"]                    # not "call the bank", not unknown ids
        assert "Completed in Todoist: 1 note(s)" in res["report"]
        # a later re-plan the same day still knows the note it already used (it's completed in Todoist)
        res["todo"].notes.append(("09:30", "feeling better"))
        later, oai2, _ = _ai_day(answer, todo=res["todo"], client_cls=FakeOpenAI,
                                 now=dt.datetime.combine(dt.date(2026, 10, 18), dt.time(10)))
        sent = json.loads(oai2.requests[0]["messages"][1]["content"].split("\n", 1)[1])
        assert sent["earlier_notes_today"] == [{"time": "07:45", "text": "legs heavy"}]
    with fresh() as tmp:                     # dry run: nothing completed
        res, _, _ = _ai_day(answer, notes=notes, client_cls=FakeOpenAI,
                            upload=False)
        assert res["todo"].completed == []


def test_ai_provider_selection():
    from coach import ai
    cfg = {"ai": {"provider": "auto"}}
    os.environ["OPENAI_API_KEY"] = "x"
    try:
        assert ai.provider(cfg) == "openai" and ai.model_for(cfg, "openai") == "gpt-5.4-mini"
        os.environ["ANTHROPIC_API_KEY"] = "y"
        assert ai.provider({"ai": {"provider": "anthropic"}}) == "anthropic"
        assert ai.provider({"ai": {"enabled": False}}) is None
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
    assert ai.provider(cfg) is None


def test_notes_from_the_last_14_hours_open_and_done():
    """At 08:00: last night's 22:00 note counts, 17:00 doesn't; done notes are context, not re-completed."""
    from coach.notes import recent_notes
    day = dt.date(2026, 10, 18)
    yesterday = day - dt.timedelta(days=1)
    todo = FakeTodoist([(dt.datetime.combine(yesterday, dt.time(17)), "old note"),
                        (dt.datetime.combine(yesterday, dt.time(22)), "very tired after the ride"),
                        ("06:50", "slept badly", True),          # you ticked it off yourself
                        ("07:30", "left calf tight")])
    todo.day = day
    os.environ["TODOIST_API_TOKEN"] = "x"
    try:
        notes = recent_notes(CFG, dt.datetime.combine(day, dt.time(8)), api=todo)
    finally:
        os.environ.pop("TODOIST_API_TOKEN", None)
    assert [(n["text"], n["done"]) for n in notes] == [
        ("very tired after the ride", False), ("slept badly", True), ("left calf tight", False)]
    assert notes[0]["time"] == "Sat 22:00" and notes[2]["time"] == "07:30"
    answer = {"decision": "adjust", "sessions": [{"kind": "run_easy", "distance_km": 4, "minutes": None}],
              "why": "Easy 4 km: tight calf and a tiring ride yesterday.", "coach_note": "",
              "notes_used": ["t1", "t2", "t3"]}                   # t2 is already done
    with fresh() as tmp:
        res, claude, _ = _ai_day(answer, todo=todo,
                                 now=dt.datetime.combine(day, dt.time(8)))
    sent = json.loads(claude.requests[0]["messages"][0]["content"].split("\n", 1)[1])
    assert [n["text"] for n in sent["athlete_notes_today"]] == ["very tired after the ride", "left calf tight"]
    assert sent["earlier_notes_today"] == [{"time": "06:50", "text": "slept badly"}]
    assert sorted(todo.completed) == ["t1", "t3"]                 # open ones it used; not the done one
    assert "“slept badly” (06:50, already done)" in res["report"]


def test_update_command_keeps_your_settings():
    """`coach update` on a "Use this template" copy: latest code, your config/data/schedule kept."""
    import shutil
    import subprocess
    from coach.update import run as update
    root = Path(__file__).resolve().parent.parent
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@x"),      # CI machines have no git identity
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@x")):
        os.environ.setdefault(k, v)
    git = lambda cwd, *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *a],
                                         cwd=cwd, check=True, capture_output=True, text=True)
    with fresh() as tmp:
        tpl, mine = Path(tmp) / "template", Path(tmp) / "mine"
        for d in (tpl, mine):
            shutil.copytree(root / "coach", d / "coach", ignore=shutil.ignore_patterns("__pycache__"))
            (d / ".github" / "workflows").mkdir(parents=True)
            shutil.copy(root / ".github" / "workflows" / "coach.yml", d / ".github" / "workflows")
            shutil.copy(root / "config.yaml", d)
            git(d, "init", "-q", "-b", "main")
            git(d, "add", "-A")
            git(d, "commit", "-qm", "Initial commit")          # unrelated histories, like the template button
        import re
        cfg = re.sub(r"min_long_run_km: \S+", "min_long_run_km: 9", (mine / "config.yaml").read_text())
        (mine / "config.yaml").write_text(cfg)
        wf = mine / ".github" / "workflows" / "coach.yml"
        flow = re.sub(r"TZ: \S+", "TZ: Asia/Tokyo", wf.read_text(), count=1)
        wf.write_text(re.sub(r'- cron: "[^"]*"', '- cron: "0 22-23,0-1 * * *"', flow, count=1))
        (mine / "data").mkdir()
        (mine / "data" / "garmin_tokens.enc").write_text("secret")
        git(mine, "add", "-A")
        git(mine, "commit", "-qm", "My setup")
        (tpl / "coach" / "why.py").write_text((tpl / "coach" / "why.py").read_text() + "\n# improved\n")
        twf = tpl / ".github" / "workflows" / "coach.yml"
        twf.write_text(twf.read_text().replace("TODOIST_API_TOKEN:", "NEW_SECRET: x\n      TODOIST_API_TOKEN:"))
        git(tpl, "commit", "-qam", "Improvement")
        assert update(mine, url=str(tpl)) == 0
        assert "min_long_run_km: 9" in (mine / "config.yaml").read_text()
        assert (mine / "data" / "garmin_tokens.enc").read_text() == "secret"
        flow = wf.read_text()
        # the schedule follows config.yaml's times in your time zone (08:00 check, 21:00 plan in Tokyo)
        assert "TZ: Asia/Tokyo" in flow and '"17,47 0-1,12-13,23 * * *"' in flow and "NEW_SECRET" in flow
        assert (mine / "coach" / "why.py").read_text().endswith("# improved\n")
        assert update(mine, url=str(tpl)) == 0                    # already up to date


def test_recurring_note_is_really_completed():
    """A note Todoist made recurring would just move to its next date: its date is removed first."""
    answer = {"decision": "rest", "sessions": [{"kind": "rest", "distance_km": None, "minutes": None}],
              "why": "Rest: heavy legs.", "coach_note": "", "notes_used": ["t0", "t1"]}
    with fresh() as tmp:
        res, _, _ = _ai_day(answer, notes=[("07:40", "legs heavy", False, True), ("07:50", "slept badly")])
    todo = res["todo"]
    assert todo.updated == [("t0", {"labels": ["coach"], "due_string": "no date"}),   # date removed: recurring
                            ("t1", {"labels": ["coach"]})]
    assert sorted(todo.completed) == ["t0", "t1"]


def test_training_diary_feeds_later_decisions():
    """A note used on Sunday is labelled in Todoist and comes back in the AI's notes_history on
    Wednesday, with what was trained around it and what the coach planned (from Garmin)."""
    sunday, wednesday = dt.date(2026, 10, 18), dt.date(2026, 10, 21)
    answer = {"decision": "keep", "sessions": [{"kind": "run_long", "distance_km": 10, "minutes": None}],
              "why": "As planned.", "coach_note": "", "notes_used": ["t0"]}
    with fresh():
        todo = FakeTodoist([(dt.datetime(2026, 10, 18, 7, 40), "legs heavy after two long rides"),
                            (dt.datetime(2026, 10, 18, 7, 50), "buy milk", True)])   # done by you: not diary
        todo.day = sunday
        engine.run_day(FakeGarmin(sunday), CFG, sunday, notes_api=todo, ai_client=FakeClaude(answer),
                       now=dt.datetime.combine(sunday, dt.time(8)))
        assert todo.completed == ["t0"] and todo.labels["t0"] == ["coach"]
        todo.day = wednesday
        claude = FakeClaude(dict(answer, notes_used=[]))
        res = engine.run_day(FakeGarmin(wednesday), CFG, wednesday, notes_api=todo,
                             ai_client=claude, now=dt.datetime.combine(wednesday, dt.time(8)))
    sent = json.loads(claude.requests[0]["messages"][0]["content"].split("\n", 1)[1])
    hist = sent["notes_history"]
    assert len(hist) == 1 and hist[0]["note"] == "legs heavy after two long rides"
    assert hist[0]["trained_day_before"] and hist[0]["readiness"] is not None
    assert [t.split(" ")[0] for t in hist[0]["coach_plan_that_day"]] == ["Long"]
    assert "Coach memory: 1 of your notes" in res["report"]


def test_yesterday_is_read_back_from_the_garmin_calendar():
    """No files: yesterday's plan is read from the workouts the coach put in the Garmin calendar."""
    from coach.calendar import Calendar
    mon, tue = dt.date(2026, 10, 12), dt.date(2026, 10, 13)
    with fresh():
        api = FakeGarmin(mon, good=False)                  # a bad day: Monday's plan gets cut
        first = engine.run_day(api, CFG, mon, now=dt.datetime(2026, 10, 12, 7, 42))
        back = Calendar(api, CFG).plan(mon)
        res = engine.run_day(FakeGarmin(tue), CFG, tue)
    uploaded = [s for s in first["final"] if s.steps]
    assert [e["session"]["kind"] for e in back] == [s.kind for s in uploaded]
    assert all(e["meta"]["r"] == first["readiness"]["score"] and e["meta"]["at"] == "07:42" for e in back)
    planned_km = sum(s.distance_km or 0 for s in first["final"] if s.sport == "run")
    assert f"Planned: {round(planned_km, 1)} km running" in res["report"]


def test_workout_description_is_readable_and_read_back():
    from coach.calendar import fit, footer, read_description, session_from
    desc = fit("x" * 2000, footer("Base — 10K in 60 days", dt.datetime(2026, 10, 11, 18, 31), False))
    assert len(desc) <= 1000 and "coach:" not in desc
    assert read_description(desc) == {"sr": 0, "at": "18:31"}
    api = FakeGarmin(dt.date(2026, 10, 18))
    with fresh():
        res = engine.run_day(api, CFG, dt.date(2026, 10, 18))
    text = api.uploaded[0]["description"]
    assert "coach:" not in text and "\n\nReadiness " in text and text.endswith(".")
    assert read_description(text)["r"] == res["readiness"]["score"]
    for title, kind, km in [("Quality · 5×1 km, 8 km", "run_quality", 8), ("Long run 12.5 km", "run_long", 12.5),
                            ("Easy run 6 km + 6 strides", "run_strides", 6), ("Easy ride 60'", "bike_easy", None),
                            ("Swim 12×100 hard", "swim_css", None), ("Rest / mobility 20'", "rest", None)]:
        got = session_from(title)
        assert (got["kind"], got["distance_km"]) == (kind, km), title


def test_plan_is_pushed_to_your_phone_when_a_topic_is_set():
    sent = []
    day = dt.date(2026, 10, 18)
    with fresh():
        os.environ.pop("NTFY_TOPIC", None)
        engine.run_day(FakeGarmin(day), CFG, day, poster=lambda url, p: sent.append(p))
        assert sent == []                                   # off unless you set it up
        os.environ["NTFY_TOPIC"] = "coach-test-topic"
        try:
            res = engine.run_day(FakeGarmin(day), CFG, day, poster=lambda url, p: sent.append((url, p)))
            engine.run_day(FakeGarmin(day), CFG, day, upload=False, poster=lambda url, p: sent.append(p))
        finally:
            os.environ.pop("NTFY_TOPIC")
    assert len(sent) == 1                                   # dry runs don't notify
    url, p = sent[0]
    assert url == "https://ntfy.sh" and p["topic"] == "coach-test-topic"
    assert p["title"].startswith("Today: Long run") and "Readiness" in p["message"]   # manual re-run, same plan
    assert "Sent to your phone and watch" in res["report"]


def _with_preview(days=6):
    return dict(CFG, garmin=dict(CFG["garmin"], preview_days=days))


def test_long_run_done_early_in_the_week_is_not_repeated():
    sunday = dt.date(2026, 10, 18)
    api = FakeGarmin(sunday)
    sat = "2026-10-17"
    api.acts = [a for a in api.acts if not a["startTimeLocal"].startswith(sat)] + [act(sat, "running", 13, 78)]
    with fresh():
        res = engine.run_day(api, CFG, sunday)
    assert not any(s.kind == "run_long" for s in res["final"])
    assert any(s.kind == "run_easy" for s in res["final"])
    assert "already did your long run this week (Saturday, 13 km), so this is an easy run of 8 km instead" in res["report"] and "more than planned" not in res["report"]
    assert "long run done Saturday, 13 km" in res["report"]


def test_swim_done_earlier_in_the_week_is_dropped():
    friday = dt.date(2026, 10, 16)                       # template: technique swim on Friday
    api = FakeGarmin(friday)
    api.acts.append(act("2026-10-13", "lap_swimming", 1.5, 40))
    with fresh():
        res = engine.run_day(api, CFG, friday)
    assert not any(s.sport == "swim" for s in res["final"])
    assert "already swum 1× this week (1 planned), so this swim is dropped" in res["report"]
    assert "No swim: you've already swum as often as planned this week." in res["report"]


def test_quality_skipped_on_tuesday_moves_to_a_later_green_day():
    thursday = dt.date(2026, 10, 15)                     # template: Tuesday quality, Thursday strides
    api = FakeGarmin(thursday)
    api.acts = [a for a in api.acts if not a["startTimeLocal"].startswith("2026-10-13")]   # skipped Tuesday
    with fresh():
        res = engine.run_day(api, CFG, thursday)
    assert any(s.kind == "run_quality" for s in res["final"])
    assert "skipped this week's quality session" in res["report"]
    api = FakeGarmin(thursday)                           # Tuesday was run (moderate TE): no second quality
    with fresh():
        res = engine.run_day(api, CFG, thursday)
    assert not any(s.kind == "run_quality" for s in res["final"])


def test_drafts_show_the_week_ahead_and_are_replaced():
    monday = dt.date(2026, 10, 12)
    cfg = _with_preview(6)
    with fresh():
        api = FakeGarmin(monday)
        res = engine.run_day(api, cfg, monday, now=dt.datetime(2026, 10, 12, 8))
        drafts = [i for i in api.cal["items"] if i["title"].startswith("Coach draft ·")]
        assert sorted({i["date"] for i in drafts}) == [f"2026-10-{n}" for n in range(13, 19)]
        assert any(i["date"] == "2026-10-18" and "Long run" in i["title"] for i in drafts)
        assert "## Coming up" in res["report"] and "Drafts for the next 6 days" in res["report"]
        # the next morning: Tuesday's draft becomes the real workout, the drafts are redrawn once
        tue = dt.date(2026, 10, 13)
        api2 = FakeGarmin(tue)
        engine.run_day(api2, cfg, tue, now=dt.datetime(2026, 10, 13, 8))
        items = api2.cal["items"]
        on_tue = [i["title"] for i in items if i["date"] == "2026-10-13"]
        assert on_tue and all(t.startswith("Coach ·") for t in on_tue)
        for day in ("2026-10-14", "2026-10-18"):
            assert len([i for i in items if i["date"] == day]) == len({i["title"] for i in items if i["date"] == day})
        assert any(i["date"] == "2026-10-19" for i in items)   # one more day ahead


def test_drafts_follow_the_week_when_you_train_early():
    """Long run done on Saturday → Sunday's draft is an easy run, already on Saturday's plan."""
    saturday = dt.date(2026, 10, 17)
    api = FakeGarmin(saturday, today_acts=[act("2026-10-17", "running", 13, 78)])
    with fresh():
        res = engine.run_day(api, _with_preview(2), saturday, now=dt.datetime(2026, 10, 17, 18))
        sunday = [i["title"] for i in api.cal["items"] if i["date"] == "2026-10-18"]
    assert sunday and not any("Long run" in t for t in sunday), sunday


def test_evening_plans_tomorrow_and_the_morning_checks_it():
    """21:00: tomorrow's real workout from today's training. 08:00: checked with the sleep data;
    unchanged → no notification, changed → 'Changed:'. Scheduled runs act by local time."""
    sat, sun = dt.date(2026, 10, 17), dt.date(2026, 10, 18)
    sent = []
    poster = lambda url, p: sent.append(p["title"])
    os.environ["NTFY_TOPIC"] = "t"
    try:
        with fresh():
            # Saturday evening, after a 13 km long run today: Sunday's long run isn't repeated
            api = FakeGarmin(sat, today_acts=[act(sat.isoformat(), "running", 13, 78)])
            res = engine.scheduled(api, CFG, dt.datetime(2026, 10, 17, 21, 4), poster=poster)
            assert res["status"] == "planned" and "## Tomorrow" in res["report"]
            sun_items = [i for i in api.cal["items"] if i["date"] == "2026-10-18"]
            assert sun_items and all(i["title"].startswith("Coach ·") for i in sun_items)
            assert not any("Long run" in i["title"] for i in sun_items)
            desc = api.cal["workouts"][sun_items[0]["workoutId"]]["description"]
            assert "Planned the evening before (Sat 21:04)" in desc and "checked in the morning" in desc
            assert sent and sent[-1].startswith("Tomorrow: ")
            # a second evening trigger an hour later does nothing
            assert engine.scheduled(FakeGarmin(sat), CFG, dt.datetime(2026, 10, 17, 22, 2))["status"] == "done"
            # Sunday 07:05 (winter: the UTC cron hits 7:00 local): too early
            assert engine.scheduled(FakeGarmin(sun), CFG, dt.datetime(2026, 10, 18, 7, 5))["status"] == "early"
            # 08:03, good night: last night's plan stands, quietly
            n = len(sent)
            api = FakeGarmin(sun)
            api.acts = [a for a in api.acts if not a["startTimeLocal"].startswith("2026-10-17")] + \
                [act("2026-10-17", "running", 13, 78)]
            res = engine.scheduled(api, CFG, dt.datetime(2026, 10, 18, 8, 3), poster=poster)
            assert res["status"] == "planned" and "Last night's plan still stands" in res["report"]
            assert len(sent) == n
            # 09:00: already checked
            assert engine.scheduled(FakeGarmin(sun), CFG, dt.datetime(2026, 10, 18, 9))["status"] == "done"
        with fresh():
            engine.scheduled(FakeGarmin(sat), CFG, dt.datetime(2026, 10, 17, 21, 4), poster=poster)
            bad = FakeGarmin(sun, good=False)                    # a bad night: the plan changes, and says so
            res = engine.scheduled(bad, CFG, dt.datetime(2026, 10, 18, 8, 3), poster=poster)
            assert "Changed from last night's plan" in res["report"] and sent[-1].startswith("Changed: ")
    finally:
        os.environ.pop("NTFY_TOPIC")


def test_ai_gets_the_season_and_the_coming_days_evening_and_morning():
    """The AI sees every upcoming race, the phases until the next one, how the weeks are built and
    the next days of the plan; in the evening it plans tomorrow without sleep data."""
    sat = dt.date(2026, 10, 17)
    keep = {"decision": "keep", "sessions": [{"kind": "run_long", "distance_km": 12, "minutes": None}],
            "why": "Long run as planned.", "coach_note": "", "notes_used": []}
    claude = FakeClaude(keep)
    with fresh():
        res = engine.run_day(FakeGarmin(sat, synced=False), CFG, sat + dt.timedelta(days=1), evening=True,
                             ai_client=claude, now=dt.datetime(2026, 10, 17, 21))
    assert res["status"] == "planned" and "Planned without sleep data" not in res["report"]
    sent = json.loads(claude.requests[0]["messages"][0]["content"].split("\n", 1)[1])
    assert sent["planning"] == "tomorrow" and sent["today"] == "2026-10-18"
    season = sent["season"]
    assert [r["name"] for r in season["races"]] == ["Autumn 8.5 km race", "Spring half marathon",
                                                    "Summer sprint triathlon"]
    assert season["races"][2]["swim_bike_run_km"] == [0.75, 20, 5] and "not maintenance" in season["goal"]
    assert season["phases_by_week"][0]["week_of"] == "2026-10-12" and season["athlete"]["run_days_per_week"] == 3
    assert len(sent["coming_days"]) >= 6 and sent["coming_days"][0]["date"] == "2026-10-19"
    assert "Decided by the AI coach" in res["report"]
    from coach.ai import SYSTEM
    assert "coming_days" in SYSTEM and "season" in SYSTEM


def test_manual_run_after_the_evening_time_plans_tomorrow():
    from coach import __main__ as M, garmin_api as G
    real = (G.connect, G.save_cloud_tokens, M.dt)
    calls = []

    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 17, 21, 30)

    class DT:
        datetime, date, timedelta, time = Clock, dt.date, dt.timedelta, dt.time
    try:
        G.connect, G.save_cloud_tokens, M.dt = (lambda *a, **k: "api"), (lambda a: None), DT
        M.run_day = lambda api, cfg, d, **kw: calls.append((d, kw.get("evening"))) or {"report": ""}
        M.main(["run", "--auto", "--dry-run", "--config", "tests/test_config.yaml"])
    finally:
        G.connect, G.save_cloud_tokens, M.dt = real
        from coach.engine import run_day
        M.run_day = run_day
    assert calls == [(dt.date(2026, 10, 18), True)]


def test_schedule_is_local_time_all_year():
    from coach.setup_wizard import cron_for
    assert cron_for("08:00", "Europe/Zurich", "10:00", "21:00") == "17,47 6-9,19-21 * * *"
    assert cron_for("08:00", "UTC", "10:00", "21:00") == "17,47 8-10,21-22 * * *"
    assert cron_for("08:00", "Australia/Sydney", "10:00", "21:00") == "17,47 0,10-12,21-23 * * *"


def test_long_and_custom_triathlons():
    from coach import setup_wizard
    from coach.planner import load_races, parse_legs
    assert parse_legs("1.5-40-10") == (1.5, 40.0, 10.0) and parse_legs("1,9/90/21,1") == (1.9, 90.0, 21.1)
    assert parse_legs("1.5-40") is None
    d1 = (dt.date.today() + dt.timedelta(days=150)).isoformat()
    d2 = (dt.date.today() + dt.timedelta(days=300)).isoformat()
    answers = iter(["1", "3", "", "Lake 70.3", "9", d1, "A", "Club tri", "11", "1.5-40", "1-30-7.5", d2, "B", ""])
    with fresh() as tmp:
        setup_wizard.run(Path(tmp), ask=lambda p: next(answers), suggestions={})
        cfg = yaml.safe_load((Path(tmp) / "config.yaml").read_text())
    assert [r["kind"] for r in cfg["races"]] == ["tri_half", "tri"]
    assert cfg["races"][1]["distances"] == "1-30-7.5"
    races = load_races(cfg)
    assert races[0].legs == (1.9, 90, 21.1) and races[1].legs == (1.0, 30.0, 7.5)
    # a 70.3 builds the long run towards ~18 km and the long ride beyond 2 h
    phase = phase_for(dt.date.fromisoformat(d1) - dt.timedelta(days=40), cfg)
    assert phase.focus == "tri" and phase.long_cap_km == 18
    from coach.planner import make_bike
    assert make_bike("bike_long", phase).duration_min > 120


def test_quality_runs_target_paces_from_your_fitness():
    from coach import paces as P
    from coach.planner import Phase, make_run
    from coach.workouts import to_garmin
    p = P.estimate(CFG, {"5k": 1325, "10k": 2760, "hm": 6120}, chronic_km=25)
    assert P.fmt(p["5k"]) == "4:25" and P.fmt(p["10k"]) == "4:36" and "race predictor" in p["source"]
    P.set_current(p)
    s = make_run("run_quality", 8, Phase("short", "base", "x", None, None, 1, False, 14), 0)
    assert s.title.startswith("Quality · Rolling 400s") and "400 m at 4:25/km, 400 m at 5:01/km" in s.detail
    rep = to_garmin(s, CFG, "x")["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][0]
    assert rep["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert 1000 / rep["targetValueTwo"] < p["5k"] < 1000 / rep["targetValueOne"]
    over = make_run("run_quality", 9, Phase("short", "base", "x", None, None, 1, False, 14), 1)
    assert "1 km at 4:43/km, 1 km at 4:25/km" in over.detail          # over-unders
    # no prediction: estimated from the easy pace; little running lately: 3 % easier
    q = P.estimate(CFG, None, chronic_km=5)
    assert "easy pace" in q["source"] and "3 % easier" in q["source"]
    # a stale prediction far from the easy pace is ignored
    assert "easy pace" in P.estimate(CFG, {"5k": 900}, 25)["source"]


def test_notes_about_the_week_rearrange_it():
    """'Can't train on Sunday' (Tuesday): Sunday becomes rest and the long run moves, today and on
    later days too (the note is re-read from the Todoist diary). The week shows in Todoist."""
    tue, wed = dt.date(2026, 10, 13), dt.date(2026, 10, 14)
    sched = dict(NO_SCHEDULE, unavailable=["2026-10-18"], summary="No training on Sunday: long run moved.",
                 notes_used=["t0"])
    keep = {"decision": "keep", "sessions": [{"kind": "run_quality", "distance_km": 9, "minutes": None}],
            "why": "As planned.", "coach_note": "", "notes_used": []}
    cfg = _with_preview(7)
    with fresh():
        todo = FakeTodoist([(dt.datetime(2026, 10, 13, 7, 30), "I can't train on Sunday, family lunch"),
                            (dt.datetime(2026, 10, 13, 7, 40), "buy milk")])
        todo.day = tue
        todo.add_task("my own reminder", project_id=None)          # not in the plan project: untouched
        claude = FakeClaude(keep, schedule=sched)
        api = FakeGarmin(tue)
        res = engine.run_day(api, cfg, tue, notes_api=todo, ai_client=claude, now=dt.datetime(2026, 10, 13, 8))
        sent = json.loads(claude.schedule_requests[0]["messages"][0]["content"].split("\n", 1)[1])
        assert [n["text"] for n in sent["open_notes"]][0].startswith("I can't train on Sunday")
        sunday = [i["title"] for i in api.cal["items"] if i["date"] == "2026-10-18"]
        assert sunday == ["Coach draft · Rest / mobility 20'"]
        long_day = [i["date"] for i in api.cal["items"] if "Long run" in i["title"]]
        assert long_day and long_day[0] < "2026-10-18"
        assert "## Your notes about the week" in res["report"] and "No training: Sun 18/10" in res["report"]
        assert todo.completed == ["t0"] and todo.labels["t0"] == ["coach"]        # not "buy milk"
        plan = [t for t in todo.plan if "coach-plan" in t.labels]
        assert len(plan) == 8 and plan[0].due_date == tue and plan[-1].due_date == dt.date(2026, 10, 20)
        assert "The next 8 days are in your Todoist" in res["report"]
        # Wednesday: the note is now in the diary (done, labelled) and still applies
        todo.day = wed
        claude2 = FakeClaude(keep, schedule=dict(sched, notes_used=[]))
        engine.run_day(FakeGarmin(wed), cfg, wed, notes_api=todo, ai_client=claude2,
                       now=dt.datetime(2026, 10, 14, 8))
        sent = json.loads(claude2.schedule_requests[0]["messages"][0]["content"].split("\n", 1)[1])
        assert any("can't train on Sunday" in e["text"] for e in sent["earlier_notes"])
        open_plan = [t for t in todo.plan if "coach-plan" in t.labels and t.id not in todo.completed]
        assert len(open_plan) == 8 and open_plan[0].due_date == wed          # replaced, not added
        done = [t for t in todo.plan if t.id in todo.completed]
        assert [t.due_date for t in done] == [tue]                           # yesterday: completed, not overdue
    # unknown or out-of-range dates and note ids from the AI are ignored
    from coach import ai
    bad = FakeClaude(schedule=dict(NO_SCHEDULE, unavailable=["2025-01-01", "soon"], notes_used=["zzz"],
                                   moves=[{"kind": "run_long", "date": "2026-10-17"}]))
    out = ai.schedule_changes(CFG, {"today": "2026-10-13", "until": "2026-10-26", "open_notes": []}, client=bad)
    assert out["unavailable"] == set() and out["notes_used"] == [] and out["moves"] == [("run_long", dt.date(2026, 10, 17))]


def test_evening_notification_lists_the_week():
    sat = dt.date(2026, 10, 17)
    sent = []
    os.environ["NTFY_TOPIC"] = "t"
    try:
        with fresh():
            engine.scheduled(FakeGarmin(sat), _with_preview(7), dt.datetime(2026, 10, 17, 21, 2),
                             poster=lambda url, p: sent.append(p))
    finally:
        os.environ.pop("NTFY_TOPIC")
    assert sent[0]["title"].startswith("Tomorrow: ")
    nxt = sent[0]["message"].split("Next: ")[1]
    assert nxt.count(";") == 5                                     # the 6 days after tomorrow


def test_doubles_and_race_specific_long_runs():
    """Performance weeks: run intervals + a recovery spin on the same day, both uploaded;
    long runs in build/specific phases finish at goal pace."""
    from coach import paces as P
    from coach.planner import Phase, make_run
    tue = dt.date(2026, 10, 13)
    cfg = dict(CFG, weekly_templates=dict(CFG["weekly_templates"],
                                          run_focus=dict(CFG["weekly_templates"]["run_focus"],
                                                         tue=["run_quality", "bike_spin"],
                                                         wed=["bike_quality"])))
    with fresh():
        api = FakeGarmin(tue)
        res = engine.run_day(api, cfg, tue)
    kinds = [s.kind for s in res["final"]]
    assert kinds == ["run_quality", "bike_spin"]
    assert [w["workoutName"] for w in api.uploaded] == [f"Coach · {s.title}" for s in res["final"]]
    assert res["final"][1].title == "Recovery spin 35'"
    P.set_current(P.estimate(CFG, {"5k": 1325, "10k": 2760, "hm": 6120}, 25))
    lr = make_run("run_long", 16, Phase("half", "build", "x", None, None, 1, False, 18))
    assert lr.steps[-1]["pace"] == "hm" and "last 2.5 km at half-marathon pace (4:50/km)" in lr.detail
    base = make_run("run_long", 16, Phase("half", "base", "x", None, None, 1, False, 18))
    assert all(st.get("pace") is None for st in base.steps)
    # a tired (orange) day keeps the recovery spin as it is
    from coach.adjust import adjust
    from coach.planner import make_bike
    out, _ = adjust([make_bike("bike_spin", Phase("short", "base", "x", None, None, 1, False, 14))],
                    {"level": "orange", "score": 40, "reasons": [], "flags": {}}, {}, 
                    Phase("short", "base", "x", None, None, 1, False, 14), [])
    assert out[0].kind == "bike_spin"


def test_evening_ticks_off_today_so_tomorrow_is_on_top():
    from coach.notes import PLAN_MARK
    mon = dt.date(2026, 10, 12)
    with fresh():
        todo = FakeTodoist([])
        todo.day = mon
        engine.run_day(FakeGarmin(mon), _with_preview(7), mon, notes_api=todo, now=dt.datetime(2026, 10, 12, 8))
        assert min(t.due_date for t in todo.plan) == mon
        engine.scheduled(FakeGarmin(mon), _with_preview(7), dt.datetime(2026, 10, 12, 21, 3), notes_api=todo)
    open_plan = [t for t in todo.plan if t.id not in todo.completed]
    assert min(t.due_date for t in open_plan) == mon + dt.timedelta(days=1)
    assert [t.due_date for t in todo.plan if t.id in todo.completed] == [mon]
    assert all(t.description.endswith(PLAN_MARK) for t in open_plan)


def test_easy_runs_not_shorter_than_your_usual_run():
    """Back to running after a cycling block: one 6 km run last week (after a long ride), long-run
    minimum 8 km. The old split gave 10 km = 8 km long run + 2 × 4 km; now no run is under 6 km."""
    tue = dt.date(2026, 10, 13)
    api = FakeGarmin(tue)
    api.acts = [act("2026-10-04", "road_biking", 90, 210), act("2026-10-04", "running", 6, 36),
                act("2026-10-08", "road_biking", 40, 90), act("2026-09-27", "road_biking", 70, 160),
                act("2026-09-20", "running", 4.2, 26)]
    cfg = dict(CFG, min_long_run_km=8, weekly_templates=dict(CFG["weekly_templates"],
               run_focus=dict(CFG["weekly_templates"]["run_focus"], tue=["run_quality", "bike_spin"])))
    with fresh():
        res = engine.run_day(api, cfg, tue)
    run = next(s for s in res["final"] if s.sport == "run")
    assert run.distance_km >= 5, run.title          # your usual 6 km run, maybe trimmed by readiness
    assert "Week raised from 10 to" in res["report"] and "usual run lately (" in res["report"]
    from coach.planner import compute_week_target
    wt = compute_week_target([a | {} for a in [
        {"date": "2026-10-04", "sport": "run", "distance_km": 6, "duration_min": 36}]], tue, cfg)
    assert wt["return_mode"] and wt["typical_run_km"] == 6


def test_lasting_preferences_are_kept_in_todoist_and_reach_the_ai():
    tue, wed = dt.date(2026, 10, 13), dt.date(2026, 10, 14)
    keep = {"decision": "keep", "sessions": [{"kind": "run_easy", "distance_km": 6, "minutes": None}],
            "why": "As planned.", "coach_note": "", "notes_used": []}
    with fresh():
        todo = FakeTodoist([(dt.datetime(2026, 10, 13, 12, 30), "next time give me 1 minute before the strides")])
        todo.day = tue
        pref = ["Walk or jog easy for 1' before the strides."]
        claude = FakeClaude(keep, schedule=dict(NO_SCHEDULE, preferences=pref, notes_used=["t0"]))
        res = engine.run_day(FakeGarmin(tue), CFG, tue, notes_api=todo, ai_client=claude,
                             now=dt.datetime(2026, 10, 13, 13))
        assert "## Saved to your coach preferences" in res["report"] and pref[0] in res["report"]
        assert todo.completed == ["t0"]
        task = next(t for t in todo.plan if t.content == "📌 Coach preferences")
        assert task.description == "• " + pref[0] and task.due_date is None
        # the next day: read back from Todoist, passed to both AI questions
        todo.day = wed
        claude2 = FakeClaude(keep, schedule=dict(NO_SCHEDULE, preferences=pref))
        res = engine.run_day(FakeGarmin(wed), CFG, wed, notes_api=todo, ai_client=claude2,
                             now=dt.datetime(2026, 10, 14, 8))
        sent = json.loads(claude2.requests[0]["messages"][0]["content"].split("\n", 1)[1])
        assert sent["athlete_preferences"] == pref
        assert len([t for t in todo.plan if t.content == "📌 Coach preferences"]) == 1
        assert "<summary>What the AI coach saw</summary>" in res["report"] and '"athlete_preferences"' in res["report"]
    from coach.planner import Phase, make_run
    strides = make_run("run_strides", 6, Phase("short", "base", "x", None, None, 1, False, 14))
    assert strides.steps[1] == {"type": "recovery", "end": "time", "value": 60, "zone": 1}


def test_rides_are_at_least_30_minutes():
    """'I could swim today' → AI picks a swim + 20' spin: the spin becomes 30'."""
    wed = dt.date(2026, 10, 14)
    answer = {"decision": "adjust", "why": "Easy swim and spin.", "coach_note": "", "notes_used": [],
              "sessions": [{"kind": "swim_technique", "distance_km": 1.3, "minutes": None},
                           {"kind": "bike_spin", "distance_km": None, "minutes": 20}]}
    cfg = dict(CFG, weekly_templates=dict(CFG["weekly_templates"],
               run_focus=dict(CFG["weekly_templates"]["run_focus"], wed=["bike_quality"])))
    with fresh():
        res = engine.run_day(FakeGarmin(wed), cfg, wed, ai_client=FakeClaude(answer))
    spin = next(s for s in res["final"] if s.sport == "bike")
    assert spin.duration_min == 30 and "shorter rides aren't worth" in res["report"]


def test_ride_to_the_pool_is_not_training():
    """A recorded 10' ride to the pool doesn't count as today's planned ride or as yesterday's."""
    wed = dt.date(2026, 10, 14)
    cfg = dict(CFG, weekly_templates=dict(CFG["weekly_templates"],
               run_focus=dict(CFG["weekly_templates"]["run_focus"], wed=["bike_quality"])))
    api = FakeGarmin(wed, today_acts=[act("2026-10-14", "road_biking", 3, 10)])
    with fresh():
        res = engine.run_day(api, cfg, wed)
    assert any(s.sport == "bike" for s in res["final"]) and "Already done today" not in res["report"]
    from coach.readiness import compliance
    c = compliance([{"title": "Bike 5×5' tempo", "sport": "bike", "kind": "bike_quality", "intensity": "hard"}],
                   [{"sport": "bike", "name": "ride", "distance_km": 3, "duration_min": 10}])
    assert c["missed"] == ["Bike 5×5' tempo"]


def test_swims_build_up_from_what_you_actually_swam():
    """Planned 1300 m, swam 300 m, rest, 100 m, rest, 100 m: the next swim is sized to that."""
    from coach.garmin_api import swim_intervals
    laps = {"lapDTOs": [
        {"startTimeGMT": "2026-10-14T10:00:00", "duration": 420, "distance": 300, "numberOfActiveLengths": 12},
        {"startTimeGMT": "2026-10-14T10:07:00", "duration": 30, "distance": 0, "numberOfActiveLengths": 0},
        {"startTimeGMT": "2026-10-14T10:07:30", "duration": 140, "distance": 100, "numberOfActiveLengths": 4},
        {"startTimeGMT": "2026-10-14T10:11:00", "duration": 140, "distance": 100, "numberOfActiveLengths": 4},
    ]}
    info = swim_intervals(laps)
    assert info["swum_m"] == 500 and info["longest_m"] == 300 and info["stops"] == 2
    assert info["intervals"].startswith('300 m · rest 30" · 100 m · rest 70"')
    fri = dt.date(2026, 10, 23)                          # next Friday: optional technique swim
    api = FakeGarmin(fri, swims=False)
    swim_act = act("2026-10-14", "lap_swimming", 0.5, 20)
    api.acts.append(swim_act)
    api.splits = {swim_act["activityId"]: laps}
    claude = FakeClaude({"decision": "keep", "sessions": [{"kind": "swim_technique", "distance_km": 0.6,
                                                          "minutes": None}],
                         "why": "x", "coach_note": "", "notes_used": []})
    with fresh():
        res = engine.run_day(api, CFG, fri, ai_client=claude)
    sw = next(s for s in res["final"] if s.sport == "swim")
    assert sw.distance_km <= 0.6 and "Build-up swim" in sw.title and "×100" in sw.detail
    sent = json.loads(claude.requests[0]["messages"][0]["content"].split("\n", 1)[1])
    done = next(a for a in sent["activities_last_6_weeks"] if a["sport"] == "swim" and a["date"] == "2026-10-14")
    assert done["swum_m"] == 500 and done["stops"] == 2 and sent["swim_capacity"]["cap_m"] == 600
    from coach.readiness import compliance
    c = compliance([{"title": "Technique swim 1300 m", "sport": "swim", "kind": "swim_technique", "intensity": "easy",
                     "distance_km": 1.3}], [{"sport": "swim", "name": "Pool", "distance_km": 0.5, "duration_min": 20}])
    assert c["vs_plan"] == [{"planned": "Technique swim 1300 m", "planned_km": 1.3, "planned_min": None,
                             "done_km": 0.5, "done_min": 20}]
    # no recent swims: a short restart, not 1300 m
    from coach.planner import swim_capacity
    assert swim_capacity([], fri)["cap_m"] == 600


def test_strength_sessions_for_a_home_gym():
    from coach import strength as S
    from coach.planner import Phase
    from coach.workouts import to_garmin
    from coach.adjust import adjust
    S.set_loads({"strength": {"loads": {"front_squat": 32, "hip_thrust": 12, "dumbbell": 10}, "max_bar_kg": 48}})
    base = Phase("short", "base", "x", None, None, 1, False, 14)
    s = S.make_strength("strength", base, 0)
    assert s.sport == "strength" and "Front squat · 8 reps @ 32 kg" in s.detail and 25 <= s.duration_min <= 50
    assert "Front squat · 8 reps @ 36 kg" in S.make_strength("strength", base, 2).detail      # +2 kg a week
    assert "@ 32 kg" in S.make_strength("strength", base, 3).detail                           # deload week
    S.set_loads({"strength": {"loads": {"front_squat": 46}, "max_bar_kg": 48}})
    assert "@ 48 kg" in S.make_strength("strength", base, 2).detail                           # what you can load
    power = S.make_strength("strength", Phase("half", "build", "x", None, None, 1, False, 18), 0)
    assert power.title.startswith("Strength · power") and "Box jump" in power.detail
    assert S.make_strength("strength", Phase("short", "taper", "x", None, None, 1, False, 11)).kind == "strength_core"
    w = to_garmin(s, CFG, "x")
    assert w["sportType"]["sportTypeKey"] == "strength_training" and w["estimatedDurationInSecs"] == s.duration_min * 60
    step = w["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][0]
    # each exercise is Garmin's own: the watch shows its name, counts the reps and shows the weight
    assert step["endCondition"]["conditionTypeKey"] == "reps" and step["endConditionValue"] == 8
    assert (step["category"], step["exerciseName"]) == ("SQUAT", "BARBELL_FRONT_SQUAT")
    assert step["weightValue"] == 32000.0 and step["weightUnit"]["unitKey"] == "kilogram"
    assert step["description"].startswith("Front squat")
    from coach.strength import GARMIN_EXERCISE
    from garminconnect.exercises import _RAW
    known = {(c, e) for _, c, e in _RAW}
    assert all(e == "" or (c, e) in known for c, e in GARMIN_EXERCISE.values())
    # tired: lower-body strength becomes strength & core (amber) or is dropped (orange)
    amber, _ = adjust([s], {"level": "amber", "score": 60, "reasons": [], "flags": {}}, {}, base, [])
    assert [x.kind for x in amber] == ["strength_core"]
    orange, _ = adjust([s], {"level": "orange", "score": 40, "reasons": [], "flags": {}}, {}, base, [])
    assert not any(x.sport == "strength" for x in orange)
    # in a week: uploaded next to the bike intervals
    wed = dt.date(2026, 10, 14)
    cfg = dict(CFG, weekly_templates=dict(CFG["weekly_templates"],
               run_focus=dict(CFG["weekly_templates"]["run_focus"], wed=["bike_quality", "strength"])))
    with fresh():
        api = FakeGarmin(wed)
        res = engine.run_day(api, cfg, wed)
    assert [x.sport for x in res["final"]] == ["bike", "strength"]
    assert any(u["workoutName"].startswith("Coach · Strength") for u in api.uploaded)
    from coach.calendar import session_from
    assert session_from("Strength & core 33'")["kind"] == "strength_core"


def test_late_evening_run_after_midnight_still_plans_the_day():
    """GitHub ran the 21:00 schedule at 01:14: today isn't planned yet, so it's planned now."""
    wed = dt.date(2026, 10, 14)
    with fresh():
        res = engine.scheduled(FakeGarmin(wed), CFG, dt.datetime(2026, 10, 14, 1, 14))
        assert res["status"] == "planned" and "## Tomorrow" in res["report"]
        # planned: a later early run does nothing, and the morning check revisits it
        assert engine.scheduled(FakeGarmin(wed), CFG, dt.datetime(2026, 10, 14, 1, 47))["status"] == "early"


def test_plan_lists_only_what_to_train_and_strength_reads_line_by_line():
    wed = dt.date(2026, 10, 14)
    cfg = dict(CFG, weekly_templates=dict(CFG["weekly_templates"],
               run_focus=dict(CFG["weekly_templates"]["run_focus"], wed=["bike_quality", "strength"])))
    api = FakeGarmin(wed, today_acts=[act("2026-10-14", "lap_swimming", 0.5, 20)])
    api.acts[-1]["activityName"] = "Lunch Swim"
    todo = FakeTodoist([])
    todo.day = wed
    with fresh():
        engine.run_day(api, cfg, wed, notes_api=todo, now=dt.datetime(2026, 10, 14, 13, 37))
    task = next(t for t in todo.plan if t.due_date == wed)
    assert task.content.startswith("Bike") and "Lunch Swim" not in task.content     # an unplanned swim isn't listed
    lines = task.description.splitlines()
    assert "• Front squat · 8 reps @ 30 kg" in lines
    assert any(x.startswith("3 rounds (90\" rest") for x in lines)
    from coach.notify import message
    from coach.planner import make_rest
    title, _ = message([make_rest()], "", "", {"score": 70}, done=["Technique swim 1300 m"])
    assert title == "Today: Technique swim 1300 m + Rest / mobility 20'"


def test_replanning_keeps_sessions_already_done():
    """Morning plan: swim + bike. You swim, then the plan is updated at noon: the swim stays in the
    calendar as it was (done), only what's left is replaced."""
    wed = dt.date(2026, 10, 14)
    cfg = dict(CFG, weekly_templates=dict(CFG["weekly_templates"],
               run_focus=dict(CFG["weekly_templates"]["run_focus"], wed=["swim_technique", "bike_quality"])))
    todo = FakeTodoist([])
    todo.day = wed
    with fresh():
        api = FakeGarmin(wed)
        engine.run_day(api, cfg, wed, now=dt.datetime(2026, 10, 14, 8), notes_api=todo)
        swim_item = next(i for i in api.cal["items"] if i["date"] == "2026-10-14" and "swim" in i["title"].lower())
        later = FakeGarmin(wed, today_acts=[act("2026-10-14", "lap_swimming", 0.5, 20)])
        res = engine.run_day(later, cfg, wed, now=dt.datetime(2026, 10, 14, 13, 37), notes_api=todo)
        today = [i for i in later.cal["items"] if i["date"] == "2026-10-14"]
        assert swim_item in today                                   # untouched
        assert ("sched", swim_item["id"]) not in later.deleted
        assert "Kept as they were (already done today)" in res["report"]
    task = next(t for t in todo.plan if t.due_date == wed and t.id not in todo.completed)
    assert "swim" in task.content.lower() and "✓" not in task.content and "done" not in task.content.lower()


def test_intervals_start_with_the_run_to_the_track_or_path():
    from coach import paces as P
    from coach.planner import Phase, make_run, set_venues
    from coach.workouts import to_garmin
    P.set_current(P.estimate(CFG, {"5k": 1325, "10k": 2760, "hm": 6120}, 25))
    set_venues({"athlete": {"track_warmup_km": 2.5, "path_warmup_km": 3}})
    try:
        track = make_run("run_quality", 9, Phase("short", "base", "x", None, None, 1, False, 14), 0)   # rolling 400s
        lines = track.detail.splitlines()
        assert lines[0] == "2.5 km easy (easy run to the track)" and lines[-2] == "2.5 km easy (easy run home)"
        assert track.steps[0]["end"] == "distance" and track.steps[0]["value"] == 2500
        assert 8 <= track.distance_km <= 10
        path = make_run("run_quality", 10, Phase("half", "build", "x", None, None, 1, False, 18), 0)  # cruise intervals
        assert path.detail.startswith("3 km easy (easy run to your flat path)")
        step = to_garmin(track, CFG, "x")["workoutSegments"][0]["workoutSteps"][0]
        assert step["endCondition"]["conditionTypeKey"] == "distance" and step["description"] == "easy run to the track"
    finally:
        set_venues({})
    assert make_run("run_quality", 9, Phase("short", "base", "x", None, None, 1, False, 14), 0).detail.startswith("15' easy")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
