"""
Web chat interface for testing reschedule flow
Uses the same reschedule_flow_handler as SMS
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import logging
from pathlib import Path

from core.conversation.hybrid_reschedule_handler import HybridRescheduleHandler
from core.conversation.gemini_adapter import GeminiConversationAdapter
from core.conversation.reschedule_flow_handler import RescheduleFlowHandler
from core.database import db

router = APIRouter()
logger = logging.getLogger(__name__)

# Initialize Hybrid Reschedule Handler (combines Gemini NLU + State Machine execution)
try:
    hybrid_handler = HybridRescheduleHandler()
    logger.info("✓ Hybrid Reschedule Handler initialized (Gemini + State Machine)")
except Exception as e:
    logger.error(f"Failed to initialize Hybrid handler: {str(e)}")
    hybrid_handler = None

# Fallback handlers if hybrid fails
try:
    gemini_adapter = GeminiConversationAdapter()
    logger.info("✓ Gemini Conversation Adapter initialized as fallback")
except Exception as e:
    logger.error(f"Failed to initialize Gemini adapter: {str(e)}")
    gemini_adapter = None

flow_handler = RescheduleFlowHandler()
logger.info("✓ RescheduleFlowHandler initialized as fallback")


class WebChatMessage(BaseModel):
    """Web chat message request"""
    session_id: str
    message: str
    patient_data: Optional[dict] = None
    crio_auth: Optional[dict] = None  # CRIO authentication tokens from V3 Dashboard


class WebChatResponse(BaseModel):
    """Web chat response"""
    response: str
    state: Optional[str] = None
    quick_replies: Optional[List[str]] = None
    data: Optional[dict] = None


@router.get("/reschedule-chat", response_class=HTMLResponse)
async def serve_chat_interface():
    """Serve the web chat HTML interface"""
    try:
        html_path = Path(__file__).parent.parent.parent / "static" / "reschedule-chat.html"
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="Chat interface not found")

        with open(html_path, 'r') as f:
            html_content = f.read()

        return HTMLResponse(content=html_content)

    except Exception as e:
        logger.error(f"Error serving chat interface: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/reschedule/web-chat")
async def handle_web_chat_message(message: WebChatMessage) -> JSONResponse:
    """
    Handle web chat messages using the same reschedule flow as SMS

    This endpoint:
    1. Receives web chat message
    2. Routes to reschedule_flow_handler (same as SMS)
    3. Returns formatted response for web UI
    """
    try:
        logger.info(f"[WEB-CHAT] Session: {message.session_id}, Message: {message.message}")

        # Log CRIO authentication status
        if message.crio_auth:
            logger.info(f"[WEB-CHAT] Request includes CRIO authentication tokens (authenticated mode)")
        else:
            logger.info(f"[WEB-CHAT] Request without CRIO authentication (test mode)")

        # Initialize test data if this is a START message
        if message.message == "START":
            await _initialize_test_session(message.session_id, message.patient_data)

            # Get personalized greeting with patient data
            greeting = await _generate_initial_greeting(message.session_id, message.patient_data)

            return JSONResponse(content={
                "response": greeting,
                "state": "rescheduling_awaiting_confirmation",
                "quick_replies": ["YES", "RESCHEDULE", "NO"]
            })

        # PRIMARY: Use Hybrid Handler (Gemini NLU + State Machine execution)
        if hybrid_handler:
            try:
                logger.info(f"[WEB-CHAT] Using Hybrid Handler (Gemini + State Machine)")

                # Get current state
                current_state = _get_session_state(message.session_id)
                logger.info(f"[WEB-CHAT] Current state: {current_state}")

                # Process with hybrid handler
                result = await hybrid_handler.process_message(
                    session_id=message.session_id,
                    phone_number=message.session_id,  # Use session_id as identifier for web
                    message=message.message,
                    current_state=current_state
                )

                logger.info(f"[WEB-CHAT] Hybrid handler result: {result}")

                # Format response for web chat
                response_text = result.get("response", "I'm here to help you reschedule.")
                new_state = result.get("new_state", current_state)
                quick_replies = _generate_quick_replies(new_state, result)

                return JSONResponse(content={
                    "response": response_text,
                    "state": new_state,
                    "quick_replies": quick_replies,
                    "data": result.get("data", {}),
                    "metadata": result.get("metadata", {})
                })

            except Exception as e:
                logger.error(f"[WEB-CHAT] Hybrid handler error: {str(e)}", exc_info=True)
                # Fall through to fallback handlers
                logger.info(f"[WEB-CHAT] Falling back to Gemini-only or state machine")

        # FALLBACK 1: Use Gemini conversation if available
        if gemini_adapter:
            try:
                logger.info(f"[WEB-CHAT] Using Gemini conversation (fallback)")

                # Get reschedule request info for context
                reschedule_info = _get_reschedule_request_info(message.session_id)

                # Build context hint for Gemini
                context_hint = ""
                if reschedule_info:
                    patient_name = reschedule_info.get('patient_name', 'the patient')
                    appt_date = reschedule_info.get('current_appointment_date')
                    context_hint = f"[RESCHEDULE CONTEXT: Patient {patient_name} needs to reschedule appointment"
                    if appt_date:
                        context_hint += f" currently scheduled for {appt_date}"
                    context_hint += ". Help them find a new appointment time.]"

                # Process with Gemini
                gemini_message = f"{context_hint}\n\n{message.message}" if context_hint else message.message

                result = await gemini_adapter.process_message(
                    session_id=message.session_id,
                    message=gemini_message
                )

                logger.info(f"[WEB-CHAT] Gemini result: {result}")

                return JSONResponse(content={
                    "response": result.get("response", "I'm here to help you reschedule."),
                    "state": result.get("state"),
                    "quick_replies": [],  # Gemini doesn't use quick replies
                    "data": result.get("metadata")
                })

            except Exception as e:
                logger.error(f"[WEB-CHAT] Gemini error: {str(e)}", exc_info=True)
                # Fall through to state machine
                logger.info(f"[WEB-CHAT] Falling back to state machine")

        # FALLBACK 2: State machine flow handler
        logger.info(f"[WEB-CHAT] Using state machine flow handler (final fallback)")
        current_state = _get_session_state(message.session_id)
        logger.info(f"[WEB-CHAT] Current state: {current_state}")

        # Process message through reschedule flow handler
        result = await flow_handler.process_message(
            session_id=message.session_id,
            phone_number=message.session_id,  # Use session_id as identifier
            message=message.message,
            current_state=current_state
        )

        logger.info(f"[WEB-CHAT] Flow result: {result}")

        # Format response for web chat
        response_text = result.get("response", "I'm processing your request...")
        new_state = result.get("new_state")

        # Generate quick replies based on state
        quick_replies = _generate_quick_replies(new_state, result)

        return JSONResponse(content={
            "response": response_text,
            "state": new_state,
            "quick_replies": quick_replies,
            "data": result.get("data")
        })

    except Exception as e:
        logger.error(f"[WEB-CHAT] Error: {str(e)}", exc_info=True)
        return JSONResponse(
            content={
                "response": f"Sorry, I encountered an error: {str(e)}. Please try again.",
                "state": "error"
            },
            status_code=500
        )


async def _initialize_test_session(session_id: str, patient_data: Optional[dict] = None):
    """Initialize a test reschedule request for web chat testing"""
    try:
        # Check if test session already exists
        existing = db.execute_query(
            "SELECT id FROM reschedule_requests WHERE session_id = %s",
            (session_id,)
        )

        if existing:
            logger.info(f"[WEB-CHAT] Test session already exists: {session_id}")
            return

        # Extract patient data or use defaults
        if patient_data is None:
            patient_data = {}

        patient_name = patient_data.get('patient_name', 'Test Patient (Web)')
        phone_number = '+1555000' + session_id[-7:]  # Auto-generate fake phone for web
        site_id = patient_data.get('site_id', '2327')
        study_id = patient_data.get('study_id', '105093')
        current_appt_id = patient_data.get('current_appointment_id')
        subject_id = patient_data.get('subject_id')
        visit_id = patient_data.get('visit_id')
        reschedule_after = patient_data.get('reschedule_after_date')

        logger.info(f"[WEB-CHAT] ========== INITIALIZING SESSION ==========")
        logger.info(f"[WEB-CHAT] Session ID: {session_id}")
        logger.info(f"[WEB-CHAT] Patient: {patient_name}")
        logger.info(f"[WEB-CHAT] Site: {site_id}, Study: {study_id}")
        logger.info(f"[WEB-CHAT] CRIO IDs: appointment={current_appt_id}, subject={subject_id}, visit={visit_id}")
        logger.info(f"[WEB-CHAT] Reschedule after: {reschedule_after}")
        logger.info(f"[WEB-CHAT] Auto-generated phone: {phone_number}")

        # Create conversation context FIRST (required by foreign key)
        db.execute_update("""
            INSERT INTO conversation_context (
                session_id,
                current_state,
                context_data,
                active,
                created_at,
                updated_at
            ) VALUES (
                %s,
                'rescheduling_initiated',
                '{"channel": "web", "test_mode": true}'::jsonb,
                true,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
        """, (session_id,))

        # Parse reschedule_after date
        reschedule_after_sql = None
        if reschedule_after:
            # Format: "2025-11-21" from date input
            reschedule_after_sql = f"DATE '{reschedule_after}'"
        else:
            reschedule_after_sql = "CURRENT_DATE + INTERVAL '2 days'"

        # Build metadata JSONB with CRIO IDs
        import json
        metadata = {
            'subject_id': subject_id,
            'visit_id': visit_id,
            'current_appointment_id': current_appt_id,
            'channel': 'web',
            'test_mode': True
        }
        metadata_json = json.dumps(metadata)

        # Build SQL with metadata storage
        sql = f"""
            INSERT INTO reschedule_requests (
                session_id,
                patient_name,
                phone_number,
                site_id,
                study_id,
                current_appointment_id,
                reschedule_after_date,
                status,
                metadata,
                created_at
            ) VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                {reschedule_after_sql},
                'pending',
                %s::jsonb,
                CURRENT_TIMESTAMP
            )
        """

        db.execute_update(sql, (
            session_id,
            patient_name,
            phone_number,
            site_id,
            study_id,
            current_appt_id,
            metadata_json
        ))

        logger.info(f"[WEB-CHAT] ✅ Database records created successfully")
        logger.info(f"[WEB-CHAT]    - conversation_context: state=rescheduling_initiated")
        logger.info(f"[WEB-CHAT]    - reschedule_requests: status=pending")
        logger.info(f"[WEB-CHAT]    - metadata stored: {json.dumps(metadata, indent=2)}")
        logger.info(f"[WEB-CHAT] ==========================================")

    except Exception as e:
        logger.error(f"[WEB-CHAT] Failed to initialize test session: {str(e)}", exc_info=True)
        raise


async def _generate_initial_greeting(session_id: str, patient_data: Optional[dict]) -> str:
    """Generate personalized initial greeting for reschedule request"""
    try:
        # Get patient info from database
        request_info = db.execute_query("""
            SELECT patient_name, current_appointment_date
            FROM reschedule_requests
            WHERE session_id = %s
        """, (session_id,))

        if not request_info:
            # Fallback if not found
            return "Hey! This is Eric at DelRicht Research. We need to reschedule your upcoming appointment due to a Study update. We apologize for any inconvenience and truly appreciate you being a valued Patient. Can we find another time that works?"

        patient_name = request_info[0]['patient_name']
        appt_date = request_info[0]['current_appointment_date']

        # Extract first name
        first_name = patient_name.split()[0] if patient_name else "there"

        # Format appointment date
        if appt_date:
            from datetime import datetime
            if isinstance(appt_date, str):
                appt_datetime = datetime.fromisoformat(appt_date.replace('Z', '+00:00'))
            else:
                appt_datetime = appt_date

            formatted_date = appt_datetime.strftime("%B %d")  # "November 21"
            formatted_time = appt_datetime.strftime("%I:%M %p").lstrip('0')  # "2:30 PM"

            greeting = (
                f"Hey {first_name}! This is Eric at DelRicht Research. "
                f"Unfortunately, we need to reschedule your upcoming appointment on {formatted_date} at {formatted_time} "
                f"due to a Study update. We apologize for any inconvenience this may cause and truly appreciate you being "
                f"a valued Patient. Can we find another time that works to reschedule?"
            )
        else:
            # No appointment date available
            greeting = (
                f"Hey {first_name}! This is Eric at DelRicht Research. "
                f"Unfortunately, we need to reschedule your upcoming appointment due to a Study update. "
                f"We apologize for any inconvenience this may cause and truly appreciate you being a valued Patient. "
                f"Can we find another time that works to reschedule?"
            )

        return greeting

    except Exception as e:
        logger.error(f"[WEB-CHAT] Error generating greeting: {str(e)}", exc_info=True)
        # Fallback generic message
        return "Hey! This is Eric at DelRicht Research. We need to reschedule your upcoming appointment due to a Study update. We apologize for any inconvenience and truly appreciate you being a valued Patient. Can we find another time that works?"


def _get_reschedule_request_info(session_id: str) -> Optional[dict]:
    """Get reschedule request information for Gemini context"""
    try:
        result = db.execute_query("""
            SELECT patient_name, current_appointment_date, reschedule_after_date, site_id, study_id
            FROM reschedule_requests
            WHERE session_id = %s
        """, (session_id,))

        if result:
            return result[0]

        return None

    except Exception as e:
        logger.error(f"[WEB-CHAT] Error getting reschedule info: {str(e)}")
        return None


def _get_session_state(session_id: str) -> Optional[str]:
    """Get current conversation state for session"""
    try:
        result = db.execute_query("""
            SELECT current_state
            FROM conversation_context
            WHERE session_id = %s AND active = true
        """, (session_id,))

        if result:
            return result[0]['current_state']

        return None

    except Exception as e:
        logger.error(f"[WEB-CHAT] Error getting session state: {str(e)}")
        return None


def _generate_quick_replies(state: Optional[str], result: dict) -> Optional[List[str]]:
    """Generate quick reply suggestions based on current state"""

    if not state:
        return None

    # State-specific quick replies
    quick_replies_map = {
        "rescheduling_awaiting_confirmation": ["YES", "RESCHEDULE", "NO"],
        "rescheduling_awaiting_availability": [
            "Afternoons next week",
            "Mornings only",
            "Any weekday",
            "Afternoons preferred"
        ],
        "rescheduling_awaiting_selection": []  # Will be populated with slot numbers
    }

    # For slot selection, dynamically generate 1, 2 based on available slots
    if state == "rescheduling_awaiting_selection":
        slots = result.get("data", {}).get("slots", [])
        return [str(i+1) for i in range(min(len(slots), 2))]

    return quick_replies_map.get(state)


@router.get("/api/reschedule/web-chat/health")
async def web_chat_health():
    """Health check for web chat endpoint"""
    return {
        "status": "healthy",
        "service": "reschedule_web_chat",
        "endpoint": "/reschedule-chat"
    }
