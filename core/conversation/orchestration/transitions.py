"""
State transition rules and validation for conversation flow.

This module defines the business logic for state transitions, including
conditional transitions and validation rules.
"""

from typing import Dict, Any, Optional, List, Callable
from models.schemas import ConversationState
import logging

logger = logging.getLogger(__name__)


class TransitionValidator:
    """Validates state transitions based on business rules"""
    
    @staticmethod
    def validate_to_prescreening(context: Dict[str, Any]) -> bool:
        """Validate transition to prescreening state"""
        # Ensure we have either a trial_id or condition+location
        has_trial = context.get('trial_id') is not None
        has_condition_location = (
            context.get('condition') is not None and 
            context.get('location') is not None
        )
        return has_trial or has_condition_location
    
    @staticmethod
    def validate_prescreening_completion(context: Dict[str, Any]) -> bool:
        """Validate if prescreening can be completed"""
        required_data = context.get('collected_data', {})
        # Check if all required prescreening data is collected
        has_age = 'age' in required_data
        has_diagnosis = 'diagnosis_confirmed' in required_data
        remaining_questions = context.get('remaining_questions', [])
        
        return has_age and has_diagnosis and len(remaining_questions) == 0
    
    @staticmethod
    def validate_location_provided(context: Dict[str, Any]) -> bool:
        """Validate that location has been provided"""
        return context.get('location') is not None
    
    @staticmethod
    def validate_condition_provided(context: Dict[str, Any]) -> bool:
        """Validate that condition has been provided"""
        return context.get('condition') is not None


class TransitionRules:
    """Defines specific rules for state transitions"""
    
    # Transition conditions mapped to validation functions
    TRANSITION_CONDITIONS: Dict[str, Callable[[Dict[str, Any]], bool]] = {
        'has_trial_or_context': TransitionValidator.validate_to_prescreening,
        'prescreening_complete': TransitionValidator.validate_prescreening_completion,
        'location_provided': TransitionValidator.validate_location_provided,
        'condition_provided': TransitionValidator.validate_condition_provided,
    }
    
    @classmethod
    def check_transition_condition(cls, condition: str, context: Dict[str, Any]) -> bool:
        """Check if a transition condition is met"""
        validator = cls.TRANSITION_CONDITIONS.get(condition)
        if not validator:
            logger.warning(f"Unknown transition condition: {condition}")
            return True  # Allow transition if condition unknown
        
        return validator(context)
    
    @classmethod
    def get_transition_reason(cls, from_state: ConversationState, 
                            to_state: ConversationState,
                            intent_type: Optional[str] = None) -> str:
        """Generate a human-readable reason for state transition"""
        reasons = {
            (ConversationState.IDLE, ConversationState.PRESCREENING_ACTIVE): 
                "Starting prescreening flow",
            (ConversationState.IDLE, ConversationState.AWAITING_LOCATION): 
                "Requesting location for trial search",
            (ConversationState.IDLE, ConversationState.AWAITING_CONDITION): 
                "Requesting condition for trial search",
            (ConversationState.PRESCREENING_ACTIVE, ConversationState.AWAITING_AGE): 
                "Collecting age for eligibility check",
            (ConversationState.PRESCREENING_ACTIVE, ConversationState.AWAITING_DIAGNOSIS): 
                "Confirming diagnosis for eligibility",
            (ConversationState.PRESCREENING_ACTIVE, ConversationState.COMPLETED): 
                "Prescreening questions completed",
            (ConversationState.AWAITING_LOCATION, ConversationState.PRESCREENING_ACTIVE): 
                "Location provided, starting prescreening",
            (ConversationState.AWAITING_CONDITION, ConversationState.PRESCREENING_ACTIVE): 
                "Condition provided, starting prescreening",
        }
        
        key = (from_state, to_state)
        reason = reasons.get(key, f"Transition based on {intent_type or 'user action'}")
        
        return reason
    
    @classmethod
    def get_abandonment_message(cls, from_state: ConversationState) -> str:
        """Get appropriate message when abandoning current flow"""
        messages = {
            ConversationState.PRESCREENING_ACTIVE: 
                "I'll pause the eligibility check. Let me know if you'd like to continue later.",
            ConversationState.AWAITING_AGE: 
                "No problem, we can check your eligibility another time.",
            ConversationState.AWAITING_DIAGNOSIS: 
                "I understand. Feel free to ask me anything else about clinical trials.",
            ConversationState.AWAITING_MEDICATIONS: 
                "Okay, let's pause here. I'm happy to help with other questions.",
            ConversationState.AWAITING_LOCATION: 
                "Sure, let me know if you'd like to search for trials later.",
            ConversationState.AWAITING_CONDITION: 
                "No problem. I'm here whenever you'd like to explore clinical trials.",
        }
        
        return messages.get(
            from_state, 
            "I'm here to help whenever you're ready to continue."
        )
    
    @classmethod
    def requires_context_preservation(cls, transition: tuple) -> bool:
        """Check if a transition requires preserving context data"""
        # These transitions should preserve context
        preserve_context = {
            (ConversationState.PRESCREENING_ACTIVE, ConversationState.AWAITING_AGE),
            (ConversationState.PRESCREENING_ACTIVE, ConversationState.AWAITING_DIAGNOSIS),
            (ConversationState.PRESCREENING_ACTIVE, ConversationState.AWAITING_MEDICATIONS),
            (ConversationState.PRESCREENING_ACTIVE, ConversationState.AWAITING_FLARES),
            (ConversationState.AWAITING_AGE, ConversationState.PRESCREENING_ACTIVE),
            (ConversationState.AWAITING_DIAGNOSIS, ConversationState.PRESCREENING_ACTIVE),
            (ConversationState.AWAITING_MEDICATIONS, ConversationState.PRESCREENING_ACTIVE),
            (ConversationState.AWAITING_FLARES, ConversationState.PRESCREENING_ACTIVE),
        }
        
        return transition in preserve_context


class StateRecovery:
    """Handles state recovery and error conditions"""
    
    @staticmethod
    def get_recovery_state(current_state: ConversationState, 
                          error_type: str) -> ConversationState:
        """Determine appropriate recovery state after an error"""
        # Default recovery states based on error type
        recovery_map = {
            'invalid_input': current_state,  # Stay in same state
            'missing_context': ConversationState.IDLE,  # Reset to idle
            'system_error': ConversationState.IDLE,  # Reset to idle
            'timeout': ConversationState.IDLE,  # Reset to idle
        }
        
        return recovery_map.get(error_type, ConversationState.IDLE)
    
    @staticmethod
    def can_resume_from_state(state: ConversationState) -> bool:
        """Check if conversation can be resumed from a given state"""
        # States that support resumption
        resumable_states = {
            ConversationState.PRESCREENING_ACTIVE,
            ConversationState.AWAITING_AGE,
            ConversationState.AWAITING_DIAGNOSIS,
            ConversationState.AWAITING_MEDICATIONS,
            ConversationState.AWAITING_FLARES,
            ConversationState.AWAITING_LOCATION,
            ConversationState.AWAITING_CONDITION,
        }
        
        return state in resumable_states