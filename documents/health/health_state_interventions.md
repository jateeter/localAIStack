---
category: interventions
source: health/health_state_interventions.md
---

# Health State Interventions — localAIStack Practical Guide

## Overview

This document provides specific, actionable guidance for each of the four health states reported by the localAIStack personal_health_baseline machine. Each state reflects the current reading of three Apple Watch metrics (resting heart rate, HRV SDNN, sleep duration) as interpreted by the CES machine.

## State: THRIVING

**Grade pattern**: every measure in scope ok

**What it means**: All three baseline metrics are in their nominal ranges simultaneously. The autonomic nervous system is well-regulated, you are adequately rested, and cardiovascular function is normal. This is the baseline target state.

**Recommended approach**:
- Proceed with planned high-intensity or cognitively demanding work
- This is the best time for deep work sessions requiring sustained attention
- It is safe to take on new challenges, learning, and complex problem-solving
- If you train, full-intensity sessions are appropriate
- No specific interventions needed — maintain the behaviours that produced this state

**localAI assistant mode**: Supportive and energising. Focuses on the user's goals and priorities without health caveats.

---

## State: BALANCED

**Grade pattern**: exactly one measure watch, none concern — most often sleep between 5 and 6.5 h. The guidance below is for that case.

**What it means**: Cardiovascular health and autonomic recovery are both in good shape, but the body is carrying some sleep debt. Short-term sleep deprivation (even by 1–2 hours) measurably impairs working memory, attention switching, and emotional regulation before impairing physical performance.

**Immediate interventions (today)**:
1. If possible, take a 20–25 min nap between 1 pm and 3 pm — this is the only period that reliably reduces sleep debt without disrupting night-time sleep onset
2. Protect tonight's sleep window — set an alarm for bedtime, not just wake time
3. Limit caffeine to before noon; avoid alcohol tonight
4. Dim screens and lighting by 9 pm

**Longer-term if recurring**:
- Audit your sleep schedule consistency — irregular wake times fragment sleep architecture even when total duration is adequate
- Check sleep environment: temperature (ideal 18–20°C), noise, light exposure
- If you regularly fall short of 6.5 hours on work nights, consider whether a structural shift in schedule is feasible

**Training guidance**: Moderate training is safe. Avoid testing new PRs or high-complexity technical movements where cognitive load matters (Olympic lifting, technical climbing, etc.).

**localAI assistant mode**: Acknowledges good vitals, notes sleep shortfall. Suggests evening wind-down plan. Avoids adding task complexity late in the day.

---

## State: WATCH

**Grade pattern**: two or more measures watch, none concern — for example HRV 20–30 ms together with a pulse of 100–120 bpm. The guidance below is for the common recovery-related case.

**What it means**: Heart rate is normal (good news) but HRV is suppressed, indicating the autonomic nervous system is under stress. This can reflect physical overtraining, psychological stress, early illness, alcohol from the night before, or accumulated fatigue. The HRV signal is integrative — it captures all stressors simultaneously.

**Immediate interventions**:
1. **Morning**: 5 min box breathing (4-4-4-4 count) or resonance frequency breathing (6 breaths/min for 5–10 min) to shift toward parasympathetic tone
2. **Activity**: replace planned hard training with easy walking, gentle yoga, or full rest. Do NOT try to "push through" on low-HRV days — training load on suppressed HRV deepens the fatigue hole
3. **Hydration**: drink 500 ml of water within 30 minutes of waking; continue regular hydration throughout the day
4. **Nutrition**: eat regularly and avoid skipping meals; caloric restriction compounds HRV suppression
5. **Evening**: keep screens off 60 min before bed; go to bed 30–60 min earlier than usual
6. **Reflect**: was there alcohol last night? An unusually late night? A stressful event or difficult conversation? A heavy training session yesterday?

**When to escalate**: If HRV remains suppressed for 5+ consecutive days despite rest and good sleep, consider medical evaluation.

**Training guidance**: Light activity only. Zone 1 cardio (conversational pace) is acceptable and may slightly improve HRV by end of day. Avoid any high-intensity intervals, heavy lifting, or competition.

