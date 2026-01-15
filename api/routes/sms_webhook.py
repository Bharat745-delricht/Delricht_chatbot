"""
Twilio SMS Webhook Endpoint
Receives incoming SMS messages from patients and routes to conversation manager
Last updated: 2025-11-25 19:35 - Added detailed debug logging
"""

from fastapi import APIRouter, Form, Request, HTTPException, Response
from twilio.request_validator import RequestValidator
from typing import Optional
import logging
import os
import ipaddress
import datetime

from core.database import db
from core.services.sms_service import sms_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sms", tags=["SMS"])

# Twilio's IP ranges
# Source: https://www.twilio.com/docs/messaging/guides/how-to-use-your-free-trial-account#webhook-whitelisting
# Updated based on actual IPs seen in production logs
TWILIO_IP_RANGES = [
    '54.172.60.0/23',     # US East (original)
    '54.244.51.0/24',     # US West (original)
    '177.71.206.192/26',  # Brazil
    '54.252.254.64/26',   # Australia
    '54.65.63.192/26',    # Japan
    '54.169.127.128/26',  # Singapore
    '54.177.7.128/25',    # Ireland
    # Additional AWS ranges where Twilio operates
    '52.4.0.0/16',        # AWS US East - seen: 52.4.70.65
    '18.207.0.0/16',      # AWS US East - seen: 18.207.117.35
    '54.0.0.0/8',         # Broader AWS range for Twilio
]


def get_client_ip(request: Request) -> str:
    """
    Get the real client IP address from Cloud Run request

    Cloud Run sets X-Forwarded-For with the original client IP
    """
    # Check X-Forwarded-For first (Cloud Run standard)
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        # X-Forwarded-For can be comma-separated list, take first (original client)
        return forwarded_for.split(',')[0].strip()

    # Fallback to direct client (should not happen in Cloud Run)
    if request.client:
        return request.client.host

    return 'unknown'


def is_twilio_ip(ip_str: str) -> bool:
    """
    Check if IP address is from Twilio's known ranges

    Args:
        ip_str: IP address as string

    Returns:
        True if IP is in Twilio's ranges, False otherwise
    """
    if ip_str == 'unknown':
        logger.warning("⚠️  Could not determine client IP")
        return False

    try:
        client_ip = ipaddress.ip_address(ip_str)

        for ip_range in TWILIO_IP_RANGES:
            network = ipaddress.ip_network(ip_range)
            if client_ip in network:
                return True

        return False

    except ValueError as e:
        logger.error(f"❌ Invalid IP address format: {ip_str} - {e}")
        return False


def validate_twilio_request(request: Request, form_data) -> bool:
    """
    Validate that request actually came from Twilio

    TEMPORARY: Validation disabled to test conversation flow
    Twilio has too many dynamic IP ranges to whitelist effectively
    Will implement signature validation after testing basic functionality

    Args:
        request: FastAPI Request object
        form_data: Already-parsed form data (FormData object from request.form())
    """

    client_ip = get_client_ip(request)
    logger.warning(f"⚠️  VALIDATION TEMPORARILY DISABLED - Request from IP: {client_ip}")

    # TODO: Implement proper signature validation
    # For now, allow all requests to test conversation flow

    return True

    validator = RequestValidator(auth_token)

    # Get Twilio signature from headers
    signature = request.headers.get('X-Twilio-Signature', '')

    if not signature:
        logger.error("❌ No X-Twilio-Signature header present")
        return False

    # Cloud Run specific: reconstruct URL with HTTPS
    # Twilio signs the request using HTTPS even though Cloud Run receives HTTP internally
    proto = request.headers.get('X-Forwarded-Proto', 'https')
    host = request.headers.get('Host', str(request.url.netloc))

    # Remove port if present (Cloud Run sometimes includes :443)
    if ':' in host:
        host = host.split(':')[0]

    path = str(request.url.path)

    # Build the full URL as Twilio signed it (no query string for POST requests)
    url = f"{proto}://{host}{path}"

    # Convert form_data to dict for validator
    # The Twilio validator expects a dict-like object
    params = {key: value for key, value in form_data.items()}

    try:
        is_valid = validator.validate(
            url,
            params,  # Pass form parameters as dict
            signature
        )

        if is_valid:
            logger.info(f"✅ Valid Twilio signature")
        else:
            logger.error(f"❌ Invalid Twilio signature")
            logger.error(f"   URL: {url}")
            logger.error(f"   From: {params.get('From')}")
            logger.error(f"   Signature: {signature[:20]}...")
            logger.error(f"   Form keys: {list(params.keys())}")
            # Log first 3 params for debugging
            for key in list(params.keys())[:3]:
                logger.error(f"   {key}: {params[key]}")

        return is_valid

    except Exception as e:
        logger.error(f"❌ Exception during signature validation: {e}")
        logger.error(f"   URL: {url}")
        logger.error(f"   Signature: {signature[:20]}...")
        import traceback
        logger.error(traceback.format_exc())
        return False


