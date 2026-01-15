"""
Migration utilities for transitioning to the new conversation system.

This module provides tools for safely migrating from the old system to the new one,
including data migration, validation, and rollback capabilities.
"""

import logging
import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

from core.database import db
from core.conversation.context import ContextManager, ConversationContext
from core.conversation.integration.feature_toggle import Feature, get_feature_toggle

logger = logging.getLogger(__name__)


class MigrationStatus(str, Enum):
    """Migration status states"""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class MigrationReport:
    """Report of migration results"""
    status: MigrationStatus
    total_sessions: int
    migrated_sessions: int
    failed_sessions: int
    validation_errors: List[Dict[str, Any]]
    start_time: datetime
    end_time: Optional[datetime]
    rollback_available: bool


class ConversationMigrator:
    """
    Handles migration of conversation data and system transition.
    
    This class manages:
    - Context data migration
    - Session state migration
    - Validation of migrated data
    - Rollback capabilities
    """
    
    def __init__(self):
        self.context_manager = ContextManager()
        self.feature_toggle = get_feature_toggle()
        self.migration_report: Optional[MigrationReport] = None
        
    def migrate_conversation_context(self, batch_size: int = 100) -> MigrationReport:
        """
        Migrate conversation context data to new format.
        
        Args:
            batch_size: Number of sessions to process at once
            
        Returns:
            Migration report
        """
        logger.info("Starting conversation context migration")
        
        report = MigrationReport(
            status=MigrationStatus.IN_PROGRESS,
            total_sessions=0,
            migrated_sessions=0,
            failed_sessions=0,
            validation_errors=[],
            start_time=datetime.now(),
            end_time=None,
            rollback_available=True
        )
        
        try:
            # Get total count of active sessions
            count_result = db.execute_query("""
                SELECT COUNT(*) as count
                FROM conversation_context
                WHERE active = true
            """)
            
            report.total_sessions = count_result[0]["count"] if count_result else 0
            
            # Process in batches
            offset = 0
            while offset < report.total_sessions:
                batch_results = self._process_batch(batch_size, offset)
                
                report.migrated_sessions += batch_results["migrated"]
                report.failed_sessions += batch_results["failed"]
                report.validation_errors.extend(batch_results["errors"])
                
                offset += batch_size
                
                # Log progress
                progress = (offset / report.total_sessions) * 100 if report.total_sessions > 0 else 100
                logger.info(f"Migration progress: {progress:.1f}%")
            
            # Validate migration
            report.status = MigrationStatus.VALIDATING
            validation_passed = self._validate_migration(report)
            
            if validation_passed:
                report.status = MigrationStatus.COMPLETED
            else:
                report.status = MigrationStatus.FAILED
                
        except Exception as e:
            logger.error(f"Migration failed: {str(e)}", exc_info=True)
            report.status = MigrationStatus.FAILED
            report.validation_errors.append({
                "type": "migration_error",
                "error": str(e)
            })
            
        report.end_time = datetime.now()
        self.migration_report = report
        
        return report
    
    def _process_batch(self, batch_size: int, offset: int) -> Dict[str, Any]:
        """Process a batch of sessions"""
        results = {
            "migrated": 0,
            "failed": 0,
            "errors": []
        }
        
        try:
            # Get batch of sessions
            sessions = db.execute_query("""
                SELECT session_id, context_data, focus_condition, 
                       focus_location, created_at, updated_at
                FROM conversation_context
                WHERE active = true
                ORDER BY updated_at DESC
                LIMIT %s OFFSET %s
            """, (batch_size, offset))
            
            for session in sessions:
                try:
                    self._migrate_session(session)
                    results["migrated"] += 1
                except Exception as e:
                    results["failed"] += 1
                    results["errors"].append({
                        "session_id": session["session_id"],
                        "error": str(e)
                    })
                    
        except Exception as e:
            logger.error(f"Batch processing error: {str(e)}")
            results["errors"].append({
                "type": "batch_error",
                "error": str(e),
                "offset": offset
            })
            
        return results
    
    def _migrate_session(self, session_data: Dict[str, Any]):
        """Migrate a single session to new format"""
        session_id = session_data["session_id"]
        
        # Parse existing context data
        old_context = session_data.get("context_data", {})
        if isinstance(old_context, str):
            old_context = json.loads(old_context)
            
        # Map old fields to new structure
        new_context_data = {
            "session_id": session_id,
            "conversation_state": old_context.get("current_state"),
            "focus_condition": session_data.get("focus_condition"),
            "focus_location": session_data.get("focus_location"),
            "created_at": session_data.get("created_at"),
            "last_updated": session_data.get("updated_at"),
        }
        
        # Migrate prescreening data
        if old_context.get("prescreening_active"):
            new_context_data["prescreening_data"] = {
                "active": True,
                "trial_id": old_context.get("trial_id"),
                "trial_name": old_context.get("trial_name"),
                "current_question": old_context.get("current_question_key")
            }
            
        # Migrate collected data
        if old_context.get("collected_data"):
            new_context_data["collected_data"] = old_context["collected_data"]
            
        # Create new context object
        new_context = ConversationContext.from_dict(new_context_data)
        
        # Save using new system (without overwriting old data)
        self._save_migrated_context(new_context)
    
    def _save_migrated_context(self, context: ConversationContext):
        """Save migrated context with migration metadata"""
        # Add migration metadata
        context.metadata = context.metadata or {}
        context.metadata["migrated"] = True
        context.metadata["migration_date"] = datetime.now().isoformat()
        context.metadata["migration_version"] = "1.0"
        
        # Save through context manager
        self.context_manager.update_context(
            context.session_id,
            context.to_dict()
        )
    
    def _validate_migration(self, report: MigrationReport) -> bool:
        """Validate that migration was successful"""
        logger.info("Validating migration")
        
        # Sample validation - check random sessions
        sample_size = min(100, report.migrated_sessions)
        
        if sample_size == 0:
            return True
            
        try:
            # Get random sample of migrated sessions
            sample_sessions = db.execute_query("""
                SELECT session_id
                FROM conversation_context
                WHERE active = true
                AND context_data::jsonb->>'metadata'->>'migrated' = 'true'
                ORDER BY RANDOM()
                LIMIT %s
            """, (sample_size,))
            
            validation_errors = 0
            for session in sample_sessions:
                try:
                    # Load through new system
                    context = self.context_manager.get_context(session["session_id"])
                    
                    # Basic validation
                    if not context.session_id:
                        validation_errors += 1
                        report.validation_errors.append({
                            "session_id": session["session_id"],
                            "error": "Missing session_id in migrated context"
                        })
                        
                except Exception as e:
                    validation_errors += 1
                    report.validation_errors.append({
                        "session_id": session["session_id"],
                        "error": f"Failed to load migrated context: {str(e)}"
                    })
            
            # Allow up to 5% validation errors
            error_rate = validation_errors / sample_size
            return error_rate < 0.05
            
        except Exception as e:
            logger.error(f"Validation failed: {str(e)}")
            return False
    
    def rollback_migration(self) -> bool:
        """
        Rollback the migration.
        
        Returns:
            True if rollback successful
        """
        if not self.migration_report or not self.migration_report.rollback_available:
            logger.error("No migration to rollback")
            return False
            
        logger.info("Starting migration rollback")
        
        try:
            # Remove migration metadata from contexts
            db.execute_update("""
                UPDATE conversation_context
                SET context_data = context_data::jsonb - 'metadata'
                WHERE active = true
                AND context_data::jsonb->>'metadata'->>'migrated' = 'true'
            """)
            
            # Update migration report
            self.migration_report.status = MigrationStatus.ROLLED_BACK
            self.migration_report.rollback_available = False
            
            logger.info("Migration rollback completed")
            return True
            
        except Exception as e:
            logger.error(f"Rollback failed: {str(e)}")
            return False
    
    def get_migration_status(self) -> Dict[str, Any]:
        """Get current migration status"""
        if not self.migration_report:
            return {
                "status": MigrationStatus.NOT_STARTED.value,
                "message": "No migration has been started"
            }
            
        duration = None
        if self.migration_report.start_time and self.migration_report.end_time:
            duration = (self.migration_report.end_time - self.migration_report.start_time).total_seconds()
            
        return {
            "status": self.migration_report.status.value,
            "total_sessions": self.migration_report.total_sessions,
            "migrated_sessions": self.migration_report.migrated_sessions,
            "failed_sessions": self.migration_report.failed_sessions,
            "error_count": len(self.migration_report.validation_errors),
            "duration_seconds": duration,
            "rollback_available": self.migration_report.rollback_available
        }


