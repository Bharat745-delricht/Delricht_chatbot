"""
API endpoints for visit-related notifications (rescheduling, cancellations, etc.)
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Optional
import logging
import asyncio

from core.services.email_service import email_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/visits", tags=["Visit Notifications"])


# =============================================================================
# Helper function to run async email service in background
# =============================================================================
def send_reschedule_email_sync(
    patient_name: str,
    patient_email: Optional[str],
    patient_id: str,
    study_name: str,
    visit_type: str,
    old_date: str,
    old_time: str,
    new_date: str,
    new_time: str,
    site_name: str,
    rescheduled_by: str,
    notes: Optional[str]
):
    """Synchronous wrapper for async email service"""
    try:
        # Run async function in new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            email_service.send_visit_rescheduled_notification(
                patient_name=patient_name,
                patient_email=patient_email,
                patient_id=patient_id,
                study_name=study_name,
                visit_type=visit_type,
                old_date=old_date,
                old_time=old_time,
                new_date=new_date,
                new_time=new_time,
                site_name=site_name,
                rescheduled_by=rescheduled_by,
                notes=notes
            )
        )
        loop.close()
    except Exception as e:
        logger.error(f"Background email task failed: {str(e)}", exc_info=True)


# =============================================================================
# Request Models
# =============================================================================
class RescheduleNotificationRequest(BaseModel):
    """Request model for visit reschedule notification"""
    patient_name: str
    patient_email: Optional[EmailStr] = None
    patient_id: str
    study_name: str
    visit_type: str
    old_date: str
    old_time: str
    new_date: str
    new_time: str
    site_name: str
    rescheduled_by: str
    notes: Optional[str] = None
    send_to_patient: bool = False  # Flag to optionally send to patient


# =============================================================================
# Endpoints
# =============================================================================
@router.post("/notify-reschedule")
async def notify_visit_rescheduled(
    request: RescheduleNotificationRequest,
    background_tasks: BackgroundTasks
):
    """
    Send notification emails when a visit is rescheduled.

    Always sends to: mmorris@delricht.com (dashboard email)
    Optionally sends to: patient email (if provided and send_to_patient=True)

    This endpoint is called from the V3 scheduling dashboard after
    a visit is successfully rescheduled in CRIO.
    """
    try:
        logger.info(f"Processing reschedule notification for patient {request.patient_id}")
        logger.info(f"Study: {request.study_name}, Visit: {request.visit_type}")
        logger.info(f"Old: {request.old_date} {request.old_time} → New: {request.new_date} {request.new_time}")

        # Validate required fields
        if not all([
            request.patient_name,
            request.patient_id,
            request.study_name,
            request.visit_type,
            request.old_date,
            request.old_time,
            request.new_date,
            request.new_time,
            request.site_name,
            request.rescheduled_by
        ]):
            raise HTTPException(
                status_code=400,
                detail="Missing required fields for reschedule notification"
            )

        # If patient email should be sent, validate it's provided
        if request.send_to_patient and not request.patient_email:
            raise HTTPException(
                status_code=400,
                detail="Patient email is required when send_to_patient=True"
            )

        # Send email notification in background (don't block response)
        background_tasks.add_task(
            send_reschedule_email_sync,
            patient_name=request.patient_name,
            patient_email=request.patient_email if request.send_to_patient else None,
            patient_id=request.patient_id,
            study_name=request.study_name,
            visit_type=request.visit_type,
            old_date=request.old_date,
            old_time=request.old_time,
            new_date=request.new_date,
            new_time=request.new_time,
            site_name=request.site_name,
            rescheduled_by=request.rescheduled_by,
            notes=request.notes
        )

        logger.info(f"Reschedule notification queued successfully for patient {request.patient_id}")

        return {
            "success": True,
            "message": "Reschedule notification queued successfully",
            "recipients": {
                "dashboard": "mmorris@delricht.com",
                "patient": request.patient_email if request.send_to_patient else None
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to queue reschedule notification: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send reschedule notification: {str(e)}"
        )


@router.get("/health")
async def health_check():
    """Health check endpoint for visit notification service"""
    return {
        "status": "healthy",
        "service": "Visit Notifications API",
        "email_provider": "SendGrid",
        "dashboard_email": "mmorris@delricht.com"
    }
