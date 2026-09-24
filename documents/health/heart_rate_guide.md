---
category: heart_rate
source: health/heart_rate_guide.md
---

# Resting Heart Rate Guide

## What is resting heart rate?

Resting heart rate (RHR) is the number of times the heart beats per minute when the body is at rest. It is a fundamental indicator of cardiovascular health and autonomic nervous system balance. Apple Watch measures RHR automatically during sleep and low-activity periods.

## Normal ranges

| RHR (bpm)  | Interpretation | localAIStack state |
|------------|----------------|-------------------|
| < 50       | Bradycardia — may be normal for athletes, investigate if symptomatic | pulse concern → attention |
| 50–60      | Athletic — excellent cardiovascular fitness | pulse watch |
| 60–80      | Healthy — typical for most adults | pulse ok |
| 80–100     | Elevated normal — may indicate stress or deconditioning | pulse ok (at boundary) |
| 100–120    | Tachycardia — outside nominal range | pulse watch |
| > 120      | Tachycardia — well outside nominal range | pulse concern → attention |

## localAIStack nominal band

localAIStack grades pulse (from the blood-pressure reading HealthKit shares) as **ok** in [60, 100] bpm, **watch** in [50, 60) or (100, 120], and **concern** beyond that. A concern alone makes the health state attention; a watch counts toward balanced or watch together with the other measures (worst band wins).

Note: For trained athletes with a chronically low resting pulse (40–55 bpm), the ok floor of 60 bpm may produce false watch or attention states. It can be lowered in `data/health/health_bands.json`.

## Causes of elevated resting heart rate

- Dehydration (even mild, 1–2% body water loss)
- Caffeine or stimulant intake
- Stress, anxiety, or emotional arousal
- Illness, fever, or infection
- Overtraining syndrome
- Poor sleep quality
- Medications (decongestants, some asthma inhalers)

## Causes of low resting heart rate (bradycardia)

- High aerobic fitness (athlete's heart)
- Beta-blockers or other cardiac medications
- Hypothyroidism
- Second-degree heart block (requires medical evaluation if symptomatic)

## Heart rate and localAI interaction

When pulse is graded concern and the health state is attention, localAI adjusts its interaction style: the assistant avoids cognitively demanding tasks, gently suggests rest or hydration, and notes that a check-in with a healthcare provider is appropriate if the elevated HR persists across multiple readings.

## Heart rate trends vs. single readings

Apple Watch can report high HR during brief activity spikes. The PE TTL for the HR sensor is 5 minutes, meaning a single spike reading will clear within 5 minutes if no new reading arrives. The health machine evaluates the most recent reading, not an average. For a more stable signal, consider averaging the previous 3 readings before writing to the sensor.

## When to seek immediate care

- HR > 150 bpm at rest with dizziness, chest pain, or shortness of breath
- HR < 40 bpm with lightheadedness or fainting (syncope)
- Sudden onset of fast, irregular heartbeat (palpitations)
