"""
Business Intelligence Analytics API
Real-time metrics for conversation optimization and business decisions

Created: January 6, 2026
Purpose: Provide actionable insights for improving conversion rates and user experience
"""

from fastapi import APIRouter, Query
from typing import Optional
import logging
from datetime import datetime, timedelta

from core.database import db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/analytics/bi/conversion-funnel")
async def get_conversion_funnel(days: int = Query(30, ge=1, le=365)):
    """
    Complete conversion funnel from initial chat to booking
    Shows drop-off at each stage
    """
    try:
        funnel = db.execute_query(f"""
            SELECT
                COUNT(DISTINCT cl.session_id) as total_conversations,
                COUNT(DISTINCT CASE WHEN ps.id IS NOT NULL THEN cl.session_id END) as started_prescreening,
                COUNT(DISTINCT CASE WHEN ps.status = 'completed' THEN cl.session_id END) as completed_prescreening,
                COUNT(DISTINCT CASE WHEN pci.id IS NOT NULL THEN cl.session_id END) as provided_contact,
                COUNT(DISTINCT CASE WHEN pci.phone_number IS NOT NULL THEN cl.session_id END) as provided_phone
            FROM chat_logs cl
            LEFT JOIN prescreening_sessions ps ON cl.session_id = ps.session_id
            LEFT JOIN patient_contact_info pci ON cl.session_id = pci.session_id
            WHERE cl.timestamp >= NOW() - INTERVAL '{days} days'
        """)

        if not funnel:
            return {"funnel": [], "conversion_rates": {}}

        f = funnel[0]
        total = f['total_conversations']

        # Calculate conversion rates
        rates = {
            "chat_to_prescreening": round((f['started_prescreening'] / total * 100), 1) if total > 0 else 0,
            "prescreening_start_to_complete": round((f['completed_prescreening'] / f['started_prescreening'] * 100), 1) if f['started_prescreening'] > 0 else 0,
            "complete_to_contact": round((f['provided_contact'] / f['completed_prescreening'] * 100), 1) if f['completed_prescreening'] > 0 else 0,
            "overall_conversion": round((f['provided_contact'] / total * 100), 1) if total > 0 else 0
        }

        # Calculate drop-offs
        dropoffs = {
            "before_prescreening": total - f['started_prescreening'],
            "during_prescreening": f['started_prescreening'] - f['completed_prescreening'],
            "after_prescreening": f['completed_prescreening'] - f['provided_contact']
        }

        return {
            "funnel": [
                {"stage": "Total Conversations", "count": total, "percentage": 100.0},
                {"stage": "Started Prescreening", "count": f['started_prescreening'], "percentage": rates["chat_to_prescreening"]},
                {"stage": "Completed Prescreening", "count": f['completed_prescreening'], "percentage": round((f['completed_prescreening'] / total * 100), 1) if total > 0 else 0},
                {"stage": "Provided Contact Info", "count": f['provided_contact'], "percentage": rates["overall_conversion"]}
            ],
            "conversion_rates": rates,
            "dropoffs": dropoffs,
            "period_days": days
        }
    except Exception as e:
        logger.error(f"Error in conversion funnel: {e}", exc_info=True)
        return {"error": str(e)}


