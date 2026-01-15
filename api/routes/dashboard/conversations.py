"""Conversation management endpoints for dashboard"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import logging
import re

from core.database import db

logger = logging.getLogger(__name__)

router = APIRouter()





@router.get("/conversations")
async def get_conversations(
    limit: int = Query(50, description="Number of conversations to return"),
    offset: int = Query(0, description="Offset for pagination"),
    search: Optional[str] = Query(None, description="Search term"),
    days: int = Query(60, description="Number of days to look back"),
    include_test_data: bool = Query(False, description="Include test/debug sessions"),
    session_type: Optional[str] = Query(None, description="Filter by session type: 'production', 'dev', or None for all")
):
    """Get list of recent conversations with filtering - OPTIMIZED VERSION"""

    # Define dev/test session prefixes for filtering
    DEV_SESSION_PREFIXES = ('dev_', 'test_', 'AUTO_TEST_', 'debug_', 'local_')

    try:
        # OPTIMIZED: Single query with JOINs to prevent N+1 queries
        # Note: Question counts are fetched separately to avoid subquery issues with GROUP BY
        query = """
            SELECT
                cl.session_id,
                cl.user_id,
                (MIN(cl.timestamp) AT TIME ZONE 'UTC') as started_at,
                (MAX(cl.timestamp) AT TIME ZONE 'UTC') as last_message_at,
                COUNT(DISTINCT cl.id) as message_count,
                COALESCE(MAX(cc.context_data->>'current_state'), 'unknown') as current_state,
                MAX(ps.id) as prescreening_id,
                MAX(ps.status) as prescreening_status,
                MAX(ps.condition) as prescreening_condition,
                MAX(ps.location) as prescreening_location,
                MAX(ps.trial_id) as prescreening_trial_id,
                (MAX(ps.started_at) AT TIME ZONE 'UTC') as prescreening_started_at,
                (MAX(ps.completed_at) AT TIME ZONE 'UTC') as prescreening_completed_at,
                CASE WHEN MAX(ps.status) = 'completed' THEN true ELSE false END as prescreening_eligible,
                MAX(ct.trial_name) as trial_name,
                MAX(ct.conditions) as trial_conditions,
                -- Contact information status
                MAX(pci.first_name) as contact_first_name,
                MAX(pci.last_name) as contact_last_name,
                MAX(pci.phone_number) as contact_phone,
                MAX(pci.email) as contact_email
            FROM chat_logs cl
            LEFT JOIN conversation_context cc ON cl.session_id = cc.session_id AND cc.active = true
            LEFT JOIN prescreening_sessions ps ON cl.session_id = ps.session_id
            LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
            LEFT JOIN patient_contact_info pci ON cl.session_id = pci.session_id
            WHERE cl.timestamp IS NOT NULL
                AND cl.timestamp::timestamp > NOW() - INTERVAL %s
                AND cl.session_id IS NOT NULL
        """

        params = [f"{days} days"]

        # Filter by session type (production vs dev/test)
        if session_type == 'production':
            # Exclude dev/test sessions
            for prefix in DEV_SESSION_PREFIXES:
                query += " AND cl.session_id NOT LIKE %s"
                params.append(f"{prefix}%")
        elif session_type == 'dev':
            # Only include dev/test sessions
            prefix_conditions = " OR ".join(["cl.session_id LIKE %s" for _ in DEV_SESSION_PREFIXES])
            query += f" AND ({prefix_conditions})"
            params.extend([f"{prefix}%" for prefix in DEV_SESSION_PREFIXES])

        # Add search filter if provided
        if search:
            query += " AND (cl.user_message ILIKE %s OR cl.bot_response ILIKE %s)"
            params.extend([f"%{search}%", f"%{search}%"])
        
        query += """
            GROUP BY cl.session_id, cl.user_id, cc.context_data
            ORDER BY MAX(cl.timestamp) DESC
            LIMIT %s OFFSET %s
        """
        
        params.extend([limit, offset])
        
        try:
            conversations = db.execute_query(query, params)
            if conversations is None:
                conversations = []
            
            # Filter out any None values
            conversations = [conv for conv in conversations if conv is not None]
        except Exception as e:
            logger.error(f"Error executing optimized conversations query: {str(e)}")
            logger.error(f"Query: {query}")
            logger.error(f"Params: {params}")
            conversations = []
        
        # Get total count for pagination - OPTIMIZED
        count_query = """
            SELECT COUNT(DISTINCT cl.session_id) as total
            FROM chat_logs cl
            WHERE cl.timestamp IS NOT NULL 
                AND cl.timestamp::timestamp > NOW() - INTERVAL %s
                AND cl.session_id IS NOT NULL
        """
        
        count_params = [f"{days} days"]
        
        if search:
            count_query += " AND (cl.user_message ILIKE %s OR cl.bot_response ILIKE %s)"
            count_params.extend([f"%{search}%", f"%{search}%"])
        
        try:
            total_result = db.execute_query(count_query, count_params)
            total_count = 0
            if total_result and len(total_result) > 0 and total_result[0]:
                total_count = total_result[0].get("total", 0) or 0
        except Exception as e:
            logger.error(f"Error executing count query: {str(e)}")
            logger.error(f"Count query: {count_query}")
            logger.error(f"Count params: {count_params}")
            total_count = 0
        
        # OPTIMIZED: Get all prescreening answers in a single query
        session_ids = [conv["session_id"] for conv in conversations if conv.get("session_id")]
        prescreening_answers_map = {}
        question_counts_map = {}  # Maps session_id -> {total_questions, answered_questions}

        if session_ids:
            try:
                # Get all prescreening answers for all sessions at once
                placeholders = ','.join(['%s'] * len(session_ids))
                answers_query = f"""
                    SELECT session_id, question_id, question_text, user_answer as answer_text,
                           parsed_value, created_at
                    FROM prescreening_answers
                    WHERE session_id IN ({placeholders})
                    ORDER BY session_id, created_at ASC
                """
                all_answers = db.execute_query(answers_query, session_ids)

                # Group answers by session_id
                if all_answers:
                    for answer in all_answers:
                        session_id = answer["session_id"]
                        if session_id not in prescreening_answers_map:
                            prescreening_answers_map[session_id] = []
                        prescreening_answers_map[session_id].append(answer)

            except Exception as e:
                logger.error(f"Error fetching prescreening answers: {str(e)}")

            # Get question counts (total required criteria per trial, answered per session)
            try:
                # Get trial_ids from conversations that have prescreening
                trial_ids = list(set([
                    conv["prescreening_trial_id"] for conv in conversations
                    if conv.get("prescreening_trial_id")
                ]))

                # Get total required questions per trial
                trial_question_counts = {}
                if trial_ids:
                    trial_placeholders = ','.join(['%s'] * len(trial_ids))
                    trial_counts_query = f"""
                        SELECT trial_id, COUNT(*) as total_questions
                        FROM trial_criteria
                        WHERE trial_id IN ({trial_placeholders}) AND is_required = true
                        GROUP BY trial_id
                    """
                    trial_counts = db.execute_query(trial_counts_query, trial_ids)
                    if trial_counts:
                        for tc in trial_counts:
                            trial_question_counts[tc["trial_id"]] = tc["total_questions"]

                # Count answered questions per session (from prescreening_answers_map)
                for session_id in session_ids:
                    answered = len(prescreening_answers_map.get(session_id, []))
                    # Find the trial_id for this session
                    conv_trial_id = next(
                        (c["prescreening_trial_id"] for c in conversations
                         if c["session_id"] == session_id and c.get("prescreening_trial_id")),
                        None
                    )
                    total = trial_question_counts.get(conv_trial_id, 0) if conv_trial_id else 0
                    question_counts_map[session_id] = {
                        "total_questions": total,
                        "answered_questions": answered
                    }

            except Exception as e:
                logger.error(f"Error fetching question counts: {str(e)}")
        
        # Format the response with enhanced data for new dashboard
        formatted_conversations = []
        for conv in conversations:
            # Add safe status determination
            try:
                status = _determine_conversation_status(conv)
            except Exception as e:
                logger.error(f"Error determining status for conversation {conv.get('session_id', 'unknown')}: {str(e)}")
                status = "inactive"
            
            # Build prescreening data from the JOIN results
            prescreening_data = None
            if conv.get("prescreening_id"):
                # Get question counts from the map we built earlier
                session_counts = question_counts_map.get(conv["session_id"], {})
                prescreening_data = {
                    "id": conv["prescreening_id"],
                    "status": conv["prescreening_status"],
                    "condition": conv["prescreening_condition"],
                    "location": conv["prescreening_location"],
                    "trial_id": conv["prescreening_trial_id"],
                    "started_at": conv["prescreening_started_at"],
                    "completed_at": conv["prescreening_completed_at"],
                    "eligible": conv["prescreening_eligible"],
                    "trial_name": conv["trial_name"],
                    "conditions": conv["trial_conditions"],
                    # Accurate question counts for progress calculation
                    "total_questions": session_counts.get("total_questions", 0),
                    "answered_questions": session_counts.get("answered_questions", 0)
                }
            
            # Get prescreening answers for this session
            prescreening_answers = prescreening_answers_map.get(conv["session_id"], [])
            
            # Determine contact status
            contact_status = _determine_contact_status({
                "first_name": conv.get("contact_first_name"),
                "last_name": conv.get("contact_last_name"),
                "phone_number": conv.get("contact_phone"),
                "email": conv.get("contact_email")
            })
            
            formatted_conversations.append({
                "session_id": conv["session_id"],
                "user_id": conv["user_id"],
                "started_at": conv["started_at"],
                "last_message_at": conv["last_message_at"],
                "message_count": conv["message_count"],
                "current_state": conv["current_state"],
                "focus": {
                    "condition": conv.get("prescreening_condition"),
                    "location": conv.get("prescreening_location"),
                    "trial_id": conv.get("prescreening_trial_id")
                },
                "prescreening": prescreening_data,
                "prescreening_answers": prescreening_answers,
                "context_data": None,
                "status": status,
                "contact_status": contact_status
            })
        
        return {
            "conversations": formatted_conversations,
            "total": total_count,
            "returned": len(formatted_conversations),
            "limit": limit,
            "offset": offset,
            "include_test_data": include_test_data
        }
        
    except Exception as e:
        logger.error(f"Error fetching conversations: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch conversations: {str(e)}")


@router.get("/conversations/{session_id}")
async def get_conversation_details(session_id: str):
    """Get full conversation details including all messages"""
    
    try:
        # Get conversation messages using the actual session_id
        messages = db.execute_query("""
            SELECT
                id,
                timestamp,
                user_message,
                bot_response,
                context_data,
                intent_detected
            FROM chat_logs
            WHERE session_id = %s
                AND timestamp IS NOT NULL
            ORDER BY timestamp ASC
        """, (session_id,))
        
        if not messages:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Get prescreening details if exists
        prescreening = db.execute_query("""
            SELECT 
                ps.*,
                ct.trial_name,
                ct.conditions,
                -- Map eligibility_result to display format
                CASE 
                    WHEN ps.eligibility_result = 'likely_eligible' THEN 'eligible'
                    WHEN ps.eligibility_result = 'potentially_eligible' THEN 'eligible'
                    WHEN ps.eligibility_result = 'likely_ineligible' THEN 'ineligible'
                    WHEN ps.eligibility_result = 'evaluated' THEN 'pending'
                    ELSE ps.eligibility_result
                END as eligible_status
            FROM prescreening_sessions ps
            LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
            WHERE ps.session_id = %s
            ORDER BY ps.started_at DESC
            LIMIT 1
        """, (session_id,))
        
        # Get prescreening answers with criteria details for ML rating
        answers = []
        if prescreening:
            answers = db.execute_query("""
                SELECT
                    pa.question_id as question_key,
                    pa.question_text,
                    pa.user_answer as answer_text,
                    pa.criterion_id,
                    tc.criterion_text,
                    tc.criterion_type,
                    tc.category,
                    pa.parsed_value as answer_value,
                    pa.auto_evaluated,
                    pa.created_at
                FROM prescreening_answers pa
                LEFT JOIN trial_criteria tc ON pa.criterion_id = tc.id
                WHERE pa.session_id = %s
                ORDER BY created_at ASC
            """, (session_id,))

            # Add eligibility status from detailed results if available
            if prescreening and prescreening[0].get('eligibility_result'):
                import json
                eligibility_data = prescreening[0]['eligibility_result']
                if isinstance(eligibility_data, str):
                    try:
                        eligibility_data = json.loads(eligibility_data)
                    except:
                        eligibility_data = {}

                # Match answers to detailed results by criterion_id
                detailed_results = eligibility_data.get('detailed_results', [])
                for answer in answers:
                    criterion_id = answer.get('criterion_id')
                    matching_result = next((r for r in detailed_results if r.get('criterion_id') == criterion_id), None)
                    if matching_result:
                        answer['eligible'] = matching_result.get('eligible')
                        answer['explanation'] = matching_result.get('explanation', '')
        
        # If no answers found, try to get answers for this session_id anyway
        if not answers:
            answers = db.execute_query("""
                SELECT 
                    question_id as question_key,
                    question_text,
                    user_answer as answer_text,
                    parsed_value as answer_value,
                    created_at
                FROM prescreening_answers
                WHERE session_id = %s
                ORDER BY created_at ASC
            """, (session_id,))
        
        # Get contact information if exists
        contact_info = db.execute_query("""
            SELECT 
                first_name,
                last_name,
                phone_number,
                email,
                eligibility_status,
                contact_preference,
                consent_timestamp,
                created_at
            FROM patient_contact_info
            WHERE session_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (session_id,))
        
        return {
            "session_id": session_id,
            "messages": messages,
            "prescreening": prescreening[0] if prescreening else None,
            "prescreening_answers": answers or [],
            "contact_info": contact_info[0] if contact_info else None,
            "total_messages": len(messages)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching conversation {session_id}: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to fetch conversation details")


@router.delete("/conversations/{session_id}")
async def delete_conversation(session_id: str):
    """Delete a conversation and all associated data."""
    try:
        # Check if conversation exists
        existing = db.execute_query("""
            SELECT session_id, user_id, MIN(timestamp) as started_at, COUNT(*) as message_count
            FROM chat_logs 
            WHERE session_id = %s
            GROUP BY session_id, user_id
        """, (session_id,))
        
        if not existing:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Delete in order due to foreign key constraints
        # 1. Delete prescreening answers
        db.execute_update("DELETE FROM prescreening_answers WHERE session_id = %s", (session_id,))
        
        # 2. Delete prescreening sessions
        db.execute_update("DELETE FROM prescreening_sessions WHERE session_id = %s", (session_id,))
        
        # 3. Delete patient contact info
        db.execute_update("DELETE FROM patient_contact_info WHERE session_id = %s", (session_id,))
        
        # 4. Delete conversation context
        db.execute_update("DELETE FROM conversation_context WHERE session_id = %s", (session_id,))
        
        # 5. Delete chat logs
        db.execute_update("DELETE FROM chat_logs WHERE session_id = %s", (session_id,))
        
        return {
            "message": "Conversation deleted successfully",
            "deleted_conversation": existing[0]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting conversation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting conversation: {str(e)}")


@router.get("/conversations/{session_id}/context")
async def get_conversation_context(session_id: str):
    """Get conversation context for debugging/export"""
    
    try:
        # Get conversation context
        context = db.execute_query("""
            SELECT 
                cc.*,
                ps.status as prescreening_status,
                ps.condition as prescreening_condition,
                ps.trial_id as prescreening_trial_id,
                ct.trial_name as prescreening_trial_name,
                ct.conditions as prescreening_trial_conditions
            FROM conversation_context cc
            LEFT JOIN prescreening_sessions ps ON cc.session_id = ps.session_id
            LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
            WHERE cc.session_id = %s AND cc.active = true
            ORDER BY cc.updated_at DESC
            LIMIT 1
        """, (session_id,))
        
        if not context:
            raise HTTPException(status_code=404, detail="Conversation context not found")
        
        # Get recent messages for context
        recent_messages = db.execute_query("""
            SELECT 
                timestamp,
                user_message,
                bot_response
            FROM chat_logs
            WHERE session_id = %s AND timestamp IS NOT NULL
            ORDER BY timestamp::timestamp DESC
            LIMIT 5
        """, (session_id,))
        
        context_data = context[0]
        
        # Format for easy debugging
        export_data = {
            "session_id": session_id,
            "context": {
                "state": context_data.get("context_data", {}).get("current_state", "unknown"),
                "focus_condition": context_data.get("focus_condition"),
                "focus_location": context_data.get("focus_location"),
                "focus_trial_id": context_data.get("focus_trial_id"),
                "mentioned_conditions": context_data.get("mentioned_conditions"),
                "mentioned_locations": context_data.get("mentioned_locations"),
                "mentioned_trials": context_data.get("mentioned_trials"),
                "full_context": context_data.get("context_data")
            },
            "prescreening": {
                "status": context_data.get("prescreening_status"),
                "condition": context_data.get("prescreening_condition"),
                "trial_id": context_data.get("prescreening_trial_id"),
                "trial_name": context_data.get("prescreening_trial_name"),
                "trial_conditions": context_data.get("prescreening_trial_conditions")
            },
            "recent_messages": recent_messages,
            "timestamps": {
                "created_at": context_data.get("created_at"),
                "updated_at": context_data.get("updated_at")
            }
        }
        
        return export_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching conversation context {session_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch conversation context")


@router.get("/analyze-criteria")
async def analyze_trial_criteria():
    """Analyze trial criteria for prescreening question generation"""
    try:
        # Get all trials with their required criteria
        trials_data = db.execute_query("""
            SELECT 
                ct.id as trial_id,
                ct.trial_name,
                ct.conditions,
                ti.site_location,
                COUNT(tc.id) as total_criteria,
                COUNT(CASE WHEN tc.is_required = true THEN 1 END) as required_criteria
            FROM clinical_trials ct
            LEFT JOIN trial_criteria tc ON ct.id = tc.trial_id
            LEFT JOIN trial_investigators ti ON ct.id = ti.trial_id
            GROUP BY ct.id, ct.trial_name, ct.conditions, ti.site_location
            ORDER BY ct.conditions
        """)
        
        # Get detailed criteria for each trial
        criteria_data = db.execute_query("""
            SELECT 
                tc.trial_id,
                tc.id as criteria_id,
                tc.criterion_type,
                tc.criterion_text,
                tc.category,
                tc.is_required,
                tc.parsed_json
            FROM trial_criteria tc
            WHERE tc.is_required = true
            ORDER BY tc.trial_id, tc.category, tc.id
        """)
        
        return {
            "trials": trials_data,
            "criteria": criteria_data,
            "analysis_timestamp": "2025-01-22",
            "status": "Multi-trial database analysis complete"
        }
    except Exception as e:
        logger.error(f"Error analyzing trial criteria: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def _determine_conversation_status(conv: Dict[str, Any]) -> str:
    """Determine the status of a conversation"""
    
    # Check if prescreening was completed
    if conv.get("prescreening_status") == "completed":
        return "prescreening_completed"
    elif conv.get("prescreening_status") == "in_progress":
        return "prescreening_active"
    
    # Check if conversation is still active (last message within 30 minutes)
    last_message = conv.get("last_message_at")
    if last_message is None:
        return "inactive"
    
    # Convert string to datetime if needed
    if isinstance(last_message, str):
        try:
            last_message = datetime.fromisoformat(last_message.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return "inactive"
    
    # Check if it's a datetime object and compare
    if hasattr(last_message, 'year'):  # Check if it's a datetime-like object
        try:
            # Use timezone-aware comparison
            from datetime import timezone
            now_utc = datetime.now(timezone.utc)

            # If last_message is naive (no timezone), assume UTC
            if last_message.tzinfo is None:
                from datetime import timezone as tz
                last_message = last_message.replace(tzinfo=tz.utc)

            time_diff = now_utc - last_message
            if time_diff < timedelta(minutes=30):
                return "active"
        except (TypeError, ValueError) as e:
            logger.warning(f"Error comparing timestamps: {e}")
            pass

    return "inactive"


def _determine_contact_status(contact_info: Dict[str, Any]) -> str:
    """Determine contact information collection status"""
    
    # Required fields for complete contact info
    required_fields = ['first_name', 'last_name', 'phone_number', 'email']
    
    # Check which fields are present and not empty
    present_fields = []
    for field in required_fields:
        value = contact_info.get(field)
        if value and str(value).strip():
            present_fields.append(field)
    
    # Determine status based on how many fields are collected
    if len(present_fields) == 0:
        return "not_collected"  # Red
    elif len(present_fields) == len(required_fields):
        return "collected"  # Green
    else:
        return "partial"  # Yellow