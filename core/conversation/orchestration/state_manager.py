"""
State management for conversation flow.

This module provides a state machine implementation for managing conversation states,
transitions, and persistence. It ensures valid state transitions and maintains
conversation context throughout the interaction.
"""

from enum import Enum
from typing import Dict, Set, Optional, Any, List, Tuple
from datetime import datetime, timedelta
import json
import logging

from models.schemas import ConversationState

logger = logging.getLogger(__name__)


class StateTransitionRule:
    """Defines allowed transitions between states"""
    
    def __init__(self, from_state: ConversationState, to_state: ConversationState, 
                 condition: Optional[str] = None):
        self.from_state = from_state
        self.to_state = to_state
        self.condition = condition  # Optional condition for transition


class ConversationStateManager:
    """
    Manages conversation state transitions and persistence.
    
    This class implements a finite state machine that controls the flow of conversations,
    ensuring valid transitions and maintaining state consistency.
    """
    
    # Define all valid state transitions
    VALID_TRANSITIONS: Dict[ConversationState, Set[ConversationState]] = {
        ConversationState.IDLE: {
            ConversationState.PRESCREENING_ACTIVE,
            ConversationState.AWAITING_LOCATION,
            ConversationState.AWAITING_CONDITION,
            ConversationState.TRIALS_SHOWN,  # Direct to trials if we have all info
        },
        ConversationState.PRESCREENING_ACTIVE: {
            ConversationState.AWAITING_AGE,
            ConversationState.AWAITING_DIAGNOSIS,
            ConversationState.AWAITING_MEDICATIONS,
            ConversationState.AWAITING_FLARES,
            ConversationState.AWAITING_LOCATION,
            ConversationState.COMPLETED,
            ConversationState.IDLE,  # User can abandon prescreening
            ConversationState.TRIALS_SHOWN,  # Can show trials during prescreening
        },
        ConversationState.AWAITING_AGE: {
            ConversationState.PRESCREENING_ACTIVE,
            ConversationState.COMPLETED,
            ConversationState.IDLE,
            ConversationState.AWAITING_DIAGNOSIS,  # Direct transition to next question
        },
        ConversationState.AWAITING_DIAGNOSIS: {
            ConversationState.PRESCREENING_ACTIVE,
            ConversationState.COMPLETED,
            ConversationState.IDLE,
            ConversationState.AWAITING_MEDICATIONS,  # Direct transition
        },
        ConversationState.AWAITING_MEDICATIONS: {
            ConversationState.PRESCREENING_ACTIVE,
            ConversationState.COMPLETED,
            ConversationState.IDLE,
            ConversationState.AWAITING_FLARES,  # Direct transition
        },
        ConversationState.AWAITING_FLARES: {
            ConversationState.PRESCREENING_ACTIVE,
            ConversationState.COMPLETED,
            ConversationState.IDLE,
        },
        ConversationState.AWAITING_LOCATION: {
            ConversationState.PRESCREENING_ACTIVE,
            ConversationState.AWAITING_CONDITION,
            ConversationState.IDLE,
            ConversationState.AWAITING_CONFIRMATION,  # For trial selection
            ConversationState.AWAITING_AGE,  # Allow direct transition to prescreening
            ConversationState.TRIALS_SHOWN,  # Can show trials when we have location
        },
        ConversationState.AWAITING_CONDITION: {
            ConversationState.PRESCREENING_ACTIVE,
            ConversationState.AWAITING_LOCATION,
            ConversationState.IDLE,
            ConversationState.TRIALS_SHOWN,  # Can show trials when we have condition
        },
        ConversationState.AWAITING_CONFIRMATION: {
            ConversationState.PRESCREENING_ACTIVE,
            ConversationState.IDLE,
            ConversationState.TRIALS_SHOWN,
        },
        ConversationState.TRIALS_SHOWN: {
            ConversationState.PRESCREENING_ACTIVE,  # Start eligibility check
            ConversationState.AWAITING_CONFIRMATION,  # Select a trial
            ConversationState.IDLE,  # Go back to idle
            ConversationState.AWAITING_CONDITION,  # Need more info
            ConversationState.AWAITING_LOCATION,  # Need more info
        },
        ConversationState.COMPLETED: {
            ConversationState.IDLE,
            ConversationState.PRESCREENING_ACTIVE,  # Start new prescreening
            ConversationState.TRIALS_SHOWN,  # Show more trials
        },
    }
    
    # Intent types expected in each state
    EXPECTED_INTENTS: Dict[ConversationState, List[str]] = {
        ConversationState.IDLE: [
            "trial_search", "eligibility", "trial_info_request", 
            "personal_condition", "location_search", "trial_interest",
            "general_query"
        ],
        ConversationState.PRESCREENING_ACTIVE: [
            "age_answer", "yes_no_answer", "number_answer", 
            "condition_answer", "location_answer", "medication_answer",
            "general_query", "trial_search", "trial_info_request"  # Allow info requests
        ],
        ConversationState.AWAITING_AGE: ["age_answer", "number_answer", "general_query"],
        ConversationState.AWAITING_DIAGNOSIS: ["yes_no_answer", "general_query"],
        ConversationState.AWAITING_MEDICATIONS: ["yes_no_answer", "medication_answer", "general_query"],
        ConversationState.AWAITING_FLARES: ["number_answer", "general_query"],
        ConversationState.AWAITING_LOCATION: [
            "location_answer", "location_search", "general_query",
            "trial_search", "trial_info_request", "eligibility",  # Allow these during location wait
            "personal_condition", "trial_interest", "yes_no_answer"  # Allow context switches
        ],
        ConversationState.AWAITING_CONDITION: [
            "condition_answer", "personal_condition", "general_query",
            "trial_search", "trial_info_request", "eligibility"  # Allow these
        ],
        ConversationState.AWAITING_CONFIRMATION: [
            "yes_no_answer", "trial_info_request", "eligibility",
            "general_query", "number_answer"  # For selecting by number
        ],
        ConversationState.TRIALS_SHOWN: [
            "trial_info_request", "eligibility", "trial_search",
            "yes_no_answer", "number_answer", "general_query"
        ],
        ConversationState.COMPLETED: [
            "trial_search", "eligibility", "trial_info_request",
            "general_query", "trial_interest"
        ],
    }
    
    def __init__(self):
        self.state_history: List[Tuple[ConversationState, datetime]] = []
        self.current_state: ConversationState = ConversationState.IDLE
        self.state_data: Dict[str, Any] = {}
        self.state_entered_at: datetime = datetime.utcnow()
        
    def can_transition_to(self, target_state: ConversationState) -> bool:
        """Check if transition to target state is valid from current state"""
        valid_targets = self.VALID_TRANSITIONS.get(self.current_state, set())
        return target_state in valid_targets
    
    def transition_to(self, target_state: ConversationState, 
                     reason: Optional[str] = None,
                     metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Attempt to transition to a new state.
        
        Args:
            target_state: The state to transition to
            reason: Optional reason for the transition
            metadata: Optional metadata about the transition
            
        Returns:
            bool: True if transition was successful, False otherwise
        """
        if not self.can_transition_to(target_state):
            logger.warning(
                f"Invalid state transition attempted: {self.current_state} -> {target_state}"
            )
            return False
        
        # Record transition in history
        self.state_history.append((self.current_state, self.state_entered_at))
        
        # Update state
        old_state = self.current_state
        self.current_state = target_state
        self.state_entered_at = datetime.utcnow()
        
        # Log transition
        logger.info(
            f"State transition: {old_state} -> {target_state}",
            extra={
                "reason": reason,
                "metadata": metadata,
                "session_duration": (datetime.utcnow() - self.state_entered_at).total_seconds()
            }
        )
        
        return True
    
    def get_expected_intents(self) -> List[str]:
        """Get list of expected intent types for current state"""
        return self.EXPECTED_INTENTS.get(self.current_state, [])
    
    def is_intent_valid_for_state(self, intent_type: str) -> bool:
        """Check if an intent type is valid for the current state"""
        expected = self.get_expected_intents()
        # Allow general_query in any state
        return intent_type in expected or intent_type == "general_query"
    
    def update_state_data(self, key: str, value: Any):
        """Update state-specific data"""
        self.state_data[key] = value
    
    def set_state_data(self, key: str, value: Any):
        """Set state-specific data (alias for update_state_data)"""
        self.state_data[key] = value
        
    def get_state_data(self, key: str, default: Any = None) -> Any:
        """Get state-specific data"""
        return self.state_data.get(key, default)
    
    def clear_state_data(self):
        """Clear all state data"""
        self.state_data = {}
        
    def get_state_duration(self) -> timedelta:
        """Get duration in current state"""
        return datetime.utcnow() - self.state_entered_at
    
    def get_state_history(self) -> List[Dict[str, Any]]:
        """Get formatted state history"""
        history = []
        for state, entered_at in self.state_history:
            history.append({
                "state": state.value,
                "entered_at": entered_at.isoformat(),
            })
        # Add current state
        history.append({
            "state": self.current_state.value,
            "entered_at": self.state_entered_at.isoformat(),
            "current": True
        })
        return history
    
    def reset(self):
        """Reset state machine to initial state"""
        self.current_state = ConversationState.IDLE
        self.state_history = []
        self.state_data = {}
        self.state_entered_at = datetime.utcnow()
        
    def serialize(self) -> Dict[str, Any]:
        """Serialize state for persistence"""
        return {
            "current_state": self.current_state.value,
            "state_data": self.state_data,
            "state_entered_at": self.state_entered_at.isoformat(),
            "state_history": [
                {"state": state.value, "entered_at": entered.isoformat()}
                for state, entered in self.state_history
            ]
        }
    
    @classmethod
    def deserialize(cls, data: Dict[str, Any]) -> 'ConversationStateManager':
        """Deserialize state from persistence"""
        manager = cls()
        manager.current_state = ConversationState(data["current_state"])
        manager.state_data = data.get("state_data", {})
        manager.state_entered_at = datetime.fromisoformat(data["state_entered_at"])
        
        # Restore history
        manager.state_history = []
        for item in data.get("state_history", []):
            state = ConversationState(item["state"])
            entered = datetime.fromisoformat(item["entered_at"])
            manager.state_history.append((state, entered))
            
        return manager
    
    def suggest_next_states(self) -> List[ConversationState]:
        """Suggest valid next states based on current state"""
        return list(self.VALID_TRANSITIONS.get(self.current_state, set()))
    
    def is_in_prescreening(self) -> bool:
        """Check if currently in any prescreening state"""
        prescreening_states = {
            ConversationState.PRESCREENING_ACTIVE,
            ConversationState.AWAITING_AGE,
            ConversationState.AWAITING_DIAGNOSIS,
            ConversationState.AWAITING_MEDICATIONS,
            ConversationState.AWAITING_FLARES,
        }
        return self.current_state in prescreening_states
    
    def is_awaiting_input(self) -> bool:
        """Check if currently awaiting user input"""
        awaiting_states = {
            ConversationState.AWAITING_AGE,
            ConversationState.AWAITING_DIAGNOSIS,
            ConversationState.AWAITING_MEDICATIONS,
            ConversationState.AWAITING_FLARES,
            ConversationState.AWAITING_LOCATION,
            ConversationState.AWAITING_CONDITION,
        }
        return self.current_state in awaiting_states