@router.get("/analytics/bi/condition-performance")
async def get_condition_performance(days: int = Query(30, ge=1, le=365), min_sessions: int = Query(3, ge=1)):
    """
    Performance matrix by medical condition
    Shows completion rates, contact rates, and engagement
    """
    try:
        performance = db.execute_query(f"""
            SELECT
                LOWER(TRIM(ps.condition)) as condition,
                COUNT(DISTINCT ps.id) as sessions_started,
                COUNT(CASE WHEN ps.status = 'completed' THEN 1 END) as sessions_completed,
                COUNT(DISTINCT pci.session_id) as provided_contact,
                ps.total_questions,
                AVG(EXTRACT(EPOCH FROM (ps.completed_at - ps.started_at))/60) as avg_duration_minutes,
                ROUND(AVG(CASE WHEN ps.status = 'completed' THEN 100.0 ELSE 0 END), 1) as completion_rate,
                ROUND(AVG(CASE WHEN pci.id IS NOT NULL THEN 100.0 ELSE 0 END), 1) as contact_rate
            FROM prescreening_sessions ps
            LEFT JOIN patient_contact_info pci ON ps.session_id = pci.session_id
            WHERE ps.started_at >= NOW() - INTERVAL '{days} days'
              AND ps.condition IS NOT NULL
            GROUP BY LOWER(TRIM(ps.condition)), ps.total_questions
            HAVING COUNT(DISTINCT ps.id) >= {min_sessions}
            ORDER BY sessions_started DESC
        """)

        # Group by condition (may have multiple question counts)
        conditions_map = {}
        for row in performance:
            condition = row['condition']
            if condition not in conditions_map:
                conditions_map[condition] = {
                    "condition": condition,
                    "sessions_started": 0,
                    "sessions_completed": 0,
                    "provided_contact": 0,
                    "avg_questions": 0,
                    "avg_duration_minutes": 0,
                    "completion_rate": 0,
                    "contact_rate": 0
                }

            conditions_map[condition]['sessions_started'] += row['sessions_started']
            conditions_map[condition]['sessions_completed'] += row['sessions_completed']
            conditions_map[condition]['provided_contact'] += row['provided_contact']
            conditions_map[condition]['avg_questions'] = row['total_questions'] or 0
            conditions_map[condition]['avg_duration_minutes'] = round(float(row['avg_duration_minutes'] or 0), 1)
            conditions_map[condition]['completion_rate'] = float(row['completion_rate'] or 0)
            conditions_map[condition]['contact_rate'] = float(row['contact_rate'] or 0)

        conditions_list = list(conditions_map.values())
        conditions_list.sort(key=lambda x: x['sessions_started'], reverse=True)

        # Find best and worst performers
        best = max(conditions_list, key=lambda x: x['completion_rate']) if conditions_list else None
        worst = min(conditions_list, key=lambda x: x['completion_rate']) if conditions_list else None

        return {
            "conditions": conditions_list,
            "summary": {
                "total_conditions": len(conditions_list),
                "best_performer": best['condition'] if best else None,
                "worst_performer": worst['condition'] if worst else None,
                "performance_gap": round(best['completion_rate'] - worst['completion_rate'], 1) if best and worst else 0
            }
        }
    except Exception as e:
        logger.error(f"Error in condition performance: {e}", exc_info=True)
        return {"error": str(e)}


@router.get("/analytics/bi/location-effectiveness")
async def get_location_effectiveness(days: int = Query(30, ge=1, le=365)):
    """
    Location-based performance analysis
    Identifies high and low performing locations
    """
    try:
        locations = db.execute_query(f"""
            SELECT
                ps.location,
                COUNT(DISTINCT ps.id) as sessions_started,
                COUNT(CASE WHEN ps.status = 'completed' THEN 1 END) as sessions_completed,
                COUNT(DISTINCT pci.session_id) as provided_contact,
                COUNT(DISTINCT ps.condition) as unique_conditions,
                ROUND(AVG(CASE WHEN ps.status = 'completed' THEN 100.0 ELSE 0 END), 1) as completion_rate,
                ROUND(AVG(CASE WHEN pci.id IS NOT NULL THEN 100.0 ELSE 0 END), 1) as contact_rate
            FROM prescreening_sessions ps
            LEFT JOIN patient_contact_info pci ON ps.session_id = pci.session_id
            WHERE ps.started_at >= NOW() - INTERVAL '{days} days'
              AND ps.location IS NOT NULL
            GROUP BY ps.location
            HAVING COUNT(DISTINCT ps.id) >= 2
            ORDER BY sessions_started DESC
        """)

        location_list = []
        for row in locations:
            location_list.append({
                "location": row['location'],
                "sessions_started": row['sessions_started'],
                "sessions_completed": row['sessions_completed'],
                "provided_contact": row['provided_contact'],
                "unique_conditions": row['unique_conditions'],
                "completion_rate": float(row['completion_rate'] or 0),
                "contact_rate": float(row['contact_rate'] or 0)
            })

        # Find performance leaders
        best = max(location_list, key=lambda x: x['completion_rate']) if location_list else None
        worst = min(location_list, key=lambda x: x['completion_rate']) if location_list else None

        return {
            "locations": location_list,
            "summary": {
                "total_locations": len(location_list),
                "best_location": best['location'] if best else None,
                "best_completion_rate": best['completion_rate'] if best else 0,
                "worst_location": worst['location'] if worst else None,
                "worst_completion_rate": worst['completion_rate'] if worst else 0,
                "performance_gap": round(best['completion_rate'] - worst['completion_rate'], 1) if best and worst else 0
            }
        }
    except Exception as e:
        logger.error(f"Error in location effectiveness: {e}", exc_info=True)
        return {"error": str(e)}


