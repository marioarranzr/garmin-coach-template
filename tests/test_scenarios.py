"""Offline tests: a fake Garmin with different scenarios.
Run:  python -m pytest -q tests   (or  python tests/test_scenarios.py)"""
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
for _k in ("ANTHROPIC_API_KEY", "TODOIST_API_TOKEN"):
    os.environ.pop(_k, None)

CFG = yaml.safe_load((Path(__file__).resolve().parent / "test_config.yaml").read_text(encoding="utf-8"))


def act(date, typ, km, mins, aero=2.5, anae=0.5, load=None):
    return {"activityId": hash((date, typ, km)) % 10**8, "activityName": typ, "startTimeLocal": f"{date} 07:00:00",
            "activityType": {"typeKey": typ}, "distance": km * 1000, "duration": mins * 60,
            "averageHR": 140, "aerobicTrainingEffect": aero, "anaerobicTrainingEffect": anae,
            "activityTrainingLoad": load or mins * 1.2}


class FakeGarmin:
    def __init__(self, today, good=True, extra_yesterday=0.0, swims=True, synced=True, steps=0, today_acts=(),
                 bb_now=None, tr_now=None, last_sync=None):
        self.today = today
        self.good = good
        self.synced = synced          # False: last night's sleep hasn't reached Garmin yet
        self.steps = steps            # steps so far today (you're up and wearing the watch)
        self.bb_now, self.tr_now = bb_now, tr_now   # later in the day, e.g. after a workout
        self.last_sync = last_sync                  # datetime of the watch's last upload
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
        self.acts = acts + list(today_acts)

    def get_activities_by_date(self, s, e):
        return [a for a in self.acts if s <= a["startTimeLocal"][:10] <= e]

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
    assert "TZ: America/New_York" in flow and '"30 11-15 * * *"' in flow
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


def test_scheduled_runs_wait_for_sleep_data():
    """Hourly morning runs: wait while the watch hasn't synced, plan once, then stop."""
    day = dt.date(2026, 10, 13)
    at = lambda h, m=0: dt.datetime.combine(day, dt.time(h, m))
    with tempfile.TemporaryDirectory() as tmp:
        sp = Path(tmp) / "s.json"
        asleep = FakeGarmin(day, synced=False)
        res = engine.run_day(asleep, CFG, day, state_path=sp, wait_for_sleep=True, now=at(7))
        assert res["status"] == "waiting" and not asleep.uploaded and not sp.exists()

        awake = FakeGarmin(day)                                   # synced after waking up
        res = engine.run_day(awake, CFG, day, state_path=sp, wait_for_sleep=True, now=at(9))
        assert res["status"] == "planned" and awake.uploaded

        later = FakeGarmin(day)
        res = engine.run_day(later, CFG, day, state_path=sp, wait_for_sleep=True, now=at(10))
        assert res["status"] == "done" and not later.uploaded and not later.deleted

        manual = FakeGarmin(day)                                  # "Run workflow" by hand: re-plans
        res = engine.run_day(manual, CFG, day, state_path=sp, now=at(10, 30))
        assert res["status"] == "planned" and manual.uploaded

    with tempfile.TemporaryDirectory() as tmp:                    # never synced: plan at the deadline
        sp = Path(tmp) / "s.json"
        api = FakeGarmin(day, synced=False)
        res = engine.run_day(api, CFG, day, state_path=sp, wait_for_sleep=True, now=at(10, 3))
        assert res["status"] == "planned" and api.uploaded and "no sleep data from last night" in res["report"]
        res = engine.run_day(FakeGarmin(day, synced=False), CFG, day, state_path=sp, wait_for_sleep=True, now=at(11))
        assert res["status"] == "done"        # still no sleep data: don't re-upload the same thing
        res = engine.run_day(FakeGarmin(day), CFG, day, state_path=sp, wait_for_sleep=True, now=at(12))
        assert res["status"] == "planned"     # sleep arrived late: re-plan once with it
        res = engine.run_day(FakeGarmin(day), CFG, day, state_path=sp, wait_for_sleep=True, now=at(13))
        assert res["status"] == "done"

    with tempfile.TemporaryDirectory() as tmp:                    # up and walking, but no sleep recorded
        sp = Path(tmp) / "s.json"
        api = FakeGarmin(day, synced=False, steps=1800)
        res = engine.run_day(api, CFG, day, state_path=sp, wait_for_sleep=True, now=at(8))
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
    with tempfile.TemporaryDirectory() as tmp:
        api = FakeGarmin(sunday, today_acts=[ride])
        res = engine.run_day(api, CFG, sunday, state_path=Path(tmp) / "s.json")
        runs = [s for s in res["final"] if s.sport == "run"]
        assert runs and any("keep this run easy" in n for n in res["notes"])
    run_done = act(sunday.isoformat(), "running", 9, 50)
    with tempfile.TemporaryDirectory() as tmp:
        api = FakeGarmin(sunday, today_acts=[run_done])
        res = engine.run_day(api, CFG, sunday, state_path=Path(tmp) / "s.json")
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
    with tempfile.TemporaryDirectory() as tmp:
        api = FakeGarmin(mon)
        api.acts = [a for a in api.acts if a["activityType"]["typeKey"] != "running"]
        for a in runs([2, 2, 2, 6]):
            api.acts.append(act(a["date"], "running", a["distance_km"], a["duration_min"]))
        res = engine.run_day(api, CFG, mon, state_path=Path(tmp) / "s.json")
        assert any("instead of Sunday" in n for n in res["notes"])


