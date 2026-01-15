"""Advanced analytics endpoints for business intelligence dashboard"""
from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, List
from datetime import datetime, timedelta
import logging
import json

from core.database import db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/analytics/auto-evaluation")
async def get_auto_evaluation_metrics(
    days: int = Query(30, description="Number of days to look back")
):
    """Return comprehensive auto-evaluation performance metrics"""
    try:
        # Get date filter
        date_filter = datetime.now() - timedelta(days=days)
        
        # BMI calculation analytics - with fallback for missing columns
        try:
            bmi_stats = db.execute_query("""
                SELECT 
                    COUNT(*) as total_bmi_questions,
                    COUNT(CASE WHEN auto_evaluated = true AND evaluation_method = 'bmi_calculation' THEN 1 END) as auto_resolved_bmi,
                    AVG(CASE WHEN evaluation_method = 'bmi_calculation' THEN confidence_score END) as avg_bmi_confidence,
                    COUNT(CASE WHEN evaluation_method = 'bmi_calculation' AND confidence_score > 0.8 THEN 1 END) as high_confidence_bmi
                FROM prescreening_answers 
                WHERE calculation_details::text LIKE '%bmi%'
                AND created_at >= %s
            """, (date_filter,))
        except Exception as e:
            # Fallback for databases without enhanced columns
            logger.warning(f"Enhanced analytics columns not found, using fallback: {str(e)}")
            bmi_stats = db.execute_query("""
                SELECT 
                    COUNT(*) as total_bmi_questions,
                    0 as auto_resolved_bmi,
                    0.0 as avg_bmi_confidence,
                    0 as high_confidence_bmi
                FROM prescreening_answers 
                WHERE created_at >= %s
                AND question_id LIKE '%bmi%'
            """, (date_filter,))
        
        # Numeric comparison analytics  
        numeric_stats = db.execute_query("""
            SELECT 
                COUNT(*) as total_numeric_questions,
                COUNT(CASE WHEN auto_evaluated = true AND evaluation_method = 'numeric_comparison' THEN 1 END) as auto_resolved_numeric,
                AVG(CASE WHEN evaluation_method = 'numeric_comparison' THEN confidence_score END) as avg_numeric_confidence,
                COUNT(CASE WHEN evaluation_method = 'numeric_comparison' AND confidence_score > 0.8 THEN 1 END) as high_confidence_numeric
            FROM prescreening_answers 
            WHERE evaluation_method = 'numeric_comparison'
            AND created_at >= %s
        """, (date_filter,))
        
        # Age extraction analytics
        age_stats = db.execute_query("""
            SELECT 
                COUNT(*) as total_age_questions,
                COUNT(CASE WHEN auto_evaluated = true AND evaluation_method = 'age_extraction' THEN 1 END) as auto_resolved_age,
                AVG(CASE WHEN evaluation_method = 'age_extraction' THEN confidence_score END) as avg_age_confidence,
                COUNT(CASE WHEN evaluation_method = 'age_extraction' AND confidence_score > 0.9 THEN 1 END) as high_confidence_age
            FROM prescreening_answers 
            WHERE evaluation_method = 'age_extraction'
            AND created_at >= %s
        """, (date_filter,))
        
        # Boolean classification analytics
        boolean_stats = db.execute_query("""
            SELECT 
                COUNT(*) as total_boolean_questions,
                COUNT(CASE WHEN auto_evaluated = true AND evaluation_method = 'boolean_classification' THEN 1 END) as auto_resolved_boolean,
                AVG(CASE WHEN evaluation_method = 'boolean_classification' THEN confidence_score END) as avg_boolean_confidence,
                COUNT(CASE WHEN evaluation_method = 'boolean_classification' AND confidence_score > 0.8 THEN 1 END) as high_confidence_boolean
            FROM prescreening_answers 
            WHERE evaluation_method = 'boolean_classification'
            AND created_at >= %s
        """, (date_filter,))
        
        # Get results safely
        bmi_data = bmi_stats[0] if bmi_stats else {}
        numeric_data = numeric_stats[0] if numeric_stats else {}
        age_data = age_stats[0] if age_stats else {}
        boolean_data = boolean_stats[0] if boolean_stats else {}
        
        # Calculate success rates
        bmi_success_rate = (bmi_data.get('auto_resolved_bmi', 0) / bmi_data.get('total_bmi_questions', 1) * 100) if bmi_data.get('total_bmi_questions', 0) > 0 else 0
        numeric_success_rate = (numeric_data.get('auto_resolved_numeric', 0) / numeric_data.get('total_numeric_questions', 1) * 100) if numeric_data.get('total_numeric_questions', 0) > 0 else 0
        age_success_rate = (age_data.get('auto_resolved_age', 0) / age_data.get('total_age_questions', 1) * 100) if age_data.get('total_age_questions', 0) > 0 else 0
        boolean_success_rate = (boolean_data.get('auto_resolved_boolean', 0) / boolean_data.get('total_boolean_questions', 1) * 100) if boolean_data.get('total_boolean_questions', 0) > 0 else 0
        
        # Calculate overall metrics
        total_questions = sum([
            bmi_data.get('total_bmi_questions', 0),
            numeric_data.get('total_numeric_questions', 0),
            age_data.get('total_age_questions', 0),
            boolean_data.get('total_boolean_questions', 0)
        ])
        
        total_auto_resolved = sum([
            bmi_data.get('auto_resolved_bmi', 0),
            numeric_data.get('auto_resolved_numeric', 0),
            age_data.get('auto_resolved_age', 0),
            boolean_data.get('auto_resolved_boolean', 0)
        ])
        
        overall_automation_rate = (total_auto_resolved / total_questions * 100) if total_questions > 0 else 0
        
        return {
            "bmi_calculations": {
                "total_attempts": bmi_data.get('total_bmi_questions', 0),
                "successful_calculations": bmi_data.get('auto_resolved_bmi', 0),
                "success_rate": round(bmi_success_rate, 1),
                "average_confidence": round(bmi_data.get('avg_bmi_confidence', 0) or 0, 2),
                "high_confidence_count": bmi_data.get('high_confidence_bmi', 0)
            },
            "numeric_comparisons": {
                "total_comparisons": numeric_data.get('total_numeric_questions', 0),
                "auto_resolved": numeric_data.get('auto_resolved_numeric', 0),
                "auto_resolution_rate": round(numeric_success_rate, 1),
                "average_confidence": round(numeric_data.get('avg_numeric_confidence', 0) or 0, 2),
                "high_confidence_count": numeric_data.get('high_confidence_numeric', 0)
            },
            "age_extraction": {
                "total_extractions": age_data.get('total_age_questions', 0),
                "successful_extractions": age_data.get('auto_resolved_age', 0),
                "success_rate": round(age_success_rate, 1),
                "average_confidence": round(age_data.get('avg_age_confidence', 0) or 0, 2),
                "high_confidence_count": age_data.get('high_confidence_age', 0)
            },
            "boolean_classification": {
                "total_classifications": boolean_data.get('total_boolean_questions', 0),
                "successful_classifications": boolean_data.get('auto_resolved_boolean', 0),
                "success_rate": round(boolean_success_rate, 1),
                "average_confidence": round(boolean_data.get('avg_boolean_confidence', 0) or 0, 2),
                "high_confidence_count": boolean_data.get('high_confidence_boolean', 0)
            },
            "overall_automation": {
                "total_questions": total_questions,
                "auto_resolved": total_auto_resolved,
                "automation_rate": round(overall_automation_rate, 1),
                "manual_review_needed": total_questions - total_auto_resolved,
                "efficiency_improvement": f"{round(overall_automation_rate, 1)}% reduction in manual review"
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching auto-evaluation metrics: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch auto-evaluation metrics: {str(e)}")


@router.get("/analytics/cross-condition")
async def get_cross_condition_performance(
    days: int = Query(30, description="Number of days to look back")
):
    """Return trial matching and conversion data by medical condition"""
    try:
        # Get date filter
        date_filter = datetime.now() - timedelta(days=days)
        
        # Get condition-specific performance data
        condition_stats = db.execute_query("""
            SELECT 
                sa.query_condition,
                COUNT(sa.id) as total_searches,
                AVG(sa.results_count) as avg_trials_found,
                AVG(sa.search_duration_ms) as avg_search_duration,
                COUNT(DISTINCT sa.session_id) as unique_sessions,
                COUNT(ps.session_id) as prescreening_started,
                COUNT(CASE WHEN ps.status = 'completed' THEN 1 END) as prescreening_completed,
                AVG(CASE WHEN sa.similarity_scores IS NOT NULL THEN 
                    (SELECT AVG(value::float) FROM jsonb_each_text(sa.similarity_scores))
                END) as avg_similarity_score
            FROM search_analytics sa
            LEFT JOIN prescreening_sessions ps ON sa.session_id = ps.session_id
            WHERE sa.created_at >= %s
            AND sa.query_condition IS NOT NULL
            AND sa.query_condition != ''
            GROUP BY sa.query_condition
            ORDER BY total_searches DESC
        """, (date_filter,))
        
        # Format response
        conditions = {}
        for stat in condition_stats:
            condition = stat['query_condition']
            completion_rate = (stat['prescreening_completed'] / stat['prescreening_started'] * 100) if stat['prescreening_started'] > 0 else 0
            search_to_prescreening_rate = (stat['prescreening_started'] / stat['total_searches'] * 100) if stat['total_searches'] > 0 else 0
            
            conditions[condition] = {
                "total_searches": stat['total_searches'],
                "avg_trials_found": round(stat['avg_trials_found'] or 0, 1),
                "avg_search_duration_ms": round(stat['avg_search_duration'] or 0, 1),
                "unique_sessions": stat['unique_sessions'],
                "prescreening_started": stat['prescreening_started'],
                "prescreening_completed": stat['prescreening_completed'],
                "completion_rate": round(completion_rate, 1),
                "search_effectiveness": {
                    "avg_similarity_score": round(stat['avg_similarity_score'] or 0, 2),
                    "search_to_prescreening_rate": round(search_to_prescreening_rate, 1)
                }
            }
        
        # Calculate summary metrics
        total_conditions = len(conditions)
        most_searched_condition = max(conditions.keys(), key=lambda x: conditions[x]['total_searches']) if conditions else None
        highest_completion_rate = max(conditions.values(), key=lambda x: x['completion_rate'])['completion_rate'] if conditions else 0
        
        return {
            "conditions": conditions,
            "summary": {
                "total_conditions_searched": total_conditions,
                "most_searched_condition": most_searched_condition,
                "highest_completion_rate": highest_completion_rate,
                "total_unique_sessions": sum(c['unique_sessions'] for c in conditions.values()),
                "avg_search_duration": round(sum(c['avg_search_duration_ms'] for c in conditions.values()) / max(1, total_conditions), 1)
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching cross-condition performance: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch cross-condition performance: {str(e)}")


@router.get("/analytics/health-metrics")
async def get_health_metrics_analytics(
    days: int = Query(30, description="Number of days to look back")
):
    """Return health metrics calculation performance"""
    try:
        # Get date filter
        date_filter = datetime.now() - timedelta(days=days)
        
        # Health metrics performance
        metrics_stats = db.execute_query("""
            SELECT 
                metric_type,
                calculation_method,
                COUNT(*) as total_calculations,
                AVG(calculated_value) as avg_value,
                MIN(calculated_value) as min_value,
                MAX(calculated_value) as max_value,
                STDDEV(calculated_value) as stddev_value
            FROM health_metrics
            WHERE created_at >= %s
            GROUP BY metric_type, calculation_method
            ORDER BY metric_type, calculation_method
        """, (date_filter,))
        
        # Format response by metric type
        metrics = {}
        for stat in metrics_stats:
            metric_type = stat['metric_type']
            method = stat['calculation_method']
            
            if metric_type not in metrics:
                metrics[metric_type] = {}
            
            metrics[metric_type][method] = {
                "total_calculations": stat['total_calculations'],
                "average_value": round(stat['avg_value'], 2),
                "range": {
                    "min": round(stat['min_value'], 2),
                    "max": round(stat['max_value'], 2)
                },
                "standard_deviation": round(stat['stddev_value'] or 0, 2)
            }
        
        # Calculate BMI statistics if available
        bmi_insights = {}
        if 'bmi' in metrics:
            bmi_data = db.execute_query("""
                SELECT 
                    COUNT(*) as total_bmi_calculations,
                    COUNT(CASE WHEN calculated_value < 18.5 THEN 1 END) as underweight,
                    COUNT(CASE WHEN calculated_value >= 18.5 AND calculated_value < 25 THEN 1 END) as normal,
                    COUNT(CASE WHEN calculated_value >= 25 AND calculated_value < 30 THEN 1 END) as overweight,
                    COUNT(CASE WHEN calculated_value >= 30 THEN 1 END) as obese
                FROM health_metrics
                WHERE metric_type = 'bmi'
                AND created_at >= %s
            """, (date_filter,))
            
            if bmi_data:
                bmi_stats = bmi_data[0]
                total = bmi_stats['total_bmi_calculations']
                if total > 0:
                    bmi_insights = {
                        "total_calculations": total,
                        "distribution": {
                            "underweight": {"count": bmi_stats['underweight'], "percentage": round(bmi_stats['underweight'] / total * 100, 1)},
                            "normal": {"count": bmi_stats['normal'], "percentage": round(bmi_stats['normal'] / total * 100, 1)},
                            "overweight": {"count": bmi_stats['overweight'], "percentage": round(bmi_stats['overweight'] / total * 100, 1)},
                            "obese": {"count": bmi_stats['obese'], "percentage": round(bmi_stats['obese'] / total * 100, 1)}
                        }
                    }
        
        # Calculate summary metrics
        total_calculations = sum(sum(method['total_calculations'] for method in metric.values()) for metric in metrics.values())
        most_calculated_metric = max(metrics.keys(), key=lambda x: sum(method['total_calculations'] for method in metrics[x].values())) if metrics else None
        
        return {
            "health_metrics": metrics,
            "bmi_insights": bmi_insights,
            "summary": {
                "total_calculations": total_calculations,
                "most_calculated_metric": most_calculated_metric,
                "unique_metric_types": len(metrics),
                "data_quality": {
                    "auto_parsed_percentage": round(sum(
                        method['total_calculations'] for metric in metrics.values() 
                        for method_name, method in metric.items() 
                        if method_name == 'auto_parsed'
                    ) / max(1, total_calculations) * 100, 1)
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching health metrics analytics: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch health metrics analytics: {str(e)}")


@router.get("/analytics/search-performance")
async def get_search_performance_analytics(
    days: int = Query(30, description="Number of days to look back")
):
    """Return search performance and effectiveness metrics"""
    try:
        # Get date filter
        date_filter = datetime.now() - timedelta(days=days)
        
        # Search performance by type
        search_stats = db.execute_query("""
            SELECT 
                search_type,
                COUNT(*) as total_searches,
                AVG(search_duration_ms) as avg_duration,
                AVG(results_count) as avg_results,
                COUNT(CASE WHEN results_count > 0 THEN 1 END) as successful_searches,
                MIN(search_duration_ms) as min_duration,
                MAX(search_duration_ms) as max_duration
            FROM search_analytics
            WHERE created_at >= %s
            GROUP BY search_type
            ORDER BY total_searches DESC
        """, (date_filter,))
        
        # Search effectiveness by condition
        condition_effectiveness = db.execute_query("""
            SELECT 
                query_condition,
                COUNT(*) as searches,
                AVG(results_count) as avg_results,
                COUNT(CASE WHEN results_count > 0 THEN 1 END) as successful_searches,
                AVG(CASE WHEN similarity_scores IS NOT NULL THEN 
                    (SELECT AVG(value::float) FROM jsonb_each_text(similarity_scores))
                END) as avg_similarity
            FROM search_analytics
            WHERE created_at >= %s
            AND query_condition IS NOT NULL
            AND query_condition != ''
            GROUP BY query_condition
            ORDER BY searches DESC
            LIMIT 10
        """, (date_filter,))
        
        # Format search performance
        search_performance = {}
        for stat in search_stats:
            search_type = stat['search_type']
            success_rate = (stat['successful_searches'] / stat['total_searches'] * 100) if stat['total_searches'] > 0 else 0
            
            search_performance[search_type] = {
                "total_searches": stat['total_searches'],
                "avg_duration_ms": round(stat['avg_duration'] or 0, 1),
                "avg_results_count": round(stat['avg_results'] or 0, 1),
                "success_rate": round(success_rate, 1),
                "performance_range": {
                    "min_duration_ms": stat['min_duration'],
                    "max_duration_ms": stat['max_duration']
                }
            }
        
        # Format condition effectiveness
        top_conditions = []
        for stat in condition_effectiveness:
            condition = stat['query_condition']
            success_rate = (stat['successful_searches'] / stat['searches'] * 100) if stat['searches'] > 0 else 0
            
            top_conditions.append({
                "condition": condition,
                "search_volume": stat['searches'],
                "avg_results": round(stat['avg_results'] or 0, 1),
                "success_rate": round(success_rate, 1),
                "avg_similarity_score": round(stat['avg_similarity'] or 0, 2)
            })
        
        # Calculate overall metrics
        total_searches = sum(perf['total_searches'] for perf in search_performance.values())
        total_successful = sum(perf['total_searches'] * perf['success_rate'] / 100 for perf in search_performance.values())
        overall_success_rate = (total_successful / total_searches * 100) if total_searches > 0 else 0
        
        return {
            "search_performance": search_performance,
            "top_conditions": top_conditions,
            "summary": {
                "total_searches": int(total_searches),
                "overall_success_rate": round(overall_success_rate, 1),
                "avg_search_duration": round(sum(perf['avg_duration_ms'] * perf['total_searches'] for perf in search_performance.values()) / max(1, total_searches), 1),
                "most_used_search_type": max(search_performance.keys(), key=lambda x: search_performance[x]['total_searches']) if search_performance else None
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching search performance analytics: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch search performance analytics: {str(e)}")


@router.get("/analytics/business-intelligence")
async def get_business_intelligence_summary(
    days: int = Query(30, description="Number of days to look back")
):
    """Return comprehensive business intelligence summary"""
    try:
        # Get date filter
        date_filter = datetime.now() - timedelta(days=days)
        
        # Overall system performance
        system_stats = db.execute_query("""
            SELECT 
                COUNT(DISTINCT ps.session_id) as total_sessions,
                COUNT(DISTINCT ps.user_id) as unique_users,
                COUNT(CASE WHEN ps.status = 'completed' THEN 1 END) as completed_prescreenings,
                COUNT(DISTINCT sa.session_id) as sessions_with_searches,
                SUM(sa.results_count) as total_trial_matches,
                AVG(pa.confidence_score) as avg_ai_confidence,
                COUNT(CASE WHEN pa.auto_evaluated = true THEN 1 END) as auto_evaluated_answers
            FROM prescreening_sessions ps
            LEFT JOIN search_analytics sa ON ps.session_id = sa.session_id
            LEFT JOIN prescreening_answers pa ON ps.session_id = pa.session_id
            WHERE ps.started_at >= %s
        """, (date_filter,))
        
        # AI effectiveness metrics
        ai_metrics = db.execute_query("""
            SELECT 
                COUNT(*) as total_ai_interactions,
                COUNT(CASE WHEN auto_evaluated = true THEN 1 END) as successful_auto_evaluations,
                AVG(confidence_score) as avg_confidence,
                COUNT(CASE WHEN confidence_score > 0.8 THEN 1 END) as high_confidence_predictions
            FROM prescreening_answers
            WHERE created_at >= %s
        """, (date_filter,))
        
        # User engagement metrics
        engagement_stats = db.execute_query("""
            SELECT 
                AVG(questions_answered) as avg_questions_per_session,
                AVG(EXTRACT(EPOCH FROM (completed_at - started_at))/60) as avg_session_duration_minutes,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completion_count,
                COUNT(*) as total_sessions
            FROM prescreening_sessions
            WHERE started_at >= %s
            AND questions_answered > 0
        """, (date_filter,))
        
        # Get results safely
        system_data = system_stats[0] if system_stats else {}
        ai_data = ai_metrics[0] if ai_metrics else {}
        engagement_data = engagement_stats[0] if engagement_stats else {}
        
        # Calculate key metrics
        completion_rate = (system_data.get('completed_prescreenings', 0) / system_data.get('total_sessions', 1) * 100) if system_data.get('total_sessions', 0) > 0 else 0
        ai_automation_rate = (ai_data.get('successful_auto_evaluations', 0) / ai_data.get('total_ai_interactions', 1) * 100) if ai_data.get('total_ai_interactions', 0) > 0 else 0
        search_adoption_rate = (system_data.get('sessions_with_searches', 0) / system_data.get('total_sessions', 1) * 100) if system_data.get('total_sessions', 0) > 0 else 0
        session_completion_rate = (engagement_data.get('completion_count', 0) / engagement_data.get('total_sessions', 1) * 100) if engagement_data.get('total_sessions', 0) > 0 else 0
        
        return {
            "system_performance": {
                "total_sessions": system_data.get('total_sessions', 0),
                "unique_users": system_data.get('unique_users', 0),
                "completed_prescreenings": system_data.get('completed_prescreenings', 0),
                "completion_rate": round(completion_rate, 1),
                "total_trial_matches": system_data.get('total_trial_matches', 0),
                "search_adoption_rate": round(search_adoption_rate, 1)
            },
            "ai_effectiveness": {
                "total_ai_interactions": ai_data.get('total_ai_interactions', 0),
                "automation_rate": round(ai_automation_rate, 1),
                "avg_confidence_score": round(ai_data.get('avg_confidence', 0) or 0, 2),
                "high_confidence_predictions": ai_data.get('high_confidence_predictions', 0),
                "efficiency_improvement": f"{round(ai_automation_rate, 1)}% reduction in manual processing"
            },
            "user_engagement": {
                "avg_questions_per_session": round(engagement_data.get('avg_questions_per_session', 0) or 0, 1),
                "avg_session_duration_minutes": round(engagement_data.get('avg_session_duration_minutes', 0) or 0, 1),
                "session_completion_rate": round(session_completion_rate, 1),
                "user_satisfaction_proxy": round((completion_rate + ai_automation_rate) / 2, 1)
            },
            "business_value": {
                "operational_efficiency": f"{round(ai_automation_rate, 1)}% automation achieved",
                "user_experience_score": round((completion_rate + session_completion_rate) / 2, 1),
                "scalability_indicator": f"{system_data.get('total_sessions', 0)} sessions processed with {round(ai_automation_rate, 1)}% automation",
                "roi_metrics": {
                    "manual_reviews_avoided": ai_data.get('successful_auto_evaluations', 0),
                    "avg_time_saved_per_session": f"{round(engagement_data.get('avg_session_duration_minutes', 0) or 0 * ai_automation_rate / 100, 1)} minutes"
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching business intelligence summary: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch business intelligence summary: {str(e)}")