@router.get("/analytics/bi/question-impact")
async def get_question_impact(days: int = Query(30, ge=1, le=365)):
    """
    Analyze impact of question count on completion rates
    Critical for trial optimization
    """
    try:
        question_impact = db.execute_query(f"""
            SELECT
                total_questions,
                COUNT(*) as sessions,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                ROUND(AVG(CASE WHEN status = 'completed' THEN 100.0 ELSE 0 END), 1) as completion_rate,
                AVG(EXTRACT(EPOCH FROM (completed_at - started_at))/60) as avg_duration_minutes
            FROM prescreening_sessions
            WHERE started_at >= NOW() - INTERVAL '{days} days'
              AND total_questions IS NOT NULL
            GROUP BY total_questions
            ORDER BY total_questions
        """)

        results = []
        for row in question_impact:
            results.append({
                "question_count": row['total_questions'],
                "sessions": row['sessions'],
                "completed": row['completed'],
                "completion_rate": float(row['completion_rate'] or 0),
                "avg_duration_minutes": round(float(row['avg_duration_minutes'] or 0), 1)
            })

        return {
            "question_impact": results,
            "recommendation": "Target 3-4 questions for optimal completion rates" if results else None
        }
    except Exception as e:
        logger.error(f"Error in question impact: {e}", exc_info=True)
        return {"error": str(e)}


@router.get("/analytics/bi/time-patterns")
async def get_time_patterns(days: int = Query(30, ge=1, le=365)):
    """
    Time-based engagement patterns
    Hour of day and day of week analysis
    """
    try:
        # Hour of day patterns
        hourly = db.execute_query(f"""
            SELECT
                EXTRACT(HOUR FROM cl.timestamp) as hour,
                COUNT(DISTINCT cl.session_id) as conversations,
                COUNT(DISTINCT ps.id) as prescreenings_started,
                ROUND(AVG(CASE WHEN ps.status = 'completed' THEN 100.0 ELSE 0 END), 1) as completion_rate
            FROM chat_logs cl
            LEFT JOIN prescreening_sessions ps ON cl.session_id = ps.session_id
            WHERE cl.timestamp >= NOW() - INTERVAL '{days} days'
            GROUP BY EXTRACT(HOUR FROM cl.timestamp)
            ORDER BY hour
        """)

        # Day of week patterns
        daily = db.execute_query(f"""
            SELECT
                EXTRACT(DOW FROM cl.timestamp) as day_of_week,
                COUNT(DISTINCT cl.session_id) as conversations,
                COUNT(DISTINCT ps.id) as prescreenings_started,
                ROUND(AVG(CASE WHEN ps.status = 'completed' THEN 100.0 ELSE 0 END), 1) as completion_rate
            FROM chat_logs cl
            LEFT JOIN prescreening_sessions ps ON cl.session_id = ps.session_id
            WHERE cl.timestamp >= NOW() - INTERVAL '{days} days'
            GROUP BY EXTRACT(DOW FROM cl.timestamp)
            ORDER BY day_of_week
        """)

        day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

        return {
            "hourly_patterns": [
                {
                    "hour": int(row['hour']),
                    "conversations": row['conversations'],
                    "prescreenings": row['prescreenings_started'],
                    "completion_rate": float(row['completion_rate'] or 0)
                }
                for row in hourly
            ],
            "daily_patterns": [
                {
                    "day": day_names[int(row['day_of_week'])],
                    "conversations": row['conversations'],
                    "prescreenings": row['prescreenings_started'],
                    "completion_rate": float(row['completion_rate'] or 0)
                }
                for row in daily
            ]
        }
    except Exception as e:
        logger.error(f"Error in time patterns: {e}", exc_info=True)
        return {"error": str(e)}