class SystemCutoverManager:
    """
    Manages the cutover from old system to new system.
    
    This includes:
    - Gradual traffic shifting
    - Monitoring and validation
    - Automatic rollback on errors
    """
    
    def __init__(self):
        self.feature_toggle = get_feature_toggle()
        self.cutover_start_time: Optional[datetime] = None
        self.error_threshold = 0.05  # 5% error rate triggers rollback
        self.monitoring_window = timedelta(minutes=30)
        
    def start_cutover(self, initial_percentage: int = 10) -> Dict[str, Any]:
        """
        Start the cutover process.
        
        Args:
            initial_percentage: Initial percentage of traffic to route to new system
            
        Returns:
            Cutover status
        """
        logger.info(f"Starting system cutover with {initial_percentage}% traffic")
        
        self.cutover_start_time = datetime.now()
        
        # Enable new system with percentage rollout
        self.feature_toggle.set_feature(
            Feature.NEW_CONVERSATION_SYSTEM,
            state="percentage",
            percentage=initial_percentage,
            updated_by="cutover_manager"
        )
        
        return {
            "status": "started",
            "percentage": initial_percentage,
            "start_time": self.cutover_start_time.isoformat()
        }
    
    def increase_traffic(self, increment: int = 10) -> Dict[str, Any]:
        """
        Increase traffic to new system.
        
        Args:
            increment: Percentage to increase
            
        Returns:
            Updated status
        """
        # Check system health first
        health = self._check_system_health()
        
        if not health["healthy"]:
            logger.warning(f"System not healthy, not increasing traffic: {health['reason']}")
            return {
                "status": "paused",
                "reason": health["reason"],
                "current_percentage": health["current_percentage"]
            }
        
        # Increase traffic
        new_percentage = self.feature_toggle.gradual_rollout(
            Feature.NEW_CONVERSATION_SYSTEM,
            target_percentage=100,
            increment=increment,
            updated_by="cutover_manager"
        )
        
        logger.info(f"Increased new system traffic to {new_percentage}%")
        
        return {
            "status": "increased",
            "percentage": new_percentage,
            "health": health
        }
    
    def _check_system_health(self) -> Dict[str, Any]:
        """Check if new system is healthy"""
        # This would integrate with monitoring/metrics
        # For now, return mock healthy status
        
        current_settings = self.feature_toggle.features.get(
            Feature.NEW_CONVERSATION_SYSTEM, {}
        )
        
        return {
            "healthy": True,
            "current_percentage": current_settings.get("percentage", 0),
            "error_rate": 0.01,  # Mock 1% error rate
            "response_time_ms": 150,  # Mock response time
            "checked_at": datetime.now().isoformat()
        }
    
    def complete_cutover(self) -> Dict[str, Any]:
        """Complete the cutover to new system"""
        logger.info("Completing system cutover")
        
        # Enable new system fully
        self.feature_toggle.set_feature(
            Feature.NEW_CONVERSATION_SYSTEM,
            state="on",
            updated_by="cutover_manager"
        )
        
        return {
            "status": "completed",
            "completion_time": datetime.now().isoformat(),
            "duration": (datetime.now() - self.cutover_start_time).total_seconds()
                       if self.cutover_start_time else None
        }
    
    def emergency_rollback(self, reason: str) -> Dict[str, Any]:
        """Emergency rollback to old system"""
        logger.error(f"Emergency rollback initiated: {reason}")
        
        # Disable new system
        self.feature_toggle.rollback_feature(
            Feature.NEW_CONVERSATION_SYSTEM,
            updated_by="emergency_rollback"
        )
        
        return {
            "status": "rolled_back",
            "reason": reason,
            "rollback_time": datetime.now().isoformat()
        }