"""
SMS Reschedule Trigger Endpoint
Sends initial SMS to patients to initiate rescheduling flow

Created: January 5, 2026
Purpose: Allow coordinators to trigger SMS rescheduling from web form
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import logging
import json
import uuid
from datetime import datetime

from core.database import db
from core.services.sms_service import sms_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reschedule", tags=["Reschedule SMS"])


class TriggerSMSRequest(BaseModel):
    """Request body for triggering reschedule SMS"""
    patient_name: str = Field(..., description="Patient's display name")
    phone_number: str = Field(..., description="Patient's phone number (any format)")
    site_id: str = Field(..., description="CRIO Site ID")
    study_id: str = Field(..., description="CRIO Study ID")
    current_appointment_id: str = Field(..., description="CRIO Calendar Appointment Key")
    subject_id: str = Field(..., description="CRIO Subject Key")
    visit_id: str = Field(..., description="CRIO Study Visit Key")
    reschedule_after_date: Optional[str] = Field(None, description="Only show slots after this date (YYYY-MM-DD)")
    site_name: Optional[str] = Field(None, description="Site display name for SMS")
    study_name: Optional[str] = Field(None, description="Study display name for SMS")


class TriggerSMSResponse(BaseModel):
    """Response from trigger endpoint"""
    success: bool
    session_id: str
    message_sid: Optional[str] = None
    error: Optional[str] = None


@router.post("/trigger-sms", response_model=TriggerSMSResponse)
async def trigger_reschedule_sms(request: TriggerSMSRequest):
    """
    Trigger a reschedule SMS conversation with a patient

    This endpoint:
    1. Creates a new conversation session
    2. Stores all CRIO IDs in metadata for later use
    3. Sends initial SMS to patient
    4. Patient responds → SMS webhook → AI conversation → CRIO reschedule

    Returns:
        session_id: The session ID for tracking this conversation
        message_sid: Twilio message SID if SMS sent successfully
    """

    logger.info(f"[TRIGGER-SMS] ========== INITIATING SMS RESCHEDULE ==========")
    logger.info(f"[TRIGGER-SMS] Patient: {request.patient_name}")
    logger.info(f"[TRIGGER-SMS] Phone: {request.phone_number}")
    logger.info(f"[TRIGGER-SMS] Site: {request.site_id}, Study: {request.study_id}")
    logger.info(f"[TRIGGER-SMS] CRIO IDs: appointment={request.current_appointment_id}, subject={request.subject_id}, visit={request.visit_id}")

    try:
        # Generate unique session ID
        session_id = f"sms_{uuid.uuid4().hex[:16]}"
        logger.info(f"[TRIGGER-SMS] Session ID: {session_id}")

        # Build metadata with all CRIO IDs
        metadata = {
            "subject_id": request.subject_id,
            "visit_id": request.visit_id,
            "current_appointment_id": request.current_appointment_id,
            "channel": "sms",
            "triggered_at": datetime.utcnow().isoformat(),
            "triggered_by": "web_form"
        }

        # Build context data
        context_data = {
            "phone_number": request.phone_number,
            "patient_name": request.patient_name,
            "channel": "sms",
            "site_id": request.site_id,
            "study_id": request.study_id
        }

        # Create conversation_context record
        logger.info(f"[TRIGGER-SMS] Creating conversation_context...")
        context_query = """
            INSERT INTO conversation_context
            (session_id, current_state, context_data, active, created_at, updated_at)
            VALUES (%s, %s, %s, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
        db.execute_update(context_query, (
            session_id,
            'rescheduling_initiated',
            json.dumps(context_data)
        ))
        logger.info(f"[TRIGGER-SMS]    ✅ conversation_context created")

        # Create reschedule_requests record with metadata
        logger.info(f"[TRIGGER-SMS] Creating reschedule_requests...")
        request_query = """
            INSERT INTO reschedule_requests
            (session_id, patient_name, phone_number, site_id, study_id,
             current_appointment_id, reschedule_after_date, status, metadata, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """

        reschedule_after = request.reschedule_after_date
        if not reschedule_after:
            # Default to today + 1 day
            from datetime import timedelta
            reschedule_after = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

        db.execute_update(request_query, (
            session_id,
            request.patient_name,
            request.phone_number,
            request.site_id,
            request.study_id,
            request.current_appointment_id,
            reschedule_after,
            'pending',
            json.dumps(metadata)
        ))
        logger.info(f"[TRIGGER-SMS]    ✅ reschedule_requests created")
        logger.info(f"[TRIGGER-SMS]    - metadata stored: {json.dumps(metadata, indent=2)}")

        # Build and send initial SMS
        site_display = request.site_name or f"Site {request.site_id}"

        initial_message = f"""Hey {request.patient_name}! This is Eric at DelRicht Research. We need to reschedule your upcoming appointment due to a Study update. We apologize for any inconvenience and truly appreciate you being a valued Patient. Can we find another time that works?

Reply YES to continue, or call (404) 355-8779 for assistance."""

        logger.info(f"[TRIGGER-SMS] Sending initial SMS...")
        logger.info(f"[TRIGGER-SMS]    Message preview: {initial_message[:100]}...")

        message_sid = await sms_service.send_sms(
            to_phone=request.phone_number,
            message=initial_message,
            session_id=session_id,
            metadata={
                "message_type": "reschedule_initiation",
                "triggered": True,
                "site_id": request.site_id,
                "study_id": request.study_id
            }
        )

        if message_sid:
            logger.info(f"[TRIGGER-SMS] ✅ SMS sent successfully!")
            logger.info(f"[TRIGGER-SMS]    - Message SID: {message_sid}")
            logger.info(f"[TRIGGER-SMS]    - Session ID: {session_id}")
            logger.info(f"[TRIGGER-SMS] ==========================================")

            return TriggerSMSResponse(
                success=True,
                session_id=session_id,
                message_sid=message_sid
            )
        else:
            logger.error(f"[TRIGGER-SMS] ❌ Failed to send SMS")
            logger.info(f"[TRIGGER-SMS] ==========================================")

            # Update status to failed
            db.execute_update(
                "UPDATE reschedule_requests SET status = 'failed' WHERE session_id = %s",
                (session_id,)
            )

            return TriggerSMSResponse(
                success=False,
                session_id=session_id,
                error="Failed to send SMS via Twilio"
            )

    except Exception as e:
        logger.error(f"[TRIGGER-SMS] ❌ Exception: {e}", exc_info=True)
        logger.info(f"[TRIGGER-SMS] ==========================================")

        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger reschedule SMS: {str(e)}"
        )