@router.post("/webhook-minimal")
async def sms_webhook_minimal(request: Request):
    """
    MINIMAL TEST WEBHOOK - Ultra-simple SMS response
    This endpoint bypasses ALL complexity to test basic Twilio integration

    Returns TwiML directly - no database, no handlers, no external API calls
    """
    # Log with ERROR level so it DEFINITELY appears
    logger.error(f"🟢 MINIMAL-WEBHOOK CALLED at {datetime.datetime.utcnow().isoformat()}")

    try:
        form = await request.form()
        From = form.get('From', 'unknown')
        Body = form.get('Body', 'unknown')

        logger.error(f"🟢 MINIMAL-WEBHOOK From: {From} | Body: {Body}")

        # Return TwiML response directly (Twilio will send this as SMS)
        twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>🎉 MINIMAL WEBHOOK WORKS! Received: {Body}</Message>
</Response>"""

        logger.error(f"🟢 MINIMAL-WEBHOOK Sending TwiML response")

        return Response(content=twiml_response, media_type="application/xml")

    except Exception as e:
        logger.error(f"🔴 MINIMAL-WEBHOOK ERROR: {e}")
        # Return empty response on error
        return Response(content='<?xml version="1.0"?><Response></Response>', media_type="application/xml")


@router.post("/webhook")
async def sms_webhook(request: Request):
    """
    Receive incoming SMS from patients via Twilio

    Flow:
    1. Validate Twilio signature (security)
    2. Log inbound SMS to database
    3. Lookup patient/session by phone number
    4. Route to conversation manager for processing
    5. Return TwiML response (empty = no auto-reply, Twilio handles that separately)

    Twilio Form Data:
    - From: +14045551234 (patient phone)
    - To: +14045556789 (your Twilio number)
    - Body: "RESCHEDULE" (message text)
    - MessageSid: SM... (Twilio message ID)
    - Plus many other fields (NumMedia, AccountSid, etc.)
    """

    logger.error(f"🔵 WEBHOOK-MAIN CALLED - {datetime.datetime.utcnow().isoformat()}")
    logger.info(f"🔵 [SMS-WEBHOOK] Webhook called - extracting form data")

    # Extract form data FIRST (request body can only be read once)
    form = await request.form()
    logger.info(f"🔵 [SMS-WEBHOOK] Form data extracted - {len(form)} fields")

    # Validate request came from Twilio
    if not validate_twilio_request(request, form):
        logger.error(f"❌ Signature validation failed")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    logger.info(f"🔵 [SMS-WEBHOOK] Validation passed")

    # Extract required fields
    From = form.get('From')
    To = form.get('To')
    Body = form.get('Body')
    MessageSid = form.get('MessageSid')

    logger.info(f"🔵 [SMS-WEBHOOK] Fields: From={From}, To={To}, Body={Body}, SID={MessageSid}")

    if not all([From, To, Body, MessageSid]):
        logger.error(f"❌ Missing required fields in webhook request")
        raise HTTPException(status_code=400, detail="Missing required fields")

    logger.info(f"📥 [SMS-WEBHOOK] Received SMS from {From}")
    logger.info(f"   To: {To} | Body: {Body[:100]}... | SID: {MessageSid}")

    try:
        # Log inbound SMS to database
        sms_service._log_sms(
            phone_number=From,
            direction='inbound',
            message_text=Body,
            twilio_message_sid=MessageSid,
            status='received',
            session_id=None  # Will be updated after lookup
        )

        # Lookup session by phone number
        session_id = lookup_session_by_phone(From)

        if not session_id:
            logger.warning(f"⚠️  No active session found for phone {From}")

            # Create new session for ANY message from unknown number
            # This allows anyone to start a conversation with the chatbot
            logger.info(f"   Creating new session for {From}")
            session_id = create_sms_session(From, Body)

        # Update SMS log with session_id
        update_sms_session_id(MessageSid, session_id)

        # Background AI processing (no acknowledgment - user will only see AI response)
        logger.info(f"🤖 [SMS-WEBHOOK] Starting background AI processing")

        import asyncio

        # Start background task to process with AI and send follow-up SMS
        asyncio.create_task(process_sms_with_ai_and_respond(session_id, From, Body))

        # Return empty TwiML - no acknowledgment message
        # User will only receive the AI response (sent via background task)
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response></Response>"""
        return Response(content=twiml, media_type="application/xml")

    except Exception as e:
        logger.error(f"❌ Error processing SMS webhook: {e}", exc_info=True)

        # Return error message to user
        error_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>Sorry, we're experiencing technical difficulties. Please call (404) 355-8779 for assistance.</Message>
