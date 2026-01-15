"""Synchronous wrapper for Gemini responder to work with sync conversation processor"""
import asyncio
import logging
import concurrent.futures
from typing import Dict, Any, Optional, List
from core.chat.gemini_responder import GeminiResponder

logger = logging.getLogger(__name__)


class SyncGeminiResponder:
    """Synchronous wrapper for GeminiResponder"""
    
    def __init__(self):
        """Initialize with async Gemini responder"""
        self._async_responder = GeminiResponder()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    
    def _run_async(self, coro):
        """Run an async coroutine from sync context"""
        try:
            # Check if we're in an async context
            loop = asyncio.get_running_loop()
            # We're in async context - run in thread pool
            future = self._executor.submit(asyncio.run, coro)
            return future.result(timeout=30)  # 30 second timeout
        except RuntimeError:
            # No running loop - we can run directly
            return asyncio.run(coro)
        except Exception as e:
            logger.error(f"Error running async function: {str(e)}")
            raise
    
    def generate_response(
        self,
        message: str,
        intent: Dict[str, Any],
        context: Dict[str, Any],
        trials_data: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """Synchronous version of generate_response"""
        try:
            coro = self._async_responder.generate_response(
                message, intent, context, trials_data
            )
            return self._run_async(coro)
        except Exception as e:
            logger.error(f"Error in sync generate_response: {str(e)}")
            # Use the fallback method directly
            return self._async_responder.fallback_response(intent, context, trials_data)
    
    def generate_trial_summary(self, trial: Dict[str, Any]) -> str:
        """Synchronous version of generate_trial_summary"""
        try:
            coro = self._async_responder.generate_trial_summary(trial)
            return self._run_async(coro)
        except Exception as e:
            logger.error(f"Error in sync generate_trial_summary: {str(e)}")
            return f"This trial is studying treatments for {trial.get('conditions', 'various conditions')}."
    
    def generate_trial_info_response(
        self, 
        trial: Dict[str, Any], 
        message: str, 
        context: Dict[str, Any]
    ) -> str:
        """Synchronous version of generate_trial_info_response"""
        try:
            coro = self._async_responder.generate_trial_info_response(
                trial, message, context
            )
            return self._run_async(coro)
        except Exception as e:
            logger.error(f"Error in sync generate_trial_info_response: {str(e)}")
            # Use the centralized fallback method
            return self._async_responder.fallback_trial_info_response(trial)
    
    def enhance_eligibility_result(self, result_text: str, trial_name: str) -> str:
        """Synchronous version of enhance_eligibility_result"""
        try:
            coro = self._async_responder.enhance_eligibility_result(
                result_text, trial_name
            )
            return self._run_async(coro)
        except Exception as e:
            logger.error(f"Error in sync enhance_eligibility_result: {str(e)}")
            return result_text
    
    def enhance_response_conversational(
        self,
        structured_response: str,
        user_message: str,
        intent_type: str,
        context: Dict[str, Any],
        entities: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Convert a structured response into a more conversational, empathetic version.
        
        Args:
            structured_response: The original structured response from handler
            user_message: Original user message for context
            intent_type: Type of intent being processed
            context: Conversation context
            entities: Extracted entities from user message
            
        Returns:
            Enhanced conversational response
        """
        try:
            coro = self._async_responder.enhance_response_conversational(
                structured_response, user_message, intent_type, context, entities
            )
            return self._run_async(coro)
        except Exception as e:
            logger.error(f"Error enhancing response conversationally: {str(e)}")
            # Return the original response if enhancement fails
            return structured_response
    
    def __del__(self):
        """Clean up thread pool executor on deletion"""
        if hasattr(self, '_executor'):
            self._executor.shutdown(wait=False)


