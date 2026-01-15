"""
Handler for personal condition intents.

This handler processes statements where users mention their medical condition,
initiating appropriate conversation flows for trial search or eligibility checks.
"""

import logging
from typing import Dict, Any, List, Optional

from core.conversation.handlers.base import BaseHandler, HandlerResponse
from core.conversation.understanding import IntentType, DetectedIntent, ExtractedEntity, EntityType
from core.conversation.context import ConversationContext
from core.conversation.orchestration import ConversationStateManager
from models.schemas import ConversationState

logger = logging.getLogger(__name__)


class PersonalConditionHandler(BaseHandler):
    """
    Handles personal condition statements.
    
    This handler manages:
    - Processing statements like "I have diabetes"
    - Updating context with the user's condition
    - Offering appropriate next steps (trial search, eligibility check)
    - Transitioning to appropriate conversation states
    """
    
    def can_handle(self, intent: DetectedIntent, context: ConversationContext) -> bool:
        """Check if this handler can process the intent"""
        return intent.intent_type == IntentType.PERSONAL_CONDITION
    
    def handle(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
              context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle personal condition statement"""
        
        logger.info(f"Processing personal condition statement")
        
        # Extract condition from entities
        condition = None
        if EntityType.CONDITION in entities:
            condition = entities[EntityType.CONDITION].normalized_value
            logger.info(f"Extracted condition: {condition}")
        
        # Extract location from entities (opportunistic extraction)
        location = None
        if EntityType.LOCATION in entities:
            location = entities[EntityType.LOCATION].normalized_value
            logger.info(f"Extracted location: {location}")
        
        if not condition:
            # No condition found in entities - this shouldn't happen with PERSONAL_CONDITION intent
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "condition_extraction_failed": True,
                        "awaiting_condition": True
                    }
                }
            ]
            
            return HandlerResponse(
                success=False,
                message="I understand you mentioned a medical condition, but I didn't catch which one. Could you tell me what condition you have?",
                actions=actions,
                metadata={"error": "condition_not_extracted"}
            )
        
        # Prepare context updates
        actions = [
            {
                "type": "update_context",
                "data": {
                    "focus_condition": condition,
                    "mentioned_conditions": [condition]
                }
            }
        ]
        
        # Update context with the condition
        context.focus_condition = condition
        context.mentioned_conditions.add(condition)
        
        # If we extracted a location, update context with it too
        if location:
            context.focus_location = location
            actions[0]["data"]["focus_location"] = location
            logger.info(f"Updated context with both condition ({condition}) and location ({location})")
        
        # Determine appropriate response based on what we know
        current_location = context.focus_location  # This might be newly set or from previous context
        
        if current_location:
            # We have both condition and location - offer to search for trials
            message = f"I understand you have {condition}. Since you're in {current_location}, would you like me to search for {condition} clinical trials in your area?"
            
            # Add action to indicate we're ready for trial search
            actions.append({
                "type": "update_context", 
                "data": {
                    "ready_for_trial_search": True,
                    "awaiting_confirmation": True
                }
            })
            
            state_manager.transition_to(ConversationState.AWAITING_CONFIRMATION)
            next_state = ConversationState.AWAITING_CONFIRMATION.value
        else:
            # We have condition but need location
            message = f"I understand you have {condition}. I can help you find relevant clinical trials and check if you might be eligible. What city or state are you located in?"
            
            # Add action to indicate we're awaiting location
            actions.append({
                "type": "update_context",
                "data": {
                    "awaiting_location": True
                }
            })
            
            state_manager.transition_to(ConversationState.AWAITING_LOCATION)
            next_state = ConversationState.AWAITING_LOCATION.value
        
        return HandlerResponse(
            success=True,
            message=message,
            actions=actions,
            next_state=next_state,
            metadata={
                "condition_captured": condition,
                "location_captured": location,
                "needs_location": current_location is None
            }
        )