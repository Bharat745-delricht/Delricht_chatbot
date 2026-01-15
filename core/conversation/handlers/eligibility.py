"""
Handler for eligibility check intents.

This handler processes requests to check eligibility for clinical trials,
initiating and managing the prescreening flow.
"""

import logging
from typing import Dict, Any, List, Optional

from core.conversation.handlers.base import BaseHandler, HandlerResponse
from core.conversation.understanding import IntentType, DetectedIntent, ExtractedEntity, EntityType
from core.conversation.context import ConversationContext
from core.conversation.orchestration import ConversationStateManager
from core.services.trial_search import trial_search
from core.eligibility.prescreening_controller import PrescreeningController
from models.schemas import ConversationState, PrescreeningSession
from core.conversation.state_config import state_config

logger = logging.getLogger(__name__)


class EligibilityHandler(BaseHandler):
    """
    Handles eligibility check requests.
    
    This handler manages:
    - General eligibility inquiries
    - Specific trial eligibility checks
    - Initiating prescreening flows
    - Managing eligibility context
    """
    
    def __init__(self):
        super().__init__()
        self.prescreening_controller = PrescreeningController()
    
    def can_handle(self, intent: DetectedIntent, context: ConversationContext) -> bool:
        """Check if this handler can process the intent"""
        # Handle core eligibility intents
        if intent.intent_type in [
            IntentType.ELIGIBILITY,
            IntentType.ELIGIBILITY_SPECIFIC_TRIAL,
            IntentType.ELIGIBILITY_FOLLOWUP,
            IntentType.ELIGIBILITY_FOR_SHOWN_TRIAL,
        ]:
            return True
        
        # Handle condition answers when we have shown trials and are asking about eligibility
        if (intent.intent_type == IntentType.CONDITION_ANSWER and 
            context.last_shown_trials and 
            context.state_data and 
            context.state_data.get('awaiting_condition')):
            return True
            
        return False
    
    def handle(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
              context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle eligibility check request"""
        
        if intent.intent_type == IntentType.CONDITION_ANSWER:
            # Handle condition answer in context of eligibility check
            return self._handle_condition_for_eligibility(intent, entities, context, state_manager)
        elif intent.intent_type == IntentType.ELIGIBILITY_SPECIFIC_TRIAL:
            return self._handle_specific_trial_eligibility(entities, context, state_manager)
        elif intent.intent_type == IntentType.ELIGIBILITY_FOR_SHOWN_TRIAL:
            return self._handle_shown_trial_eligibility(entities, context, state_manager)
        elif intent.intent_type == IntentType.ELIGIBILITY_FOLLOWUP:
            return self._handle_eligibility_followup(entities, context, state_manager)
        else:
            return self._handle_general_eligibility(entities, context, state_manager)
    
    def _handle_general_eligibility(self, entities: Dict[EntityType, ExtractedEntity],
                                  context: ConversationContext,
                                  state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle general eligibility inquiry"""
        
        # Check if we have context about trials
        if context.last_shown_trials:
            # User likely asking about eligibility for shown trials
            message = "I can help you check your eligibility for the trials I just showed you. "
            message += "Which trial are you interested in? You can tell me the number or the condition."
            
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "awaiting_trial_specification": True,
                        "eligibility_context": "shown_trials"
                    }
                }
            ]
            
            return HandlerResponse(
                success=True,
                message=message,
                metadata={"context": "shown_trials"},
                actions=actions
            )
        
        # Check if we have condition/location context
        condition = self.get_condition(entities, context)
        location = self.get_location(entities, context)
        
        if condition and location:
            # Search for trials to check eligibility
            matching_trials = trial_search.get_trials_by_condition_and_location(condition, location)
            
            if matching_trials:
                # Start prescreening for first matching trial
                trial = matching_trials[0]
                return self._start_prescreening_for_trial(
                    trial_id=trial.get("id"),
                    trial_name=trial.get("trial_name"),
                    condition=trial.get("conditions"),
                    location=location,
                    context=context,
                    state_manager=state_manager
                )
            else:
                message = f"I couldn't find any trials for {condition} in {location} to check eligibility for. "
                message += "Would you like to search for trials in a different location?"
                
                # Add actions for no trials found case
                actions = [
                    {
                        "type": "update_context",
                        "data": {
                            "no_trials_found": True,
                            "searched_condition": condition,
                            "searched_location": location,
                            "eligibility_check_failed": True
                        }
                    }
                ]
                
                return HandlerResponse(
                    success=True,
                    message=message,
                    metadata={"no_trials_found": True},
                    actions=actions
                )
        
        # Need more information
        message = "I'd be happy to check your eligibility for clinical trials. "
        if not condition and not location:
            message += "What condition are you interested in, and what's your location?"
        elif not condition:
            message += "What medical condition are you interested in?"
        else:
            message += "What location are you interested in?"
        
        # Set appropriate awaiting state
        if not condition:
            next_state = ConversationState.AWAITING_CONDITION
            awaiting_field = "awaiting_condition"
        else:
            next_state = ConversationState.AWAITING_LOCATION
            awaiting_field = "awaiting_location"
        
        state_manager.transition_to(next_state, reason="Need information for eligibility check")
        
        actions = [
            {
                "type": "update_context",
                "data": {
                    awaiting_field: True,
                    "eligibility_intent": True
                }
            }
        ]
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={"needs_info": True},
            next_state=next_state.value,
            actions=actions
        )
    
    def _handle_specific_trial_eligibility(self, entities: Dict[EntityType, ExtractedEntity],
                                         context: ConversationContext,
                                         state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle eligibility check for specific trial"""
        
        condition = self.get_condition(entities, context)
        location = self.get_location(entities, context)
        
        if not condition or not location:
            # Need more information
            return self._handle_general_eligibility(entities, context, state_manager)
        
        # Search for the specific trial
        trials = trial_search.get_trials_by_condition_and_location(condition, location)
        
        if not trials:
            message = f"I couldn't find a {condition} trial in {location}. "
            message += "Would you like me to search in nearby locations?"
            
            # Add actions for trial not found case
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "specific_trial_not_found": True,
                        "searched_condition": condition,
                        "searched_location": location
                    }
                }
            ]
            
            return HandlerResponse(
                success=True,
                message=message,
                metadata={"trial_not_found": True},
                actions=actions
            )
        
        # Start prescreening for the first matching trial
        trial = trials[0]
        return self._start_prescreening_for_trial(
            trial_id=trial.get("id"),
            trial_name=trial.get("trial_name"),
            condition=condition,
            location=location,
            context=context,
            state_manager=state_manager
        )
    
    def _handle_shown_trial_eligibility(self, entities: Dict[EntityType, ExtractedEntity],
                                      context: ConversationContext,
                                      state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle eligibility check for previously shown trial"""
        
        # Get trial ID from entities or context
        trial_id = None
        if EntityType.TRIAL_ID in entities:
            trial_id = entities[EntityType.TRIAL_ID].value
        elif context.metadata.get("last_shown_trial"):
            trial_id = context.metadata["last_shown_trial"]
        
        if not trial_id:
            # Try to infer from last shown trials
            if context.last_shown_trials:
                trial_info = context.last_shown_trials[0]
                trial_id = trial_info.get("id")
                trial_name = trial_info.get("name")
                condition = trial_info.get("condition")
                location = trial_info.get("location")
                
                return self._start_prescreening_for_trial(
                    trial_id=trial_id,
                    trial_name=trial_name,
                    condition=condition,
                    location=location,
                    context=context,
                    state_manager=state_manager
                )
        
        # Need clarification
        message = "Which trial would you like to check your eligibility for? "
        message += "You can tell me the number from the list or the condition name."
        
        actions = [
            {
                "type": "update_context",
                "data": {
                    "awaiting_trial_specification": True,
                    "eligibility_intent": True
                }
            }
        ]
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={"needs_clarification": True},
            actions=actions
        )
    
    def _handle_eligibility_followup(self, entities: Dict[EntityType, ExtractedEntity],
                                   context: ConversationContext,
                                   state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle follow-up eligibility check"""
        
        # Check if we just showed trial info
        if context.just_showed_trial_info:
            condition = self._get_condition(entities, context)
            location = self._get_location(entities, context)
            
            if condition and location:
                # Find the trial
                trials = trial_search.get_trials_by_condition_and_location(condition, location)
                
                if trials:
                    trial = trials[0]
                    return self._start_prescreening_for_trial(
                        trial_id=trial.get("id"),
                        trial_name=trial.get("trial_name"),
                        condition=condition,
                        location=location,
                        context=context,
                        state_manager=state_manager
                    )
        
        # Default to general eligibility handling
        return self._handle_general_eligibility(entities, context, state_manager)
    
    def _start_prescreening_for_trial(self, trial_id: int, trial_name: str,
                                     condition: str, location: str,
                                     context: ConversationContext,
                                     state_manager: ConversationStateManager) -> HandlerResponse:
        """Start prescreening flow for a specific trial"""
        
        try:
            # Create prescreening session
            session = PrescreeningSession(
                session_id=context.session_id,
                trial_id=trial_id,
                trial_name=trial_name,
                condition=condition,
                location=location,
                current_state=ConversationState.PRESCREENING_ACTIVE
            )
            
            # Start prescreening
            # Note: prescreening_controller.start_prescreening is async, 
            # but we're in a sync context. For now, create a simple sync result
            result = {
                "success": True,
                "question": "Let's start by checking your eligibility. What is your age?",
                "question_key": "age",
                "remaining_questions": ["diagnosis", "medications", "other_conditions"]
            }
            
            if result["success"]:
                # Transition to prescreening state
                state_manager.transition_to(
                    ConversationState.PRESCREENING_ACTIVE,
                    reason=f"Starting eligibility check for {trial_name}"
                )
                
                # Get first question
                question = result.get("question", "Let me check your eligibility for this trial.")
                
                message = f"Great! I'll help you check your eligibility for the {trial_name}.\n\n"
                message += "I'll ask you a few questions to determine if you might qualify. "
                message += "Please answer as accurately as possible.\n\n"
                message += question
                
                actions = [
                    {
                        "type": "update_context",
                        "data": {
                            "prescreening_active": True,
                            "trial_id": trial_id,
                            "trial_name": trial_name,
                            "current_question_key": result.get("question_key"),
                            "remaining_questions": result.get("remaining_questions", [])
                        }
                    },
                    {
                        "type": "log_prescreening_start",
                        "data": {
                            "trial_id": trial_id,
                            "trial_name": trial_name,
                            "condition": condition,
                            "location": location
                        }
                    }
                ]
                
                # Set next state based on question type
                next_state = self._get_next_state_for_question(result.get("question_key"))
                if next_state:
                    state_manager.transition_to(next_state, reason="Asking prescreening question")
                
                return HandlerResponse(
                    success=True,
                    message=message,
                    metadata={
                        "prescreening_started": True,
                        "trial_id": trial_id,
                        "question_key": result.get("question_key")
                    },
                    next_state=next_state.value if next_state else None,
                    actions=actions
                )
            else:
                # Error starting prescreening
                message = "I encountered an issue starting the eligibility check. "
                message += "Please try again or search for other trials."
                
                # Add error tracking for prescreening failure
                actions = [
                    {
                        "type": "update_context",
                        "data": {
                            "prescreening_start_failed": True,
                            "last_error": result.get("error", "Failed to start prescreening"),
                            "trial_id": trial_id,
                            "trial_name": trial_name
                        }
                    }
                ]
                
                return HandlerResponse(
                    success=False,
                    message=message,
                    error=result.get("error", "Failed to start prescreening"),
                    actions=actions
                )
                
        except Exception as e:
            logger.error(f"Error starting prescreening: {str(e)}")
            # Add error tracking for exception
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "prescreening_exception": True,
                        "last_error": str(e),
                        "error_type": "prescreening_start_exception",
                        "trial_id": trial_id,
                        "trial_name": trial_name
                    }
                }
            ]
            
            return HandlerResponse(
                success=False,
                message="I encountered an error starting the eligibility check. Please try again.",
                error=str(e),
                actions=actions
            )
    
    
    def _get_next_state_for_question(self, question_key: Optional[str]) -> Optional[ConversationState]:
        """Get the appropriate state for a question type"""
        return state_config.get_state_for_question(question_key, fallback_state=None)
    
    def _handle_condition_for_eligibility(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
                                        context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle condition answer when checking eligibility"""
        
        # Get the condition from the entities
        condition = self.get_condition(entities, context)
        location = self.get_location(entities, context)
        
        if not condition:
            # Add actions for missing condition case
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "condition_not_understood": True,
                        "awaiting_condition": True
                    }
                }
            ]
            
            return HandlerResponse(
                success=False,
                message="I didn't catch the condition. Could you please tell me what medical condition you're interested in?",
                metadata={"error": "condition_not_found"},
                actions=actions
            )
        
        if not location:
            # Add actions for missing location case
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "focus_condition": condition,
                        "awaiting_location": True,
                        "eligibility_context": True
                    }
                }
            ]
            
            return HandlerResponse(
                success=False,
                message=f"I understand you're interested in {condition} trials. What location are you interested in?",
                metadata={"condition_captured": condition, "needs_location": True},
                actions=actions
            )
        
        # We have both condition and location - start eligibility check
        # Find matching trial from shown trials
        matching_trial = None
        if context.last_shown_trials:
            for trial in context.last_shown_trials:
                if condition.lower() in trial.get("conditions", "").lower():
                    matching_trial = trial
                    break
        
        if matching_trial:
            # Start prescreening for the specific trial
            return self._start_prescreening_for_trial(
                trial_id=matching_trial.get("id", 1),
                trial_name=matching_trial.get("trial_name", f"{condition} Trial"),
                condition=condition,
                location=location,
                context=context,
                state_manager=state_manager
            )
        else:
            # Search for trials in the area
            return self._handle_general_eligibility(entities, context, state_manager)