**localAI assistant mode**: Mindful and restorative. Suggests breathing practices, lighter scheduling. Notes that pushing through low-HRV states is counterproductive and will delay recovery. Asks if there are external stressors to discuss.

---

## State: ATTENTION

**Grade pattern**: at least one measure concern — pulse below 50 or above 120 bpm, blood pressure at or above 140/90 mmHg, sleep under 5 h, exercise under 10 min, or HRV below 20 ms. The guidance below is for an out-of-range pulse; for other measures, see that measure's guide.

**What it means**: For pulse: the reading is well outside the nominal band — either elevated (> 120 bpm) or low (< 50 bpm). Pulse is among the most directly physiologically significant of the tracked measures.

**Important**: A single reading outside the nominal range is common and may reflect a false positive (movement during reading, recent caffeine, stress response). Context matters.

### High HR (> 100 bpm) — likely causes and responses

| Likely cause | Time course | Response |
|---|---|---|
| Dehydration | Appears within hours of fluid loss | Drink 500–1000 ml water; remeasure in 30 min |
| Recent caffeine or stimulants | 1–4 hours post-consumption | Wait; HR will normalise with time |
| Fever or early illness | Concurrent with symptoms | Rest; if > 38.5°C, contact healthcare provider |
| Emotional stress or anxiety | Acute, situational | Breathing practice; address stressor if possible |
| Overexertion (previous day) | Morning after hard effort | Rest day; monitor over 24 hours |
| Sustained (2+ days) without cause | Persistent | Medical evaluation recommended |

### Low HR (< 60 bpm) — likely causes and responses

| Likely cause | Context | Response |
|---|---|---|
| Athletic bradycardia | Trained endurance/strength athlete | Normal finding; lower the pulse band's floor in `data/health/health_bands.json` |
| Beta-blockers or cardiac medication | On prescribed medication | Expected; discuss personalised thresholds with physician |
| Hypothyroidism | Concurrent fatigue, cold intolerance | Thyroid panel recommended |
| Vasovagal or vagal surge | After prolonged rest | Benign; increases with activity |
| Complete heart block | Dizziness, syncope, or symptoms | Urgent evaluation |

**Immediate interventions (high HR)**:
1. Hydrate (500 ml water immediately)
2. Sit or lie down; avoid strenuous activity
3. Check for environmental factors: heat, recent exercise, stimulants
4. Remeasure HR in 15–30 minutes
5. If HR > 130 bpm at rest with symptoms (chest tightness, shortness of breath, dizziness): seek immediate medical evaluation

**Immediate interventions (low HR in non-athlete)**:
1. Note whether you have any symptoms (dizziness, near-fainting, fatigue)
2. If symptomatic: seek medical evaluation promptly
3. If asymptomatic and HR is 50–60 bpm: monitor for several days; consult physician if persistent

**localAI assistant mode**: Calm and non-alarmist. Notes the HR anomaly, identifies the most common benign explanations, and recommends remeasuring. Clearly recommends professional evaluation if the pattern persists or is accompanied by symptoms. Does not diagnose.

---

## State escalation and remeasurement

Health state classifications use a single point-in-time sensor reading. Before acting on an unexpected health state:

1. **Remeasure**: re-push (via simulate script or HealthKit delivery) with a fresh reading
2. **Check freshness**: a measure whose HealthKit source has lapsed is left out of the grading rather than graded on stale data; if everything has lapsed, no state is reported
3. **Consider context**: exercise, caffeine, stress, and poor watch placement all affect readings
4. **Track trends**: the health state is most actionable when it reflects a pattern (3+ consecutive days) rather than a single reading

## Personalising the thresholds

The default ranges are population-level norms; the watch zones are provisional. Personalise in `data/health/health_bands.json`:
- **pulse** `ok.gte`: lower for trained athletes (e.g., 48 for athletes with resting pulse 48–55)
- **pulse** `ok.lte`: unchanged unless clinically instructed (100 bpm is the standard tachycardia definition)
- **hrv** `ok.gte`: raise for individuals with consistently high baseline HRV (e.g., 40 or 50 ms)
- **sleep** `ok.gte`: raise to 7.0 or 7.5 for individuals who function best on more sleep
