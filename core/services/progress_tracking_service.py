"""
Real-Time Progress Tracking and Manual Review Workflow Service

This service provides comprehensive progress tracking and manual review capabilities:
- Real-time WebSocket updates for processing status
- Manual review queue management with priority scoring
- Automated review assignment based on expertise and workload
- Progress analytics and performance monitoring
- Integration with intelligent matching confidence thresholds
- Audit trail and approval workflow management
"""

import logging
import asyncio
from typing import Dict, List, Any, Optional, Set
from datetime import datetime, timedelta
from enum import Enum
import json

from core.database import db

logger = logging.getLogger(__name__)


class ReviewPriority(Enum):
    """Priority levels for manual reviews"""
    CRITICAL = 1    # Production issues, high-value protocols
    HIGH = 2        # Low confidence matches, complex protocols
    MEDIUM = 3      # Standard review requirements
    LOW = 4         # Routine verification, high confidence items


class ReviewStatus(Enum):
    """Status of review items"""
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MORE_INFO = "needs_more_info"
    ESCALATED = "escalated"


class ReviewType(Enum):
    """Types of review items"""
    TRIAL_MATCHING = "trial_matching"
    CRITERIA_EXTRACTION = "criteria_extraction"
    METADATA_VALIDATION = "metadata_validation"
    PROTOCOL_QUALITY = "protocol_quality"
    BATCH_COMPLETION = "batch_completion"


