"""
Centralized configuration for conversation state mappings.

This module provides shared state mappings and configurations used across
the conversation system to avoid duplication and ensure consistency.
"""

from models.schemas import ConversationState


class StateConfig:
    """Centralized configuration for conversation states and mappings"""
    
    # Question types to conversation states mapping
    QUESTION_TO_STATE_MAP = {
        "age": ConversationState.AWAITING_AGE,
        "diagnosis": ConversationState.AWAITING_DIAGNOSIS,
        "medications": ConversationState.AWAITING_MEDICATIONS,
        "flares": ConversationState.AWAITING_FLARES,
    }
    
    # Conversation states to expected intent types mapping (using string values)
    STATE_TO_INTENT_MAP = {
        ConversationState.AWAITING_AGE.value: "age_answer",
        ConversationState.AWAITING_DIAGNOSIS.value: "yes_no_answer",
        ConversationState.AWAITING_MEDICATIONS.value: "yes_no_answer",
        ConversationState.AWAITING_FLARES.value: "number_answer",
        ConversationState.AWAITING_CONDITION.value: "condition_answer",
        ConversationState.AWAITING_LOCATION.value: "location_answer",
    }
    
    # Valid intents for each conversation state (using string values)
    VALID_INTENTS_BY_STATE = {
        ConversationState.IDLE: [
            "eligibility",
            "trial_search", 
            "trial_info_request",
            "personal_condition",
            "location_search",
            "trial_interest",
            "general_query",
        ],
        ConversationState.PRESCREENING_ACTIVE: [
            "age_answer",
            "yes_no_answer",
            "number_answer",
            "condition_answer",
            "location_answer",
            "question_during_prescreening",
            "general_query",
        ],
        ConversationState.AWAITING_AGE: [
            "age_answer",
            "number_answer",
            "general_query",
        ],
        ConversationState.AWAITING_DIAGNOSIS: [
            "yes_no_answer",
            "general_query",
        ],
        ConversationState.AWAITING_MEDICATIONS: [
            "yes_no_answer",
            "medication_answer",
            "general_query",
        ],
        ConversationState.AWAITING_FLARES: [
            "number_answer",
            "general_query",
        ],
        ConversationState.AWAITING_LOCATION: [
            "location_answer",
            "location_search",
            "general_query",
        ],
        ConversationState.AWAITING_CONDITION: [
            "condition_answer",
            "personal_condition",
            "general_query",
        ],
        ConversationState.COMPLETED: [
            "trial_search",
            "eligibility",
            "trial_info_request",
            "general_query",
        ],
    }
    
    @classmethod
    def get_state_for_question(cls, question_key: str, fallback_state: ConversationState = None) -> ConversationState:
        """Get the appropriate state for a question type"""
        if not question_key:
            return fallback_state
            
        for key, state in cls.QUESTION_TO_STATE_MAP.items():
            if key in question_key.lower():
                return state
                
        return fallback_state or ConversationState.PRESCREENING_ACTIVE
    
    @classmethod
    def get_expected_intent_for_state(cls, state: str) -> str:
        """Get the expected intent type for a conversation state"""
        return cls.STATE_TO_INTENT_MAP.get(state)
    
    @classmethod
    def get_valid_intents_for_state(cls, state: ConversationState) -> list:
        """Get list of valid intent types for a given state"""
        return cls.VALID_INTENTS_BY_STATE.get(state, ["general_query"])


# Singleton instance for easy access
state_config = StateConfig()