"""
SMS Lead Campaign Management API
Batch SMS outreach for clinical trial recruitment

Created: January 5, 2026
Purpose: Enable coordinators to manage SMS lead campaigns with pre-populated context
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field, validator
from typing import List, Dict, Optional, Any
import logging
import json
import uuid
import csv
import io
from datetime import datetime

from core.database import db
from core.services.sms_service import sms_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lead-campaigns", tags=["Lead Campaigns"])


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class CreateCampaignRequest(BaseModel):
    """Request to create a new lead campaign"""
    campaign_name: str = Field(..., min_length=1, max_length=255)
    trial_id: Optional[int] = Field(None, description="Clinical trial ID (optional)")
    trial_name: str = Field(..., min_length=1, max_length=255)
    condition: str = Field(..., min_length=1, max_length=200)
    location: str = Field(..., min_length=1, max_length=200)
    site_id: Optional[str] = Field(None, max_length=20)
    initial_message: str = Field(..., min_length=10, description="SMS template with {first_name}, {last_name}, {condition}, {location} variables")
    created_by: Optional[str] = None


class AddLeadRequest(BaseModel):
    """Request to add a single lead to a campaign"""
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone_number: str = Field(..., description="Phone number in any format")
    email: Optional[str] = Field(None, max_length=255)


class TriggerCampaignRequest(BaseModel):
    """Request to trigger SMS campaign"""
    test_mode: bool = Field(default=False, description="If true, only sends to first 5 leads")
    delay_seconds: int = Field(default=2, ge=1, le=10, description="Delay between SMS sends")


class CampaignResponse(BaseModel):
    """Campaign details response"""
    id: int
    campaign_name: str
    trial_id: Optional[int]
    trial_name: str
    condition: str
    location: str
    site_id: Optional[str]
    initial_message: str
    status: str
    total_leads: int
    sent_count: int
    responded_count: int
    interested_count: int
    not_interested_count: int
    error_count: int
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime


class LeadContactResponse(BaseModel):
    """Lead contact details response"""
    id: int
    campaign_id: int
    first_name: str
    last_name: str
    phone_number: str
    email: Optional[str]
    status: str
    session_id: Optional[str]
    response_type: Optional[str]
    eligibility_result: Optional[str]
    sent_at: Optional[datetime]
    responded_at: Optional[datetime]


# ============================================================================
# CAMPAIGN MANAGEMENT ENDPOINTS
# ============================================================================

@router.post("/campaigns", response_model=CampaignResponse)
async def create_campaign(request: CreateCampaignRequest):
    """Create a new SMS lead campaign"""

    logger.info(f"[LEAD-CAMPAIGN] Creating campaign: {request.campaign_name}")

    try:
        # Validate trial_id exists if provided
        if request.trial_id:
            trial_check = db.execute_query(
                "SELECT id FROM clinical_trials WHERE id = %s",
                (request.trial_id,)
            )
            if not trial_check:
                raise HTTPException(status_code=404, detail=f"Trial {request.trial_id} not found")

        # Insert campaign
        query = """
            INSERT INTO lead_campaigns
            (campaign_name, trial_id, trial_name, condition, location, site_id,
             initial_message, status, created_by, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING *
        """

        result = db.execute_insert_returning(query, (
            request.campaign_name,
            request.trial_id,
            request.trial_name,
            request.condition,
            request.location,
            request.site_id,
            request.initial_message,
            request.created_by
        ))

        logger.info(f"[LEAD-CAMPAIGN] ✅ Campaign created: ID={result['id']}")

        return CampaignResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error creating campaign: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create campaign: {str(e)}")


@router.get("/campaigns", response_model=List[CampaignResponse])
async def list_campaigns(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """List all campaigns with optional status filter"""

    try:
        query = """
            SELECT * FROM lead_campaigns
            WHERE 1=1
        """
        params = []

        if status:
            query += " AND status = %s"
            params.append(status)

        query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        campaigns = db.execute_query(query, tuple(params))

        return [CampaignResponse(**c) for c in campaigns]

    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error listing campaigns: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int):
    """Get campaign details with leads"""

    try:
        # Get campaign
        campaign_query = "SELECT * FROM lead_campaigns WHERE id = %s"
        campaign_result = db.execute_query(campaign_query, (campaign_id,))

        if not campaign_result:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        campaign = campaign_result[0]

        # Get leads
        leads_query = """
            SELECT * FROM lead_campaign_contacts
            WHERE campaign_id = %s
            ORDER BY created_at DESC
        """
        leads = db.execute_query(leads_query, (campaign_id,))

        return {
            "campaign": CampaignResponse(**campaign),
            "leads": [LeadContactResponse(**lead) for lead in leads]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error getting campaign: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int):
    """Delete a campaign and all its leads (CASCADE)"""

    logger.info(f"[LEAD-CAMPAIGN] Deleting campaign: {campaign_id}")

    try:
        result = db.execute_update(
            "DELETE FROM lead_campaigns WHERE id = %s",
            (campaign_id,)
        )

        if result == 0:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        logger.info(f"[LEAD-CAMPAIGN] ✅ Campaign deleted: {campaign_id}")

        return {"success": True, "campaign_id": campaign_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error deleting campaign: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# LEAD MANAGEMENT ENDPOINTS
# ============================================================================

@router.post("/campaigns/{campaign_id}/leads", response_model=LeadContactResponse)
async def add_lead(campaign_id: int, request: AddLeadRequest):
    """Add a single lead to a campaign"""

    logger.info(f"[LEAD-CAMPAIGN] Adding lead to campaign {campaign_id}: {request.first_name} {request.last_name}")

    try:
        # Verify campaign exists
        campaign_check = db.execute_query(
            "SELECT id FROM lead_campaigns WHERE id = %s",
            (campaign_id,)
        )
        if not campaign_check:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        # Normalize phone number
        normalized_phone = sms_service._normalize_phone_number(request.phone_number)

        # Insert lead
        query = """
            INSERT INTO lead_campaign_contacts
            (campaign_id, first_name, last_name, phone_number, email, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING *
        """

        result = db.execute_insert_returning(query, (
            campaign_id,
            request.first_name,
            request.last_name,
            normalized_phone,
            request.email
        ))

        # Update campaign total_leads count
        db.execute_update(
            "UPDATE lead_campaigns SET total_leads = total_leads + 1, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (campaign_id,)
        )

        logger.info(f"[LEAD-CAMPAIGN] ✅ Lead added: ID={result['id']}")

        return LeadContactResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error adding lead: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/{campaign_id}/upload-csv")
async def upload_csv(campaign_id: int, file: UploadFile = File(...)):
    """Upload CSV file with leads (columns: first_name, last_name, phone_number, email)"""

    logger.info(f"[LEAD-CAMPAIGN] Uploading CSV to campaign {campaign_id}")

    try:
        # Verify campaign exists
        campaign_check = db.execute_query(
            "SELECT id FROM lead_campaigns WHERE id = %s",
            (campaign_id,)
        )
        if not campaign_check:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        # Read and parse CSV
        contents = await file.read()
        csv_text = contents.decode('utf-8')
        csv_reader = csv.DictReader(io.StringIO(csv_text))

        leads_added = 0
        errors = []

        for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 (header is row 1)
            try:
                # Validate required fields
                first_name = row.get('first_name', '').strip()
                last_name = row.get('last_name', '').strip()
                phone = row.get('phone_number', '').strip()
                email = row.get('email', '').strip() or None

                if not first_name or not last_name or not phone:
                    errors.append(f"Row {row_num}: Missing required field (first_name, last_name, or phone_number)")
                    continue

                # Normalize phone
                try:
                    normalized_phone = sms_service._normalize_phone_number(phone)
                except Exception as phone_error:
                    errors.append(f"Row {row_num}: Invalid phone number '{phone}': {str(phone_error)}")
                    continue

                # Insert lead
                query = """
                    INSERT INTO lead_campaign_contacts
                    (campaign_id, first_name, last_name, phone_number, email, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """

                db.execute_update(query, (
                    campaign_id,
                    first_name,
                    last_name,
                    normalized_phone,
                    email
                ))

                leads_added += 1

            except Exception as row_error:
                errors.append(f"Row {row_num}: {str(row_error)}")

        # Update campaign total_leads
        if leads_added > 0:
            db.execute_update(
                "UPDATE lead_campaigns SET total_leads = total_leads + %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (leads_added, campaign_id)
            )

        logger.info(f"[LEAD-CAMPAIGN] ✅ CSV uploaded: {leads_added} leads added, {len(errors)} errors")

        return {
            "success": True,
            "leads_added": leads_added,
            "errors": errors,
            "error_count": len(errors)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error uploading CSV: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to upload CSV: {str(e)}")


# ============================================================================
# CAMPAIGN TRIGGERING
# ============================================================================

@router.post("/campaigns/{campaign_id}/trigger")
async def trigger_campaign(campaign_id: int, request: TriggerCampaignRequest):
    """Trigger SMS blast for campaign (all pending leads or test mode)"""

    logger.info(f"[LEAD-CAMPAIGN] Triggering campaign {campaign_id} (test_mode={request.test_mode})")

    try:
        # Get campaign details
        campaign_query = "SELECT * FROM lead_campaigns WHERE id = %s"
        campaign_result = db.execute_query(campaign_query, (campaign_id,))

        if not campaign_result:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        campaign = campaign_result[0]

        # Get pending leads
        leads_query = """
            SELECT * FROM lead_campaign_contacts
            WHERE campaign_id = %s AND status = 'pending'
            ORDER BY created_at ASC
        """

        if request.test_mode:
            leads_query += " LIMIT 5"

        leads = db.execute_query(leads_query, (campaign_id,))

        if not leads:
            return {
                "success": True,
                "message": "No pending leads to send to",
                "sent_count": 0
            }

        # Update campaign status to active
        db.execute_update(
            "UPDATE lead_campaigns SET status = 'active', started_at = COALESCE(started_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (campaign_id,)
        )

        sent_count = 0
        error_count = 0

        # Send SMS to each lead
        for lead in leads:
            try:
                # Create session with pre-populated context
                session_id = await _create_lead_session(campaign, lead)

                # Personalize message with all template variables
                message = campaign['initial_message']
                message = message.replace('{first_name}', lead['first_name'])
                message = message.replace('{last_name}', lead['last_name'])
                message = message.replace('{condition}', campaign['condition'])
                message = message.replace('{location}', campaign['location'])

                # Send SMS
                message_sid = await sms_service.send_sms(
                    to_phone=lead['phone_number'],
                    message=message,
                    session_id=session_id,
                    metadata={
                        "campaign_id": campaign_id,
                        "lead_id": lead['id'],
                        "message_type": "lead_campaign_initiation"
                    }
                )

                if message_sid:
                    # Update lead status
                    db.execute_update("""
                        UPDATE lead_campaign_contacts
                        SET status = 'sent',
                            session_id = %s,
                            initial_message_sid = %s,
                            sent_at = CURRENT_TIMESTAMP,
                            last_sms_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (session_id, message_sid, lead['id']))

                    sent_count += 1
                    logger.info(f"[LEAD-CAMPAIGN]    ✅ SMS sent to {lead['first_name']} {lead['last_name']} ({lead['phone_number']})")

                    # Small delay between sends
                    if request.delay_seconds > 0:
                        import asyncio
                        await asyncio.sleep(request.delay_seconds)

                else:
                    # SMS send failed
                    db.execute_update("""
                        UPDATE lead_campaign_contacts
                        SET status = 'error',
                            error_message = 'Failed to send SMS',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (lead['id'],))

                    error_count += 1

            except Exception as lead_error:
                logger.error(f"[LEAD-CAMPAIGN]    ❌ Error sending to lead {lead['id']}: {lead_error}")

                db.execute_update("""
                    UPDATE lead_campaign_contacts
                    SET status = 'error',
                        error_message = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (str(lead_error), lead['id']))

                error_count += 1

        logger.info(f"[LEAD-CAMPAIGN] ✅ Campaign triggered: {sent_count} sent, {error_count} errors")

        return {
            "success": True,
            "sent_count": sent_count,
            "error_count": error_count,
            "test_mode": request.test_mode
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error triggering campaign: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# SESSION CREATION WITH PRE-POPULATED CONTEXT
# ============================================================================

async def _create_lead_session(campaign: dict, lead: dict) -> str:
    """
    Create conversation session with pre-populated context for lead outreach

    This session:
    - Has contact info pre-filled (skips contact collection)
    - Has trial/condition/location pre-filled (skips trial search)
    - Ready to start prescreening when user expresses interest
    - Phone stored in user_id field for session continuity
    """

    session_id = f"lead_{uuid.uuid4().hex[:16]}"

    logger.info(f"[LEAD-CAMPAIGN]    Creating session {session_id} for lead {lead['id']}")

    # Build pre-populated context data
    context_data = {
        "campaign_id": campaign['id'],
        "lead_id": lead['id'],
        "channel": "sms",

        # Pre-fill trial search data (SKIPS trial search)
        "focus_condition": campaign['condition'],
        "focus_location": campaign['location'],
        "trial_id": campaign.get('trial_id'),
        "trial_name": campaign['trial_name'],

        # Pre-fill contact data (SKIPS contact collection)
        "contact_partial_data": {
            "first_name": lead['first_name'],
            "last_name": lead['last_name'],
            "phone_number": lead['phone_number'],
            "email": lead.get('email')
        },
        "contact_collection_state": "contact_complete",
        "contact_collection_initiated": True,

        # State markers
        "lead_campaign": True,
        "awaiting_prescreening_interest": True,

        "lead_metadata": {
            "campaign_name": campaign['campaign_name'],
            "initiated_at": datetime.utcnow().isoformat()
        }
    }

    # Create conversation_context record
    # CRITICAL: Store phone in user_id for SMS webhook session lookup
    # CRITICAL: Store campaign_id and lead_id in dedicated columns (won't be overwritten)
    query = """
        INSERT INTO conversation_context
        (session_id, user_id, current_state, context_data,
         focus_condition, focus_location, campaign_id, lead_id,
         active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """

    db.execute_update(query, (
        session_id,
        lead['phone_number'],  # CRITICAL: phone in user_id for lookup
        'lead_campaign_initiated',
        json.dumps(context_data),
        campaign['condition'],
        campaign['location'],
        campaign['id'],  # CRITICAL: campaign_id in dedicated column
        lead['id']  # CRITICAL: lead_id in dedicated column
    ))

    logger.info(f"[LEAD-CAMPAIGN]    ✅ Session created with pre-populated context")

    return session_id


# ============================================================================
# ANALYTICS & STATUS
# ============================================================================

@router.get("/campaigns/{campaign_id}/analytics")
async def get_campaign_analytics(campaign_id: int):
    """Get detailed analytics for a campaign"""

    try:
        # Get campaign
        campaign_query = "SELECT * FROM lead_campaigns WHERE id = %s"
        campaign_result = db.execute_query(campaign_query, (campaign_id,))

        if not campaign_result:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        campaign = campaign_result[0]

        # Get status breakdown
        status_query = """
            SELECT status, COUNT(*) as count
            FROM lead_campaign_contacts
            WHERE campaign_id = %s
            GROUP BY status
        """
        status_breakdown = db.execute_query(status_query, (campaign_id,))

        # Get response type breakdown
        response_query = """
            SELECT response_type, COUNT(*) as count
            FROM lead_campaign_contacts
            WHERE campaign_id = %s AND response_type IS NOT NULL
            GROUP BY response_type
        """
        response_breakdown = db.execute_query(response_query, (campaign_id,))

        # Get eligibility breakdown
        eligibility_query = """
            SELECT eligibility_result, COUNT(*) as count
            FROM lead_campaign_contacts
            WHERE campaign_id = %s AND eligibility_result IS NOT NULL
            GROUP BY eligibility_result
        """
        eligibility_breakdown = db.execute_query(eligibility_query, (campaign_id,))

        # Calculate conversion rates
        total = campaign['total_leads']
        sent = campaign['sent_count']
        responded = campaign['responded_count']
        interested = campaign['interested_count']

        response_rate = (responded / sent * 100) if sent > 0 else 0
        interest_rate = (interested / responded * 100) if responded > 0 else 0
        overall_conversion = (interested / sent * 100) if sent > 0 else 0

        return {
            "campaign": CampaignResponse(**campaign),
            "status_breakdown": {item['status']: item['count'] for item in status_breakdown},
            "response_breakdown": {item['response_type']: item['count'] for item in response_breakdown},
            "eligibility_breakdown": {item['eligibility_result']: item['count'] for item in eligibility_breakdown},
            "conversion_metrics": {
                "total_leads": total,
                "sent_count": sent,
                "responded_count": responded,
                "interested_count": interested,
                "response_rate": round(response_rate, 1),
                "interest_rate": round(interest_rate, 1),
                "overall_conversion": round(overall_conversion, 1)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error getting analytics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaigns/{campaign_id}/leads/{lead_id}")
async def get_lead_details(campaign_id: int, lead_id: int):
    """Get detailed information about a specific lead including conversation history"""

    try:
        # Get lead
        lead_query = """
            SELECT * FROM lead_campaign_contacts
            WHERE id = %s AND campaign_id = %s
        """
        lead_result = db.execute_query(lead_query, (lead_id, campaign_id))

        if not lead_result:
            raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found in campaign {campaign_id}")

        lead = lead_result[0]

        # Get SMS history if session exists
        sms_history = []
        if lead['session_id']:
            sms_query = """
                SELECT direction, message_text, status, created_at
                FROM sms_conversations
                WHERE session_id = %s
                ORDER BY created_at ASC
            """
            sms_history = db.execute_query(sms_query, (lead['session_id'],))

        # Get prescreening results if exists
        prescreening_result = None
        if lead['prescreening_session_id']:
            prescreening_query = """
                SELECT ps.*,
                       (SELECT COUNT(*) FROM prescreening_answers WHERE session_id = ps.session_id) as answers_count
                FROM prescreening_sessions ps
                WHERE ps.id = %s
            """
            prescreening_data = db.execute_query(prescreening_query, (lead['prescreening_session_id'],))
            if prescreening_data:
                prescreening_result = prescreening_data[0]

        return {
            "lead": LeadContactResponse(**lead),
            "sms_history": sms_history,
            "sms_count": len(sms_history),
            "prescreening": prescreening_result
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error getting lead details: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================

@router.get("/campaigns/{campaign_id}/export")
async def export_campaign_results(campaign_id: int):
    """Export campaign results as CSV"""

    try:
        # Get campaign
        campaign_query = "SELECT * FROM lead_campaigns WHERE id = %s"
        campaign_result = db.execute_query(campaign_query, (campaign_id,))

        if not campaign_result:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        # Get all leads with details
        leads_query = """
            SELECT
                first_name, last_name, phone_number, email,
                status, response_type, eligibility_result,
                sent_at, responded_at
            FROM lead_campaign_contacts
            WHERE campaign_id = %s
            ORDER BY id ASC
        """
        leads = db.execute_query(leads_query, (campaign_id,))

        # Build CSV
        output = io.StringIO()
        if leads:
            writer = csv.DictWriter(output, fieldnames=leads[0].keys())
            writer.writeheader()
            writer.writerows(leads)

        csv_content = output.getvalue()

        from fastapi.responses import Response
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=campaign_{campaign_id}_results.csv"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error exporting results: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: int, updates: dict):
    """Update campaign fields (status, message, etc.)"""

    logger.info(f"[LEAD-CAMPAIGN] Updating campaign {campaign_id}: {list(updates.keys())}")

    try:
        # Validate campaign exists
        campaign_check = db.execute_query(
            "SELECT id FROM lead_campaigns WHERE id = %s",
            (campaign_id,)
        )
        if not campaign_check:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        # Build dynamic UPDATE query
        allowed_fields = ['campaign_name', 'initial_message', 'status', 'trial_name', 'condition', 'location', 'site_id']
        update_fields = []
        params = []

        for field, value in updates.items():
            if field in allowed_fields:
                update_fields.append(f"{field} = %s")
                params.append(value)

        if not update_fields:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        query = f"""
            UPDATE lead_campaigns
            SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
        """
        params.append(campaign_id)

        result = db.execute_insert_returning(query, tuple(params))

        logger.info(f"[LEAD-CAMPAIGN] ✅ Campaign updated")

        return CampaignResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LEAD-CAMPAIGN] ❌ Error updating campaign: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    """Health check for lead campaigns system"""
    return {
        "status": "healthy",
        "service": "lead_campaigns",
        "endpoints": [
            "POST /campaigns",
            "GET /campaigns",
            "GET /campaigns/{id}",
            "POST /campaigns/{id}/leads",
            "POST /campaigns/{id}/upload-csv",
            "POST /campaigns/{id}/trigger",
            "GET /campaigns/{id}/analytics"
        ]
    }
