"""Offline tests: a fake Garmin with different scenarios.
Run:  python -m pytest -q tests   (or  python tests/test_scenarios.py)"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yaml  # noqa: E402

from coach import engine  # noqa: E402
from coach.planner import plan_day, phase_for  # noqa: E402
from coach.workouts import to_garmin  # noqa: E402

CFG = yaml.safe_load((Path(__file__).resolve().parent / "test_config.yaml").read_text(encoding="utf-8"))


def act(date, typ, km, mins, aero=2.5, anae=0.5, load=None):
    return {"activityId": hash((date, typ, km)) % 10**8, "activityName": typ, "startTimeLocal": f"{date} 07:00:00",
            "activityType": {"typeKey": typ}, "distance": km * 1000, "duration": mins * 60,
            "averageHR": 140, "aerobicTrainingEffect": aero, "anaerobicTrainingEffect": anae,
            "activityTrainingLoad": load or mins * 1.2}


class FakeGarmin:
    def __init__(self, today, good=True, extra_yesterday=0.0, swims=True):
        self.today = today
        self.good = good
        self.uploaded, self.scheduled, self.deleted = [], [], []
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
        self.acts = acts

    def get_activities_by_date(self, s, e):
        return [a for a in self.acts if s <= a["startTimeLocal"][:10] <= e]

    def get_sleep_data(self, d):
        return {"dailySleepDTO": {"sleepTimeSeconds": (7.8 if self.good else 5.2) * 3600,
                                  "sleepScores": {"overall": {"value": 85 if self.good else 48}}}}

    def get_hrv_data(self, d):
        return {"hrvSummary": {"lastNightAvg": 62 if self.good else 41, "weeklyAvg": 60,
                               "status": "BALANCED" if self.good else "LOW",
                               "baseline": {"balancedLow": 52, "balancedUpper": 70}}}

    def get_stats(self, d):
        return {"restingHeartRate": 48 if self.good else 57, "lastSevenDaysAvgRestingHeartRate": 49,
                "averageStressLevel": 24 if self.good else 48, "bodyBatteryHighestValue": 88 if self.good else 35}

    def get_body_battery(self, d):
        v = 88 if self.good else 35
        return [{"bodyBatteryValuesArray": [[1, v - 20], [2, v]]}]

    def get_training_readiness(self, d):
        return [{"score": 82 if self.good else 22, "level": "HIGH" if self.good else "LOW"}]

    def get_training_status(self, d):
        return {"mostRecentTrainingStatus": {"latestTrainingStatusData": {"123": {
            "trainingStatusFeedbackPhrase": "PRODUCTIVE",
            "acuteTrainingLoadDTO": {"dailyAcuteChronicWorkloadRatio": 1.1}}}}}

    def upload_workout(self, w):
        json.dumps(w)  # must be serializable
        self.uploaded.append(w)
        return {"workoutId": len(self.uploaded)}

    def schedule_workout(self, wid, date):
        self.scheduled.append((wid, date))
        return {"workoutScheduleId": 1000 + wid}

    def unschedule_workout(self, sid):
        self.deleted.append(("sched", sid))

    def delete_workout(self, wid):
        self.deleted.append(("w", wid))


def _run(day, **kw):
    api = FakeGarmin(day, **kw)
    with tempfile.TemporaryDirectory() as tmp:
        res = engine.run_day(api, CFG, day, upload=True, state_path=Path(tmp) / "s.json")
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


def test_no_swims_marks_priority():
    day = dt.date(2026, 10, 16)  # Friday → swim
    api, res = _run(day, swims=False)
    assert any(s.priority for s in res["final"] if s.sport == "swim")


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
    with tempfile.TemporaryDirectory() as tmp:
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
    with tempfile.TemporaryDirectory() as tmp:
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
    assert "TZ: America/New_York" in flow and '"30 11 * * *"' in flow
    for i in range(0, 260, 3):   # the planner accepts it for the whole season
        plan_day(dt.date.today() + dt.timedelta(days=i), cfg, 30)


def test_setup_accepts_proposed_settings_with_enter():
    """With suggestions from Garmin, pressing Enter keeps them; only races are typed."""
    from coach import setup_wizard
    d1 = (dt.date.today() + dt.timedelta(days=90)).isoformat()
    answers = iter(["", "Gran fondo", "", "150", "2000", d1, "", ""])
    sug = {"found": ["x"], "sport": "bike", "run_days": 1, "long_day": "sat", "rest_day": "mon",
           "pace": "5:20", "tz": "Europe/Zurich", "pool": 25}
    with tempfile.TemporaryDirectory() as tmp:
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
