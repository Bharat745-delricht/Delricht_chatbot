"""
Main conversation processing pipeline.

This module orchestrates the conversation flow through stages of understanding,
context management, state transitions, and response generation.
"""

import logging
import time
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from datetime import datetime

from core.conversation.context import ContextManager, ConversationContext
from core.conversation.orchestration import ConversationFlowController, ConversationStateManager
from core.conversation.understanding import IntentDetector, EntityExtractor, ContextAnalyzer, EntityType
from core.conversation.handlers import HandlerRegistry, BaseHandler
from core.chat.sync_gemini_responder import SyncGeminiResponder

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    """Result of pipeline processing"""
    success: bool
    response: str
    intent: Dict[str, Any]
    entities: Dict[str, Any]
    metadata: Dict[str, Any]
    processing_time_ms: float
    error: Optional[str] = None


class ConversationProcessor:
    """
    Main conversation processing pipeline.
    
    This class coordinates all components to process user messages through:
    1. Context retrieval and enrichment
    2. Intent detection and entity extraction
    3. State management and transitions
    4. Handler routing and execution
    5. Response generation and formatting
    """
    
    def __init__(self):
        # Initialize components
        self.context_manager = ContextManager()
        self.intent_detector = IntentDetector()
        self.entity_extractor = EntityExtractor()
        self.context_analyzer = ContextAnalyzer()
        self.state_manager = ConversationStateManager()
        self.flow_controller = ConversationFlowController(self.state_manager)
        self.handler_registry = HandlerRegistry()
        self.gemini_responder = SyncGeminiResponder()
        
        # Register handlers
        self._register_handlers()
        
        # Processing metrics
        self.metrics = {
            "total_processed": 0,
            "successful": 0,
            "failed": 0,
            "avg_processing_time": 0
        }
    
    def _register_handlers(self):
        """Register all conversation handlers"""
        from core.conversation.handlers import (
            TrialSearchHandler,
            EligibilityHandler,
            TrialInfoHandler,
            PrescreeningHandler
        )
        from core.conversation.handlers.personal_condition import PersonalConditionHandler
        from core.conversation.handlers.conversation import ConversationHandler
        # AnswerHandler removed - merged into PrescreeningHandler
        from core.conversation.understanding import IntentType
        
        # Register conversation handler for general queries and trial interest
        self.handler_registry.register(
            ConversationHandler(),
            [IntentType.GENERAL_QUERY, IntentType.TRIAL_INTEREST]
        )
        
        # Register trial search handler for specific trial searches only
        self.handler_registry.register(
            TrialSearchHandler(),
            [IntentType.TRIAL_SEARCH, IntentType.LOCATION_SEARCH]
        )
        self.handler_registry.register(
            EligibilityHandler(),
            [IntentType.ELIGIBILITY, IntentType.ELIGIBILITY_SPECIFIC_TRIAL,
             IntentType.ELIGIBILITY_FOLLOWUP, IntentType.ELIGIBILITY_FOR_SHOWN_TRIAL]
        )
        self.handler_registry.register(
            TrialInfoHandler(),
            [IntentType.TRIAL_INFO_REQUEST]
        )
        self.handler_registry.register(
            PersonalConditionHandler(),
            [IntentType.PERSONAL_CONDITION]
        )
        # Register PrescreeningHandler to handle ALL answer types (merged from AnswerHandler)
        self.handler_registry.register(
            PrescreeningHandler(),
            [IntentType.AGE_ANSWER, IntentType.YES_NO_ANSWER, IntentType.NUMBER_ANSWER,
             IntentType.CONDITION_ANSWER, IntentType.LOCATION_ANSWER, IntentType.MEDICATION_ANSWER,
             IntentType.QUESTION_DURING_PRESCREENING]
        )
    
    def process_message(self, message: str, session_id: str, 
                       user_id: Optional[str] = None) -> ProcessingResult:
        """
        Process a user message through the conversation pipeline.
        
        Args:
            message: User's message
            session_id: Session identifier
            user_id: Optional user identifier
            
        Returns:
            Processing result with response and metadata
        """
        start_time = time.time()
        
        try:
            # Stage 1: Context retrieval and enrichment
            context = self._retrieve_context(session_id, user_id)
            
            # Stage 2: Understanding - Intent and Entity extraction
            understanding_result = self._understand_message(message, context)
            intent = understanding_result["intent"]
            entities = understanding_result["entities"]
            contextual_clues = understanding_result["clues"]
            
            # Log intent detection for debugging
            logger.info(f"Intent detected: {intent.intent_type.value} (confidence: {intent.confidence})")
            logger.info(f"Current state: {context.conversation_state}")
            logger.info(f"Context has trial info: {context.just_showed_trial_info}")
            
            # Stage 3: State management
            flow_result = self._manage_flow(intent, context)
            
            # Stage 4: Handler execution
            handler_result = self._execute_handler(message, intent, entities, context)
            
            # Stage 4.5: Process handler actions
            if handler_result.get("actions"):
                self._process_handler_actions(handler_result["actions"], context)
            
            # Stage 5: Response generation
            response = self._generate_response(handler_result, context)
            
            # Stage 6: Context update
            self._update_context(session_id, handler_result, intent, entities)
            
            # Calculate processing time
            processing_time = (time.time() - start_time) * 1000
            
            # Update metrics
            self._update_metrics(True, processing_time)
            
            # Build result
            return ProcessingResult(
                success=True,
                response=response,
                intent=self._serialize_intent(intent),
                entities=self._serialize_entities(entities),
                metadata={
                    "session_id": session_id,
                    "current_state": self.state_manager.current_state.value,
                    "handler_used": handler_result.get("handler_name"),
                    "contextual_clues": len(contextual_clues),
                    **handler_result.get("metadata", {})
                },
                processing_time_ms=processing_time
            )
            
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}", exc_info=True)
            
            # Update metrics
            processing_time = (time.time() - start_time) * 1000
            self._update_metrics(False, processing_time)
            
            # Generate fallback response
            fallback_response = self._generate_fallback_response(message, session_id)
            
            return ProcessingResult(
                success=False,
                response=fallback_response,
                intent={},
                entities={},
                metadata={"error_type": type(e).__name__},
                processing_time_ms=processing_time,
                error=str(e)
            )
    
    def _retrieve_context(self, session_id: str, user_id: Optional[str]) -> ConversationContext:
        """Retrieve and enrich conversation context"""
        logger.debug(f"Retrieving context for session {session_id}")
        
        # Get context with history
        context = self.context_manager.get_context(session_id, include_history=True)
        
        # Update user ID if provided
        if user_id and context.user_id == "anonymous":
            context.user_id = user_id
        
        # Sync with state manager
        if context.conversation_state:
            try:
                # Restore state manager from context
                state_data = {
                    "current_state": context.conversation_state,
                    "state_data": context.state_data,
                    "state_entered_at": context.last_updated.isoformat() if context.last_updated else None,
                    "state_history": []
                }
                self.state_manager = ConversationStateManager.deserialize(state_data)
            except:
                # Reset if deserialization fails
                self.state_manager = ConversationStateManager()
        
        return context
    
    def _understand_message(self, message: str, 
                          context: ConversationContext) -> Dict[str, Any]:
        """Understand user message - detect intent and extract entities"""
        logger.debug(f"Understanding message: {message[:50]}...")
        
        # Analyze context for clues
        contextual_clues = self.context_analyzer.analyze_context(message, context)
        
        # Detect intent with context awareness
        detected_intent = self.intent_detector.detect_intent(message, context)
        
        # Extract entities based on intent
        extracted_entities = self.entity_extractor.extract_entities(
            message, detected_intent, context
        )
        
        # Apply contextual inference
        inferred_info = self.context_analyzer.infer_missing_information(
            message, context, contextual_clues
        )
        
        # Merge inferred information into context
        if inferred_info:
            for key, value in inferred_info.items():
                context.state_data[key] = value
        
        return {
            "intent": detected_intent,
            "entities": extracted_entities,
            "clues": contextual_clues,
            "inferred": inferred_info
        }
    
    def _manage_flow(self, intent, context: ConversationContext) -> Dict[str, Any]:
        """Manage conversation flow and state transitions"""
        logger.debug(f"Managing flow for intent {intent.intent_type}")
        
        # Sync state manager with current context BEFORE flow control
        self.flow_controller.state_manager = self.state_manager
        
        # Handle intent through flow controller
        flow_result = self.flow_controller.handle_intent(
            intent_type=intent.intent_type.value,
            context=context.to_dict()
        )
        
        # Sync state back to context
        if flow_result.get("current_state"):
            context.conversation_state = flow_result["current_state"]
        
        return flow_result
    
    def _execute_handler(self, message: str, intent, entities, 
                        context: ConversationContext) -> Dict[str, Any]:
        """Execute appropriate handler for the intent"""
        logger.debug(f"Executing handler for intent {intent.intent_type}")
        
        # Get appropriate handler
        handler = self.handler_registry.get_handler(intent, context)
        
        if not handler:
            # No specific handler - use OpenAI for general queries
            logger.info(f"No handler found for intent {intent.intent_type}, using OpenAI")
            return self._handle_with_openai(message, intent, entities, context)
        
        # Execute handler
        handler_response = handler.handle(intent, entities, context, self.state_manager)
        
        # Process handler actions
        if handler_response.actions:
            self._process_handler_actions(handler_response.actions, context)
            # Save context updates from actions
            self._save_context_updates(context.session_id, context)
        
        return {
            "success": handler_response.success,
            "message": handler_response.message,
            "metadata": handler_response.metadata,
            "next_state": handler_response.next_state,
            "handler_name": handler.__class__.__name__,
            "error": handler_response.error
        }
    
    def _handle_with_openai(self, message: str, intent, entities, 
                          context: ConversationContext) -> Dict[str, Any]:
        """Handle message with OpenAI when no specific handler available"""
        try:
            # Generate context summary
            context_summary = self.context_analyzer.get_context_summary(context)
            
            # Get entities summary
            entities_summary = self.entity_extractor.get_entities_summary(entities)
            
            # Convert context to dict for OpenAI responder
            context_dict = {
                "focus_condition": context.focus_condition,
                "focus_location": context.focus_location,
                "trial_id": context.trial_id,
                "prescreening_active": context.prescreening_data is not None,
                "conversation_state": context.conversation_state
            }
            
            # Build intent dict
            intent_dict = {
                "type": intent.intent_type.value,
                "entities": entities_summary
            }
            
            # Generate response using OpenAI
            response = self.gemini_responder.generate_response(
                message,  # Use the original message from the method parameter
                intent_dict,
                context_dict,
                context.relevant_trials if hasattr(context, 'relevant_trials') else None
            )
            
            return {
                "success": True,
                "message": response,
                "metadata": {
                    "handler": "gemini",
                    "intent_type": intent.intent_type.value
                },
                "handler_name": "OpenAIHandler"
            }
            
        except Exception as e:
            logger.error(f"Error handling with OpenAI: {str(e)}")
            # Fall back to contextual response if OpenAI fails
            response = self._generate_contextual_fallback(intent, entities, context)
            return {
                "success": True,
                "message": response,
                "metadata": {"error": "gemini_failed", "fallback": True},
                "handler_name": "FallbackHandler",
                "error": str(e)
            }
    
    def _generate_response(self, handler_result: Dict[str, Any], 
                         context: ConversationContext) -> str:
        """Generate final response from handler result"""
        response = handler_result.get("message", "")
        
        # Add any contextual information if needed
        if context.prescreening_data and not handler_result.get("metadata", {}).get("prescreening_active"):
            # Add reminder about ongoing prescreening if interrupted
            response += "\n\nWhen you're ready, we can continue with the eligibility check."
        
        return response
    
    def _update_context(self, session_id: str, handler_result: Dict[str, Any],
                       intent, entities):
        """Update context after processing"""
        updates = {}
        
        # Update state data from state manager
        updates["conversation_state"] = self.state_manager.current_state.value
        updates["state_data"] = self.state_manager.state_data
        
        # Add intent and entities to context
        updates["last_intent"] = {
            "type": intent.intent_type.value,
            "confidence": intent.confidence
        }
        
        # Update focus fields from entities
        from core.conversation.understanding import EntityType
        if EntityType.CONDITION in entities:
            updates["focus_condition"] = entities[EntityType.CONDITION].normalized_value
        if EntityType.LOCATION in entities:
            updates["focus_location"] = entities[EntityType.LOCATION].normalized_value
        
        # Apply updates from context state_data (which includes action updates)
        # This ensures all updates from handler actions are persisted
        for key in ["focus_location", "focus_condition", "trial_id", "trial_name"]:
            if key in self.state_manager.state_data:
                updates[key] = self.state_manager.state_data[key]
        
        # Apply any handler-specific updates
        if handler_result.get("metadata"):
            for key, value in handler_result["metadata"].items():
                if key in ["focus_condition", "focus_location", "trial_id", "trial_name"]:
                    updates[key] = value
        
        # Save updated context
        self.context_manager.update_context(session_id, updates)
    
    def _process_handler_actions(self, actions: List[Dict[str, Any]], 
                               context: ConversationContext):
        """Process actions returned by handlers"""
        for action in actions:
            action_type = action.get("type")
            action_data = action.get("data", {})
            
            if action_type == "update_context":
                # Apply context updates
                for key, value in action_data.items():
                    # Update both context attributes and state_data for persistence
                    setattr(context, key, value)
                    # Also update state_data to ensure persistence
                    if key in ["focus_location", "focus_condition", "trial_id", "trial_name"]:
                        context.state_data[key] = value
                
                # Clear just_showed_trial_info after being used in eligibility context
                if (hasattr(context, 'just_showed_trial_info') and 
                    context.just_showed_trial_info and 
                    action_data.get("eligibility_intent")):
                    context.just_showed_trial_info = False
                    
            elif action_type == "log_search":
                # Log search action
                logger.info(
                    "Trial search performed",
                    extra=action_data
                )
                
            elif action_type == "log_prescreening_start":
                # Log prescreening start
                logger.info(
                    "Prescreening started",
                    extra=action_data
                )
                
            # Add more action types as needed
    
    def _save_context_updates(self, session_id: str, context: ConversationContext):
        """Save context updates to persistent storage"""
        updates = {}
        
        # Save important context fields
        if context.focus_condition:
            updates["focus_condition"] = context.focus_condition
        if context.focus_location:
            updates["focus_location"] = context.focus_location
        if context.trial_id:
            updates["trial_id"] = context.trial_id
        if context.trial_name:
            updates["trial_name"] = context.trial_name
        if context.last_shown_trials:
            updates["last_shown_trials"] = context.last_shown_trials
        if hasattr(context, 'just_showed_trial_info') and context.just_showed_trial_info:
            updates["just_showed_trial_info"] = True
            
        # Save state data
        if context.state_data:
            updates["state_data"] = context.state_data
            
        if updates:
            self.context_manager.update_context(session_id, updates)
    
    def _generate_fallback_response(self, message: str, session_id: str) -> str:
        """Generate fallback response when processing fails"""
        # Return a helpful fallback message
        return ("I apologize, but I'm having trouble processing your request right now. "
               "Please try rephrasing your question or ask about clinical trials, "
               "eligibility checks, or trial information.")
    
    def _generate_contextual_fallback(self, intent, entities, context) -> str:
        """Generate contextual fallback response based on intent"""
        intent_type = intent.intent_type.value
        
        if intent_type == "GENERAL_QUERY":
            return ("I can help you find clinical trials and check eligibility. "
                   "You can ask me about:\n"
                   "- Clinical trials for specific conditions\n"
                   "- Trials in your location\n"
                   "- Eligibility requirements\n"
                   "- Trial information\n\n"
                   "What would you like to know?")
        elif intent_type == "PERSONAL_CONDITION":
            condition = None
            if entities and EntityType.CONDITION in entities:
                condition = entities[EntityType.CONDITION].normalized_value
            
            if condition:
                return (f"I understand you have {condition}. I can help you find clinical trials "
                       f"for {condition}. What location are you interested in?")
            else:
                return ("I can help you find clinical trials for your condition. "
                       "What condition are you interested in researching?")
        elif intent_type == "TRIAL_INTEREST":
            return ("Great! I can help you find clinical trials. To get started, "
                   "please tell me:\n"
                   "- What medical condition you're interested in\n"
                   "- Your preferred location\n\n"
                   "Or I can check your eligibility for specific trials.")
        else:
            return ("I can help you with clinical trial information. "
                   "Please ask about specific trials, eligibility checks, "
                   "or search for trials by condition and location.")
    
    def _serialize_intent(self, intent) -> Dict[str, Any]:
        """Serialize intent for response"""
        return {
            "type": intent.intent_type.value,
            "confidence": intent.confidence,
            "matched_pattern": intent.matched_pattern,
            "trigger_prescreening": intent.trigger_prescreening
        }
    
    def _serialize_entities(self, entities) -> Dict[str, Any]:
        """Serialize entities for response"""
        from core.conversation.understanding import EntityType
        
        serialized = {}
        for entity_type, entity in entities.items():
            serialized[entity_type.value] = {
                "value": entity.value,
                "normalized": entity.normalized_value,
                "confidence": entity.confidence,
                "source": entity.source
            }
        
        return serialized
    
    def _update_metrics(self, success: bool, processing_time: float):
        """Update processing metrics"""
        self.metrics["total_processed"] += 1
        
        if success:
            self.metrics["successful"] += 1
        else:
            self.metrics["failed"] += 1
        
        # Update average processing time
        current_avg = self.metrics["avg_processing_time"]
        total = self.metrics["total_processed"]
        self.metrics["avg_processing_time"] = (
            (current_avg * (total - 1) + processing_time) / total
        )
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get processing metrics"""
        return self.metrics.copy()
    
    def reset_metrics(self):
        """Reset processing metrics"""
        self.metrics = {
            "total_processed": 0,
            "successful": 0,
            "failed": 0,
            "avg_processing_time": 0
        }