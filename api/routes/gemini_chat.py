"""
Gemini-powered chat endpoint using the new conversation system.

This endpoint uses Gemini's structured responses for intent detection,
entity extraction, and conversation management.

Safety Features:
- Emergency detection with redirect to emergency services
- Medical advice request blocking
- Output validation to prevent medical advice generation
- Prompt injection protection
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
import uuid
import os

from core.conversation.gemini_adapter import GeminiConversationAdapter
from core.safety import SafetyValidator, SafetyCheckResult

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    """Chat request model"""
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None


class FeedbackRequest(BaseModel):
    """Message feedback request model"""
    session_id: str
    message_id: int
    feedback_type: str  # 'positive' or 'negative'
    rating_context: Optional[str] = 'response_timing'  # 'response_timing' or 'question_quality'
    intent_type: Optional[str] = None


class ChatResponse(BaseModel):
    """Chat response model"""
    response: str
    session_id: str
    intent: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    quick_replies: Optional[list] = None  # For interactive buttons/suggestions


class ConversationStateResponse(BaseModel):
    """Conversation state response model"""
    state: str
    condition: Optional[str] = None
    location: Optional[str] = None
    history_length: int


class ResetResponse(BaseModel):
    """Reset response model"""
    status: str
    message: str
    new_state: Optional[str] = None


# Initialize the Gemini conversation adapter
try:
    gemini_adapter = GeminiConversationAdapter()
    logger.info("Gemini Conversation Adapter initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Gemini Conversation Adapter: {str(e)}")
    gemini_adapter = None

# Initialize safety validator
safety_validator = SafetyValidator()
logger.info("Safety Validator initialized")


@router.post("/chat", response_model=ChatResponse)
async def gemini_chat_endpoint(request: ChatRequest):
    """
    Gemini-powered chat endpoint with safety validation.

    This endpoint uses Gemini's structured responses for:
    - Intent detection
    - Entity extraction
    - Conversation state management
    - Response generation

    Safety features:
    - Emergency detection (redirect to 911/crisis lines)
    - Medical advice request blocking
    - Output validation
    - Prompt injection protection
    """
    if not gemini_adapter:
        raise HTTPException(
            status_code=503,
            detail="Gemini conversation system is not available"
        )

    # Generate session ID if not provided
    session_id = request.session_id or str(uuid.uuid4())

    # Prepend "dev_" to session IDs in dev environment for filtering
    environment = os.getenv("ENVIRONMENT", "prod")
    if environment == "dev" and not session_id.startswith("dev_"):
        session_id = f"dev_{session_id}"
        logger.info(f"Dev environment detected - prepended 'dev_' to session ID")

    logger.info("="*80)
    logger.info("🎯 GEMINI CHAT ENDPOINT - NEW REQUEST")
    logger.info(f"Session ID: {session_id}")
    logger.info(f"User ID: {request.user_id}")
    logger.info(f"User Message: '{request.message}'")
    logger.info(f"Message Length: {len(request.message)} chars")
    logger.info("="*80)

    # ==========================================================================
    # SAFETY CHECK: Validate input BEFORE processing
    # ==========================================================================
    input_safety = safety_validator.check_input(request.message)

    if not input_safety.is_safe:
        logger.warning(f"🚨 SAFETY: Input blocked - Status: {input_safety.status.value}")
        logger.warning(f"   Reason: {input_safety.reason}")
        logger.warning(f"   Matched: {input_safety.matched_pattern}")

        # Return safety response without processing through Gemini
        return ChatResponse(
            response=input_safety.response,
            session_id=session_id,
            intent={
                "type": "safety_intervention",
                "confidence": 1.0,
                "entities": {},
                "next_action": input_safety.status.value,
                "reasoning": f"Safety check triggered: {input_safety.reason}"
            },
            metadata={
                "safety_triggered": True,
                "safety_status": input_safety.status.value,
                "safety_reason": input_safety.reason,
                "processing_method": "safety_validator"
            }
        )

    logger.info("✅ SAFETY: Input validation passed")

    try:
        # Process message through Gemini conversation system
        response_data = await gemini_adapter.process_chat_message(
            message=request.message,
            session_id=session_id,
            user_id=request.user_id
        )
        
        # Log successful processing with detailed information
        logger.info("✅ GEMINI PROCESSING COMPLETED")
        logger.info(f"   - Response Length: {len(response_data.get('response', ''))} chars")
        logger.info(f"   - Response Preview: {response_data.get('response', '')[:100]}...")
        
        intent_data = response_data.get("intent", {})
        logger.info(f"   - Intent Type: {intent_data.get('type', 'N/A')}")
        logger.info(f"   - Intent Confidence: {intent_data.get('confidence', 'N/A')}")
        logger.info(f"   - Intent Reasoning: {intent_data.get('reasoning', 'N/A')}")
        logger.info(f"   - Next Action: {intent_data.get('next_action', 'N/A')}")
        
        # Log entity extraction details
        entities = intent_data.get('entities', {})
        if entities:
            logger.info("🔍 ENTITY EXTRACTION RESULTS:")
            logger.info(f"   - Condition: {entities.get('condition', 'N/A')}")
            logger.info(f"   - Location: {entities.get('location', 'N/A')}")
            logger.info(f"   - Age: {entities.get('age', 'N/A')}")
            logger.info(f"   - Boolean Answer: {entities.get('boolean_answer', 'N/A')}")
            logger.info(f"   - Number: {entities.get('number', 'N/A')}")
            logger.info(f"   - Trial Name: {entities.get('trial_name', 'N/A')}")
            logger.info(f"   - Medication: {entities.get('medication', 'N/A')}")
        
        # Log metadata
        metadata = response_data.get("metadata", {})
        if metadata:
            logger.info("📊 RESPONSE METADATA:")
            logger.info(f"   - Processing Method: {metadata.get('processing_method', 'N/A')}")
            logger.info(f"   - Action Type: {metadata.get('action_type', 'N/A')}")
            logger.info(f"   - Conversation State: {metadata.get('conversation_state', 'N/A')}")
            
            # Log prescreening-specific metadata
            if 'prescreening' in str(metadata).lower():
                logger.info("📝 PRESCREENING METADATA:")
                for key, value in metadata.items():
                    if 'prescreening' in key.lower() or 'question' in key.lower():
                        logger.info(f"   - {key}: {value}")
        
        # Log quick_replies if present
        quick_replies = response_data.get("quick_replies")
        logger.error(f"🔍 API ROUTE - response_data keys: {list(response_data.keys())}")
        logger.error(f"🔍 API ROUTE - quick_replies value: {quick_replies}")
        logger.error(f"🔍 API ROUTE - quick_replies type: {type(quick_replies)}")
        if quick_replies:
            logger.error(f"🎯 QUICK REPLIES: {len(quick_replies)} buttons being sent to frontend")
            logger.error(f"   Labels: {[qr.get('label') for qr in quick_replies]}")
        else:
            logger.error("⚠️  No quick_replies in response_data")

        logger.info("🎉 GEMINI CHAT ENDPOINT - REQUEST COMPLETED")
        logger.info("="*80)

        # ======================================================================
        # SAFETY CHECK: Validate output BEFORE returning to user
        # ======================================================================
        bot_response = response_data["response"]
        output_safety = safety_validator.check_output(bot_response)

        if not output_safety.is_safe:
            logger.warning(f"🚨 SAFETY: Output blocked - Status: {output_safety.status.value}")
            logger.warning(f"   Reason: {output_safety.reason}")
            logger.warning(f"   Matched: {output_safety.matched_pattern}")
            logger.warning(f"   Original response (truncated): {bot_response[:200]}...")

            # Return safe fallback response instead
            return ChatResponse(
                response=output_safety.response,
                session_id=session_id,
                intent={
                    "type": "safety_intervention",
                    "confidence": 1.0,
                    "entities": {},
                    "next_action": "output_blocked",
                    "reasoning": f"Output safety check triggered: {output_safety.reason}"
                },
                metadata={
                    "safety_triggered": True,
                    "safety_status": output_safety.status.value,
                    "safety_reason": output_safety.reason,
                    "processing_method": "safety_validator_output",
                    "original_metadata": response_data.get("metadata")
                }
            )

        logger.info("✅ SAFETY: Output validation passed")

        # Return response
        return ChatResponse(
            response=response_data["response"],
            session_id=session_id,
            intent=response_data.get("intent"),
            metadata=response_data.get("metadata"),
            quick_replies=quick_replies  # Pass quick_replies to frontend
        )
        
    except Exception as e:
        logger.error("❌ GEMINI CHAT ENDPOINT - ERROR OCCURRED")
        logger.error(f"Error: {str(e)}")
        logger.error(f"Error Type: {type(e).__name__}")
        import traceback
        logger.error(f"Stack Trace: {traceback.format_exc()}")
        logger.error("="*80)
        
        raise HTTPException(
            status_code=500, 
            detail="Error processing chat message"
        )


@router.get("/conversation/{session_id}/state", response_model=ConversationStateResponse)
async def get_conversation_state(session_id: str):
    """Get current conversation state for a session"""
    if not gemini_adapter:
        raise HTTPException(
            status_code=503, 
            detail="Gemini conversation system is not available"
        )
    
    try:
        state_data = gemini_adapter.get_conversation_state(session_id)
        return ConversationStateResponse(**state_data)
    except Exception as e:
        logger.error(f"Error getting conversation state: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Error retrieving conversation state"
        )


@router.post("/conversation/{session_id}/reset", response_model=ResetResponse)
async def reset_conversation(session_id: str):
    """Reset conversation state for a session"""
    if not gemini_adapter:
        raise HTTPException(
            status_code=503, 
            detail="Gemini conversation system is not available"
        )
    
    try:
        result = gemini_adapter.reset_conversation(session_id)
        return ResetResponse(**result)
    except Exception as e:
        logger.error(f"Error resetting conversation: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Error resetting conversation"
        )


@router.get("/health")
async def health_check():
    """Health check for Gemini chat system with performance metrics"""
    if not gemini_adapter:
        return {
            "status": "unhealthy",
            "message": "Gemini conversation system is not available",
            "gemini_adapter": False
        }
    
    # Get Gemini service cache stats
    from core.services.gemini_service import gemini_service
    cache_stats = gemini_service.get_cache_stats()
    
    return {
        "status": "healthy",
        "message": "Gemini conversation system is operational",
        "gemini_adapter": True,
        "performance": {
            "cache_enabled": True,
            "cache_size": cache_stats["cache_size"],
            "cache_ttl_seconds": cache_stats["cache_ttl"],
            "request_timeout_seconds": cache_stats["request_timeout"],
            "max_retries": cache_stats["max_retries"]
        }
    }


@router.post("/feedback")
async def submit_message_feedback(request: FeedbackRequest):
    """
    Record user feedback on bot responses for ML training.

    Thumbs up/down signals help identify:
    - Which responses work well
    - Which intents fail most often
    - Patterns to avoid or reinforce
    """
    from core.database import db

    try:
        # Get the message details
        message = db.execute_query("""
            SELECT id, user_message, bot_response, intent_detected, processing_time_ms
            FROM chat_logs
            WHERE id = %s AND session_id = %s
        """, (request.message_id, request.session_id))

        if not message or len(message) == 0:
            raise HTTPException(status_code=404, detail="Message not found")

        msg = message[0]

        # Store feedback with context (question_quality vs response_timing)
        result = db.execute_insert_returning("""
            INSERT INTO message_feedback
            (session_id, chat_log_id, feedback_type, rating_context, intent_type,
             bot_response, user_message, response_time_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            request.session_id,
            request.message_id,
            request.feedback_type,
            request.rating_context,
            request.intent_type or msg.get('intent_detected'),
            msg.get('bot_response'),
            msg.get('user_message'),
            msg.get('processing_time_ms')
        ))

        if result:
            logger.info(f"✓ Recorded {request.feedback_type} feedback for message {request.message_id}")
            return {"status": "success", "message": "Thank you for your feedback!"}
        else:
            return {"status": "duplicate", "message": "Feedback already recorded"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording feedback: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to record feedback")


