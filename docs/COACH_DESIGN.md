# Coach v2 — design: from "plan reviewer" to a real triathlon coach

Status: agreed with Mario on 2026-11-24. Built so far: bigger model + cheaper daily model + AI cost log; ski counts runs down only; load model v1 (`coach/load.py`: stress per activity, CTL/ATL/TSB per sport, leg-fatigue index, given to the day check). Week designer built (`coach/design.py`, `ai.week_design`, default off): the AI chooses which sessions go on which day, code validates, a design is saved in `data/coach_log/week-<monday>.json` and the daily check follows it; preview with the workflow input `design_preview` (`python -m coach design`). Weekly review built too (`coach/review.py`, `ai.weekly_review`). Block review (`coach/block.py`) and body data (`coach/body.py`, from notes) built. Next: backtest on past weeks, threshold estimates, Withings API.

## 1. Goal
Take Mario to his best form for the A races (Silvesterlauf 8.5 km 2026-12-13, Zürich half 2027-04-11,
Zurich City Triathlon sprint 2027-06-27) by planning like a serious triathlon coach: periodised blocks,
load that is measured per sport, weeks designed (not just trimmed), and regular reviews that learn
from what happened.

## 2. What is wrong today
| Problem | Today | Consequence |
|---|---|---|
| Small model | `gpt-5.4-mini` | Weak reasoning over many signals |
| AI can only subtract | keeps / shortens / eases / rests; template week is fixed | No coaching, only a brake |
| Crude load | hours per sport + running load (ACWR) | Skiing, strength, hikes, leg fatigue not understood |
| No learning loop | no review of whether the plan worked | Same mistakes repeat |
| Thin data | no body composition, thresholds typed by hand | Plan can't react to fuelling, muscle loss, fitness gains |

## 3. Target architecture (three clocks)
1. **Block plan (every 3–4 weeks, and after a race):** target phase, weekly load ramp, key sessions, test
   weeks, deloads, taper. Output: `block` (JSON) saved in the repo (`data/coach_log/block-*.json`).
2. **Week design (Sunday evening, and on any big change):** the AI designs the 7 days: sports, durations,
   intensity, key sessions, strength placement. Code validates it against hard limits (below);
   if invalid it is repaired or the rule-based week is used.
