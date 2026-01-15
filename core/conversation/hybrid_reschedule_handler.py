"""
Hybrid Reschedule Handler - Combines Gemini NLU with RescheduleFlowHandler execution

This handler provides the best of both worlds:
1. Gemini for natural language understanding and conversational responses
2. RescheduleFlowHandler for workflow execution and CRIO integration
"""

from typing import Optional, Dict
from datetime import datetime
import logging
import json

from core.conversation.gemini_adapter import GeminiConversationAdapter
from core.conversation.reschedule_flow_handler import RescheduleFlowHandler
from core.database import db

logger = logging.getLogger(__name__)


class HybridRescheduleHandler:
    """
    Hybrid handler that uses Gemini for NLU and RescheduleFlowHandler for execution.

    Architecture:
    - User message → Gemini (understand intent, extract entities)
    - Gemini intent → RescheduleFlowHandler (execute workflow, book appointments)
    - Response from both → Combined conversational reply
    """

    def __init__(self):
        try:
            self.gemini_adapter = GeminiConversationAdapter()
            logger.info("✓ Gemini adapter initialized")
        except Exception as e:
            logger.warning(f"⚠️  Could not initialize Gemini adapter: {e}")
            self.gemini_adapter = None

        self.flow_handler = RescheduleFlowHandler()
        logger.info("✓ RescheduleFlowHandler initialized")

    async def process_message(
        self,
        session_id: str,
        phone_number: str,
        message: str,
        current_state: Optional[str] = None
    ) -> Dict:
        """
        Process a reschedule message using hybrid Gemini + State Machine approach.

        Args:
            session_id: Conversation session ID
            phone_number: Patient phone number (for SMS compatibility)
            message: User's message
            current_state: Current conversation state

        Returns:
            {
                'response': str,  # Combined conversational response
                'new_state': str,  # Updated state
                'data': dict,  # Additional data (slots, etc.)
                'metadata': dict  # Processing metadata
            }
        """
        try:
            logger.info(f"[HYBRID] Processing message for session {session_id}")
            logger.info(f"[HYBRID] Current state: {current_state}")
            logger.info(f"[HYBRID] Message: {message}")

            # Get current state if not provided
            if not current_state:
                current_state = self._get_session_state(session_id)
                logger.info(f"[HYBRID] Retrieved state from DB: {current_state}")

            # Determine processing strategy based on state
            if current_state in [
                'rescheduling_awaiting_confirmation',
                'rescheduling_awaiting_availability',
                'rescheduling_awaiting_selection'
            ]:
                # Workflow states - use flow handler with Gemini enrichment
                return await self._process_with_flow_handler(
                    session_id, phone_number, message, current_state
                )
            else:
                # General conversation - use Gemini primarily
                return await self._process_with_gemini(
                    session_id, message, current_state
                )

        except Exception as e:
            logger.error(f"[HYBRID] Error processing message: {str(e)}", exc_info=True)
            return {
                'response': "I apologize for the confusion. Let me help you reschedule. When would work better for you?",
                'new_state': 'rescheduling_awaiting_availability',
                'metadata': {
                    'error': True,
                    'error_message': str(e)
                }
            }

    async def _process_with_flow_handler(
        self,
        session_id: str,
        phone_number: str,
        message: str,
        current_state: str
    ) -> Dict:
        """
        Process message through RescheduleFlowHandler with Gemini enhancement.

        Flow:
        1. Use Gemini to understand message intent
        2. Pass to RescheduleFlowHandler for execution
        3. Enhance response with Gemini conversational style
        """
        logger.info(f"[HYBRID] Using flow handler for state: {current_state}")

        # SKIP Gemini for workflow states - flow handler has keyword detection
        # Gemini is too slow and causes timeouts for simple messages like "RESCHEDULE"
        # The flow handler can handle these without AI assistance
        logger.info(f"[HYBRID] Bypassing Gemini for workflow state - using flow handler directly")

        # Execute workflow through flow handler
        flow_result = await self.flow_handler.process_message(
            session_id=session_id,
            phone_number=phone_number,
            message=message,
            current_state=current_state
        )

        logger.info(f"[HYBRID] Flow handler result: {flow_result}")

        # Map flow handler response to web chat format
        response_text = flow_result.get('response', 'Processing your request...')
        new_state = flow_result.get('new_state', current_state)

        return {
            'response': response_text,
            'new_state': new_state,
            'data': flow_result.get('data', {}),
            'metadata': {
                'handler': 'hybrid_flow_handler',
                'gemini_intent': gemini_intent,
                'flow_status': flow_result.get('status'),
                **flow_result.get('metadata', {})
            }
        }

    async def _process_with_gemini(
        self,
        session_id: str,
        message: str,
        current_state: Optional[str]
    ) -> Dict:
        """
        Process message primarily with Gemini for general conversation.
        """
        logger.info(f"[HYBRID] Using Gemini for general conversation")

        if not self.gemini_adapter:
            # Fallback if Gemini not available
            return {
                'response': "I'm here to help you reschedule your appointment. Could you tell me when works better for you?",
                'new_state': 'rescheduling_awaiting_availability',
                'metadata': {'gemini_available': False}
            }

        try:
            result = await self.gemini_adapter.process_message(
                session_id=session_id,
                message=message
            )

            return {
                'response': result.get('response'),
                'new_state': result.get('state', current_state),
                'data': {},
                'metadata': {
                    'handler': 'hybrid_gemini',
                    **result.get('metadata', {})
                }
            }

        except Exception as e:
            logger.error(f"[HYBRID] Gemini processing failed: {e}", exc_info=True)
            return {
                'response': "I'm here to help you reschedule. When would work better for you?",
                'new_state': 'rescheduling_awaiting_availability',
                'metadata': {'error': True, 'error_message': str(e)}
            }

    def _get_session_state(self, session_id: str) -> Optional[str]:
        """Get current conversation state for session"""
        try:
            result = db.execute_query("""
                SELECT current_state
                FROM conversation_context
                WHERE session_id = %s AND active = true
            """, (session_id,))

            if result:
                return result[0]['current_state']

            return None

        except Exception as e:
            logger.error(f"[HYBRID] Error getting session state: {str(e)}")
            return None