def test_setup_rerun_keeps_current_settings_and_races():
    """Re-running setup starts from config.yaml: Enter + Enter keeps everything, including tweaks."""
    import shutil
    from coach import setup_wizard
    future = (dt.date.today() + dt.timedelta(days=60)).isoformat()
    with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
            res = engine.run_day(api, CFG, api.today, state_path=Path(tmp) / "s.json")
        line = res["report"].splitlines()[2]
        assert expected in line, line
        assert line.count(".") <= 2 and len(line) < 180          # one short sentence
        assert api.uploaded[0]["description"].startswith(line.strip("*"))
    with tempfile.TemporaryDirectory() as tmp:                   # B race: mini taper wording
        res = engine.run_day(FakeGarmin(dt.date(2026, 12, 11)), CFG, dt.date(2026, 12, 11),
                             state_path=Path(tmp) / "s.json")
    assert "A few easy days before Autumn 8.5 km race" in res["report"]


def test_moderate_ride_today_is_counted():
    """A 30 km / 55' easy ride this morning: listed in the report, and today's run stays easy."""
    sunday = dt.date(2026, 10, 18)
    ride = act(sunday.isoformat(), "road_biking", 30, 55, aero=2.2)
    with tempfile.TemporaryDirectory() as tmp:
        api = FakeGarmin(sunday, today_acts=[ride])
        res = engine.run_day(api, CFG, sunday, state_path=Path(tmp) / "s.json")
    assert "Done today so far: road_biking (30 km, 55')" in res["report"]
    assert "Keep it easy: you already did a 30 km ride today." in res["report"]
    assert "(scores " in res["report"] and "→" not in res["report"].split("## Yesterday")[0]


def test_after_training_uses_current_body_battery_and_readiness():
    """Woke at Body Battery 88, rode in the morning, now 40 and Training Readiness down to 35."""
    sunday = dt.date(2026, 10, 18)
    ride = act(sunday.isoformat(), "road_biking", 30, 58, aero=2.3)
    with tempfile.TemporaryDirectory() as tmp:
        res = engine.run_day(FakeGarmin(sunday, today_acts=[ride], bb_now=40, tr_now=35), CFG, sunday,
                             state_path=Path(tmp) / "s.json")
    rep = res["report"]
    assert "Body Battery now: 40" in rep and "Training Readiness now: 35" in rep
    assert res["readiness"]["level"] != "green"
    assert "Body Battery" in rep.splitlines()[2] or "Training Readiness" in rep.splitlines()[2]
    # first thing in the morning the latest values are the waking ones
    with tempfile.TemporaryDirectory() as tmp:
        res = engine.run_day(FakeGarmin(sunday), CFG, sunday, state_path=Path(tmp) / "s.json")
    assert "- Body Battery: 88" in res["report"] and "- Training Readiness: 82" in res["report"]
    # later in the day without training: still the latest values
    with tempfile.TemporaryDirectory() as tmp:
        res = engine.run_day(FakeGarmin(sunday, bb_now=60, tr_now=70), CFG, sunday, state_path=Path(tmp) / "s.json")
    assert "- Body Battery now: 60" in res["report"] and "- Training Readiness now: 70" in res["report"]


def test_report_shows_watch_sync_and_warns_when_stale():
    day = dt.date(2026, 10, 13)
    now = dt.datetime.combine(day, dt.time(16, 0))
    with tempfile.TemporaryDirectory() as tmp:
        res = engine.run_day(FakeGarmin(day, last_sync=now - dt.timedelta(minutes=20)), CFG, day,
                             state_path=Path(tmp) / "s.json", now=now)
    assert "last sync: Tue 15:40" in res["report"] and "⚠️" not in res["report"]
    with tempfile.TemporaryDirectory() as tmp:
        res = engine.run_day(FakeGarmin(day, last_sync=now - dt.timedelta(hours=9)), CFG, day,
                             state_path=Path(tmp) / "s.json", now=now)
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
    with tempfile.TemporaryDirectory() as tmp:
        res = engine.run_day(api, cfg, sunday, state_path=Path(tmp) / "s.json")
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
    def __init__(self, notes):              # notes: list of (hh:mm, text) for "today"
        from types import SimpleNamespace as NS
        self.day = None
        self.notes = notes
        self.NS = NS

    def get_projects(self):
        yield [self.NS(id="p1", name="Work", is_inbox_project=False),
               self.NS(id="inbox", name="Inbox", is_inbox_project=True)]

    def get_tasks(self, project_id=None):
        assert project_id == "inbox"
        yield [self.NS(id=f"t{i}", content=text, description="",
                       created_at=dt.datetime.combine(self.day, dt.time(*map(int, hhmm.split(":")))))
               for i, (hhmm, text) in enumerate(self.notes)]


