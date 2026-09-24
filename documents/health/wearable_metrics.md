---
category: wearables
source: health/wearable_metrics.md
---

# Apple Watch Health Metrics: How They Are Measured and Their Accuracy

## Heart Rate (Resting Heart Rate)

**How it's measured:** Apple Watch uses photoplethysmography (PPG) — green LED lights shine through the skin on the back of the watch and a light sensor detects the volume of blood pulsing through the wrist capillaries with each heartbeat. During activity, red and infrared LEDs are also used.

**Resting HR determination:** Apple Watch samples HR throughout the day and identifies the lowest reading during periods of low motion (typically early morning or extended sedentary periods) as the resting heart rate. iOS Health averages these readings to produce a daily resting HR value.

**Accuracy:** ±5 bpm in most conditions. Accuracy decreases during high-intensity exercise with wrist movement (tattoos, cold extremities, and loose watch fit also degrade accuracy). For resting measurement, accuracy is generally high (within ±2 bpm compared to ECG).

**Latency:** Resting HR is recalculated daily and appears in HealthKit as a sampled value with a timestamp when the reading was taken. localAIStack grades pulse from the blood-pressure reading the HealthKit bridge shares; a reading that has lapsed is left out of the grading rather than graded stale.

## Heart Rate Variability (HRV — SDNN from Apple Watch)

**How it's measured:** Apple Watch measures HRV during sleep, using the PPG sensor to detect the interval between successive heartbeats (called NN intervals or RR intervals in clinical contexts). The SDNN metric is the standard deviation of these intervals in milliseconds.

**When it's captured:** Apple Watch calculates HRV from a brief measurement period during sleep (typically late in the sleep session). It does NOT continuously monitor HRV; a single SDNN value is produced per night. The iOS Heart Rate app displays the most recent overnight HRV reading.

**Accuracy:** Apple Watch SDNN correlates reasonably well (r ≈ 0.8–0.9) with medical-grade HRV monitors under laboratory conditions, but shows greater variability in real-world use. It is adequate for tracking relative trends but not for clinical diagnostics. The key is using your own historical baseline rather than comparing to population norms.

**Wrist vs. chest ECG:** Chest strap HRV (e.g., Polar H10 with Kubios analysis) is the gold standard. Apple Watch tends to read slightly higher SDNN values due to PPG motion sensitivity, but the day-to-day pattern (rising/falling relative to baseline) is reliable.

**localAIStack threshold (≥ 30 ms):** The 30 ms threshold is a pragmatic midpoint. For most adults, SDNN below 30 ms indicates below-average autonomic recovery. Elite athletes often have SDNN above 80 ms; sedentary adults may have SDNN in the 20–40 ms range. Personalisation of this threshold is recommended (the `hrv` band in `data/health/health_bands.json`). The HealthKit bridge does not yet share HRV, so localAIStack grades it only when a reading is supplied directly.

## Sleep Analysis (SleepAnalysis)

**How it's measured:** Apple Watch uses accelerometer data (wrist movement), heart rate, and respiratory rate to classify each 30-second epoch of the night into:
- **Awake** — movement, elevated HR, or active disturbance
- **REM** — characteristic HR variability pattern, low movement, rapid eye movements inferred from wrist actigraphy
- **Core (light NREM)** — low movement, moderate HR
- **Deep (slow-wave NREM)** — very low movement, lowest HR, largest HRV amplitude

**Accuracy:** Stage-by-stage classification accuracy is approximately 70–80% compared to polysomnography (PSG), the clinical gold standard. Total sleep duration accuracy is higher (±15–20 minutes vs. PSG). The most common errors are misclassifying Core as REM and underestimating Deep sleep.

**Sleep duration in HealthKit:** `HKCategoryTypeIdentifierSleepAnalysis` provides individual sleep stage samples with start/end timestamps. The iOS Health app sums InBed or Asleep time to produce duration. The localHealthkitBridge should sum `asleepCore + asleepREM + asleepDeep` (excluding `awake` and `inBed`) for the most accurate sleep duration estimate.

**localAIStack threshold (≥ 6.5 h):** This is the minimum duration for most adults to avoid measurable cognitive performance deficits; 5–6.5 h grades watch and under 5 h grades concern. The bridge delivers sleep once per day.

## Respiratory Rate

**Not currently mapped in localAIStack.** Apple Watch measures respiratory rate during sleep (breaths per minute) via the accelerometer detecting chest expansion patterns. Typical adult range: 12–20 breaths/min during sleep. Elevated respiratory rate (> 20 bpm) can indicate respiratory illness, sleep apnea, or other cardiorespiratory stress. Mapping it would take a HealthKit lane from the bridge and a band in `data/health/health_bands.json`.

## Blood Oxygen (SpO2)

**Not currently mapped in localAIStack.** Apple Watch Series 6+ measures blood oxygen saturation via PPG with red and infrared LEDs. Normal range: 95–100%. Values below 90% indicate hypoxaemia (low blood oxygen), which requires urgent evaluation. Apple Watch accuracy is ±2–3% vs. pulse oximetry; insufficient for medical diagnosis but useful for trend monitoring. Mapping it would take a HealthKit lane from the bridge and a band in `data/health/health_bands.json`.

## ECG (Electrocardiogram)

**Not currently mapped in localAIStack.** Apple Watch Series 4+ can generate a single-lead ECG trace when the user touches the digital crown. The on-device algorithm classifies the rhythm as sinus rhythm, AFib, or inconclusive. Clinical use requires physician interpretation of the PDF trace. This would map to a dedicated alert machine rather than a continuous sensor.

## Reliability guidelines for localAIStack integration

1. **Always use overnight readings** for HRV and sleep, not daytime spot checks.
2. **Use the first-morning reading** for resting HR — before caffeine, food, or activity.
3. **Exclude days** where the watch was not worn during sleep (a HealthKit source that has lapsed is left out of the grading automatically).
4. **Trend over 7 days** is more meaningful than any single day for HRV.
5. **Personalise thresholds** in `data/health/health_bands.json` after collecting 2–4 weeks of baseline data.
