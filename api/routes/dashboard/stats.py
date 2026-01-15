"""Dashboard statistics endpoints with standardized time windows and accurate metrics"""
from fastapi import APIRouter, HTTPException
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from core.database import db

logger = logging.getLogger(__name__)

router = APIRouter()


def get_time_windows() -> Dict[str, str]:
    """Get standardized time window SQL expressions"""
    return {
        'last_24h': "NOW() - INTERVAL '24 hours'",
        'this_week': "DATE_TRUNC('week', CURRENT_DATE)",
        'last_7_days': "NOW() - INTERVAL '7 days'",
        'last_30_days': "NOW() - INTERVAL '30 days'",
        'last_90_days': "NOW() - INTERVAL '90 days'"
    }


def get_safe_metric_result(query_result: list, default_value: Any = 0) -> Any:
    """Safely extract metric from query result"""
    if query_result and len(query_result) > 0 and query_result[0]:
        return query_result[0]
    return {"count": default_value} if isinstance(default_value, (int, float)) else default_value


@router.get("/stats")
async def get_dashboard_stats():
    """Get comprehensive dashboard statistics with accurate calculations"""
    try:
        # Total conversations (all time)
        total_conversations_result = db.execute_query("""
            SELECT COUNT(DISTINCT session_id) as count
            FROM chat_logs
            WHERE session_id IS NOT NULL
        """)
        total_conversations = total_conversations_result[0]["count"] if total_conversations_result else 0
        
        # Active sessions (last 24 hours) - properly filtered
        active_sessions_result = db.execute_query("""
            SELECT COUNT(DISTINCT cl.session_id) as count
            FROM chat_logs cl
            WHERE cl.timestamp IS NOT NULL 
            AND cl.timestamp::timestamp >= NOW() - INTERVAL '24 hours'
            AND cl.session_id IS NOT NULL
        """)
        active_sessions = active_sessions_result[0]["count"] if active_sessions_result else 0
        
        # All-time prescreening metrics for accurate completion rate
        all_time_prescreenings_result = db.execute_query("""
            SELECT 
                COUNT(*) as total_started,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as total_completed
            FROM prescreening_sessions
        """)
        
        if all_time_prescreenings_result and all_time_prescreenings_result[0]:
            prescreening_data = all_time_prescreenings_result[0]
            prescreenings_started = prescreening_data["total_started"]
            prescreenings_completed = prescreening_data["total_completed"] 
            completion_rate = round((prescreenings_completed / prescreenings_started * 100), 1) if prescreenings_started > 0 else 0.0
        else:
            prescreenings_started = 0
            prescreenings_completed = 0
            completion_rate = 0.0
        
        # Weekly prescreening metrics for context
        weekly_prescreenings_result = db.execute_query("""
            SELECT 
                COUNT(*) as started_weekly,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed_weekly
            FROM prescreening_sessions
            WHERE started_at IS NOT NULL 
            AND started_at >= NOW() - INTERVAL '7 days'
        """)
        
        if weekly_prescreenings_result and weekly_prescreenings_result[0]:
            weekly_data = weekly_prescreenings_result[0]
            prescreenings_started_weekly = weekly_data["started_weekly"]
            prescreenings_completed_weekly = weekly_data["completed_weekly"]
        else:
            prescreenings_started_weekly = 0
            prescreenings_completed_weekly = 0
        
        return {
            # Core metrics
            "total_conversations": total_conversations,
            "active_sessions": active_sessions,
            
            # All-time prescreening metrics
            "prescreenings_started": prescreenings_started,
            "prescreenings_completed": prescreenings_completed,
            "completion_rate": f"{completion_rate}%",
            
            # Weekly metrics for trend analysis
            "prescreenings_started_weekly": prescreenings_started_weekly,
            "prescreenings_completed_weekly": prescreenings_completed_weekly,
            
            # Metadata
            "last_updated": datetime.utcnow().isoformat(),
            "metrics_version": "2.0"
        }
        
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch statistics: {str(e)}")