@router.get("/trigger-sms/test")
async def test_trigger_endpoint():
    """Test endpoint to verify trigger route is registered"""
    return {
        "status": "ok",
        "endpoint": "/api/reschedule/trigger-sms",
        "method": "POST",
        "description": "Trigger reschedule SMS to patient",
        "required_fields": [
            "patient_name",
            "phone_number",
            "site_id",
            "study_id",
            "current_appointment_id",
            "subject_id",
            "visit_id"
        ]
    }


@router.get("/session/{session_id}")
async def get_session_status(session_id: str):
    """
    Get the current status of a reschedule session

    Useful for monitoring after triggering SMS
    """

    # Get conversation_context
    context_query = """
        SELECT session_id, current_state, context_data, active, created_at, updated_at
        FROM conversation_context
        WHERE session_id = %s
    """
    context_result = db.execute_query(context_query, (session_id,))

    # Get reschedule_requests
    request_query = """
        SELECT session_id, patient_name, phone_number, site_id, study_id,
               current_appointment_id, reschedule_after_date, status,
               metadata, created_at, updated_at
        FROM reschedule_requests
        WHERE session_id = %s
    """
    request_result = db.execute_query(request_query, (session_id,))

    # Get SMS history
    sms_query = """
        SELECT direction, message_text, status, created_at
        FROM sms_conversations
        WHERE session_id = %s
        ORDER BY created_at ASC
    """
    sms_result = db.execute_query(sms_query, (session_id,))

    if not context_result and not request_result:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    return {
        "session_id": session_id,
        "conversation_context": context_result[0] if context_result else None,
        "reschedule_request": request_result[0] if request_result else None,
        "sms_history": sms_result or [],
        "sms_count": len(sms_result) if sms_result else 0
    }
