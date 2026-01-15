"""
Integration adapter for the new conversation system.

This module provides an adapter that allows the new conversation system
to be used in place of the existing chat handling logic while maintaining
backward compatibility.
"""

import logging
import uuid
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime

from core.conversation.pipeline import (
    ConversationProcessor, 
    ProcessingResult,
    MiddlewarePipeline,
    LoggingMiddleware,
    ValidationMiddleware,
    MetricsMiddleware,
    ErrorHandlingMiddleware
)
from core.conversation.context import ContextStorage
from core.database import db
from core.services.gemini_service import gemini_service

logger = logging.getLogger(__name__)


class ConversationSystemAdapter:
    """
    Adapter for integrating the new conversation system with existing code.
    
    This adapter provides a compatible interface that can be used as a drop-in
    replacement for the existing chat handling logic.
    """
    
    def __init__(self, use_middleware: bool = True, enable_metrics: bool = True):
        """
        Initialize the adapter.
        
        Args:
            use_middleware: Whether to use middleware pipeline
            enable_metrics: Whether to enable metrics collection
        """
        self.processor = ConversationProcessor()
        self.context_storage = ContextStorage()
        self.enable_metrics = enable_metrics
        self.gemini = gemini_service
        
        # Set up middleware pipeline if enabled
        if use_middleware:
            self.middleware_pipeline = self._setup_middleware()
        else:
            self.middleware_pipeline = None
            
        # Feature flags
        self.feature_flags = {
            "use_new_system": True,
            "parallel_execution": False,  # For comparing old vs new
            "log_differences": True,
        }
    
    def _setup_middleware(self) -> MiddlewarePipeline:
        """Set up middleware pipeline"""
        pipeline = MiddlewarePipeline()
        
        # Add middleware in order of execution
        pipeline.add(ValidationMiddleware())
        pipeline.add(LoggingMiddleware())
        
        if self.enable_metrics:
            self.metrics_middleware = MetricsMiddleware()
            pipeline.add(self.metrics_middleware)
        
        pipeline.add(ErrorHandlingMiddleware())
        
        return pipeline
    
    async def process_chat_message(self, 
                                 message: str,
                                 session_id: Optional[str] = None,
                                 user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Process a chat message using the new system.
        
        This method provides a compatible interface with the existing chat endpoint.
        
        Args:
            message: User's message
            session_id: Optional session ID (will be generated if not provided)
            user_id: Optional user ID
            
        Returns:
            Response dictionary compatible with existing API
        """
        # Generate session ID if not provided
        if not session_id:
            session_id = str(uuid.uuid4())
            
        # Prepare request data
        # Generate unique anonymous user ID if not provided
        if not user_id:
            user_id = f"anonymous_{uuid.uuid4().hex[:8]}"
        
        request_data = {
            "message": message,
            "session_id": session_id,
            "user_id": user_id
        }
        
        # Process through pipeline
        if self.middleware_pipeline:
            # Build pipeline with processor as final handler
            handler = self.middleware_pipeline.build(self._process_with_new_system)
            result = handler(request_data)
        else:
            # Process directly
            result = self._process_with_new_system(request_data)
        
        # Convert to API response format
        return self._convert_to_api_response(result, session_id)
    
    def _process_with_new_system(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process using the new conversation system"""
        try:
            # Extract data
            message = data["message"]
            session_id = data["session_id"]
            user_id = data.get("user_id")
            
            # Process through new system
            result = self.processor.process_message(message, session_id, user_id)
            
            # Log conversation
            self._log_conversation(session_id, user_id, message, result.response, result)
            
            # Convert ProcessingResult to dict
            return {
                "success": result.success,
                "response": result.response,
                "intent": result.intent,
                "entities": result.entities,
                "metadata": result.metadata,
                "processing_time_ms": result.processing_time_ms,
                "error": result.error
            }
            
        except Exception as e:
            logger.error(f"Error in new system processing: {str(e)}", exc_info=True)
            return {
                "success": False,
                "response": "I encountered an error processing your message. Please try again.",
                "error": str(e),
                "metadata": {"error_type": type(e).__name__}
            }
    
    def _convert_to_api_response(self, result: Dict[str, Any], 
                               session_id: str) -> Dict[str, Any]:
        """Convert internal result to API response format"""
        # Build response compatible with existing API
        response = {
            "response": result.get("response", ""),
            "session_id": session_id,
            "intent": result.get("intent", {}),
            "metadata": result.get("metadata", {})
        }
        
        # Add processing info to metadata
        if "processing_time_ms" in result:
            response["metadata"]["processing_time_ms"] = result["processing_time_ms"]
            
        # Add error info if failed
        if not result.get("success", True):
            response["metadata"]["error"] = result.get("error", "Unknown error")
            
        return response
    
    def _log_conversation(self, session_id: str, user_id: str,
                        message: str, response: str,
                        result: ProcessingResult):
        """Log conversation to database"""
        try:
            # Prepare context data
            context_data = {
                "user_id": user_id,
                "intent": result.intent,
                "entities": result.entities,
                "metadata": result.metadata,
                "processing_time_ms": result.processing_time_ms,
                "success": result.success
            }
            
            # Save to database
            self.context_storage.save_conversation_turn(
                session_id=session_id,
                user_message=message,
                bot_response=response,
                context_data=context_data
            )
            
        except Exception as e:
            logger.error(f"Failed to log conversation: {str(e)}")
    
    def set_feature_flag(self, flag: str, value: bool):
        """Set a feature flag"""
        if flag in self.feature_flags:
            self.feature_flags[flag] = value
            logger.info(f"Feature flag '{flag}' set to {value}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get processing metrics"""
        metrics = {
            "processor_metrics": self.processor.get_metrics()
        }
        
        if hasattr(self, "metrics_middleware"):
            metrics["middleware_metrics"] = self.metrics_middleware.get_metrics()
            
        return metrics
    
    def reset_metrics(self):
        """Reset all metrics"""
        self.processor.reset_metrics()
        
        if hasattr(self, "metrics_middleware"):
            self.metrics_middleware.reset_metrics()
    
    async def detect_intent_with_gemini(self, message: str, context) -> str:
        """Gemini-powered intent detection"""
        intents = [
            'trial_search', 'eligibility_check', 'prescreening', 
            'contact_collection', 'general_question', 'condition_inquiry'
        ]
        
        prompt = f"""
        Classify this user message into one of these intents: {intents}
        
        Current context:
        - Focus condition: {getattr(context, 'focus_condition', 'None') if context else 'None'}
        - Current state: {getattr(context, 'current_state', 'idle') if context else 'idle'}
        - Previously mentioned conditions: {getattr(context, 'mentioned_conditions', []) if context else []}
        
        User message: "{message}"
        
        Return only the intent name.
        """
        
        try:
            response = await self.gemini.generate_text(prompt, max_tokens=50)
            detected_intent = response.strip().lower()
            
            return detected_intent if detected_intent in intents else 'general_question'
        except Exception as e:
            logger.error(f"Error in Gemini intent detection: {str(e)}")
            return 'general_question'

    async def generate_response(self, intent: str, message: str, context) -> str:
        """Generate contextual response using Gemini"""
        system_prompt = f"""
        You are a helpful clinical trials chatbot assistant. Your role is to help users find and qualify for clinical trials.
        
        Current conversation context:
        - Intent: {intent}
        - Focus condition: {getattr(context, 'focus_condition', 'None') if context else 'None'}
        - Focus location: {getattr(context, 'focus_location', 'None') if context else 'None'}
        - Current state: {getattr(context, 'current_state', 'idle') if context else 'idle'}
        
        User message: "{message}"
        
        Provide a helpful, empathetic response that guides the user towards finding suitable clinical trials.
        Keep responses concise and actionable.
        """
        
        try:
            return await self.gemini.generate_text(system_prompt, max_tokens=500)
        except Exception as e:
            logger.error(f"Error generating Gemini response: {str(e)}")
            return "I apologize, but I'm having trouble processing your request right now. Please try again."


class LegacyCompatibilityAdapter:
    """
    Provides compatibility with legacy code by wrapping the new system.
    
    This adapter mimics the old IntentClassifier and other legacy interfaces
    while using the new system underneath.
    """
    
    def __init__(self):
        self.adapter = ConversationSystemAdapter(use_middleware=False)
        self.gemini = gemini_service
        
    def classify(self, message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Mimics the old IntentClassifier.classify method.
        
        Args:
            message: User message
            context: Legacy context dictionary
            
        Returns:
            Intent dictionary in legacy format
        """
        # Convert legacy context to session ID
        session_id = context.get("session_id", str(uuid.uuid4()))
        
        # Generate unique anonymous user ID if not provided
        user_id = context.get("user_id")
        if not user_id:
            user_id = f"anonymous_{uuid.uuid4().hex[:8]}"
        
        # Process through new system
        result = self.adapter._process_with_new_system({
            "message": message,
            "session_id": session_id,
            "user_id": user_id
        })
        
        # Extract intent in legacy format
        intent = result.get("intent", {})
        entities = result.get("entities", {})
        
        # Convert to legacy format
        legacy_intent = {
            "type": intent.get("type", "general_query"),
            "confidence": intent.get("confidence", 0.5),
            "entities": self._convert_entities_to_legacy(entities),
            "trigger_prescreening": intent.get("trigger_prescreening", False),
            "in_prescreening": result.get("metadata", {}).get("prescreening_active", False)
        }
        
        return legacy_intent
    
    def _convert_entities_to_legacy(self, entities: Dict[str, Any]) -> Dict[str, Any]:
        """Convert new entity format to legacy format"""
        legacy_entities = {}
        
        for entity_type, entity_data in entities.items():
            if isinstance(entity_data, dict):
                # Use normalized value if available, otherwise raw value
                legacy_entities[entity_type] = entity_data.get("normalized", entity_data.get("value"))
            else:
                legacy_entities[entity_type] = entity_data
                
        return legacy_entities


class ParallelExecutionAdapter:
    """
    Runs both old and new systems in parallel for comparison.
    
    This adapter is useful for testing and validation during migration.
    """
    
    def __init__(self, old_system, new_adapter: ConversationSystemAdapter):
        self.old_system = old_system
        self.new_adapter = new_adapter
        self.comparison_logs = []
        
    async def process_chat_message(self, message: str, session_id: str,
                                 user_id: Optional[str] = None) -> Dict[str, Any]:
        """Process message through both systems and compare"""
        import asyncio
        
        # Run both systems in parallel
        old_task = asyncio.create_task(
            self._run_old_system(message, session_id, user_id)
        )
        new_task = asyncio.create_task(
            self.new_adapter.process_chat_message(message, session_id, user_id)
        )
        
        # Wait for both
        old_result, new_result = await asyncio.gather(old_task, new_task)
        
        # Compare results
        differences = self._compare_results(old_result, new_result)
        
        if differences:
            self._log_differences(message, old_result, new_result, differences)
            
        # Return new system result (or old based on flag)
        if self.new_adapter.feature_flags.get("use_new_system", True):
            return new_result
        else:
            return old_result
    
    async def _run_old_system(self, message: str, session_id: str,
                            user_id: Optional[str] = None) -> Dict[str, Any]:
        """Run the old system (placeholder - implement based on actual old system)"""
        # This would call the existing chat endpoint logic
        # For now, return a placeholder
        return {
            "response": "Old system response",
            "session_id": session_id,
            "intent": {"type": "unknown"},
            "metadata": {}
        }
    
    def _compare_results(self, old_result: Dict[str, Any],
                       new_result: Dict[str, Any]) -> List[str]:
        """Compare results from both systems"""
        differences = []
        
        # Compare intent classification
        old_intent = old_result.get("intent", {}).get("type")
        new_intent = new_result.get("intent", {}).get("type")
        
        if old_intent != new_intent:
            differences.append(f"Intent mismatch: old={old_intent}, new={new_intent}")
            
        # Compare response length (allowing some variation)
        old_len = len(old_result.get("response", ""))
        new_len = len(new_result.get("response", ""))
        
        if abs(old_len - new_len) > 100:
            differences.append(f"Response length differs significantly: old={old_len}, new={new_len}")
            
        return differences
    
    def _log_differences(self, message: str, old_result: Dict[str, Any],
                       new_result: Dict[str, Any], differences: List[str]):
        """Log differences between systems"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "message": message,
            "differences": differences,
            "old_intent": old_result.get("intent", {}).get("type"),
            "new_intent": new_result.get("intent", {}).get("type"),
        }
        
        self.comparison_logs.append(log_entry)
        
        logger.warning(
            f"System comparison differences detected",
            extra=log_entry
        )
    
    def get_comparison_report(self) -> Dict[str, Any]:
        """Get report of all comparisons"""
        total_comparisons = len(self.comparison_logs)
        total_differences = sum(1 for log in self.comparison_logs if log["differences"])
        
        return {
            "total_comparisons": total_comparisons,
            "total_differences": total_differences,
            "difference_rate": total_differences / total_comparisons if total_comparisons > 0 else 0,
            "recent_differences": self.comparison_logs[-10:]  # Last 10 differences
        }