3. **Day check (today's morning check / evening plan):** as now, adjusts the designed day with sleep,
   HRV, notes. Can ease, move or swap within the week's intent; can't add load beyond the week plan.

State stays stateless-in-spirit: the plan lives in Garmin/Todoist as today; reviews and blocks are small
JSON files the bot commits (`data/coach_log/`, no secrets).

## 4. Load model (the "serious" part)
- **Per-activity stress score (TSS-like), per sport:**
  - Bike: from power and FTP (NP/IF), else HR (TRIMP).
  - Run: pace vs threshold pace (rTSS), else HR.
  - Swim: pace vs CSS, else duration × intensity.
  - Strength: session RPE × duration; separate **leg-strength** tag.
  - Other (ski, hike, climbing…): HR-based TRIMP, with moving time only (lifts excluded; fixed 2026-11-24)
    and a **leg-load multiplier** (alpine skiing is heavy on quads, back, core; arms light).
- **Fitness / fatigue / form:** CTL (42 d) / ATL (7 d) / TSB per sport and overall.
- **Musculoskeletal load for running:** run-only ATL/CTL (as ACWR today) plus a **leg-fatigue index**
  (runs + heavy leg strength + skiing + long rides over the last 72 h) used to place runs and leg strength.
- **Monotony / strain** and hard-easy distribution (target ≈ 80/20 by time).
- Everything computed in code from Garmin/Strava data (deterministic, testable); the AI receives the
  numbers, it does not compute them.

## 5. Week designer (AI) — inputs, outputs, guardrails
- **Inputs:** block target (weekly stress, key sessions), CTL/ATL/TSB per sport, leg-fatigue index,
  last 6 weeks per sport, races and days to go, calendar constraints (notes: travel, injury, ski days),
  sleep/HRV trend, body data trend, athlete preferences, last weekly review.
- **Output (strict JSON):** for each day: sessions (sport, kind, minutes/km, intensity target, purpose),
  week intent in one sentence, risks, what to watch.
- **Hard guardrails enforced in code (AI can't override):**
  - Run volume ramp ≤ ~10 %/week; no long run +>10 %; return-to-running mode after breaks.
  - No runs on consecutive days (unless a note asks); at least one full rest day; Thursday rest.
  - Max hard sessions per week by phase; no hard session the day after a hard one unless planned.
  - Weekly stress ramp (CTL growth) ≤ ~5–7 per week; deload every 3rd–4th week.
  - Injury notes win: `no_run_until`, pain → no running; heavy leg strength not within 24 h of a quality run
    or the day after a long ski/hike (leg-fatigue index).
  - Taper before A races; nothing new in race week.
- Fallback: if the AI fails or output breaks guardrails, today's rule-based template is used.

## 6. Reviews (the learning loop)
- **Weekly review (Sunday night):** planned vs done (match.py), stress per sport, CTL/ATL/TSB change,
  sleep/HRV trend, notes, body trend; verdict + 2–3 adjustments for next week. Sent to ntfy, shown in the
  dashboard (Status/Why), stored in `data/coach_log/`.
- **Block review (end of each block / after a race):** did fitness rise as planned? update thresholds
  (FTP, run threshold pace, CSS) from the data, set the next block. Race debrief after each race.
- The next week design reads the last reviews, so mistakes are not repeated.

## 7. Data to add
- **Body composition** (Withings/Garmin scale): weight, fat, muscle, lean, water, BMR. Mario sends a
  screenshot or a Todoist note (e.g. "scale 75.2 kg, fat 8.9, muscle 62.9") regularly or after a big change;
  the coach reads it into `data/body.csv`. Withings API: done (`coach/withings.py`).
  *First reading (24 Nov):* weight stable 75.2 kg, but muscle −1.0 kg, lean −0.9 kg, fat +0.7 kg over 8
  weigh-ins. Bio-impedance is noisy (water −0.8 kg, hydration), so trends only; but with less running and a
  heel injury it is a reason to keep strength in and check protein/energy intake.
- **Threshold estimates** from activities (best 20' power, best 5 km, CSS from swims).
- HRV / resting HR long-term trend; training readiness already read.
- Other-sport tagging (ski, hike) so they enter the load model.

## 8. Models and cost
- Step 0 (done): OpenAI `gpt-5.5` ($5 / $30 per 1M tokens in/out) instead of `gpt-5.4-mini`; provider
  stays OpenAI. Claude (`ANTHROPIC_API_KEY`) remains a one-line switch.
- Cost guide: a day check ≈ 15k tokens in + reasoning/output ≈ 0.1–0.2 USD; a week design or review
  ≈ 0.3–0.6 USD. A few calls a day plus weekly ones ≈ 1–2 USD/day at most; the intraday re-checks are the
  main cost, so they stay limited to "new training or new note".
- Reasoning effort configurable (`ai.reasoning_effort`: low for day checks, medium/high for week design).

## 9. Rollout (each step ships and is checked on real days)
0. **Done:** bigger model; ski counts runs down only.
1. **Load model + data:** per-sport stress, CTL/ATL/TSB, leg-fatigue index, other-sport handling (ski/hike
   → affects next-day leg strength and run placement). Shown in the dashboard Status tab.
2. **Week designer** with validator and fallback; day check respects the week intent.
3. **Weekly review**, then **block review** and threshold updates.
4. **Body data** ingestion and use; Withings API optional.
5. **Backtest:** replay the past weeks against the new planner and compare with what Mario did and how he
   felt; tune before trusting it fully.

## 10. Risks and open decisions
- LLM variance: mitigated by guardrails + fallback + backtest; the AI proposes, code disposes.
- More autonomy means more surprises on the watch: the week is shown in Todoist/dashboard on Sunday night so
  Mario can object before the week starts.
- Open: how much Mario wants to be asked vs informed (default: informed, notes override); whether race
  priorities (A/B/C) should be set in `config.yaml`; whether to give the AI web research (training
  science) or keep it self-contained (default: self-contained).
