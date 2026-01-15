"""
Handler for trial search intents.

This handler processes requests to search for clinical trials by condition
and/or location, managing the search flow and result presentation.
"""

import logging
from typing import Dict, Any, List, Optional

from core.conversation.handlers.base import BaseHandler, HandlerResponse
from core.conversation.understanding import IntentType, DetectedIntent, ExtractedEntity, EntityType
from core.conversation.context import ConversationContext
from core.conversation.orchestration import ConversationStateManager
from core.services.trial_search import trial_search
from core.services.trial_fallback import trial_fallback
from models.schemas import ConversationState

logger = logging.getLogger(__name__)


class TrialSearchHandler(BaseHandler):
    """
    Handles trial search requests.
    
    This handler manages:
    - Searching for trials by condition and/or location
    - Handling missing information gracefully
    - Providing fallback suggestions
    - Managing search context
    """
    
    def can_handle(self, intent: DetectedIntent, context: ConversationContext) -> bool:
        """Check if this handler can process the intent"""
        return intent.intent_type in [
            IntentType.TRIAL_SEARCH,
            IntentType.LOCATION_SEARCH,
        ]
    
    def handle(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
              context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle trial search request"""
        
        # Extract condition and location
        condition = self.get_condition(entities, context)
        location = self.get_location(entities, context)
        
        # Determine what information we have
        has_condition = condition is not None
        has_location = location is not None
        
        # Handle different scenarios
        if has_condition and has_location:
            return self._handle_complete_search(condition, location, context, state_manager)
        elif has_location and not has_condition:
            return self._handle_location_only_search(location, context, state_manager)
        elif has_condition and not has_location:
            return self._handle_condition_only_search(condition, context, state_manager)
        else:
            return self._handle_no_criteria_search(context, state_manager)
    
    
    def _handle_complete_search(self, condition: str, location: str,
                              context: ConversationContext,
                              state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle search with both condition and location"""
        try:
            # Search for trials by condition and location
            filtered_trials = trial_search.get_trials_by_condition_and_location(condition, location)
            
            if filtered_trials:
                # Format results for the specific condition
                message = f"I found {len(filtered_trials)} {condition} trial{'s' if len(filtered_trials) > 1 else ''} in {location}:\n\n"
                
                # Show first few trials
                for trial in filtered_trials[:3]:
                    message += f"**{trial['conditions']}** trial\n"
                    message += f"*Investigator: {trial['investigator_name']}*\n\n"
                
                if len(filtered_trials) > 3:
                    message += f"... and {len(filtered_trials) - 3} more.\n\n"
                
                message += "Would you like to:\n"
                message += "1. Check your eligibility for any of these trials?\n"
                message += "2. Get more details about a specific trial?"
                
                # Update context
                actions = [
                    {
                        "type": "update_context",
                        "data": {
                            "focus_condition": condition,
                            "focus_location": location,
                            "last_shown_trials": [
                                {
                                    "id": trial.get("id"),
                                    "name": trial.get("trial_name"),
                                    "condition": trial.get("conditions"),
                                    "location": location
                                }
                                for trial in filtered_trials[:5]
                            ]
                        }
                    },
                    {
                        "type": "log_search",
                        "data": {
                            "condition": condition,
                            "location": location,
                            "result_count": len(filtered_trials)
                        }
                    }
                ]
                
                return HandlerResponse(
                        success=True,
                        message=message,
                        metadata={
                            "trial_count": len(filtered_trials),
                            "search_criteria": {
                                "condition": condition,
                                "location": location
                            }
                        },
                        actions=actions
                    )
            
            # No trials found - use fallback service
            fallback_message = trial_fallback.suggest_alternatives(
                condition=condition,
                location=location,
                context=context.__dict__
            )
            
            # Add actions even for fallback cases to track what happened
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "last_search_failed": True,
                        "fallback_shown": True,
                        "searched_condition": condition,
                        "searched_location": location
                    }
                }
            ]
            
            return HandlerResponse(
                success=True,
                message=fallback_message,
                metadata={
                    "no_results": True,
                    "used_fallback": True
                },
                actions=actions
            )
                
        except Exception as e:
            logger.error(f"Error searching trials: {str(e)}")
            # Add error tracking
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "last_error": str(e),
                        "error_type": "search_failed",
                        "searched_condition": condition,
                        "searched_location": location
                    }
                }
            ]
            
            return HandlerResponse(
                success=False,
                message="I encountered an error while searching for trials. Please try again.",
                error=str(e),
                actions=actions
            )
    
    def _handle_location_only_search(self, location: str,
                                   context: ConversationContext,
                                   state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle search with only location"""
        try:
            # Search all trials in location
            trials = trial_search.get_trials_by_location(location)
            
            if trials:
                # Use the formatted message from trial_search service
                message = trial_search.format_location_trials_message(location, trials)
                
                # Extract unique conditions for context
                conditions = list(set([trial.get("conditions", "Unknown") for trial in trials]))
                
                # Update context
                actions = [
                    {
                        "type": "update_context", 
                        "data": {
                            "focus_location": location,
                            "awaiting_condition": True,
                            "available_conditions": conditions[:10]  # Store top 10 conditions
                        }
                    }
                ]
                
                # Transition to awaiting condition state
                state_manager.transition_to(
                    ConversationState.AWAITING_CONDITION,
                    reason="Need condition for trial search"
                )
                
                return HandlerResponse(
                    success=True,
                    message=message,
                    metadata={
                        "trial_count": len(trials),
                        "condition_count": len(conditions),
                        "location": location
                    },
                    next_state=ConversationState.AWAITING_CONDITION.value,
                    actions=actions
                )
            else:
                message = f"I couldn't find any clinical trials in {location}. "
                message += "Would you like to search in a different location?"
                
                # Add actions for no results case
                actions = [
                    {
                        "type": "update_context",
                        "data": {
                            "no_results_in_location": location,
                            "location_search_failed": True
                        }
                    }
                ]
                
                return HandlerResponse(
                    success=True,
                    message=message,
                    metadata={"no_results": True},
                    actions=actions
                )
                
        except Exception as e:
            logger.error(f"Error searching by location: {str(e)}")
            # Add error tracking
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "last_error": str(e),
                        "error_type": "location_search_failed",
                        "searched_location": location
                    }
                }
            ]
            
            return HandlerResponse(
                success=False,
                message="I encountered an error while searching. Please try again.",
                error=str(e),
                actions=actions
            )
    
    def _handle_condition_only_search(self, condition: str,
                                    context: ConversationContext,
                                    state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle search with only condition"""
        # We need location - ask for it
        message = f"I can help you find clinical trials for {condition}. "
        message += "What location are you interested in?"
        
        # Update context
        actions = [
            {
                "type": "update_context",
                "data": {
                    "focus_condition": condition,
                    "awaiting_location": True
                }
            }
        ]
        
        # Transition to awaiting location state
        state_manager.transition_to(
            ConversationState.AWAITING_LOCATION,
            reason="Need location for trial search"
        )
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={
                "condition": condition,
                "needs_location": True
            },
            next_state=ConversationState.AWAITING_LOCATION.value,
            actions=actions
        )
    
    def _handle_no_criteria_search(self, context: ConversationContext,
                                 state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle search with no criteria"""
        message = "I'd be happy to help you find clinical trials. "
        message += "What medical condition are you interested in, and what's your location?"
        
        # Update context
        actions = [
            {
                "type": "update_context",
                "data": {
                    "awaiting_trial_criteria": True
                }
            }
        ]
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={"needs_criteria": True},
            actions=actions
        )
    
