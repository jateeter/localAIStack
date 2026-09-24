---
category: wellness
source: health/wellness_baselines.md
---

# Personal Health Baseline States in localAIStack

## Overview

The localAIStack personal_health_baseline machine classifies the user's current wellness state from the health measures their iPhone and Apple Watch share through HealthKit: pulse, blood pressure, sleep and exercise today. Each state influences how the localAI assistant interacts with the user.

Each measure in scope is graded **ok**, **watch** (slightly outside its nominal range) or **concern** (well outside it), and the health state is the worst of them:

| Grades across the measures in scope | Health state |
|---|---|
| every measure ok | thriving |
| exactly one watch, no concern | balanced |
| two or more watch, no concern | watch |
| any concern | attention |

Which measures are in scope is not fixed. It follows what the user has authorised the HealthKit bridge to share, and changes when that authorisation does. A measure with no recent, confident reading is left out rather than counted as a failure; with nothing to grade, no state is reported.

Default ranges (ok / watch; anything beyond watch is concern):

| Measure | ok | watch |
|---|---|---|
| Pulse | 60–100 bpm | 50–120 bpm |
| Blood pressure | below 130/80 mmHg | below 140/90 mmHg |
| Sleep | ≥ 6.5 h | ≥ 5 h |
| Exercise | ≥ 30 min | ≥ 10 min |
| HRV SDNN | ≥ 30 ms | ≥ 20 ms |

HRV has thresholds but no HealthKit lane yet, so it is graded only when a reading is supplied directly (for example by the simulator).

## The four health states

### Thriving (health state: thriving)

Every measure in scope is in its nominal range.

**User context:** Full capacity — rested, recovered, and cardiovascularly healthy.

**localAI behavior:** Normal assistant mode with no adjustments. The assistant may affirm the user's good baseline and offer to support any ambitious goals they have for the day.

---

### Balanced (health state: balanced)

Exactly one measure is slightly outside its nominal range and none is well outside it — most often a short night (5–6.5 h of sleep).

**User context:** Mostly in good shape, with one thing slightly off. After a short night, the user is capable but carrying some sleep debt and may benefit from recovery focus.

**localAI behavior:** Acknowledges what is going well while noting the one shortfall. After short sleep, suggests an early wind-down later in the day and avoids scheduling heavy cognitive tasks in the evening.

---

### Watch (health state: watch)

Two or more measures are slightly outside their nominal ranges, none well outside — for example a slightly raised pulse together with lower HRV, or a short night together with little exercise.

**User context:** Several small signals point the same way. Together they often indicate incomplete recovery from training, accumulated stress, or early illness.

**localAI behavior:** Recommends lighter activities, rest, and stress management. Avoids adding to the user's cognitive or physical load. Suggests breathing exercises, hydration, and an earlier bedtime. Checks in gently rather than driving a task-heavy agenda.

---

### Attention (health state: attention)

At least one measure is well outside its nominal range: pulse below 50 or above 120 bpm, blood pressure at or above 140/90 mmHg, under 5 hours of sleep, under 10 minutes of exercise, or HRV below 20 ms.

**User context:** The cause depends on the measure. An out-of-range pulse may reflect dehydration, fever, medication side effects, overexertion, or a cardiac condition. A single reading is rarely alarming; a persistent pattern warrants evaluation.

**localAI behavior:** Gently notes which reading is outside the typical range. Does not diagnose. Encourages the user to hydrate, rest, and check again in 15–30 minutes. Recommends consulting a healthcare provider if the pattern persists or is accompanied by symptoms (dizziness, chest tightness, shortness of breath).

---

## State transition examples

| Scenario | State |
|----------|-------|
| Good night's sleep, pulse 68, HRV 42 ms | thriving |
| 5.5 hours of sleep, pulse 72, HRV 38 ms | balanced (one watch: sleep) |
| Good sleep, pulse 110, HRV 25 ms | watch (two watch: pulse, HRV) |
| Good sleep, pulse 71, HRV 18 ms (post-hard training) | attention (concern: HRV) |
| Pulse 130 bpm (fever or overexertion) | attention (concern: pulse) |
| Blood pressure 145/92 mmHg | attention (concern: blood pressure) |

## Machine output layout

localAIStack writes the worst-wins state as a one-hot vector at [7574:7578]; the personal_health_baseline CES machine reads it and writes its own one-hot at [7578:7582] in the perceptual space:
- [7578] thriving
- [7579] balanced
- [7580] watch
- [7581] attention

Each graded measure also has its own slot in [7600:7632] (ok 1.0, watch 0.5, concern 0.0) while it is in scope. All zeros at [7578:7582] means no measure has been graded.

## Keeping the baseline calibrated

The default ranges are population-level norms appropriate for most adults, and the watch zones are provisional. All of them live in `data/health/health_bands.json`. Athletes with chronically low resting pulse (< 60 bpm) may lower the pulse band's `ok` floor; users with higher baseline HRV may raise the HRV band's. Individual calibration improves the signal-to-noise ratio and reduces false attention states.
