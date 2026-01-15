"""Email report generation endpoints"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Optional
import logging
from datetime import datetime

from core.services.email_service import EmailService
from core.database import db

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize email service (will be created next)
email_service = EmailService()


class ConversationExportRequest(BaseModel):
    session_id: str
    email: EmailStr
    format: str = "email"  # email, pdf, csv


class DailySummaryRequest(BaseModel):
    email: EmailStr
    date: Optional[str] = None  # YYYY-MM-DD format


@router.post("/export/conversation")
async def export_conversation(
    request: ConversationExportRequest,
    background_tasks: BackgroundTasks
):
    """Export a conversation and send via email"""
    
    try:
        # Verify conversation exists
        conversation = db.execute_query("""
            SELECT COUNT(*) as count FROM chat_logs WHERE session_id = %s
        """, (request.session_id,))
        
        if not conversation or conversation[0]["count"] == 0:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Add email task to background
        if request.format == "email":
            background_tasks.add_task(
                email_service.send_conversation_report,
                session_id=request.session_id,
                recipient=request.email
            )
            
            return {
                "status": "queued",
                "message": f"Conversation report will be sent to {request.email}",
                "session_id": request.session_id
            }
        else:
            raise HTTPException(status_code=400, detail=f"Format {request.format} not supported yet")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting conversation: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to export conversation")


@router.post("/export/daily-summary")
async def export_daily_summary(
    request: DailySummaryRequest,
    background_tasks: BackgroundTasks
):
    """Generate and send daily summary report"""
    
    try:
        # Use today if no date provided
        report_date = request.date or datetime.utcnow().strftime("%Y-%m-%d")
        
        # Add email task to background
        background_tasks.add_task(
            email_service.send_daily_summary,
            recipient=request.email,
            date=report_date
        )
        
        return {
            "status": "queued",
            "message": f"Daily summary for {report_date} will be sent to {request.email}"
        }
        
    except Exception as e:
        logger.error(f"Error generating daily summary: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to generate daily summary")


@router.post("/alerts/eligible-user")
async def send_eligibility_alert(
    session_id: str,
    background_tasks: BackgroundTasks
):
    """Send alert when a user is found eligible for a trial"""
    
    try:
        # Get prescreening details
        prescreening = db.execute_query("""
            SELECT 
                ps.*,
                ct.trial_name,
                ct.conditions,
                ti.investigator_name,
                ti.contact_email
            FROM prescreening_sessions ps
            JOIN clinical_trials ct ON ps.trial_id = ct.id
            LEFT JOIN trial_investigators ti ON ct.id = ti.trial_id
            WHERE ps.session_id = %s AND ps.status = 'completed'
            ORDER BY ps.completed_at DESC
            LIMIT 1
        """, (session_id,))
        
        if not prescreening:
            raise HTTPException(status_code=404, detail="No completed prescreening found")
        
        ps_data = prescreening[0]
        
        # Send to investigator if email available
        if ps_data.get("contact_email"):
            background_tasks.add_task(
                email_service.send_eligibility_notification,
                session_id=session_id,
                recipient=ps_data["contact_email"],
                trial_name=ps_data["trial_name"],
                condition=ps_data["conditions"]
            )
        
        # Also send to dashboard admin if configured
        if email_service.dashboard_email:
            background_tasks.add_task(
                email_service.send_eligibility_notification,
                session_id=session_id,
                recipient=email_service.dashboard_email,
                trial_name=ps_data["trial_name"],
                condition=ps_data["conditions"]
            )
        
        return {
            "status": "queued",
            "message": "Eligibility notifications queued",
            "session_id": session_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending eligibility alert: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to send eligibility alert")