from fastapi import APIRouter, HTTPException

from core.patient_wellness_bridge import simulate_alert_decline_workflow

router = APIRouter(prefix="/patient-wellness", tags=["patient-wellness"])


@router.post("/alert-decline/simulate")
def simulate_patient_wellness_alert_decline():
    try:
        return simulate_alert_decline_workflow()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