@router.get("/prescreening")
async def get_prescreening_sessions():
    """Get recent prescreening sessions with details"""
    try:
        sessions = db.execute_query("""
            SELECT 
                ps.session_id,
                ps.user_id,
                ps.condition,
                ps.location,
                ps.status,
                ps.started_at,
                ps.completed_at,
                ct.trial_name,
                ct.conditions as trial_condition,
                CASE 
                    WHEN ps.eligibility_result = 'likely_eligible' THEN 'ELIGIBLE'
                    WHEN ps.eligibility_result = 'potentially_eligible' THEN 'ELIGIBLE'
                    WHEN ps.eligibility_result = 'likely_not_eligible' THEN 'NOT ELIGIBLE'
                    WHEN ps.eligibility_result = 'not_eligible' THEN 'NOT ELIGIBLE'
                    WHEN ps.status = 'completed' AND ps.eligibility_result IS NULL THEN 'UNKNOWN'
                    ELSE 'UNKNOWN'
                END as eligible
            FROM prescreening_sessions ps
            LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
            ORDER BY ps.started_at DESC
            LIMIT 100
        """)
        
        return sessions
        
    except Exception as e:
        logger.error(f"Error fetching prescreening sessions: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch prescreening sessions")


@router.get("/analytics")
async def get_analytics_data():
    """Get data for analytics charts"""
    try:
        # Conditions by interest
        conditions_data = db.execute_query("""
            SELECT 
                condition,
                COUNT(*) as count
            FROM prescreening_sessions
            WHERE condition IS NOT NULL
            GROUP BY condition
            ORDER BY count DESC
            LIMIT 10
        """)
        
        # Prescreening funnel - standardized with proper NULL handling
        funnel_data_result = db.execute_query("""
            SELECT 
                COUNT(DISTINCT cl.session_id) as total_visitors,
                COUNT(DISTINCT ps.session_id) as started_prescreening,
                COUNT(DISTINCT CASE WHEN ps.status = 'completed' THEN ps.session_id END) as completed,
                COUNT(DISTINCT CASE WHEN ps.status = 'completed' THEN ps.session_id END) as eligible
            FROM chat_logs cl
            LEFT JOIN prescreening_sessions ps ON cl.session_id = ps.session_id
            WHERE cl.timestamp IS NOT NULL 
            AND cl.timestamp::timestamp >= NOW() - INTERVAL '30 days'
        """)
        funnel_data = get_safe_metric_result(funnel_data_result, 
                                           {"total_visitors": 0, "started_prescreening": 0, "completed": 0, "eligible": 0})
        
        return {
            "conditions": conditions_data,
            "funnel": funnel_data
        }
        
    except Exception as e:
        logger.error(f"Error fetching analytics data: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch analytics data")


