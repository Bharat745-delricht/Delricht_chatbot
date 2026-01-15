"""
Base handler interface for conversation handling.

This module defines the abstract base class for all conversation handlers,
providing a consistent interface for handling different types of user intents.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
import logging

from core.conversation.understanding import IntentType, DetectedIntent, ExtractedEntity, EntityType
from core.conversation.context import ConversationContext
from core.conversation.orchestration import ConversationStateManager

logger = logging.getLogger(__name__)


@dataclass
class HandlerResponse:
    """Response from a handler"""
    success: bool
    message: str
    metadata: Dict[str, Any] = None
    next_state: Optional[str] = None
    actions: List[Dict[str, Any]] = None
    error: Optional[str] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.actions is None:
            self.actions = []


class BaseHandler(ABC):
    """
    Abstract base class for conversation handlers.
    
    Each handler is responsible for processing a specific type of intent
    and generating appropriate responses and actions.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        
    @abstractmethod
    def can_handle(self, intent: DetectedIntent, context: ConversationContext) -> bool:
        """
        Check if this handler can process the given intent.
        
        Args:
            intent: The detected intent
            context: Current conversation context
            
        Returns:
            True if this handler can process the intent
        """
        pass
    
    @abstractmethod
    def handle(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
              context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """
        Handle the intent and generate a response.
        
        Args:
            intent: The detected intent
            entities: Extracted entities
            context: Current conversation context
            state_manager: State manager for transitions
            
        Returns:
            Handler response with message and actions
        """
        pass
    
    def validate_entities(self, required_entities: List[EntityType],
                         entities: Dict[EntityType, ExtractedEntity]) -> Tuple[bool, List[str]]:
        """
        Validate that required entities are present.
        
        Args:
            required_entities: List of required entity types
            entities: Extracted entities
            
        Returns:
            Tuple of (is_valid, missing_entities)
        """
        missing = []
        for entity_type in required_entities:
            if entity_type not in entities:
                missing.append(entity_type.value)
        
        return len(missing) == 0, missing
    
    def build_clarification_message(self, missing_entities: List[str],
                                  context: ConversationContext) -> str:
        """
        Build a clarification message for missing entities.
        
        Args:
            missing_entities: List of missing entity types
            context: Current conversation context
            
        Returns:
            Clarification message
        """
        if "location" in missing_entities and "condition" in missing_entities:
            return "I'd be happy to help you find clinical trials. Could you tell me what condition you're interested in and your location?"
        elif "location" in missing_entities:
            condition = context.focus_condition or "that condition"
            return f"I can help you find trials for {condition}. What location are you interested in?"
        elif "condition" in missing_entities:
            return "What medical condition are you interested in finding trials for?"
        else:
            return "Could you provide more information about what you're looking for?"
    
    def format_trial_results(self, trials: List[Dict[str, Any]], 
                           condition: str, location: str) -> str:
        """
        Format trial search results for display.
        
        Args:
            trials: List of trial data
            condition: Condition searched for
            location: Location searched for
            
        Returns:
            Formatted message
        """
        if not trials:
            return f"I couldn't find any clinical trials for {condition} in {location}. Would you like me to search in nearby locations?"
        
        message = f"I found {len(trials)} clinical trial{'s' if len(trials) > 1 else ''} for {condition} in {location}:\n\n"
        
        for i, trial in enumerate(trials[:5], 1):  # Show max 5 trials
            message += f"**{i}. {trial.get('name', 'Unnamed Trial')}**\n"
            if trial.get('brief_summary'):
                message += f"   {trial['brief_summary'][:150]}...\n"
            if trial.get('phase'):
                message += f"   Phase: {trial['phase']}\n"
            if trial.get('status'):
                message += f"   Status: {trial['status']}\n"
            message += "\n"
        
        if len(trials) > 5:
            message += f"... and {len(trials) - 5} more trials.\n\n"
        
        message += "Would you like to check your eligibility for any of these trials?"
        
        return message
    
    def get_condition(self, entities: Dict[EntityType, ExtractedEntity],
                      context: ConversationContext) -> Optional[str]:
        """
        Get condition from entities or context with fallback logic.
        
        Args:
            entities: Extracted entities
            context: Current conversation context
            
        Returns:
            Condition string or None
        """
        # Primary: Check entities
        if EntityType.CONDITION in entities:
            return entities[EntityType.CONDITION].normalized_value
        
        # Secondary: Check context focus
        if context.focus_condition:
            return context.focus_condition
        
        # Tertiary: Check inferred condition from state data
        if context.state_data.get("likely_condition"):
            return context.state_data["likely_condition"]
        
        # Quaternary: Check recently mentioned conditions
        if hasattr(context, 'mentioned_conditions') and context.mentioned_conditions:
            return list(context.mentioned_conditions)[-1]
        
        return None
    
    def get_location(self, entities: Dict[EntityType, ExtractedEntity],
                     context: ConversationContext) -> Optional[str]:
        """
        Get location from entities or context with fallback logic.
        
        Args:
            entities: Extracted entities
            context: Current conversation context
            
        Returns:
            Location string or None
        """
        # Primary: Check entities
        if EntityType.LOCATION in entities:
            return entities[EntityType.LOCATION].normalized_value
        
        # Secondary: Check context focus
        if context.focus_location:
            return context.focus_location
        
        # Tertiary: Check inferred location from state data
        if context.state_data.get("likely_location"):
            return context.state_data["likely_location"]
        
        # Quaternary: Check recently mentioned locations
        if hasattr(context, 'mentioned_locations') and context.mentioned_locations:
            return list(context.mentioned_locations)[-1]
        
        return None

    def add_tracking_metadata(self, response: HandlerResponse, 
                            intent: DetectedIntent,
                            entities: Dict[EntityType, ExtractedEntity]):
        """
        Add tracking metadata to response.
        
        Args:
            response: Handler response
            intent: Detected intent
            entities: Extracted entities
        """
        response.metadata.update({
            "intent_type": intent.intent_type.value,
            "intent_confidence": intent.confidence,
            "entity_count": len(entities),
            "entities": {
                entity_type.value: {
                    "value": entity.value,
                    "confidence": entity.confidence
                }
                for entity_type, entity in entities.items()
            }
        })
    
    def log_handling(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
                    response: HandlerResponse):
        """Log handler processing for debugging"""
        self.logger.info(
            f"Handled {intent.intent_type.value} intent",
            extra={
                "intent_confidence": intent.confidence,
                "entities": list(entities.keys()),
                "response_success": response.success,
                "has_error": response.error is not None
            }
        )


class HandlerRegistry:
    """Registry for managing conversation handlers"""
    
    def __init__(self):
        self.handlers: List[BaseHandler] = []
        self._intent_handler_map: Dict[IntentType, List[BaseHandler]] = {}
        
    def register(self, handler: BaseHandler, intent_types: List[IntentType] = None):
        """
        Register a handler.
        
        Args:
            handler: Handler instance
            intent_types: Optional list of intent types this handler processes
        """
        self.handlers.append(handler)
        
        if intent_types:
            for intent_type in intent_types:
                if intent_type not in self._intent_handler_map:
                    self._intent_handler_map[intent_type] = []
                self._intent_handler_map[intent_type].append(handler)
    
    def get_handler(self, intent: DetectedIntent, context: ConversationContext) -> Optional[BaseHandler]:
        """
        Get appropriate handler for intent.
        
        Args:
            intent: Detected intent
            context: Conversation context
            
        Returns:
            Handler instance or None
        """
        # First check intent-specific handlers
        if intent.intent_type in self._intent_handler_map:
            for handler in self._intent_handler_map[intent.intent_type]:
                if handler.can_handle(intent, context):
                    return handler
        
        # Then check all handlers
        for handler in self.handlers:
            if handler.can_handle(intent, context):
                return handler
        
        return None
    
    def get_all_handlers(self) -> List[BaseHandler]:
        """Get all registered handlers"""
        return self.handlers.copy()