@router.get("/analytics/bi/eligibility-outcomes")
async def get_eligibility_outcomes(days: int = Query(30, ge=1, le=365)):
    """
    Eligibility pass/fail rates by trial
    Identifies trials with too-strict or too-loose criteria
    """
    try:
        outcomes = db.execute_query(f"""
            SELECT
                ps.trial_id,
                ct.trial_name,
                ps.condition,
                COUNT(*) as total_completed,
                COUNT(CASE WHEN ps.overall_eligibility_status = 'eligible' THEN 1 END) as eligible,
                COUNT(CASE WHEN ps.overall_eligibility_status = 'ineligible' THEN 1 END) as ineligible,
                COUNT(CASE WHEN ps.overall_eligibility_status = 'needs_review' THEN 1 END) as needs_review,
                ROUND(AVG(CASE WHEN ps.overall_eligibility_status = 'eligible' THEN 100.0 ELSE 0 END), 1) as eligibility_rate
            FROM prescreening_sessions ps
            LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
            WHERE ps.started_at >= NOW() - INTERVAL '{days} days'
              AND ps.status = 'completed'
            GROUP BY ps.trial_id, ct.trial_name, ps.condition
            HAVING COUNT(*) >= 3
            ORDER BY total_completed DESC
        """)

        results = []
        for row in outcomes:
            results.append({
                "trial_id": row['trial_id'],
                "trial_name": row['trial_name'],
                "condition": row['condition'],
                "total_completed": row['total_completed'],
                "eligible": row['eligible'],
                "ineligible": row['ineligible'],
                "needs_review": row['needs_review'],
                "eligibility_rate": float(row['eligibility_rate'] or 0)
            })

        # Find trials with poor eligibility rates (too strict)
        strict_trials = [r for r in results if r['eligibility_rate'] < 20 and r['total_completed'] >= 5]
        loose_trials = [r for r in results if r['eligibility_rate'] > 80 and r['total_completed'] >= 5]

        return {
            "trials": results,
            "insights": {
                "strict_trials": strict_trials,  # May need criteria relaxation
                "loose_trials": loose_trials,     # Good eligibility rates
                "total_trials_analyzed": len(results)
            }
        }
    except Exception as e:
        logger.error(f"Error in eligibility outcomes: {e}", exc_info=True)
        return {"error": str(e)}


@router.get("/analytics/bi/high-abandonment-trials")
async def get_high_abandonment_trials(days: int = Query(30, ge=1, le=365)):
    """
    Identify trials with high abandonment rates
    Critical for optimization efforts
    """
    try:
        trials = db.execute_query(f"""
            SELECT
                ps.trial_id,
                ct.trial_name,
                ps.condition,
                COUNT(*) as started,
                COUNT(CASE WHEN ps.status = 'completed' THEN 1 END) as completed,
                ps.total_questions,
                ROUND(AVG(CASE WHEN ps.status = 'completed' THEN 100.0 ELSE 0 END), 1) as completion_rate
            FROM prescreening_sessions ps
            LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
            WHERE ps.started_at >= NOW() - INTERVAL '{days} days'
            GROUP BY ps.trial_id, ct.trial_name, ps.condition, ps.total_questions
            HAVING COUNT(*) >= 5
            ORDER BY completion_rate ASC, started DESC
            LIMIT 20
        """)

        results = []
        for row in trials:
            abandonment_rate = 100 - float(row['completion_rate'] or 0)
            results.append({
                "trial_id": row['trial_id'],
                "trial_name": row['trial_name'],
                "condition": row['condition'],
                "started": row['started'],
                "completed": row['completed'],
                "abandoned": row['started'] - row['completed'],
                "completion_rate": float(row['completion_rate'] or 0),
                "abandonment_rate": round(abandonment_rate, 1),
                "total_questions": row['total_questions']
            })

        return {
            "high_abandonment_trials": results,
            "summary": {
                "trials_analyzed": len(results),
                "avg_abandonment_rate": round(sum(r['abandonment_rate'] for r in results) / len(results), 1) if results else 0
            }
        }
    except Exception as e:
        logger.error(f"Error in high abandonment trials: {e}", exc_info=True)
        return {"error": str(e)}