class ProgressTrackingService:
    """Service for real-time progress tracking and manual review workflows"""
    
    def __init__(self):
        self.active_subscriptions = {}  # job_id -> set of websocket connections
        self.review_queue = {}  # priority -> list of review items
        self.reviewer_assignments = {}  # reviewer_id -> set of assigned review_ids
        self.review_metrics = {
            "total_reviews": 0,
            "avg_review_time": 0.0,
            "approval_rate": 0.0,
            "quality_score": 0.0
        }
        
        # Initialize review queue
        self._initialize_review_queue()
    
    def _initialize_review_queue(self):
        """Initialize review queue structure"""
        for priority in ReviewPriority:
            self.review_queue[priority] = []
    
    # =====================================================
    # Real-Time Progress Tracking
    # =====================================================
    
    async def track_job_progress(self, job_id: str, progress_data: Dict[str, Any]):
        """Update and broadcast job progress to subscribers"""
        try:
            # Update database with progress
            await self._update_job_progress_database(job_id, progress_data)
            
            # Broadcast to subscribers (WebSocket implementation would go here)
            await self._broadcast_progress_update(job_id, progress_data)
            
            # Check if manual review is needed
            await self._check_review_requirements(job_id, progress_data)
            
            logger.debug(f"Progress updated for job {job_id}: {progress_data.get('progress_percentage', 0):.1f}%")
            
        except Exception as e:
            logger.error(f"Error tracking job progress: {e}")
    
    async def _update_job_progress_database(self, job_id: str, progress_data: Dict[str, Any]):
        """Update job progress in database"""
        try:
            update_fields = []
            update_values = []
            
            if 'progress_percentage' in progress_data:
                update_fields.append("progress_percentage = %s")
                update_values.append(progress_data['progress_percentage'])
            
            if 'current_file' in progress_data:
                update_fields.append("current_file = %s")
                update_values.append(progress_data['current_file'])
            
            if 'status' in progress_data:
                update_fields.append("status = %s")
                update_values.append(progress_data['status'])
            
            if 'processed_files' in progress_data:
                update_fields.append("processed_files = %s")
                update_values.append(progress_data['processed_files'])
            
            if 'failed_files' in progress_data:
                update_fields.append("failed_files = %s")
                update_values.append(progress_data['failed_files'])
            
            if 'estimated_completion' in progress_data:
                update_fields.append("estimated_completion = %s")
                update_values.append(progress_data['estimated_completion'])
            
            if update_fields:
                update_values.append(job_id)
                
                db.execute_update(f"""
                    UPDATE protocol_processing_jobs 
                    SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = %s
                """, tuple(update_values))
            
        except Exception as e:
            logger.error(f"Error updating job progress in database: {e}")
    
    async def _broadcast_progress_update(self, job_id: str, progress_data: Dict[str, Any]):
        """Broadcast progress update to subscribers (WebSocket placeholder)"""
        # This would integrate with WebSocket connections
        # For now, we'll store the latest update for polling
        try:
            # Store latest progress for polling endpoints
            progress_update = {
                "job_id": job_id,
                "timestamp": datetime.now().isoformat(),
                "progress_data": progress_data
            }
            
            # Could integrate with Redis or similar for real-time updates
            logger.debug(f"Broadcasting progress update for job {job_id}")
            
        except Exception as e:
            logger.error(f"Error broadcasting progress update: {e}")
    
    async def _check_review_requirements(self, job_id: str, progress_data: Dict[str, Any]):
        """Check if job requires manual review and add to queue"""
        try:
            # Get job details
            job_data = db.execute_query("""
                SELECT * FROM protocol_processing_jobs WHERE job_id = %s
            """, (job_id,))
            
            if not job_data:
                return
            
            job = job_data[0]
            
            # Check various conditions that might require review
            review_needed = False
            review_reasons = []
            priority = ReviewPriority.MEDIUM
            
            # 1. Check completion status
            if progress_data.get('status') == 'completed':
                # Get trial matching results
                match_results = await self._get_trial_matching_results(job_id)
                
                if match_results:
                    confidence = match_results.get('confidence_score', 0.0)
                    
                    # Low confidence matches need review
                    if confidence < 0.70:
                        review_needed = True
                        review_reasons.append(f"Low confidence match: {confidence:.2f}")
                        priority = ReviewPriority.HIGH
                    
                    # Medium confidence matches need review
                    elif confidence < 0.85:
                        review_needed = True
                        review_reasons.append(f"Medium confidence match: {confidence:.2f}")
                        priority = ReviewPriority.MEDIUM
                
                # Check extraction quality
                results = job.get('results', {})
                if results:
                    field_coverage = results.get('field_coverage_percentage', 0)
                    extraction_confidence = results.get('extraction_confidence', 0)
                    
                    if field_coverage < 60:
                        review_needed = True
                        review_reasons.append(f"Low field coverage: {field_coverage}%")
                        priority = min(priority, ReviewPriority.HIGH)
                    
                    if extraction_confidence < 0.60:
                        review_needed = True
                        review_reasons.append(f"Low extraction confidence: {extraction_confidence:.2f}")
                        priority = min(priority, ReviewPriority.HIGH)
            
            # 2. Check for processing errors or warnings
            elif progress_data.get('status') == 'failed':
                review_needed = True
                review_reasons.append("Processing failed")
                priority = ReviewPriority.CRITICAL
            
            elif progress_data.get('status') == 'completed_with_errors':
                review_needed = True
                review_reasons.append("Processing completed with errors")
                priority = ReviewPriority.HIGH
            
            # 3. Add to review queue if needed
            if review_needed:
                await self._add_to_review_queue(
                    job_id, ReviewType.PROTOCOL_QUALITY, priority, review_reasons
                )
            
        except Exception as e:
            logger.error(f"Error checking review requirements: {e}")
    
    async def _get_trial_matching_results(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get trial matching results for a job"""
        try:
            results = db.execute_query("""
                SELECT confidence_score, match_type, review_status, match_reasons
                FROM intelligent_trial_matches 
                WHERE processing_job_id = %s
                ORDER BY confidence_score DESC
                LIMIT 1
            """, (job_id,))
            
            return results[0] if results else None
            
        except Exception as e:
            logger.error(f"Error getting trial matching results: {e}")
            return None
    
    # =====================================================
    # Manual Review Queue Management
    # =====================================================
    
    async def _add_to_review_queue(self, job_id: str, review_type: ReviewType, 
                                 priority: ReviewPriority, reasons: List[str]):
        """Add item to manual review queue"""
        try:
            # Create review record
            review_id = await self._create_review_record(job_id, review_type, priority, reasons)
            
            if review_id:
                # Add to in-memory queue
                review_item = {
                    "review_id": review_id,
                    "job_id": job_id,
                    "review_type": review_type.value,
                    "priority": priority.value,
                    "reasons": reasons,
                    "created_at": datetime.now(),
                    "estimated_review_time": self._estimate_review_time(review_type, priority)
                }
                
                self.review_queue[priority].append(review_item)
                
                # Sort queue by creation time (FIFO within priority)
                self.review_queue[priority].sort(key=lambda x: x['created_at'])
                
                logger.info(f"Added job {job_id} to review queue with priority {priority.name}")
                
                # Notify available reviewers
                await self._notify_reviewers(review_item)
        
        except Exception as e:
            logger.error(f"Error adding to review queue: {e}")
    
    async def _create_review_record(self, job_id: str, review_type: ReviewType,
                                  priority: ReviewPriority, reasons: List[str]) -> Optional[int]:
        """Create review record in database"""
        try:
            result = db.execute_insert_returning("""
                INSERT INTO processing_audit_log 
                (job_id, action_type, table_name, changes_summary, 
                 automated, confidence_score)
                VALUES (%s, 'review', 'manual_review_queue', %s, false, %s)
                RETURNING id
            """, (
                job_id,
                json.dumps({
                    "review_type": review_type.value,
                    "priority": priority.value,
                    "reasons": reasons,
                    "status": ReviewStatus.PENDING.value
                }),
                priority.value / 10.0  # Convert priority to confidence-like score
            ))
            
            return result['id'] if result else None
            
        except Exception as e:
            logger.error(f"Error creating review record: {e}")
            return None
    
    def _estimate_review_time(self, review_type: ReviewType, priority: ReviewPriority) -> int:
        """Estimate review time in minutes"""
        base_times = {
            ReviewType.TRIAL_MATCHING: 10,
            ReviewType.CRITERIA_EXTRACTION: 15,
            ReviewType.METADATA_VALIDATION: 8,
            ReviewType.PROTOCOL_QUALITY: 20,
            ReviewType.BATCH_COMPLETION: 25
        }
        
        base_time = base_times.get(review_type, 15)
        
        # Adjust for priority
        if priority == ReviewPriority.CRITICAL:
            return base_time * 2  # More thorough review
        elif priority == ReviewPriority.LOW:
            return base_time // 2  # Quick verification
        
        return base_time
    
    async def _notify_reviewers(self, review_item: Dict[str, Any]):
        """Notify available reviewers about new review item"""
        try:
            # Get available reviewers (this would integrate with user management system)
            available_reviewers = await self._get_available_reviewers(review_item['review_type'])
            
            for reviewer in available_reviewers:
                # Send notification (email, Slack, etc.)
                await self._send_review_notification(reviewer, review_item)
            
        except Exception as e:
            logger.error(f"Error notifying reviewers: {e}")
    
    async def _get_available_reviewers(self, review_type: str) -> List[Dict[str, Any]]:
        """Get available reviewers for a specific review type"""
        # This would integrate with user management system
        # For now, return placeholder data
        return [
            {"id": "reviewer1", "name": "Clinical Reviewer", "expertise": ["trial_matching", "criteria_extraction"]},
            {"id": "reviewer2", "name": "Data Reviewer", "expertise": ["metadata_validation", "protocol_quality"]}
        ]
    
    async def _send_review_notification(self, reviewer: Dict[str, Any], review_item: Dict[str, Any]):
        """Send notification to reviewer"""
        # Placeholder for notification system integration
        logger.info(f"Notifying {reviewer['name']} about review {review_item['review_id']}")
    
    # =====================================================
    # Review Assignment and Management
    # =====================================================
    
    async def assign_review(self, review_id: int, reviewer_id: str) -> Dict[str, Any]:
        """Assign review to a specific reviewer"""
        try:
            # Update review record
            db.execute_update("""
                UPDATE processing_audit_log 
                SET changes_summary = jsonb_set(
                    changes_summary,
                    '{assigned_to}',
                    to_jsonb(%s::text)
                ),
                changes_summary = jsonb_set(
                    changes_summary,
                    '{status}',
                    to_jsonb(%s::text)
                ),
                changes_summary = jsonb_set(
                    changes_summary,
                    '{assigned_at}',
                    to_jsonb(%s::text)
                )
                WHERE id = %s AND action_type = 'review'
            """, (reviewer_id, ReviewStatus.IN_REVIEW.value, datetime.now().isoformat(), review_id))
            
            # Update in-memory assignment
            if reviewer_id not in self.reviewer_assignments:
                self.reviewer_assignments[reviewer_id] = set()
            self.reviewer_assignments[reviewer_id].add(review_id)
            
            # Remove from queue
            self._remove_from_queue(review_id)
            
            return {
                "success": True,
                "review_id": review_id,
                "assigned_to": reviewer_id,
                "status": ReviewStatus.IN_REVIEW.value
            }
            
        except Exception as e:
            logger.error(f"Error assigning review: {e}")
            return {"success": False, "error": str(e)}
    
    def _remove_from_queue(self, review_id: int):
        """Remove review item from queue"""
        for priority_queue in self.review_queue.values():
            self.review_queue[priority_queue] = [
                item for item in priority_queue if item['review_id'] != review_id
            ]
    
    async def complete_review(self, review_id: int, reviewer_id: str, 
                            decision: ReviewStatus, notes: str = None) -> Dict[str, Any]:
        """Complete a review with decision and notes"""
        try:
            # Update review record
            db.execute_update("""
                UPDATE processing_audit_log 
                SET changes_summary = jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            changes_summary,
                            '{status}',
                            to_jsonb(%s::text)
                        ),
                        '{completed_at}',
                        to_jsonb(%s::text)
                    ),
                    '{reviewer_notes}',
                    to_jsonb(%s::text)
                )
                WHERE id = %s AND action_type = 'review'
            """, (decision.value, datetime.now().isoformat(), notes or "", review_id))
            
            # Remove from assignments
            if reviewer_id in self.reviewer_assignments:
                self.reviewer_assignments[reviewer_id].discard(review_id)
            
            # Update metrics
            await self._update_review_metrics(decision)
            
            # Handle decision-specific actions
            await self._handle_review_decision(review_id, decision, notes)
            
            return {
                "success": True,
                "review_id": review_id,
                "decision": decision.value,
                "completed_by": reviewer_id
            }
            
        except Exception as e:
            logger.error(f"Error completing review: {e}")
            return {"success": False, "error": str(e)}
    
    async def _update_review_metrics(self, decision: ReviewStatus):
        """Update review performance metrics"""
        try:
            self.review_metrics["total_reviews"] += 1
            
            if decision == ReviewStatus.APPROVED:
                # Update approval rate
                current_rate = self.review_metrics["approval_rate"]
                total_reviews = self.review_metrics["total_reviews"]
                
                # Calculate new approval rate
                approved_count = int(current_rate * (total_reviews - 1)) + 1
                self.review_metrics["approval_rate"] = approved_count / total_reviews
            
        except Exception as e:
            logger.error(f"Error updating review metrics: {e}")
    
    async def _handle_review_decision(self, review_id: int, decision: ReviewStatus, notes: str):
        """Handle actions based on review decision"""
        try:
            # Get review details
            review_data = db.execute_query("""
                SELECT job_id, changes_summary FROM processing_audit_log 
                WHERE id = %s AND action_type = 'review'
            """, (review_id,))
            
            if not review_data:
                return
            
            job_id = review_data[0]['job_id']
            review_summary = review_data[0]['changes_summary']
            
            if decision == ReviewStatus.APPROVED:
                # Auto-approve related trial matches
                await self._auto_approve_trial_match(job_id)
                
            elif decision == ReviewStatus.REJECTED:
                # Mark job for reprocessing or escalation
                await self._handle_rejection(job_id, notes)
                
            elif decision == ReviewStatus.NEEDS_MORE_INFO:
                # Request additional information
                await self._request_additional_info(job_id, notes)
                
            elif decision == ReviewStatus.ESCALATED:
                # Escalate to higher-level reviewer
                await self._escalate_review(job_id, review_id, notes)
            
        except Exception as e:
            logger.error(f"Error handling review decision: {e}")
    
    async def _auto_approve_trial_match(self, job_id: str):
        """Auto-approve trial match based on review approval"""
        try:
            db.execute_update("""
                UPDATE intelligent_trial_matches 
                SET review_status = 'approved', 
                    reviewed_at = CURRENT_TIMESTAMP,
                    review_notes = 'Auto-approved based on manual review'
                WHERE processing_job_id = %s AND review_status = 'pending'
            """, (job_id,))
            
        except Exception as e:
            logger.error(f"Error auto-approving trial match: {e}")
    
    async def _handle_rejection(self, job_id: str, notes: str):
        """Handle rejection by marking for reprocessing"""
        try:
            db.execute_update("""
                UPDATE protocol_processing_jobs 
                SET status = 'needs_reprocessing',
                    error_messages = array_append(
                        COALESCE(error_messages, ARRAY[]::text[]), 
                        %s
                    )
                WHERE job_id = %s
            """, (f"Rejected in manual review: {notes}", job_id))
            
        except Exception as e:
            logger.error(f"Error handling rejection: {e}")
    
    async def _request_additional_info(self, job_id: str, notes: str):
        """Request additional information for review"""
        try:
            # This would integrate with notification system
            logger.info(f"Requesting additional info for job {job_id}: {notes}")
            
        except Exception as e:
            logger.error(f"Error requesting additional info: {e}")
    
    async def _escalate_review(self, job_id: str, review_id: int, notes: str):
        """Escalate review to higher-level reviewer"""
        try:
            # Create escalated review record
            await self._add_to_review_queue(
                job_id, ReviewType.PROTOCOL_QUALITY, ReviewPriority.CRITICAL,
                [f"Escalated from review {review_id}: {notes}"]
            )
            
        except Exception as e:
            logger.error(f"Error escalating review: {e}")
    
    # =====================================================
    # Review Queue and Status APIs
    # =====================================================
    
    async def get_review_queue(self, reviewer_id: str = None, priority: ReviewPriority = None) -> Dict[str, Any]:
        """Get current review queue with filtering options"""
        try:
            queue_data = {}
            
            # Get specified priority or all priorities
            priorities_to_check = [priority] if priority else list(ReviewPriority)
            
            for prio in priorities_to_check:
                queue_items = []
                
                for item in self.review_queue.get(prio, []):
                    # Filter by reviewer if specified
                    if reviewer_id:
                        # Check if reviewer can handle this type of review
                        available_reviewers = await self._get_available_reviewers(item['review_type'])
                        reviewer_ids = [r['id'] for r in available_reviewers]
                        
                        if reviewer_id not in reviewer_ids:
                            continue
                    
                    # Get additional details from database
                    review_details = await self._get_review_details(item['review_id'])
                    queue_items.append({
                        **item,
                        **review_details
                    })
                
                if queue_items:
                    queue_data[prio.name] = queue_items
            
            # Get queue statistics
            total_items = sum(len(items) for items in queue_data.values())
            avg_wait_time = await self._calculate_average_wait_time()
            
            return {
                "success": True,
                "queue": queue_data,
                "statistics": {
                    "total_items": total_items,
                    "average_wait_time_minutes": avg_wait_time,
                    "queue_by_priority": {
                        prio.name: len(self.review_queue.get(prio, []))
                        for prio in ReviewPriority
                    }
                },
                "retrieved_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting review queue: {e}")
            return {"success": False, "error": str(e)}
    
    async def _get_review_details(self, review_id: int) -> Dict[str, Any]:
        """Get additional review details from database"""
        try:
            details = db.execute_query("""
                SELECT pal.*, ppj.file_paths, ppj.processing_options
                FROM processing_audit_log pal
                LEFT JOIN protocol_processing_jobs ppj ON pal.job_id = ppj.job_id
                WHERE pal.id = %s AND pal.action_type = 'review'
            """, (review_id,))
            
            if details:
                detail = details[0]
                return {
                    "job_details": {
                        "file_paths": detail.get('file_paths', []),
                        "processing_options": detail.get('processing_options', {})
                    },
                    "created_at": detail['created_at'].isoformat(),
                    "changes_summary": detail.get('changes_summary', {})
                }
            
        except Exception as e:
            logger.error(f"Error getting review details: {e}")
        
        return {}
    
    async def _calculate_average_wait_time(self) -> float:
        """Calculate average wait time for items in queue"""
        try:
            total_wait_time = 0
            total_items = 0
            current_time = datetime.now()
            
            for priority_queue in self.review_queue.values():
                for item in priority_queue:
                    wait_time = (current_time - item['created_at']).total_seconds() / 60  # Minutes
                    total_wait_time += wait_time
                    total_items += 1
            
            return total_wait_time / total_items if total_items > 0 else 0.0
            
        except Exception as e:
            logger.error(f"Error calculating average wait time: {e}")
            return 0.0
    
    async def get_reviewer_dashboard(self, reviewer_id: str) -> Dict[str, Any]:
        """Get comprehensive dashboard for a reviewer"""
        try:
            # Get assigned reviews
            assigned_reviews = self.reviewer_assignments.get(reviewer_id, set())
            
            # Get review details for assigned reviews
            assigned_details = []
            for review_id in assigned_reviews:
                details = await self._get_review_details(review_id)
                assigned_details.append({
                    "review_id": review_id,
                    **details
                })
            
            # Get available reviews in queue
            available_queue = await self.get_review_queue(reviewer_id=reviewer_id)
            
            # Get reviewer statistics
            reviewer_stats = await self._get_reviewer_statistics(reviewer_id)
            
            return {
                "success": True,
                "reviewer_id": reviewer_id,
                "assigned_reviews": assigned_details,
                "available_queue": available_queue.get('queue', {}),
                "statistics": reviewer_stats,
                "dashboard_updated": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting reviewer dashboard: {e}")
            return {"success": False, "error": str(e)}
    
    async def _get_reviewer_statistics(self, reviewer_id: str) -> Dict[str, Any]:
        """Get statistics for a specific reviewer"""
        try:
            # Get completed reviews for this reviewer
            completed_reviews = db.execute_query("""
                SELECT 
                    COUNT(*) as total_completed,
                    AVG(EXTRACT(EPOCH FROM (
                        (changes_summary->>'completed_at')::timestamp - 
                        (changes_summary->>'assigned_at')::timestamp
                    ))) as avg_review_time_seconds,
                    COUNT(CASE WHEN changes_summary->>'status' = 'approved' THEN 1 END) as approved_count
                FROM processing_audit_log 
                WHERE action_type = 'review' 
                AND changes_summary->>'assigned_to' = %s
                AND changes_summary->>'completed_at' IS NOT NULL
                AND created_at >= NOW() - INTERVAL '30 days'
            """, (reviewer_id,))
            
            if completed_reviews:
                stats = completed_reviews[0]
                total = stats['total_completed'] or 0
                avg_time = stats['avg_review_time_seconds'] or 0
                approved = stats['approved_count'] or 0
                
                return {
                    "total_completed_30_days": total,
                    "average_review_time_minutes": avg_time / 60 if avg_time else 0,
                    "approval_rate": (approved / total) if total > 0 else 0,
                    "currently_assigned": len(self.reviewer_assignments.get(reviewer_id, set()))
                }
            
        except Exception as e:
            logger.error(f"Error getting reviewer statistics: {e}")
        
        return {
            "total_completed_30_days": 0,
            "average_review_time_minutes": 0,
            "approval_rate": 0,
            "currently_assigned": len(self.reviewer_assignments.get(reviewer_id, set()))
        }


# Global instance
progress_tracking_service = ProgressTrackingService()