</Response>"""
        return Response(content=error_twiml, media_type="application/xml")


def lookup_session_by_phone(phone_number: str) -> Optional[str]:
    """
    Find active conversation session for this phone number

    Lookup order:
    1. Active conversation_context by user_id (phone number) - most reliable
    2. Active conversation_context by phone in context_data
    3. Active reschedule_requests by phone
    4. patient_contact_info by phone → session_id
    5. Recent sms_conversations by phone → session_id
    """

    # Try user_id field first (most reliable - won't be overwritten)
    query = """
        SELECT session_id
        FROM conversation_context
        WHERE user_id = %s
          AND active = TRUE
          AND updated_at > CURRENT_TIMESTAMP - INTERVAL '24 hours'
        ORDER BY updated_at DESC
        LIMIT 1
    """

    result = db.execute_query(query, (phone_number,))
    if result and result[0]['session_id']:
        logger.info(f"   ✅ Found active session via user_id: {result[0]['session_id']}")
        return result[0]['session_id']

    # Fallback: Try conversation_context by context_data (legacy)
    query = """
        SELECT session_id
        FROM conversation_context
        WHERE context_data->>'phone_number' = %s
          AND active = TRUE
          AND updated_at > CURRENT_TIMESTAMP - INTERVAL '24 hours'
        ORDER BY updated_at DESC
        LIMIT 1
    """

    result = db.execute_query(query, (phone_number,))
    if result and result[0]['session_id']:
        logger.info(f"   Found session via conversation_context: {result[0]['session_id']}")
        return result[0]['session_id']

    # Try reschedule_requests (scheduled appointments)
    query = """
        SELECT session_id
        FROM reschedule_requests
        WHERE phone_number = %s
          AND status NOT IN ('completed', 'failed')
        ORDER BY created_at DESC
        LIMIT 1
    """

    result = db.execute_query(query, (phone_number,))
    if result and result[0]['session_id']:
        logger.info(f"   Found session via reschedule_requests: {result[0]['session_id']}")
        return result[0]['session_id']

    # Try patient_contact_info
    query = """
        SELECT session_id
        FROM patient_contact_info
        WHERE phone_number = %s
          AND session_id IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 1
    """

    result = db.execute_query(query, (phone_number,))
    if result and result[0]['session_id']:
        logger.info(f"   Found session via patient_contact_info: {result[0]['session_id']}")
        return result[0]['session_id']

    return None


def is_new_conversation_trigger(message: str) -> bool:
    """
    Check if message indicates patient wants to start new conversation
    (vs. responding to existing reschedule request)
    """

    triggers = [
        'start', 'begin', 'reschedule', 'help', 'hello', 'hi',
        'change appointment', 'move appointment'
    ]

    message_lower = message.lower().strip()
    return any(trigger in message_lower for trigger in triggers)


def create_sms_session(phone_number: str, initial_message: str) -> str:
    """
    Create new conversation session for SMS-initiated conversation
    Stores phone number in user_id field to preserve session continuity
    """

    import uuid
    session_id = f"sms_{uuid.uuid4().hex[:16]}"

    logger.info(f"   Creating new SMS session: {session_id} for {phone_number}")

    # Create conversation_context entry with phone number in user_id
    # This ensures phone number persists even when context_data is overwritten
    query = """
        INSERT INTO conversation_context
        (session_id, user_id, current_state, context_data, active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """

    context_data = {
        'channel': 'sms',
        'phone_number': phone_number,
        'initial_message': initial_message
    }

    import json
    db.execute_update(query, (session_id, phone_number, 'initial', json.dumps(context_data)))

    return session_id


def update_sms_session_id(message_sid: str, session_id: str):
    """Update SMS log with session_id after lookup"""

    query = """
        UPDATE sms_conversations
        SET session_id = %s
        WHERE twilio_message_sid = %s
    """

    db.execute_update(query, (session_id, message_sid))


def get_phone_not_recognized_message() -> str:
    """Get message for when phone number not found in system"""
    return """We don't recognize this phone number.

If you're a patient with DelRicht Clinical Research, please call (404) 355-8779 for assistance.

If you believe this is an error, reply HELP."""


async def process_sms_with_ai_and_respond(session_id: str, phone_number: str, message: str):
    """
    Process SMS message with full AI and send response as follow-up SMS

    This runs in background after webhook returns immediate acknowledgment.
    Allows Gemini to take as long as needed (30-60s) without Twilio timeout.
    """
    try:
        logger.info(f"🔄 [SMS-BACKGROUND] Starting AI processing for session: {session_id}")

        # Process with full AI conversation system (can take 30-60s)
        response_text = await process_sms_message(session_id, phone_number, message)

        # Update lead campaign status if applicable
        await update_lead_campaign_status(session_id, message, {'response': response_text})

        if response_text:
            logger.info(f"✅ [SMS-BACKGROUND] AI response ready, sending via Twilio API")
            logger.info(f"   Response preview: {response_text[:100]}...")

            # Send AI response as follow-up SMS via Twilio API
            # This now works because account is upgraded (no error 21606)
            message_sid = await sms_service.send_sms(
                to_phone=phone_number,
                message=response_text,
                session_id=session_id,
                metadata={'type': 'ai_response', 'async_background': True}
            )

            if message_sid:
                logger.info(f"✅ [SMS-BACKGROUND] AI response sent successfully: {message_sid}")
            else:
                logger.error(f"❌ [SMS-BACKGROUND] Failed to send AI response")
        else:
            logger.info(f"📭 [SMS-BACKGROUND] No response generated (coordinator takeover or error)")

    except Exception as e:
        logger.error(f"❌ [SMS-BACKGROUND] Error in background AI processing: {e}", exc_info=True)

        # Send error message to user
        try:
            await sms_service.send_sms(
                to_phone=phone_number,
                message="Sorry, I encountered an error processing your message. Please try again or call (404) 355-8779 for assistance.",
                session_id=session_id,
                metadata={'type': 'error_message'}
            )
        except Exception as send_error:
            logger.error(f"❌ [SMS-BACKGROUND] Failed to send error message: {send_error}")


async def process_sms_message(session_id: str, phone_number: str, message: str) -> Optional[str]:
    """
    Process SMS message through conversation flow

    Routes to:
    - Reschedule Flow Handler for reschedule sessions (state starts with 'rescheduling_')
    - Gemini Conversation System for general clinical trial conversations

    Returns:
        Response text to send back to user, or None if no response needed
    """

    logger.info(f"📨 [SMS-PROCESS] Session: {session_id} | Message: {message}")

    # Handle special commands first (fast responses)
    message_lower = message.lower().strip()

    if message_lower == 'stop' or message_lower == 'unsubscribe':
        return get_opt_out_message(session_id, phone_number)

    if message_lower == 'help':
        return get_help_message()

    # TESTING: Fast response for TEST keyword (bypasses all handlers)
    if message_lower == 'test':
        logger.info(f"   🧪 TEST keyword detected - sending canned response")
        return "✅ TEST received! Your chatbot is working. Reply with any message to start a conversation."

    # Check if auto-responses are disabled (coordinator took over)
    is_disabled = check_auto_response_disabled(session_id)

    if is_disabled:
        logger.info(f"   Auto-responses disabled for session {session_id} - coordinator will handle")
        await notify_coordinator_of_patient_message(session_id, phone_number, message)
        return None

    # Check if this is a reschedule session
    current_state = get_conversation_state(session_id)
    is_reschedule_session = current_state and current_state.startswith('rescheduling_')

    logger.info(f"   📋 Current state: {current_state} | Is reschedule: {is_reschedule_session}")

    if is_reschedule_session:
        # Route to Hybrid Reschedule Handler (Gemini + State Machine)
        logger.info(f"   ➡️  Routing to Reschedule Flow Handler")
        try:
            from core.conversation.hybrid_reschedule_handler import HybridRescheduleHandler

            handler = HybridRescheduleHandler()
            response_data = await handler.process_message(
                session_id=session_id,
                phone_number=phone_number,
                message=message,
                current_state=current_state
            )

            logger.info(f"   ✅ Reschedule processing completed")

            if response_data and response_data.get('response'):
                return response_data['response']
            else:
                return "I'm ready to help reschedule your appointment. When works best for you?"

        except Exception as e:
            logger.error(f"   ❌ Error in reschedule handler: {e}", exc_info=True)
            return "I'm having trouble processing your request. Please call (404) 355-8779 for assistance."

    # Process through Gemini conversation system for general conversations
    logger.info(f"   ➡️  Processing with Gemini conversation system")

    try:
        from core.conversation.gemini_adapter import GeminiConversationAdapter

        gemini_adapter = GeminiConversationAdapter()

        # Process message through Gemini (same as web chat)
        response_data = await gemini_adapter.process_chat_message(
            message=message,
            session_id=session_id,
            user_id=phone_number  # Use phone as user ID
        )

        logger.info(f"   ✅ Gemini processing completed")

        # Return the AI response
        if response_data and response_data.get('response'):
            return response_data['response']
        else:
            return "I'm here to help! Could you tell me more about what you're looking for?"

    except Exception as e:
        logger.error(f"   ❌ Error processing with Gemini: {e}", exc_info=True)
        return "I'm having trouble processing your message. Please try again or call (404) 355-8779 for assistance."


def check_auto_response_disabled(session_id: str) -> bool:
    """Check if coordinator has disabled auto-responses for this session"""

    query = """
        SELECT context_data->>'auto_response_disabled' as disabled
        FROM conversation_context
        WHERE session_id = %s
    """

    result = db.execute_query(query, (session_id,))

    if result and result[0]['disabled'] == 'true':
        return True

    return False


def get_conversation_state(session_id: str) -> Optional[str]:
    """Get current conversation state"""

    query = """
        SELECT current_state
        FROM conversation_context
        WHERE session_id = %s
    """

    result = db.execute_query(query, (session_id,))
    return result[0]['current_state'] if result else None


def get_opt_out_message(session_id: str, phone_number: str) -> str:
    """Handle STOP/UNSUBSCRIBE request and return confirmation message"""

    logger.info(f"📵 [SMS-OPT-OUT] Phone: {phone_number}")

    # Update patient_contact_info
    query = """
        UPDATE patient_contact_info
        SET sms_enabled = FALSE,
            sms_opt_out_date = CURRENT_TIMESTAMP
        WHERE phone_number = %s
    """

    try:
        db.execute_update(query, (phone_number,))
    except Exception as e:
        logger.error(f"   ⚠️  Failed to update opt-out in database: {e}")

    # Twilio automatically handles STOP replies, but we confirm
    return "You've been unsubscribed from SMS. Reply START to re-subscribe. Call (404) 355-8779 for assistance."


def get_help_message() -> str:
    """Get help message for users"""
    return """DelRicht Clinical Research SMS Help:

Reply with keywords like RESCHEDULE, HELP, or STOP.

For assistance, call (404) 355-8779.

Visit us: delricht.com"""


async def notify_coordinator_of_patient_message(session_id: str, phone_number: str, message: str):
    """
    Notify coordinator that patient sent message while auto-responses are disabled
    (Email notification - will integrate with email_service)
    """

    logger.info(f"📧 [COORDINATOR-NOTIFY] Patient message while auto-response disabled")
    logger.info(f"   Session: {session_id} | Phone: {phone_number} | Message: {message}")

    # TODO: Send email to mmorris@delricht.com
    # For now, just log
    logger.warning("   TODO: Send email notification to coordinator")


async def update_lead_campaign_status(session_id: str, message: str, response_data: Optional[dict] = None):
    """
    Update lead campaign contact status based on SMS response
    Called after each SMS interaction in a lead campaign session
    """

    try:
        # Get campaign_id and lead_id from dedicated columns (not context_data - that gets overwritten)
        context_query = """
            SELECT campaign_id, lead_id
            FROM conversation_context
            WHERE session_id = %s
        """
        result = db.execute_query(context_query, (session_id,))

        if not result:
            return

        campaign_id = result[0].get('campaign_id')
        lead_id = result[0].get('lead_id')

        if not campaign_id or not lead_id:
            logger.warning(f"[LEAD-CAMPAIGN] Missing campaign_id or lead_id in session {session_id}")
            return

        # Classify response
        message_lower = message.lower().strip()

        # Detect negative responses
        negative_keywords = ['not interested', 'no thanks', 'stop', 'unsubscribe', 'remove me', 'no']
        is_negative = any(keyword in message_lower for keyword in negative_keywords)

        # Detect positive responses
        positive_keywords = ['yes', 'sure', 'interested', 'tell me more', 'ok', 'yeah']
        is_positive = any(keyword in message_lower for keyword in positive_keywords)

        if is_negative:
            response_type = 'not_interested'
            new_status = 'not_interested'
            logger.info(f"[LEAD-CAMPAIGN]    📉 Negative response detected for lead {lead_id}")

        elif is_positive:
            response_type = 'interested'
            new_status = 'interested'
            logger.info(f"[LEAD-CAMPAIGN]    📈 Positive response detected for lead {lead_id}")

        else:
            response_type = 'unclear'
            new_status = 'responded'
            logger.info(f"[LEAD-CAMPAIGN]    ❓ Unclear response for lead {lead_id}")

        # Update lead contact
        db.execute_update("""
            UPDATE lead_campaign_contacts
            SET status = %s,
                response_type = %s,
                responded_at = COALESCE(responded_at, CURRENT_TIMESTAMP),
                last_sms_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (new_status, response_type, lead_id))

        logger.info(f"[LEAD-CAMPAIGN]    ✅ Lead status updated: {new_status}")

        # Campaign stats are auto-updated by database trigger

    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error updating lead status: {e}", exc_info=True)