@router.get("/analytics/bi/sms-campaign-roi")
async def get_sms_campaign_roi():
    """
    SMS Lead Campaign performance and ROI metrics
    """
    try:
        campaigns = db.execute_query("""
            SELECT
                lc.id,
                lc.campaign_name,
                lc.condition,
                lc.location,
                lc.total_leads,
                lc.sent_count,
                lc.responded_count,
                lc.interested_count,
                lc.not_interested_count,
                ROUND((lc.responded_count::FLOAT / NULLIF(lc.sent_count, 0) * 100), 1) as response_rate,
                ROUND((lc.interested_count::FLOAT / NULLIF(lc.responded_count, 0) * 100), 1) as interest_rate,
                ROUND((lc.interested_count::FLOAT / NULLIF(lc.sent_count, 0) * 100), 1) as overall_conversion,
                lc.created_at,
                lc.status
            FROM lead_campaigns lc
            ORDER BY lc.created_at DESC
        """)

        campaign_list = []
        for row in campaigns:
            campaign_list.append({
                "id": row['id'],
                "campaign_name": row['campaign_name'],
                "condition": row['condition'],
                "location": row['location'],
                "total_leads": row['total_leads'],
                "sent": row['sent_count'],
                "responded": row['responded_count'],
                "interested": row['interested_count'],
                "declined": row['not_interested_count'],
                "response_rate": float(row['response_rate'] or 0),
                "interest_rate": float(row['interest_rate'] or 0),
                "overall_conversion": float(row['overall_conversion'] or 0),
                "status": row['status']
            })

        # Calculate aggregate ROI
        total_sent = sum(c['sent'] for c in campaign_list)
        total_responded = sum(c['responded'] for c in campaign_list)
        total_interested = sum(c['interested'] for c in campaign_list)

        return {
            "campaigns": campaign_list,
            "aggregate": {
                "total_campaigns": len(campaign_list),
                "total_sent": total_sent,
                "total_responded": total_responded,
                "total_interested": total_interested,
                "overall_response_rate": round((total_responded / total_sent * 100), 1) if total_sent > 0 else 0,
                "overall_interest_rate": round((total_interested / total_responded * 100), 1) if total_responded > 0 else 0
            }
        }
    except Exception as e:
        logger.error(f"Error in SMS campaign ROI: {e}", exc_info=True)
        return {"error": str(e)}


@router.get("/analytics/bi/summary")
async def get_business_intelligence_summary(days: int = Query(30, ge=1, le=365)):
    """
    Executive summary with key performance indicators
    """
    try:
        # Get all metrics in parallel
        funnel_data = await get_conversion_funnel(days)
        condition_data = await get_condition_performance(days, min_sessions=1)
        location_data = await get_location_effectiveness(days)
        question_data = await get_question_impact(days)

        # Extract key insights
        total_convos = funnel_data.get('funnel', [{}])[0].get('count', 0) if funnel_data.get('funnel') else 0
        overall_conversion = funnel_data.get('conversion_rates', {}).get('overall_conversion', 0)

        conditions = condition_data.get('conditions', [])
        best_condition = max(conditions, key=lambda x: x['completion_rate'])['condition'] if conditions else 'N/A'
        worst_condition = min(conditions, key=lambda x: x['completion_rate'])['condition'] if conditions else 'N/A'

        locations = location_data.get('locations', [])
        best_location = max(locations, key=lambda x: x['completion_rate'])['location'] if locations else 'N/A'

        return {
            "period_days": days,
            "kpis": {
                "total_conversations": total_convos,
                "overall_conversion_rate": overall_conversion,
                "prescreening_completion_rate": funnel_data.get('conversion_rates', {}).get('prescreening_start_to_complete', 0),
                "contact_collection_rate": funnel_data.get('conversion_rates', {}).get('complete_to_contact', 0)
            },
            "top_performers": {
                "best_condition": best_condition,
                "best_location": best_location,
                "optimal_question_count": "3-4 questions"
            },
            "opportunities": {
                "biggest_dropoff": "Before prescreening (64% abandon)",
                "worst_condition": worst_condition,
                "focus_areas": [
                    "Reduce questions in low-performing trials",
                    "Investigate Atlanta location issues",
                    "Optimize diabetes trial criteria"
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in BI summary: {e}", exc_info=True)
        return {"error": str(e)}
