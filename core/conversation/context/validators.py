"""
Context validation utilities.

This module provides validation functions to ensure context data
integrity and consistency throughout the conversation flow.
"""

from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timezone
from enum import Enum

from models.schemas import ConversationState


class ValidationError:
    """Represents a context validation error"""
    
    def __init__(self, field: str, message: str, severity: str = "error"):
        self.field = field
        self.message = message
        self.severity = severity  # "error", "warning", "info"
        
    def __repr__(self):
        return f"ValidationError({self.field}: {self.message})"


class ContextValidator:
    """Validates conversation context for consistency and completeness"""
    
    # Required fields for different states
    REQUIRED_FIELDS = {
        ConversationState.PRESCREENING_ACTIVE: {
            "session_id", "conversation_state"
        },
        ConversationState.AWAITING_AGE: {
            "session_id", "conversation_state", "current_question_key"
        },
        ConversationState.AWAITING_DIAGNOSIS: {
            "session_id", "conversation_state", "current_question_key"
        },
        ConversationState.AWAITING_LOCATION: {
            "session_id", "conversation_state"
        },
        ConversationState.AWAITING_CONDITION: {
            "session_id", "conversation_state"
        },
    }
    
    # Fields that should be preserved across updates
    PRESERVED_FIELDS = {
        "session_id", "user_id", "created_at", 
        "mentioned_conditions", "mentioned_locations",
        "last_shown_trials", "conversation_history"
    }
    
    @classmethod
    def validate_context(cls, context: Dict[str, Any], 
                        state: Optional[ConversationState] = None) -> List[ValidationError]:
        """
        Validate context data.
        
        Args:
            context: Context dictionary to validate
            state: Current conversation state
            
        Returns:
            List of validation errors
        """
        errors = []
        
        # Basic validation
        errors.extend(cls._validate_basic_fields(context))
        
        # State-specific validation
        if state:
            errors.extend(cls._validate_state_requirements(context, state))
        
        # Prescreening validation
        if context.get("conversation_state") in [
            "PRESCREENING_ACTIVE", "AWAITING_AGE", "AWAITING_DIAGNOSIS",
            "AWAITING_MEDICATIONS", "AWAITING_FLARES"
        ]:
            errors.extend(cls._validate_prescreening_context(context))
        
        # Data consistency validation
        errors.extend(cls._validate_data_consistency(context))
        
        return errors
    
    @classmethod
    def _validate_basic_fields(cls, context: Dict[str, Any]) -> List[ValidationError]:
        """Validate basic required fields"""
        errors = []
        
        # Session ID is always required
        if not context.get("session_id"):
            errors.append(ValidationError("session_id", "Session ID is required"))
        
        # Validate timestamps
        for field in ["created_at", "last_updated"]:
            if field in context and context[field]:
                if not cls._is_valid_timestamp(context[field]):
                    errors.append(ValidationError(
                        field, 
                        f"Invalid timestamp format for {field}"
                    ))
        
        # Validate lists and sets
        list_fields = ["remaining_questions", "last_shown_trials", 
                      "mentioned_conditions", "mentioned_locations"]
        for field in list_fields:
            if field in context and not isinstance(context[field], (list, set)):
                errors.append(ValidationError(
                    field,
                    f"{field} must be a list or set"
                ))
        
        return errors
    
    @classmethod
    def _validate_state_requirements(cls, context: Dict[str, Any], 
                                   state: ConversationState) -> List[ValidationError]:
        """Validate state-specific requirements"""
        errors = []
        
        required = cls.REQUIRED_FIELDS.get(state, set())
        for field in required:
            if field not in context or context[field] is None:
                errors.append(ValidationError(
                    field,
                    f"{field} is required for state {state.value}"
                ))
        
        return errors
    
    @classmethod
    def _validate_prescreening_context(cls, context: Dict[str, Any]) -> List[ValidationError]:
        """Validate prescreening-specific context"""
        errors = []
        
        # Must have either trial_id or condition+location
        has_trial = context.get("trial_id") is not None
        has_context = (
            context.get("focus_condition") is not None and 
            context.get("focus_location") is not None
        )
        
        if not has_trial and not has_context:
            errors.append(ValidationError(
                "prescreening",
                "Prescreening requires either trial_id or condition+location",
                severity="warning"
            ))
        
        # Validate collected data
        collected = context.get("collected_data", {})
        if collected:
            # Validate age if present
            if "age" in collected:
                age = collected["age"]
                if not isinstance(age, (int, float)) or age < 0 or age > 150:
                    errors.append(ValidationError(
                        "collected_data.age",
                        "Invalid age value"
                    ))
            
            # Validate boolean fields
            for field in ["diagnosis_confirmed", "taking_medications"]:
                if field in collected and not isinstance(collected[field], bool):
                    errors.append(ValidationError(
                        f"collected_data.{field}",
                        f"{field} must be boolean"
                    ))
        
        return errors
    
    @classmethod
    def _validate_data_consistency(cls, context: Dict[str, Any]) -> List[ValidationError]:
        """Validate internal data consistency"""
        errors = []
        
        # Check state_data consistency
        state_data = context.get("state_data", {})
        if state_data and not isinstance(state_data, dict):
            errors.append(ValidationError(
                "state_data",
                "state_data must be a dictionary"
            ))
        
        # Validate question consistency
        current_question = context.get("current_question_key")
        remaining = context.get("remaining_questions", [])
        
        if current_question and remaining and current_question not in remaining:
            errors.append(ValidationError(
                "current_question_key",
                "Current question not in remaining questions",
                severity="warning"
            ))
        
        # Validate trial references
        trial_id = context.get("trial_id")
        trial_name = context.get("trial_name")
        
        if trial_id and not trial_name:
            errors.append(ValidationError(
                "trial_name",
                "Trial name missing for trial_id",
                severity="warning"
            ))
        
        return errors
    
    @classmethod
    def _is_valid_timestamp(cls, timestamp: Any) -> bool:
        """Check if timestamp is valid"""
        if isinstance(timestamp, datetime):
            return True
        
        if isinstance(timestamp, str):
            try:
                datetime.fromisoformat(timestamp)
                return True
            except:
                return False
        
        return False
    
    @classmethod
    def sanitize_context(cls, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize context data by removing invalid fields and fixing types.
        
        Args:
            context: Context to sanitize
            
        Returns:
            Sanitized context
        """
        sanitized = {}
        
        for key, value in context.items():
            # Skip None values
            if value is None:
                continue
            
            # Convert sets to lists for JSON serialization
            if isinstance(value, set):
                sanitized[key] = list(value)
            
            # Ensure timestamps are strings
            elif isinstance(value, datetime):
                sanitized[key] = value.isoformat()
            
            # Recursively sanitize nested dicts
            elif isinstance(value, dict):
                sanitized[key] = cls.sanitize_context(value)
            
            # Keep other values as-is
            else:
                sanitized[key] = value
        
        return sanitized
    
    @classmethod
    def merge_contexts(cls, base: Dict[str, Any], 
                      updates: Dict[str, Any],
                      preserve_fields: Optional[Set[str]] = None) -> Dict[str, Any]:
        """
        Merge two contexts while preserving important fields.
        
        Args:
            base: Base context
            updates: Updates to apply
            preserve_fields: Additional fields to preserve
            
        Returns:
            Merged context
        """
        merged = base.copy()
        preserve = cls.PRESERVED_FIELDS.copy()
        
        if preserve_fields:
            preserve.update(preserve_fields)
        
        for key, value in updates.items():
            if key in preserve and key in base:
                # Special handling for collections
                if key in ["mentioned_conditions", "mentioned_locations"]:
                    # Merge sets/lists
                    existing = set(base.get(key, []))
                    new = set(value) if isinstance(value, (list, set)) else {value}
                    merged[key] = list(existing.union(new))
                    
                elif key == "last_shown_trials":
                    # Append new trials
                    existing = base.get(key, [])
                    new = value if isinstance(value, list) else [value]
                    merged[key] = existing + [t for t in new if t not in existing]
                    
                elif key == "conversation_history":
                    # Append new history
                    existing = base.get(key, [])
                    new = value if isinstance(value, list) else [value]
                    merged[key] = existing + new
                    
                else:
                    # Keep existing value for other preserved fields
                    pass
            else:
                # Update non-preserved fields
                merged[key] = value
        
        return merged