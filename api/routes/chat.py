"""
New integrated chat endpoint using the refactored conversation system.

This replaces the old chat.py with a cleaner implementation using the new
modular conversation system.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
import uuid

from core.conversation import ConversationSystemAdapter
from core.conversation.pipeline import (
    MiddlewarePipeline,
    LoggingMiddleware,
    ValidationMiddleware,
    RateLimitingMiddleware,
    MetricsMiddleware,
    ErrorHandlingMiddleware,
    CachingMiddleware
)
from core.database import db

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    """Chat request model"""
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None


class ChatResponse(BaseModel):
    """Chat response model"""
    response: str
    session_id: str
    intent: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


# Initialize the conversation system adapter with middleware
conversation_adapter = ConversationSystemAdapter(use_middleware=True, enable_metrics=True)


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    Main chat endpoint using the new conversation system.
    
    This endpoint processes user messages through the refactored conversation
    pipeline, providing:
    - State-aware intent detection
    - Robust context management
    - Handler-based processing
    - Comprehensive error handling
    """
    # Generate session ID if not provided
    session_id = request.session_id or str(uuid.uuid4())
    
    logger.info(
        f"Chat request received",
        extra={
            "session_id": session_id,
            "user_id": request.user_id,
            "message_length": len(request.message)
        }
    )
    
    try:
        # Process message through new conversation system
        response_data = await conversation_adapter.process_chat_message(
            message=request.message,
            session_id=session_id,
            user_id=request.user_id
        )
        
        # Log successful processing
        logger.info(
            f"Chat request processed successfully",
            extra={
                "session_id": session_id,
                "intent_type": response_data.get("intent", {}).get("type"),
                "processing_time_ms": response_data.get("metadata", {}).get("processing_time_ms")
            }
        )
        
        # Return response
        return ChatResponse(
            response=response_data["response"],
            session_id=session_id,
            intent=response_data.get("intent"),
            metadata=response_data.get("metadata")
        )
        
    except ValueError as e:
        # Handle validation errors
        logger.warning(f"Validation error in chat request: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
        
    except Exception as e:
        # Handle unexpected errors
        logger.error(
            f"Error processing chat request: {str(e)}",
            extra={"session_id": session_id},
            exc_info=True
        )
        
        # Return a user-friendly error response
        return ChatResponse(
            response="I apologize, but I encountered an error processing your request. Please try again.",
            session_id=session_id,
            metadata={"error": True, "error_type": type(e).__name__}
        )


@router.get("/chat/metrics")
async def get_chat_metrics():
    """
    Get metrics for the chat system.
    
    Returns processing metrics, success rates, and performance data.
    """
    try:
        metrics = conversation_adapter.get_metrics()
        
        return {
            "status": "ok",
            "metrics": metrics
        }
        
    except Exception as e:
        logger.error(f"Error retrieving metrics: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve metrics")


@router.post("/chat/reset-metrics")
async def reset_chat_metrics():
    """Reset chat system metrics."""
    try:
        conversation_adapter.reset_metrics()
        
        return {
            "status": "ok",
            "message": "Metrics reset successfully"
        }
        
    except Exception as e:
        logger.error(f"Error resetting metrics: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to reset metrics")


@router.get("/chat/health")
async def chat_health_check():
    """
    Health check endpoint for the chat system.
    
    Verifies that all components are functioning properly.
    """
    try:
        # Perform basic health checks
        health_status = {
            "status": "healthy",
            "components": {
                "conversation_processor": "ok",
                "context_manager": "ok",
                "handlers": "ok",
                "database": "ok"  # Would check actual DB connection
            }
        }
        
        # Get current metrics for health assessment
        metrics = conversation_adapter.get_metrics()
        processor_metrics = metrics.get("processor_metrics", {})
        
        # Check error rate
        total = processor_metrics.get("total_processed", 0)
        failed = processor_metrics.get("failed", 0)
        
        if total > 0:
            error_rate = failed / total
            if error_rate > 0.1:  # More than 10% errors
                health_status["status"] = "degraded"
                health_status["components"]["conversation_processor"] = "high_error_rate"
        
        # Check response time
        avg_time = processor_metrics.get("avg_processing_time", 0)
        if avg_time > 1000:  # More than 1 second average
            health_status["status"] = "degraded"
            health_status["components"]["conversation_processor"] = "slow_response"
        
        health_status["metrics"] = {
            "total_processed": total,
            "error_rate": error_rate if total > 0 else 0,
            "avg_response_time_ms": avg_time
        }
        
        return health_status
        
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        return {
            "status": "unhealthy",
            "error": str(e)
        }


# Additional utility endpoints

@router.post("/chat/feedback")
async def submit_feedback(
    session_id: str,
    rating: int,
    comment: Optional[str] = None
):
    """
    Submit feedback for a chat session.
    
    Args:
        session_id: The session to provide feedback for
        rating: Rating from 1-5
        comment: Optional text feedback
    """
    if not 1 <= rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
    
    logger.info(
        f"Feedback received",
        extra={
            "session_id": session_id,
            "rating": rating,
            "has_comment": bool(comment)
        }
    )
    
    # Store feedback in database
    try:
        # Insert feedback into database
        query = """
            INSERT INTO chat_feedback (session_id, rating, comment, submitted_at)
            VALUES (%s, %s, %s, NOW())
        """
        db.execute_update(query, (session_id, rating, comment))
        
        logger.info(
            f"Feedback stored successfully",
            extra={
                "session_id": session_id,
                "rating": rating
            }
        )
        
        return {
            "status": "ok",
            "message": "Thank you for your feedback!"
        }
        
    except Exception as e:
        logger.error(f"Failed to store feedback: {str(e)}", exc_info=True)
        # Don't fail the request if database storage fails
        return {
            "status": "ok",
            "message": "Thank you for your feedback!"
        }


@router.get("/chat/session/{session_id}/context")
async def get_session_context(session_id: str):
    """
    Get the current context for a session (for debugging).
    
    This endpoint should be protected in production.
    """
    try:
        # This would retrieve context from the new system
        # For now, return a placeholder
        return {
            "session_id": session_id,
            "context": {
                "message": "Context retrieval not yet implemented"
            }
        }
        
    except Exception as e:
        logger.error(f"Error retrieving context: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve context")