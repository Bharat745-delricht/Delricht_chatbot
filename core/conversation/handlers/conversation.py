"""
Handler for general conversation and trial interest intents.

This handler processes general greetings, trial interest expressions,
and other conversational intents that don't involve specific trial searches.
"""

import logging
from typing import Dict, Any

from core.conversation.handlers.base import BaseHandler, HandlerResponse
from core.conversation.understanding import IntentType, DetectedIntent, ExtractedEntity, EntityType
from core.conversation.context import ConversationContext
from core.conversation.orchestration import ConversationStateManager
from core.chat.sync_gemini_responder import SyncGeminiResponder
from models.schemas import ConversationState

logger = logging.getLogger(__name__)


class ConversationHandler(BaseHandler):
    """
    Handles general conversation and trial interest intents.
    
    This handler manages:
    - General greetings and queries
    - Trial interest expressions
    - Conversational responses that guide users toward trial search
    """
    
    def __init__(self):
        super().__init__()
        self.gemini_responder = SyncGeminiResponder()
    
    def can_handle(self, intent: DetectedIntent, context: ConversationContext) -> bool:
        """Check if this handler can process the intent"""
        return intent.intent_type in [
            IntentType.GENERAL_QUERY,
            IntentType.TRIAL_INTEREST,
        ]
    
    def handle(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
              context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle conversation intent"""
        
        if intent.intent_type == IntentType.GENERAL_QUERY:
            return self._handle_general_query(intent, entities, context, state_manager)
        elif intent.intent_type == IntentType.TRIAL_INTEREST:
            return self._handle_trial_interest(intent, entities, context, state_manager)
        else:
            # Add actions even for error cases to track what happened
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "last_error": f"Unexpected intent type: {intent.intent_type}",
                        "error_type": "unexpected_intent",
                        "failed_intent": intent.intent_type.value
                    }
                }
            ]
            
            return HandlerResponse(
                success=False,
                message="I'm not sure how to help with that.",
                error=f"Unexpected intent type: {intent.intent_type}",
                actions=actions
            )
    
    def _handle_general_query(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
                             context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle general queries and greetings"""
        
        # Check if this might be a condition or location answer that was misclassified
        message_text = getattr(intent, 'original_message', '') or intent.matched_pattern or ''
        
        # Check if we're expecting trial criteria and this looks like a condition
        if (hasattr(context, 'state_data') and 
            context.state_data and 
            context.state_data.get('awaiting_trial_criteria')):
            
            # Try to extract condition or location from the general query
            condition = self.get_condition(entities, context)
            location = self.get_location(entities, context)
            
            # Also try to detect condition from raw text
            if not condition:
                condition = self._detect_condition_from_text(message_text)
            
            # If we found either, treat this as trial interest with partial info
            if condition or location:
                # Create mock entities for the detected condition
                if condition and EntityType.CONDITION not in entities:
                    entities = dict(entities)  # Make a copy
                    entities[EntityType.CONDITION] = type('MockEntity', (), {
                        'normalized_value': condition,
                        'value': condition,
                        'confidence': 0.8,
                        'source': 'text_detection'
                    })()
                
                return self._handle_trial_interest(intent, entities, context, state_manager)
        
        # Generate response using OpenAI
        try:
            user_message = getattr(intent, 'original_message', message_text)
            context_dict = {
                'focus_condition': context.focus_condition,
                'focus_location': context.focus_location,
                'conversation_history': context.conversation_history or [],
                'prescreening_active': bool(context.prescreening_data),
                'conversation_state': context.conversation_state
            }
            
            # Build intent context for OpenAI
            intent_dict = {
                'type': intent.intent_type.value,
                'entities': {}
            }
            
            message = self.gemini_responder.generate_response(
                message=user_message,
                intent=intent_dict,
                context=context_dict
            )
            
        except Exception as e:
            logger.warning(f"Failed to generate OpenAI response: {e}")
            # Fallback to simple response
            message = (
                "Hello! I'm here to help you find clinical trials and check eligibility requirements. "
                "What would you like to know about clinical trials?"
            )
        
        # Add actions to track general query handling
        actions = [
            {
                "type": "update_context",
                "data": {
                    "last_general_query": getattr(intent, 'original_message', message_text),
                    "general_query_handled": True
                }
            }
        ]
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={
                "intent_type": "general_query",
                "response_type": "gemini_generated"
            },
            actions=actions
        )
    
    def _handle_trial_interest(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
                              context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle expressions of trial interest"""
        
        # Check if we already have condition or location information
        condition = self.get_condition(entities, context)
        location = self.get_location(entities, context)
        
        if condition and location:
            # Direct them to trial search
            message = (
                f"Great! I can help you find clinical trials for {condition} in {location}. "
                "Let me search for available trials that might be suitable for you."
            )
            # Trigger a trial search by updating context
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "focus_condition": condition,
                        "focus_location": location,
                        "ready_for_search": True
                    }
                }
            ]
        elif condition:
            # Ask for location
            message = (
                f"I'd be happy to help you find clinical trials for {condition}. "
                "What location are you interested in?"
            )
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "focus_condition": condition,
                        "awaiting_location": True
                    }
                }
            ]
        elif location:
            # Ask for condition
            message = (
                f"I can help you find clinical trials in {location}. "
                "What medical condition are you interested in researching?"
            )
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "focus_location": location,
                        "awaiting_condition": True
                    }
                }
            ]
        else:
            # Generate response asking for both condition and location
            try:
                user_message = getattr(intent, 'original_message', '')
                context_dict = {
                    'focus_condition': context.focus_condition,
                    'focus_location': context.focus_location,
                    'conversation_history': context.conversation_history or [],
                    'needs_condition': True,
                    'needs_location': True
                }
                
                intent_dict = {
                    'type': intent.intent_type.value,
                    'entities': {}
                }
                
                message = self.gemini_responder.generate_response(
                    message=user_message,
                    intent=intent_dict,
                    context=context_dict
                )
                
            except Exception as e:
                logger.warning(f"Failed to generate trial interest response: {e}")
                message = (
                    "I'd be happy to help you find clinical trials! "
                    "To get started, please tell me what medical condition you're interested in and your location."
                )
            
            # Transition to awaiting condition state so next response gets classified correctly
            state_manager.transition_to(
                ConversationState.AWAITING_CONDITION,
                reason="Awaiting condition/location for trial search"
            )
            
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
            next_state=ConversationState.AWAITING_CONDITION.value if not (condition and location) else None,
            metadata={
                "intent_type": "trial_interest",
                "has_condition": condition is not None,
                "has_location": location is not None,
                "response_type": "gemini_generated"
            },
            actions=actions
        )
    
    
    def _detect_condition_from_text(self, text: str) -> str:
        """Detect medical condition from raw text"""
        # Common medical conditions that might be mentioned as single words
        medical_conditions = [
            'diabetes', 'gout', 'arthritis', 'cancer', 'depression', 'anxiety',
            'asthma', 'copd', 'hypertension', 'heart disease', 'stroke', 'migraine',
            'fibromyalgia', 'lupus', 'psoriasis', 'eczema', 'epilepsy', 'parkinsons',
            'alzheimers', 'dementia', 'obesity', 'insomnia', 'ptsd', 'bipolar',
            'schizophrenia', 'autism', 'adhd', 'thyroid', 'kidney disease',
            'liver disease', 'hepatitis', 'hiv', 'aids', 'multiple sclerosis',
            'crohns', 'colitis', 'ibs', 'osteoporosis', 'anemia'
        ]
        
        text_lower = text.lower().strip()
        for condition in medical_conditions:
            if condition in text_lower:
                return condition
        
        return None
    
    def _get_location_filtered(self, entities: Dict[EntityType, ExtractedEntity],
                              context: ConversationContext) -> str:
        """Get location from entities or context with false positive filtering"""
        if EntityType.LOCATION in entities:
            location = entities[EntityType.LOCATION].normalized_value
            # Filter out common false positives from trial interest messages
            false_positives = [
                'trials', 'clinical trials', 'studies', 'research',
                'participate', 'join', 'enroll', 'available', 'hello',
                'trials are available', 'tell me about your trials'
            ]
            if location and location.lower() not in false_positives:
                return location
        
        # Check context for location
        if context.focus_location:
            return context.focus_location
        
        return None