---
category: sleep
source: health/sleep_quality.md
---

# Sleep Quality and Duration

## Why sleep matters

Sleep is the primary mechanism by which the brain and body consolidate learning, repair tissue, regulate hormone levels, and reset the autonomic nervous system. Chronic short sleep (< 6 hours) is associated with impaired cognitive performance comparable to 24 hours of wakefulness.

## Sleep duration recommendations

| Duration        | Category | localAIStack state |
|-----------------|----------|-------------------|
| ≥ 8 hours       | Optimal — full recovery | sleep ok |
| 7–8 hours       | Recommended — meets most adults' needs | sleep ok |
| 6.5–7 hours     | Marginal — acceptable but approaching threshold | sleep ok (at boundary) |
| 5–6.5 hours     | Below threshold | sleep watch → balanced, or watch with another |
| < 5 hours       | Significantly short — cognitive impairment likely | sleep concern → attention |

## localAIStack sleep threshold

localAIStack grades total sleep as **ok** at ≥ 6.5 hours, **watch** from 5 to 6.5 hours, and **concern** below 5 hours, from the nightly sleep summary HealthKit shares.

A short night (watch) with every other measure ok produces the **balanced** state: the user is otherwise healthy but not fully rested.

## Sleep stages and quality

Apple Watch estimates four sleep stages:

- **Awake** — brief arousals; too many indicate fragmented sleep
- **REM** (rapid eye movement) — memory consolidation, emotional processing
- **Core** (light NREM) — transitional, largest proportion of a sleep period
- **Deep** (slow-wave NREM) — physical restoration, growth hormone release

HRV and RHR recovery happen predominantly during deep sleep. If HRV is low despite adequate sleep duration, sleep quality (fragmentation, insufficient deep sleep) may be the cause.

## Sleep hygiene practices

- Maintain a consistent sleep and wake schedule 7 days a week
- Keep the bedroom cool (18–20°C / 64–68°F) and dark
- Avoid screens 60 minutes before bed (or use blue light blocking)
- Avoid caffeine after noon; avoid alcohol within 3 hours of bed
- Expose yourself to natural light within 30 minutes of waking
- Use the bed only for sleep (not work, TV, or prolonged phone use)

## Sleep debt

Sleep debt is cumulative. One hour of short sleep per night for a week creates a 7-hour deficit. Recovery from sleep debt requires multiple nights of extended sleep (not a single 10-hour rebound). Performance deficits from sleep debt are often underestimated: people feel adapted but remain cognitively impaired.

## Sleep and localAI interactions

When sleep is below 6.5 h:

- On its own (the **balanced** state), it triggers a gentle nudge toward an early wind-down today.
- Together with another slightly-off measure such as low HRV (the **watch** state), or below 5 h (the **attention** state), localAI will suggest recovery-focused activities over demanding work.
- The agent graph may recommend lighter task scheduling and ask if the user wants a reminder to start a sleep wind-down routine.

## Improving sleep duration

1. Set a consistent alarm for the same time every morning
2. Work backwards from your wake time to calculate a target bed time (add 15–20 min for sleep onset latency)
3. Dim lights and lower room temperature 2 hours before bed
4. Use Apple Watch's Wind Down feature to build a consistent pre-sleep routine
5. Track sleep trends over 2–4 weeks rather than reacting to individual nights