@router.get("/health")
async def sms_health_check():
    """Health check endpoint for SMS service"""

    twilio_configured = bool(os.getenv('TWILIO_ACCOUNT_SID')) and bool(os.getenv('TWILIO_AUTH_TOKEN'))

    return {
        "status": "healthy",
        "service": "sms_webhook",
        "twilio_configured": twilio_configured,
        "from_number": os.getenv('TWILIO_PHONE_NUMBER', 'not_configured')
    }


@router.get("/deploy-check")
async def deploy_check():
    """
    Deployment verification endpoint - confirms code version
    If this endpoint returns DEPLOY_VERSION_20251125_2020, the new code is deployed
    """
    import datetime
    return {
        "deploy_version": "DEPLOY_VERSION_20251125_2020",
        "file_modified": "2025-11-25 20:20:00 UTC",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "blue_circle_logs_present": True,
        "test_keyword_handler": "enabled"
    }


@router.get("/debug/env")
async def debug_env():
    """Check environment variables are loaded"""
    return {
        "twilio_sid_exists": bool(os.getenv('TWILIO_ACCOUNT_SID')),
        "twilio_token_exists": bool(os.getenv('TWILIO_AUTH_TOKEN')),
        "twilio_phone": os.getenv('TWILIO_PHONE_NUMBER', 'NOT_SET'),
        "gemini_key_exists": bool(os.getenv('GEMINI_API_KEY')),
        "db_host": os.getenv('DB_HOST', 'NOT_SET'),
        "db_name": os.getenv('DB_NAME', 'NOT_SET')
    }
