---
category: hrv
source: health/hrv_interpretation.md
---

# Heart Rate Variability (HRV) Interpretation

## What is HRV?

Heart rate variability (HRV) is the variation in time between consecutive heartbeats, measured in milliseconds. A higher HRV indicates a well-recovered, adaptable autonomic nervous system. SDNN (standard deviation of NN intervals) is the most common HRV metric reported by wearables including Apple Watch.

## What SDNN values mean

| SDNN (ms) | Interpretation | localAIStack state |
|-----------|----------------|-------------------|
| ≥ 50      | Excellent recovery — high resilience | HRV ok |
| 30–50     | Good recovery — within healthy range | HRV ok |
| 20–30     | Low recovery — elevated stress or under-recovered | HRV watch |
| < 20      | Poor recovery — consider rest, reduced training | HRV concern → attention |

## HRV thresholds in localAIStack

localAIStack grades HRV as **ok** at SDNN ≥ 30 ms, **watch** from 20 to 30 ms, and **concern** below 20 ms; the health state is the worst grade across the measures in scope. The HealthKit bridge does not yet share HRV, so today it is graded only when a reading is supplied directly (for example by the simulator).

## Factors that lower HRV

- Insufficient sleep (< 6.5 hours)
- High-intensity training without recovery time
- Alcohol consumption
- Illness, infection, or fever
- Emotional stress or high cognitive load
- Poor nutrition or dehydration

## Factors that improve HRV

- Consistent sleep schedule targeting 7–9 hours
- Aerobic exercise (moderate intensity, 3–5 sessions per week)
- Cold exposure (cold showers, cold water immersion)
- Breathing exercises: 4-7-8, box breathing, or slow diaphragmatic breathing
- Reducing caffeine after noon
- Mindfulness and stress reduction practices

## HRV and cognitive performance

HRV is strongly correlated with executive function, working memory, and decision-making quality. When HRV is low, the nervous system is in a more sympathetic (fight-or-flight) state, which can impair nuanced thinking. A watch or attention health state in localAIStack suggests the user may benefit from cognitively lighter tasks and recovery-focused support.

## When to act on low HRV

A single low-HRV day is not a concern; HRV naturally fluctuates. A trend of 3+ days below the personal baseline threshold warrants attention. If HRV is persistently low alongside elevated resting HR, consider consulting a healthcare provider to rule out overtraining syndrome, infection, or cardiac irregularity.