class FakeClaude:
    def __init__(self, answer=None, fail=False):
        from types import SimpleNamespace as NS
        self.answer, self.fail, self.requests = answer, fail, []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        from types import SimpleNamespace as NS
        self.requests.append(kw)
        if self.fail:
            raise RuntimeError("overloaded")
        return NS(content=[NS(type="text", text=json.dumps(self.answer))])


def _ai_day(answer=None, notes=(), fail=False, now=None, state_path=None, wait=False, api=None):
    day = dt.date(2026, 10, 18)                     # Sunday: long run planned
    todo = FakeTodoist(list(notes))
    todo.day = day
    claude = FakeClaude(answer, fail)
    api = api or FakeGarmin(day)
    res = engine.run_day(api, CFG, day, state_path=state_path, notes_api=todo, ai_client=claude,
                         now=now or dt.datetime.combine(day, dt.time(9)), wait_for_sleep=wait)
    return res, claude, api


def test_ai_coach_can_choose_rest_from_your_note():
    answer = {"decision": "rest", "sessions": [{"kind": "rest", "distance_km": None, "minutes": None}],
              "why": "Rest today: you mention knee pain, and running on it risks a longer layoff.",
              "coach_note": "Ice it and see how it feels tomorrow; an easy swim is fine if it doesn't hurt.",
              "notes_used": ["knee hurts a bit"]}
    with tempfile.TemporaryDirectory() as tmp:
        res, claude, api = _ai_day(answer, notes=[("07:40", "knee hurts a bit"), ("08:05", "buy milk")],
                                   state_path=Path(tmp) / "s.json")
    assert [s.kind for s in res["final"]] == ["rest"]
    rep = res["report"]
    assert rep.splitlines()[2] == "**Rest today: you mention knee pain, and running on it risks a longer layoff.**"
    assert "Decided by the AI coach" in rep and "“knee hurts a bit” (07:40)" in rep and "Taken into account" in rep
    sent = json.loads(claude.requests[0]["messages"][0]["content"].split("\n", 1)[1])
    assert sent["athlete_notes_today"][0]["text"] == "knee hurts a bit"
    assert sent["rule_proposal_today"] and sent["activities_last_14_days"]
    assert api.uploaded[0]["description"].startswith("Rest today")


def test_ai_coach_cannot_exceed_the_plan():
    answer = {"decision": "adjust", "why": "Feeling strong, go long.", "coach_note": "", "notes_used": [],
              "sessions": [{"kind": "run_long", "distance_km": 25, "minutes": None},
                           {"kind": "bike_quality", "distance_km": None, "minutes": 90}]}
    with tempfile.TemporaryDirectory() as tmp:
        res, _, _ = _ai_day(answer, state_path=Path(tmp) / "s.json")
    long_run = next(s for s in res["final"] if s.kind == "run_long")
    assert long_run.distance_km <= 13                       # the rule proposal's long run
    assert not any(s.kind == "bike_quality" for s in res["final"])
    assert any("Safety limit" in n for n in res["notes"])


def test_ai_failure_falls_back_to_rules():
    with tempfile.TemporaryDirectory() as tmp:
        res, _, api = _ai_day(fail=True, state_path=Path(tmp) / "s.json")
    assert any(s.kind == "run_long" for s in res["final"]) and api.uploaded
    assert "AI coach unavailable today (overloaded): rule-based plan used." in res["report"]


def test_new_note_after_planning_triggers_replan():
    answer = {"decision": "keep", "sessions": [{"kind": "run_long", "distance_km": 13, "minutes": None}],
              "why": "As planned.", "coach_note": "", "notes_used": []}
    with tempfile.TemporaryDirectory() as tmp:
        sp = Path(tmp) / "s.json"
        day = dt.date(2026, 10, 18)
        at = lambda h: dt.datetime.combine(day, dt.time(h))
        res, _, _ = _ai_day(answer, notes=[("07:40", "slept ok")], state_path=sp, wait=True, now=at(8))
        assert res["status"] == "planned"
        res, _, _ = _ai_day(answer, notes=[("07:40", "slept ok")], state_path=sp, wait=True, now=at(9))
        assert res["status"] == "done"                      # nothing new
        res, claude, _ = _ai_day(answer, notes=[("07:40", "slept ok"), ("09:20", "legs very heavy")],
                                 state_path=sp, wait=True, now=at(10))
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
