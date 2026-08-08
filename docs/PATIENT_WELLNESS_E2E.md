# PatientWellness localAIStack E2E

This effort adds a live localAIStack support path for the shared
`PatientWellness` machine's `Alert Wellness - Multiple Deficits with Decline
Trend` CES.

## Workflow

1. localAIStack registers two PE sources:
   - `localai_patient_wellness_assessment` at `[1955:1963]`
   - `localai_patient_wellness_feedback` at `[3941:3949]`
2. The e2e simulation writes the PatientWellness TC-03 alert-decline vectors:
   - `[H,L,L,L,L,L,L,L]`
   - `[H,L,L,H,L,L,L,L]`
   - `[H,L,L,H,H,L,L,L]`
   - `[H,L,L,H,H,L,H,L]`
3. PE `/api/push` drives the Scala RE, where `PatientWellness` emits ALERT at
   `[3931:3939]` as `[0,0,1,0,0,0,0,0]`.
4. localAIStack normalizes the result into AI feedback:
   - classification: `ALERT`
   - deficits: `anxiety`, `stress`, `exercise`
   - decline trend: `true`
   - actions: `COACH_CLINICAL_ESCALATE`, `NOTIFY_FAMILY`,
     `ACT_REVISE_CARE_PLAN`
5. localAIStack writes `[0,0,1,0,1,0,1,0]` through
   `localai_patient_wellness_feedback`, which matches the AIWellnessCoach alert
   escalation output region `[3941:3949]`.

## Run

Start the Scala universe and localAIStack, then run:

```bash
RE_SSL_VERIFY=false \
PE_URL=https://localhost:3004 \
RE_URL=https://localhost:5001 \
pytest services/api/tests/e2e/test_patient_wellness_workflow.py --live -v
```

The localAIStack API also exposes the workflow directly:

```bash
curl -sS -X POST http://localhost:4000/patient-wellness/alert-decline/simulate | jq .
```

## Release Gaps

- The e2e assumes `PatientWellness` and `AI Wellness Coach` are already loaded
  into the RE from `RealityEngine_Machines`; localAIStack does not yet import
  those shared-domain machines at startup.
- The localAI recognition layer is deterministic support for this CES scenario.
  It recognizes the normalized vector and trend flag; it does not yet use an
  LLM/RAG clinical reasoning step.
- The feedback PE source writes the normalized AIWellnessCoach-compatible action
  vector, but there is no durable care-plan task, family notification dispatch,
  or audit trail yet.
- The input is already normalized to the PatientWellness dimension vector. A
  release workflow still needs age-cohort scoring, sensor provenance, and
  clinical validation before accepting raw health assessment payloads.
- The live e2e uses simulated resident data only and does not include PHI
  authorization, consent, or device identity checks.
- Older e2e helpers used the pre-Scala HTTP defaults. This change updates the
  live fixture defaults to `https://localhost:3004` and
  `https://localhost:5001`, but the broader suite still needs a pass for any
  hard-coded `http://localhost:3000` runbook text.
