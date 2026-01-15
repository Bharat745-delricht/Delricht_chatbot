"""
Conversation flow controller that orchestrates state transitions and flow logic.

This module coordinates between state management, transition rules, and 
conversation handlers to control the overall flow of conversations.
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import logging

from models.schemas import ConversationState, PrescreeningSession
from .state_manager import ConversationStateManager
from .transitions import TransitionRules, StateRecovery

logger = logging.getLogger(__name__)


class ConversationFlowController:
    """
    Controls conversation flow by coordinating state management and business logic.
    
    This class acts as the main orchestrator for conversation flow, managing
    state transitions, validation, and flow control.
    """
    
    def __init__(self, state_manager: Optional[ConversationStateManager] = None):
        self.state_manager = state_manager or ConversationStateManager()
        self.flow_metadata: Dict[str, Any] = {}
        
    def handle_intent(self, intent_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle an intent and determine appropriate state transition.
        
        Args:
            intent_type: The classified intent type
            context: Current conversation context
            
        Returns:
            Dict containing next_state, transition_valid, and any actions
        """
        current_state = self.state_manager.current_state
        
        # Check if intent is valid for current state
        if not self.state_manager.is_intent_valid_for_state(intent_type):
            logger.warning(
                f"Intent {intent_type} not expected in state {current_state}. "
                "Attempting recovery."
            )
            return self._handle_unexpected_intent(intent_type, context)
        
        # Determine next state based on intent and current state
        next_state = self._determine_next_state(intent_type, context)
        
        if next_state and next_state != current_state:
            # Attempt transition
            reason = TransitionRules.get_transition_reason(
                current_state, next_state, intent_type
            )
            
            success = self.state_manager.transition_to(
                next_state, 
                reason=reason,
                metadata={'intent': intent_type, 'context': context}
            )
            
            if success:
                return {
                    'success': True,
                    'previous_state': current_state.value,
                    'current_state': next_state.value,
                    'transition_reason': reason,
                    'actions': self._get_transition_actions(current_state, next_state, context)
                }
            else:
                return {
                    'success': False,
                    'error': 'Invalid state transition',
                    'current_state': current_state.value,
                    'attempted_state': next_state.value
                }
        
        # No state change needed
        return {
            'success': True,
            'current_state': current_state.value,
            'no_transition': True,
            'actions': []
        }
    
    def _determine_next_state(self, intent_type: str, 
                            context: Dict[str, Any]) -> Optional[ConversationState]:
        """Determine the next state based on intent and context"""
        current_state = self.state_manager.current_state
        
        # State-specific logic for determining next state
        state_intent_map = {
            # From IDLE state
            (ConversationState.IDLE, "eligibility"): ConversationState.PRESCREENING_ACTIVE,
            (ConversationState.IDLE, "eligibility_specific_trial"): ConversationState.PRESCREENING_ACTIVE,
            (ConversationState.IDLE, "trial_search"): self._determine_search_next_state(context),
            (ConversationState.IDLE, "personal_condition"): ConversationState.AWAITING_LOCATION,
            (ConversationState.IDLE, "location_search"): ConversationState.AWAITING_CONDITION,
            
            # From PRESCREENING_ACTIVE
            (ConversationState.PRESCREENING_ACTIVE, "age_answer"): self._get_next_prescreening_state(context),
            (ConversationState.PRESCREENING_ACTIVE, "yes_no_answer"): self._get_next_prescreening_state(context),
            (ConversationState.PRESCREENING_ACTIVE, "number_answer"): self._get_next_prescreening_state(context),
            
            # From AWAITING states
            (ConversationState.AWAITING_AGE, "age_answer"): ConversationState.PRESCREENING_ACTIVE,
            (ConversationState.AWAITING_DIAGNOSIS, "yes_no_answer"): ConversationState.PRESCREENING_ACTIVE,
            (ConversationState.AWAITING_MEDICATIONS, "yes_no_answer"): ConversationState.PRESCREENING_ACTIVE,
            (ConversationState.AWAITING_MEDICATIONS, "medication_answer"): ConversationState.PRESCREENING_ACTIVE,
            (ConversationState.AWAITING_FLARES, "number_answer"): ConversationState.PRESCREENING_ACTIVE,
            (ConversationState.AWAITING_LOCATION, "location_answer"): self._determine_after_location_state(context),
            (ConversationState.AWAITING_CONDITION, "condition_answer"): self._determine_after_condition_state(context),
            
            # From COMPLETED
            (ConversationState.COMPLETED, "eligibility"): ConversationState.PRESCREENING_ACTIVE,
            (ConversationState.COMPLETED, "trial_search"): self._determine_search_next_state(context),
        }
        
        key = (current_state, intent_type)
        next_state = state_intent_map.get(key)
        
        # Handle callable next states (dynamic determination)
        if callable(next_state):
            next_state = next_state
        
        return next_state
    
    def _determine_search_next_state(self, context: Dict[str, Any]) -> ConversationState:
        """Determine next state for trial search intent"""
        has_location = context.get('location') is not None
        has_condition = context.get('condition') is not None
        
        if not has_location and not has_condition:
            # Need to ask for either location or condition
            return ConversationState.AWAITING_LOCATION
        elif has_condition and not has_location:
            return ConversationState.AWAITING_LOCATION
        elif has_location and not has_condition:
            return ConversationState.AWAITING_CONDITION
        else:
            # Have both, stay in current state (search can proceed)
            return self.state_manager.current_state
    
    def _determine_after_location_state(self, context: Dict[str, Any]) -> ConversationState:
        """Determine state after location is provided"""
        if context.get('awaiting_prescreening'):
            return ConversationState.PRESCREENING_ACTIVE
        elif context.get('condition') is None:
            return ConversationState.AWAITING_CONDITION
        else:
            return ConversationState.IDLE
    
    def _determine_after_condition_state(self, context: Dict[str, Any]) -> ConversationState:
        """Determine state after condition is provided"""
        if context.get('awaiting_prescreening'):
            return ConversationState.PRESCREENING_ACTIVE
        elif context.get('location') is None:
            return ConversationState.AWAITING_LOCATION
        else:
            return ConversationState.IDLE
    
    def _get_next_prescreening_state(self, context: Dict[str, Any]) -> ConversationState:
        """Determine next prescreening state based on remaining questions"""
        remaining_questions = context.get('remaining_questions', [])
        
        if not remaining_questions:
            return ConversationState.COMPLETED
        
        # Map question types to states
        next_question = remaining_questions[0] if remaining_questions else None
        question_state_map = {
            'age': ConversationState.AWAITING_AGE,
            'diagnosis': ConversationState.AWAITING_DIAGNOSIS,
            'medications': ConversationState.AWAITING_MEDICATIONS,
            'flares': ConversationState.AWAITING_FLARES,
        }
        
        for key, state in question_state_map.items():
            if next_question and key in next_question:
                return state
        
        # Default to staying in prescreening active
        return ConversationState.PRESCREENING_ACTIVE
    
    def _get_transition_actions(self, from_state: ConversationState, 
                              to_state: ConversationState,
                              context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get actions to perform during state transition"""
        actions = []
        
        # Entering prescreening
        if to_state == ConversationState.PRESCREENING_ACTIVE and from_state != to_state:
            actions.append({
                'type': 'start_prescreening',
                'data': {
                    'trial_id': context.get('trial_id'),
                    'condition': context.get('condition'),
                    'location': context.get('location')
                }
            })
        
        # Completing prescreening
        elif to_state == ConversationState.COMPLETED and self.state_manager.is_in_prescreening():
            actions.append({
                'type': 'evaluate_eligibility',
                'data': context.get('collected_data', {})
            })
        
        # Abandoning prescreening
        elif from_state in [ConversationState.PRESCREENING_ACTIVE, 
                           ConversationState.AWAITING_AGE,
                           ConversationState.AWAITING_DIAGNOSIS] and to_state == ConversationState.IDLE:
            actions.append({
                'type': 'show_abandonment_message',
                'message': TransitionRules.get_abandonment_message(from_state)
            })
        
        return actions
    
    def _handle_unexpected_intent(self, intent_type: str, 
                                context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle intents that are unexpected in current state with smart recovery"""
        current_state = self.state_manager.current_state
        
        logger.info(f"Handling unexpected intent {intent_type} in state {current_state}")
        
        # Define intent categories for recovery strategy
        new_flow_intents = ["trial_search", "eligibility", "trial_info_request", "personal_condition"]
        answer_intents = ["yes_no_answer", "age_answer", "number_answer", "location_answer", "condition_answer"]
        
        # Strategy 1: User wants to start new flow - allow graceful context switch
        if intent_type in new_flow_intents:
            logger.info(f"User initiating new flow with {intent_type}, allowing context switch")
            # Reset to appropriate state if needed
            if self.state_manager.is_awaiting_input() and intent_type == "eligibility":
                # User wants to check eligibility while we're waiting for something else
                return {
                    'success': True,
                    'recovery_strategy': 'context_switch',
                    'message': 'Switching context to eligibility check',
                    'current_state': current_state.value,
                    'allow_intent': True,
                    'actions': [{'type': 'clear_awaiting_flags'}]
                }
        
        # Strategy 2: User providing answer in wrong context - try to route it properly
        if intent_type in answer_intents and self.state_manager.is_awaiting_input():
            logger.info(f"Answer intent {intent_type} in awaiting state, attempting smart routing")
            return {
                'success': True,
                'recovery_strategy': 'answer_routing',
                'current_state': current_state.value,
                'allow_intent': True,
                'actions': []
            }
        
        # Strategy 3: Always allow general queries and trial interest
        if intent_type in ["general_query", "trial_interest"]:
            return {
                'success': True,
                'recovery_strategy': 'always_allow',
                'current_state': current_state.value,
                'allow_intent': True,
                'actions': []
            }
        
        # Default: Allow but log for monitoring
        logger.info(f"Allowing unexpected intent {intent_type} to proceed with monitoring")
        return {
            'success': True,
            'recovery_strategy': 'monitored_allow',
            'warning': f'Intent {intent_type} unexpected but allowed in {current_state}',
            'current_state': current_state.value,
            'allow_intent': True,
            'actions': []
        }
    
    def can_resume(self) -> bool:
        """Check if conversation can be resumed from current state"""
        return StateRecovery.can_resume_from_state(self.state_manager.current_state)
    
    def handle_timeout(self) -> Dict[str, Any]:
        """Handle conversation timeout"""
        recovery_state = StateRecovery.get_recovery_state(
            self.state_manager.current_state, 'timeout'
        )
        
        self.state_manager.transition_to(
            recovery_state,
            reason="Conversation timeout",
            metadata={'timeout_from': self.state_manager.current_state.value}
        )
        
        return {
            'success': True,
            'action': 'timeout_recovery',
            'new_state': recovery_state.value
        }
    
    def get_state_summary(self) -> Dict[str, Any]:
        """Get current flow state summary"""
        return {
            'current_state': self.state_manager.current_state.value,
            'is_in_prescreening': self.state_manager.is_in_prescreening(),
            'is_awaiting_input': self.state_manager.is_awaiting_input(),
            'expected_intents': self.state_manager.get_expected_intents(),
            'valid_next_states': [s.value for s in self.state_manager.suggest_next_states()],
            'state_duration': self.state_manager.get_state_duration().total_seconds(),
            'can_resume': self.can_resume()
        }