@router.get("/stats/detailed")
async def get_detailed_stats():
    """Get detailed statistics with time-based breakdowns"""
    try:
        windows = get_time_windows()
        
        # Conversation trends by time period
        conversation_trends_result = db.execute_query(f"""
            SELECT 
                COUNT(DISTINCT CASE WHEN cl.timestamp::timestamp >= {windows['last_24h']} THEN cl.session_id END) as last_24h,
                COUNT(DISTINCT CASE WHEN cl.timestamp::timestamp >= {windows['last_7_days']} THEN cl.session_id END) as last_7d,
                COUNT(DISTINCT CASE WHEN cl.timestamp::timestamp >= {windows['last_30_days']} THEN cl.session_id END) as last_30d,
                COUNT(DISTINCT cl.session_id) as all_time
            FROM chat_logs cl
            WHERE cl.timestamp IS NOT NULL
            AND cl.session_id IS NOT NULL
        """)
        conversation_trends = get_safe_metric_result(conversation_trends_result, 
                                                   {"last_24h": 0, "last_7d": 0, "last_30d": 0, "all_time": 0})
        
        # Prescreening performance by time period
        prescreening_trends_result = db.execute_query(f"""
            SELECT 
                COUNT(CASE WHEN started_at >= {windows['last_24h']} THEN 1 END) as started_24h,
                COUNT(CASE WHEN started_at >= {windows['last_7_days']} THEN 1 END) as started_7d,
                COUNT(CASE WHEN started_at >= {windows['last_30_days']} THEN 1 END) as started_30d,
                COUNT(*) as started_all_time,
                
                COUNT(CASE WHEN completed_at >= {windows['last_24h']} THEN 1 END) as completed_24h,
                COUNT(CASE WHEN completed_at >= {windows['last_7_days']} THEN 1 END) as completed_7d,
                COUNT(CASE WHEN completed_at >= {windows['last_30_days']} THEN 1 END) as completed_30d,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed_all_time
            FROM prescreening_sessions
            WHERE started_at IS NOT NULL
        """)
        prescreening_trends = get_safe_metric_result(prescreening_trends_result, 
                                                   {"started_24h": 0, "started_7d": 0, "started_30d": 0, "started_all_time": 0,
                                                    "completed_24h": 0, "completed_7d": 0, "completed_30d": 0, "completed_all_time": 0})
        
        # Calculate completion rates for each period
        def calculate_completion_rate(started: int, completed: int) -> float:
            return round((completed / started * 100), 1) if started > 0 else 0.0
        
        completion_rates = {
            "rate_24h": calculate_completion_rate(prescreening_trends["started_24h"], prescreening_trends["completed_24h"]),
            "rate_7d": calculate_completion_rate(prescreening_trends["started_7d"], prescreening_trends["completed_7d"]),
            "rate_30d": calculate_completion_rate(prescreening_trends["started_30d"], prescreening_trends["completed_30d"]),
            "rate_all_time": calculate_completion_rate(prescreening_trends["started_all_time"], prescreening_trends["completed_all_time"])
        }
        
        # User engagement patterns
        engagement_patterns_result = db.execute_query("""
            SELECT 
                AVG(message_count) as avg_messages_per_session,
                MAX(message_count) as max_messages_per_session,
                COUNT(DISTINCT user_id) as unique_users,
                COUNT(DISTINCT session_id) as total_sessions
            FROM (
                SELECT 
                    session_id,
                    user_id,
                    COUNT(*) as message_count
                FROM chat_logs
                WHERE timestamp IS NOT NULL
                GROUP BY session_id, user_id
            ) session_stats
        """)
        engagement_patterns = get_safe_metric_result(engagement_patterns_result, 
                                                   {"avg_messages_per_session": 0, "max_messages_per_session": 0, 
                                                    "unique_users": 0, "total_sessions": 0})
        
        return {
            "conversation_trends": conversation_trends,
            "prescreening_trends": prescreening_trends,
            "completion_rates": completion_rates,
            "engagement_patterns": engagement_patterns,
            "last_updated": datetime.utcnow().isoformat(),
            "time_windows_used": windows
        }
        
    except Exception as e:
        logger.error(f"Error fetching detailed stats: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to fetch detailed statistics")


