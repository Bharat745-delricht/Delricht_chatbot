"""
Gemini Conversation System Adapter

This adapter integrates the Gemini-powered conversation system with the existing
chat endpoint, providing a clean interface for the new AI-driven approach.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from core.conversation.gemini_conversation_manager import GeminiConversationManager
from core.conversation.context import ConversationContext, ContextManager
from core.conversation.context.storage import ContextStorage
from models.schemas import ConversationState

logger = logging.getLogger(__name__)


class GeminiConversationAdapter:
    """
    Adapter that integrates Gemini-powered conversation management
    with the existing conversation system.
    """
    
    def __init__(self):
        """Initialize the Gemini conversation adapter"""
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")
        
        self.conversation_manager = GeminiConversationManager(self.gemini_api_key)
        self.context_manager = ContextManager()
        self.storage = ContextStorage()
        
        logger.info("Gemini Conversation Adapter initialized")
    
    async def process_chat_message(
        self,
        message: str,
        session_id: str,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a chat message using Gemini-powered conversation management.

        Args:
            message: User's input message
            session_id: Session identifier
            user_id: Optional user identifier

        Returns:
            Dictionary with response and metadata
        """
        try:
            # Get conversation context
            context = self.context_manager.get_context(session_id, include_history=True)

            # 🐛 DEBUG: Log prescreening state BEFORE processing
            logger.error(f"🔵 BEFORE PROCESSING - Session: {session_id}")
            logger.error(f"   Message: '{message}'")
            logger.error(f"   Context object ID: {id(context)}")
            if hasattr(context, 'prescreening_data') and context.prescreening_data:
                p_data = context.prescreening_data
                logger.error(f"   Prescreening Index: {p_data.get('current_question_index', 'N/A')}")
                logger.error(f"   Total Questions: {len(p_data.get('questions', []))}")
                logger.error(f"   Prescreening_data ID: {id(context.prescreening_data)}")
            else:
                logger.error(f"   Prescreening_data: EMPTY or missing")

            # Process message with Gemini
            result = await self.conversation_manager.process_message(message, context)

            # 🐛 DEBUG: Log prescreening state AFTER processing
            logger.error(f"AFTER PROCESSING - Session: {session_id}")
            logger.error(f"   Context object ID: {id(context)}")
            if hasattr(context, 'prescreening_data') and context.prescreening_data:
                p_data = context.prescreening_data
                logger.error(f"   🟢 Prescreening_data EXISTS")
                logger.error(f"   Index: {p_data.get('current_question_index', 'N/A')}")
                logger.error(f"   Total Questions: {len(p_data.get('questions', []))}")
                logger.error(f"   Prescreening_data ID: {id(context.prescreening_data)}")
            else:
                logger.error(f"   🔴 Prescreening_data EMPTY or MISSING")
                if hasattr(context, 'prescreening_data'):
                    logger.error(f"   Value: {context.prescreening_data}")
                    logger.error(f"   Is empty dict: {context.prescreening_data == {}}")

            # Update conversation context
            await self._update_context(
                session_id=session_id,
                user_id=user_id,
                message=message,
                result=result,
                context=context
            )

            # Return formatted response
            return {
                "response": result["response"],
                "session_id": session_id,
                "intent": result.get("intent", {}),
                "quick_replies": result.get("quick_replies"),  # Interactive buttons for frontend
                "metadata": {
                    "session_id": session_id,
                    "current_state": result.get("new_state", "initial"),
                    "handler_used": "GeminiConversationManager",
                    "processing_method": "gemini_powered",
                    **result.get("metadata", {})
                }
            }

        except Exception as e:
            logger.error(f"Error processing chat message: {str(e)}")
            return self._fallback_response(message, session_id)

    async def process_message(
        self,
        session_id: str,
        message: str
    ) -> Dict[str, Any]:
        """
        Simplified process_message method for reschedule web chat.
        Uses Gemini for natural language understanding.

        Args:
            session_id: Session identifier
            message: User's input message

        Returns:
            Dictionary with response, state, and metadata
        """
        try:
            logger.info(f"[GeminiAdapter] Processing message for session {session_id}: {message}")

            # Get conversation context
            context = self.context_manager.get_context(session_id, include_history=True)

            # Process message with Gemini
            result = await self.conversation_manager.process_message(message, context)

            # Update conversation context
            await self._update_context(
                session_id=session_id,
                user_id="web_chat_user",
                message=message,
                result=result,
                context=context
            )

            # Return formatted response for reschedule chat
            return {
                "response": result["response"],
                "state": result.get("new_state", context.conversation_state),
                "metadata": {
                    "session_id": session_id,
                    "intent": result.get("intent", {}),
                    "handler_used": "GeminiConversationManager",
                    **result.get("metadata", {})
                }
            }

        except Exception as e:
            logger.error(f"[GeminiAdapter] Error processing message: {str(e)}", exc_info=True)
            # Return graceful fallback
            return {
                "response": "I'm here to help you reschedule your appointment. Could you tell me when works better for you?",
                "state": "rescheduling_awaiting_availability",
                "metadata": {
                    "error": True,
                    "error_message": str(e)
                }
            }
    
    async def _update_context(
        self, 
        session_id: str, 
        user_id: Optional[str], 
        message: str, 
        result: Dict[str, Any],
        context: ConversationContext
    ) -> None:
        """Update conversation context with new information"""
        try:
            # Extract entities from result
            intent = result.get("intent", {})
            entities = intent.get("entities", {})

            # Update focus entities
            # Check if user is asking for "all" or "generic" studies - clear condition filter
            message_lower = message.lower()
            clear_condition_phrases = [
                "all studies", "all trials", "generic studies", "generic trials",
                "general studies", "general trials", "any studies", "any trials",
                "other studies", "other trials", "what else", "different condition",
                "something else", "other options"
            ]
            should_clear_condition = any(phrase in message_lower for phrase in clear_condition_phrases)

            if should_clear_condition:
                context.focus_condition = None  # Clear the condition filter
                logger.info(f"Cleared focus_condition - user requested all/generic studies")
            elif entities.get("condition"):
                context.focus_condition = entities["condition"]

            if entities.get("location"):
                context.focus_location = entities["location"]
            
            # Update conversation state
            new_state = result.get("new_state")
            if new_state:
                context.conversation_state = new_state
            
            # Add to conversation history
            turn = {
                "user_message": message,
                "bot_response": result["response"],
                "intent": intent,
                "timestamp": datetime.now().isoformat(),
                "metadata": result.get("metadata", {})
            }
            
            if not context.conversation_history:
                context.conversation_history = []
            context.conversation_history.append(turn)
            
            # Update state data
            context.state_data.update({
                "last_intent": intent.get("type"),
                "last_confidence": intent.get("confidence"),
                "processing_method": "gemini_powered",
                "entities_collected": entities
            })
            
            # Handle state data updates from contact collection service
            state_data_updates = result.get("state_data_updates")
            if state_data_updates:
                context.state_data.update(state_data_updates)
            
            # CRITICAL FIX: Update context metadata with conversation result metadata
            # This ensures prescreening_complete and other metadata persists for subsequent messages
            result_metadata = result.get("metadata", {})
            if result_metadata:
                if not hasattr(context, 'metadata') or context.metadata is None:
                    context.metadata = {}
                context.metadata.update(result_metadata)
            
            # Save context - use update_context which exists
            # Serialize datetime objects in conversation history
            serializable_history = []
            if context.conversation_history:
                for turn in context.conversation_history:
                    serializable_turn = dict(turn)
                    if 'timestamp' in serializable_turn and hasattr(serializable_turn['timestamp'], 'isoformat'):
                        serializable_turn['timestamp'] = serializable_turn['timestamp'].isoformat()
                    serializable_history.append(serializable_turn)
            
            # Save the conversation turn to chat_logs table
            context_data = {
                "focus_condition": context.focus_condition,
                "focus_location": context.focus_location,
                "conversation_state": context.conversation_state,
                "state_data": context.state_data,
                "user_id": user_id or "anonymous"
            }

            message_id = self.storage.save_conversation_turn(
                session_id=session_id,
                user_message=message,
                bot_response=result["response"],
                context_data=context_data
            )

            # Add message_id to result metadata for feedback functionality
            if message_id and "metadata" not in result:
                result["metadata"] = {}
            if message_id:
                result["metadata"]["message_id"] = message_id
            
            # Also save to conversation_context table
            # CRITICAL FIX: Include prescreening_data in context save
            context_updates = {
                "focus_condition": context.focus_condition,
                "focus_location": context.focus_location,
                "conversation_state": context.conversation_state,
                "conversation_history": serializable_history,
                "state_data": context.state_data,
                "metadata": getattr(context, 'metadata', {})
            }

            # Add prescreening_data if present (critical for multi-question flow)
            # CRITICAL: Save prescreening_data even if empty to maintain state
            if hasattr(context, 'prescreening_data'):
                # ALWAYS include prescreening_data in updates (even if empty)
                context_updates["prescreening_data"] = context.prescreening_data

                if context.prescreening_data:  # Not empty - log details
                    logger.error(f"💾 SAVING CONTEXT WITH DATA - Session: {session_id}")
                    logger.error(f"   Prescreening Index: {context.prescreening_data.get('current_question_index', 'N/A')}")
                    logger.error(f"   Total Questions: {len(context.prescreening_data.get('questions', []))}")
                else:  # Empty dict - still save it but log warning
                    logger.error(f"⚠️  SAVING EMPTY prescreening_data - Session: {session_id}")
                    logger.error(f"   This indicates prescreening was cleared/reset during processing")
            else:
                logger.error(f"⚠️  NO prescreening_data ATTRIBUTE - Session: {session_id}")

            # CRITICAL FIX: Persist the modified context WITHOUT reloading from DB
            # The context object was modified by process_message, so use it directly

            # Apply any additional updates from context_updates
            if 'conversation_state' in context_updates:
                context.conversation_state = context_updates['conversation_state']
            if 'conversation_history' in context_updates:
                context.conversation_history = context_updates['conversation_history']
            if 'state_data' in context_updates:
                context.state_data.update(context_updates['state_data'])
            if 'metadata' in context_updates:
                if not hasattr(context, 'metadata') or context.metadata is None:
                    context.metadata = {}
                context.metadata.update(context_updates['metadata'])

            # Update timestamp
            context.last_updated = datetime.now(timezone.utc)

            logger.error(f"💾 PERSISTING MODIFIED CONTEXT - Prescreening_data has {len(context.prescreening_data)} keys")

            # Persist the MODIFIED context object (preserves all changes from process_message)
            self.context_manager._persist_context(context)

            # DON'T clear cache - update it with the modified context
            self.context_manager._context_cache[session_id] = context

            logger.error(f"✅ Context persisted and cached")

            # 🐛 DEBUG: Verify what was actually saved by reloading
            logger.error(f"🔄 VERIFYING SAVE - Reloading context from DB...")
            verification_context = self.context_manager.get_context(session_id, include_history=False)
            if hasattr(verification_context, 'prescreening_data') and verification_context.prescreening_data:
                logger.error(f"✅ VERIFIED IN DB:")
                logger.error(f"   Prescreening Index in DB: {verification_context.prescreening_data.get('current_question_index', 'N/A')}")
            else:
                logger.error(f"❌ VERIFICATION FAILED: No prescreening_data found in reloaded context!")
            
        except Exception as e:
            logger.error(f"Error updating context: {str(e)}")
    
    def _fallback_response(self, message: str, session_id: str) -> Dict[str, Any]:
        """Fallback response when Gemini processing fails - provide context-aware recovery"""
        # Get context to provide better fallback
        try:
            context = self.context_manager.get_context(session_id)
            
            # Context-aware fallback based on current state
            if context.conversation_state == "trials_shown":
                response = "I'm having trouble understanding your response. Would you like me to check your eligibility for the trials I showed you, or would you like to search for different trials?"
            elif context.conversation_state in ["prescreening_active", "awaiting_age"]:
                response = "I'm having trouble processing your answer. Could you please provide your age as a number (for example: 35)?"
            elif context.conversation_state == "awaiting_location":
                response = "I'm having trouble understanding your location. Could you please tell me your city or state? (for example: Dallas or Texas)"
            elif context.conversation_state == "awaiting_condition":
                response = "I'm having trouble understanding your medical condition. Could you please tell me the name of your condition? (for example: diabetes, gout, or arthritis)"
            elif context.focus_condition and not context.focus_location:
                response = f"I understand you have {context.focus_condition}. To help you find clinical trials, I need to know your location. What city or state are you in?"
            elif context.focus_location and not context.focus_condition:
                response = f"I know you're in {context.focus_location}. What medical condition are you interested in finding trials for?"
            else:
                response = "I'm having trouble understanding. Let me help you find clinical trials. What medical condition are you interested in?"
                
        except Exception as e:
            logger.error(f"Error in fallback response: {str(e)}")
            response = "I apologize, but I'm having trouble processing your request right now. Let's start fresh - what medical condition are you interested in finding trials for?"
        
        return {
            "response": response,
            "session_id": session_id,
            "intent": {"type": "general_query", "confidence": 0.3},
            "metadata": {
                "session_id": session_id,
                "current_state": context.conversation_state if 'context' in locals() else "initial",
                "handler_used": "FallbackHandler",
                "processing_method": "context_aware_fallback",
                "error": True,
                "recovery_attempted": True
            }
        }
    
    def get_conversation_state(self, session_id: str) -> Dict[str, Any]:
        """Get current conversation state for a session"""
        try:
            context = self.context_manager.get_context(session_id)
            return {
                "state": context.conversation_state,
                "condition": context.focus_condition,
                "location": context.focus_location,
                "history_length": len(context.conversation_history) if context.conversation_history else 0,
                "metadata": getattr(context, 'metadata', {})
            }
        except Exception as e:
            logger.error(f"Error getting conversation state: {str(e)}")
            return {"state": "initial", "condition": None, "location": None, "history_length": 0}
    
    def reset_conversation(self, session_id: str) -> Dict[str, Any]:
        """Reset conversation state for a session"""
        try:
            # Create fresh context
            context = ConversationContext(
                session_id=session_id,
                conversation_state="initial",
                focus_condition=None,
                focus_location=None,
                conversation_history=[],
                state_data={}
            )
            
            # Save clean context
            self.context_manager.save_context(session_id, context)
            
            return {
                "status": "success",
                "message": "Conversation reset successfully",
                "new_state": "initial"
            }
            
        except Exception as e:
            logger.error(f"Error resetting conversation: {str(e)}")
            return {
                "status": "error",
                "message": "Failed to reset conversation",
                "error": str(e)
            }


