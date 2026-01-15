"""
CRIO Session Sync API
Allows V3 Dashboard to store active CRIO session tokens in shared database
Backend chatbot services can then use these tokens for availability lookups
"""

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
import logging
import os

from core.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crio-session", tags=["CRIO Session Management"])


# =============================================================================
# Request/Response Models
# =============================================================================
class SessionSyncRequest(BaseModel):
    """Request to sync CRIO session tokens to database"""
    session_id: str
    csrf_token: str
    authenticated_by: Optional[str] = None  # Email of user who logged in
    expires_in_hours: int = 8  # Default CRIO session duration


class SessionSyncResponse(BaseModel):
    """Response from session sync"""
    success: bool
    message: str
    expires_at: Optional[str] = None


class SessionStatusResponse(BaseModel):
    """Response for session status check"""
    has_valid_session: bool
    expires_at: Optional[str] = None
    time_remaining_hours: Optional[float] = None
    authenticated_by: Optional[str] = None


# =============================================================================
# Security: Internal API Key Validation
# =============================================================================
INTERNAL_API_KEY = os.getenv('INTERNAL_API_KEY', 'dev-key-change-in-production')

def validate_internal_request(x_api_key: Optional[str] = Header(None)) -> None:
    """Validate that request is from internal service (V3 Dashboard)"""
    if x_api_key != INTERNAL_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid or missing API key"
        )


# =============================================================================
# Endpoints
# =============================================================================
@router.post("/sync", response_model=SessionSyncResponse)
async def sync_session_tokens(
    request: SessionSyncRequest,
    x_api_key: Optional[str] = Header(None)
):
    """
    Sync CRIO session tokens from V3 Dashboard to shared database

    Called by V3 Dashboard after successful authentication.
    Invalidates any previous active session (only one active at a time).

    Headers:
        X-API-Key: Internal API key for service-to-service auth

    Returns:
        Success status and expiration timestamp
    """

    # Validate internal request
    validate_internal_request(x_api_key)

    try:
        # Calculate expiration timestamp
        expires_at = datetime.utcnow() + timedelta(hours=request.expires_in_hours)

        # Invalidate any existing active sessions
        db.execute_update("""
            UPDATE crio_shared_session
            SET is_active = FALSE,
                invalidated_at = CURRENT_TIMESTAMP,
                invalidation_reason = 'New session created'
            WHERE is_active = TRUE
        """)

        logger.info("Invalidated previous active CRIO sessions")

        # Insert new session
        db.execute_update("""
            INSERT INTO crio_shared_session
            (session_id, csrf_token, authenticated_by, expires_at, is_active)
            VALUES (%s, %s, %s, %s, TRUE)
        """, (
            request.session_id,
            request.csrf_token,
            request.authenticated_by or 'unknown',
            expires_at
        ))

        logger.info(f"✅ Synced new CRIO session to database")
        logger.info(f"   Authenticated by: {request.authenticated_by}")
        logger.info(f"   Expires at: {expires_at.isoformat()}")

        return SessionSyncResponse(
            success=True,
            message="Session tokens synced successfully",
            expires_at=expires_at.isoformat()
        )

    except Exception as e:
        logger.error(f"❌ Failed to sync CRIO session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to sync session: {str(e)}"
        )


@router.get("/status", response_model=SessionStatusResponse)
async def get_session_status(
    x_api_key: Optional[str] = Header(None)
):
    """
    Check if there's a valid CRIO session available

    Used by both V3 Dashboard and Chatbot Backend to check session validity.

    Headers:
        X-API-Key: Internal API key for service-to-service auth

    Returns:
        Session validity status and time remaining
    """

    # Validate internal request
    validate_internal_request(x_api_key)

    try:
        # Query for active, non-expired session
        result = db.execute_query("""
            SELECT
                session_id,
                csrf_token,
                authenticated_by,
                expires_at,
                EXTRACT(EPOCH FROM (expires_at - NOW())) / 3600 as hours_remaining
            FROM crio_shared_session
            WHERE is_active = TRUE
              AND expires_at > NOW()
            ORDER BY authenticated_at DESC
            LIMIT 1
        """)

        if result and len(result) > 0:
            session = result[0]

            logger.info(f"✅ Valid CRIO session found")
            logger.info(f"   Hours remaining: {session['hours_remaining']:.1f}")

            return SessionStatusResponse(
                has_valid_session=True,
                expires_at=session['expires_at'].isoformat(),
                time_remaining_hours=round(session['hours_remaining'], 2),
                authenticated_by=session['authenticated_by']
            )
        else:
            logger.info("No valid CRIO session found")
            return SessionStatusResponse(
                has_valid_session=False
            )

    except Exception as e:
        logger.error(f"❌ Failed to check session status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to check session status: {str(e)}"
        )


@router.delete("/invalidate")
async def invalidate_session(
    x_api_key: Optional[str] = Header(None)
):
    """
    Manually invalidate the current active session

    Used when logging out or when tokens are known to be invalid.

    Headers:
        X-API-Key: Internal API key for service-to-service auth
    """

    # Validate internal request
    validate_internal_request(x_api_key)

    try:
        db.execute_update("""
            UPDATE crio_shared_session
            SET is_active = FALSE,
                invalidated_at = CURRENT_TIMESTAMP,
                invalidation_reason = 'Manual invalidation'
            WHERE is_active = TRUE
        """)

        logger.info("✅ Invalidated active CRIO session")

        return {"success": True, "message": "Session invalidated"}

    except Exception as e:
        logger.error(f"❌ Failed to invalidate session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to invalidate session: {str(e)}"
        )