@router.get("/protocol-field-completion")
async def get_protocol_field_completion():
    """Get protocol field completion percentages for dashboard display"""
    try:
        # Get comprehensive field completion statistics
        completion_stats_result = db.execute_query("""
            WITH field_completion AS (
                SELECT 
                    ct.id,
                    ct.protocol_number,
                    -- Clinical trials table fields
                    CASE WHEN ct.sponsor IS NOT NULL AND ct.sponsor != '' AND ct.sponsor != 'Unknown Sponsor' THEN 1 ELSE 0 END as has_sponsor,
                    CASE WHEN ct.enrollment_target IS NOT NULL THEN 1 ELSE 0 END as has_enrollment,
                    CASE WHEN ct.medications IS NOT NULL AND ct.medications != '' THEN 1 ELSE 0 END as has_medications,
                    CASE WHEN ct.nct_number IS NOT NULL AND ct.nct_number != '' THEN 1 ELSE 0 END as has_nct,
                    -- Protocol metadata fields
                    CASE WHEN pm.primary_objectives IS NOT NULL AND pm.primary_objectives != '' THEN 1 ELSE 0 END as has_primary_obj,
                    CASE WHEN pm.secondary_objectives IS NOT NULL AND pm.secondary_objectives != '' THEN 1 ELSE 0 END as has_secondary_obj,
                    CASE WHEN pm.target_population IS NOT NULL AND pm.target_population != '' THEN 1 ELSE 0 END as has_target_pop,
                    CASE WHEN pm.protocol_summary IS NOT NULL AND pm.protocol_summary != '' AND LENGTH(pm.protocol_summary) >= 200 THEN 1 ELSE 0 END as has_summary,
                    -- Protocol documents availability
                    CASE WHEN COUNT(pd.id) > 0 THEN 1 ELSE 0 END as has_documents,
                    -- Trial criteria availability
                    CASE WHEN COUNT(tc.id) > 0 THEN 1 ELSE 0 END as has_criteria
                FROM clinical_trials ct 
                LEFT JOIN protocol_metadata pm ON ct.id = pm.trial_id
                LEFT JOIN protocol_documents pd ON ct.id = pd.trial_id
                LEFT JOIN trial_criteria tc ON ct.id = tc.trial_id
                GROUP BY ct.id, ct.protocol_number, ct.sponsor, ct.enrollment_target, 
                         ct.medications, ct.nct_number, pm.primary_objectives, 
                         pm.secondary_objectives, pm.target_population, pm.protocol_summary
            )
            SELECT 
                COUNT(*) as total_protocols,
                
                -- Field completion percentages
                ROUND(AVG(has_sponsor) * 100, 1) as sponsor_completion_pct,
                ROUND(AVG(has_enrollment) * 100, 1) as enrollment_completion_pct,
                ROUND(AVG(has_medications) * 100, 1) as medications_completion_pct,
                ROUND(AVG(has_nct) * 100, 1) as nct_completion_pct,
                ROUND(AVG(has_primary_obj) * 100, 1) as primary_obj_completion_pct,
                ROUND(AVG(has_secondary_obj) * 100, 1) as secondary_obj_completion_pct,
                ROUND(AVG(has_target_pop) * 100, 1) as target_pop_completion_pct,
                ROUND(AVG(has_summary) * 100, 1) as summary_completion_pct,
                ROUND(AVG(has_documents) * 100, 1) as documents_completion_pct,
                ROUND(AVG(has_criteria) * 100, 1) as criteria_completion_pct,
                
                -- Overall completion score (average of all fields)
                ROUND((AVG(has_sponsor) + AVG(has_enrollment) + AVG(has_medications) + 
                       AVG(has_primary_obj) + AVG(has_secondary_obj) + AVG(has_target_pop) + 
                       AVG(has_summary) + AVG(has_documents) + AVG(has_criteria)) / 9 * 100, 1) as overall_completion_pct,
                
                -- Count of missing fields
                SUM(CASE WHEN has_sponsor = 0 THEN 1 ELSE 0 END) as missing_sponsors,
                SUM(CASE WHEN has_enrollment = 0 THEN 1 ELSE 0 END) as missing_enrollment,
                SUM(CASE WHEN has_medications = 0 THEN 1 ELSE 0 END) as missing_medications,
                SUM(CASE WHEN has_nct = 0 THEN 1 ELSE 0 END) as missing_nct,
                SUM(CASE WHEN has_primary_obj = 0 THEN 1 ELSE 0 END) as missing_primary_obj,
                SUM(CASE WHEN has_secondary_obj = 0 THEN 1 ELSE 0 END) as missing_secondary_obj,
                SUM(CASE WHEN has_target_pop = 0 THEN 1 ELSE 0 END) as missing_target_pop,
                SUM(CASE WHEN has_summary = 0 THEN 1 ELSE 0 END) as missing_summary,
                SUM(CASE WHEN has_documents = 0 THEN 1 ELSE 0 END) as missing_documents,
                SUM(CASE WHEN has_criteria = 0 THEN 1 ELSE 0 END) as missing_criteria
            FROM field_completion
        """)
        
        completion_stats = get_safe_metric_result(completion_stats_result, {
            "total_protocols": 0,
            "sponsor_completion_pct": 0,
            "enrollment_completion_pct": 0,
            "medications_completion_pct": 0,
            "nct_completion_pct": 0,
            "primary_obj_completion_pct": 0,
            "secondary_obj_completion_pct": 0,
            "target_pop_completion_pct": 0,
            "summary_completion_pct": 0,
            "documents_completion_pct": 0,
            "criteria_completion_pct": 0,
            "overall_completion_pct": 0
        })
        
        return {
            "field_completion": completion_stats,
            "last_updated": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error fetching protocol field completion stats: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to fetch protocol field completion statistics")