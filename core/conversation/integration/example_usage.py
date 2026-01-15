"""
Example usage of the new conversation system integration.

This module demonstrates how to integrate the new conversation system
into the existing application.
"""

import asyncio
import logging
from typing import Dict, Any

from core.conversation.integration import (
    ConversationSystemAdapter,
    LegacyCompatibilityAdapter,
    get_feature_toggle,
    Feature,
    FeatureState
)

logger = logging.getLogger(__name__)


async def example_direct_usage():
    """Example of using the new system directly"""
    
    # Create adapter with middleware enabled
    adapter = ConversationSystemAdapter(use_middleware=True)
    
    # Process a message
    response = await adapter.process_chat_message(
        message="I'm looking for diabetes trials in Boston",
        session_id="example-session-123",
        user_id="user-456"
    )
    
    logger.info(f"Response: {response['response']}")
    logger.info(f"Intent: {response['intent']}")
    logger.info(f"Processing time: {response['metadata'].get('processing_time_ms')}ms")
    
    # Check metrics
    metrics = adapter.get_metrics()
    logger.info(f"Metrics: {metrics}")


async def example_legacy_compatibility():
    """Example of using legacy compatibility mode"""
    
    # Create legacy adapter
    legacy_adapter = LegacyCompatibilityAdapter()
    
    # Use like old classifier
    context = {
        "session_id": "legacy-session-789",
        "user_id": "legacy-user",
        "focus_condition": "diabetes",
        "focus_location": "Boston"
    }
    
    intent = legacy_adapter.classify(
        message="Am I eligible for this trial?",
        context=context
    )
    
    logger.info(f"Legacy intent: {intent}")


async def example_feature_toggle_usage():
    """Example of using feature toggles"""
    
    feature_toggle = get_feature_toggle()
    
    # Enable new system for 25% of users
    feature_toggle.set_feature(
        Feature.NEW_CONVERSATION_SYSTEM,
        FeatureState.PERCENTAGE,
        percentage=25
    )
    
    # Check if enabled for specific session
    session_id = "test-session-001"
    if feature_toggle.is_enabled(Feature.NEW_CONVERSATION_SYSTEM, session_id=session_id):
        logger.info(f"New system enabled for session {session_id}")
        adapter = ConversationSystemAdapter()
        # Use new system
    else:
        logger.info(f"Using old system for session {session_id}")
        # Use old system
    
    # Enable for specific users
    feature_toggle.set_feature(
        Feature.NEW_CONVERSATION_SYSTEM,
        FeatureState.USER_LIST,
        user_list=["beta-user-1", "beta-user-2", "beta-user-3"]
    )
    
    # Check feature status
    status = feature_toggle.get_status()
    logger.info(f"Feature status: {status}")


async def example_modified_chat_endpoint():
    """Example of how to modify the existing chat endpoint"""
    
    from fastapi import APIRouter
    from pydantic import BaseModel
    from typing import Optional
    
    # Import existing components
    from core.intents.classifier import IntentClassifier
    from core.conversation.integration import (
        ConversationSystemAdapter,
        is_feature_enabled,
        Feature
    )
    
    router = APIRouter()
    
    # Initialize both systems
    old_classifier = IntentClassifier()
    new_adapter = ConversationSystemAdapter()
    
    class ChatRequest(BaseModel):
        message: str
        session_id: Optional[str] = None
        user_id: Optional[str] = None
    
    @router.post("/chat")
    async def integrated_chat(request: ChatRequest):
        """Modified chat endpoint with feature toggle"""
        
        # Check if new system is enabled for this user/session
        if is_feature_enabled(
            Feature.NEW_CONVERSATION_SYSTEM,
            user_id=request.user_id,
            session_id=request.session_id
        ):
            # Use new system
            response = await new_adapter.process_chat_message(
                message=request.message,
                session_id=request.session_id,
                user_id=request.user_id
            )
            
            # Add flag to indicate new system was used
            response["metadata"]["system"] = "new"
            
        else:
            # Use old system (existing logic)
            # ... existing chat processing logic ...
            response = {
                "response": "Old system response",
                "session_id": request.session_id,
                "metadata": {"system": "old"}
            }
        
        return response


async def example_gradual_rollout():
    """Example of gradual rollout process"""
    
    from core.conversation.integration import SystemCutoverManager
    
    cutover_manager = SystemCutoverManager()
    
    # Start with 10% of traffic
    status = cutover_manager.start_cutover(initial_percentage=10)
    logger.info(f"Cutover started: {status}")
    
    # Simulate monitoring and gradual increase
    for i in range(9):  # Increase to 100% in steps
        await asyncio.sleep(5)  # Wait 5 seconds between increases
        
        status = cutover_manager.increase_traffic(increment=10)
        logger.info(f"Traffic increased: {status}")
        
        # Check if we need to rollback
        if status.get("status") == "paused":
            logger.warning("Issues detected, pausing rollout")
            break
    
    # Complete cutover if successful
    if status.get("percentage", 0) >= 100:
        final_status = cutover_manager.complete_cutover()
        logger.info(f"Cutover completed: {final_status}")


async def example_parallel_execution():
    """Example of running both systems in parallel for comparison"""
    
    from core.conversation.integration import ParallelExecutionAdapter
    
    # Create mock old system
    class MockOldSystem:
        async def process(self, message, session_id, user_id):
            return {
                "response": "Old system: I found trials for you",
                "session_id": session_id,
                "intent": {"type": "trial_search"}
            }
    
    # Set up parallel execution
    old_system = MockOldSystem()
    new_adapter = ConversationSystemAdapter()
    
    parallel_adapter = ParallelExecutionAdapter(old_system, new_adapter)
    
    # Process messages through both systems
    test_messages = [
        "I'm looking for diabetes trials",
        "Am I eligible?",
        "Tell me about the trial in Boston"
    ]
    
    for message in test_messages:
        response = await parallel_adapter.process_chat_message(
            message=message,
            session_id="parallel-test-001"
        )
        logger.info(f"Message: {message}")
        logger.info(f"Response: {response['response'][:100]}...")
        logger.info(f"System used: {response['metadata'].get('system', 'unknown')}")
    
    # Get comparison report
    report = parallel_adapter.get_comparison_report()
    logger.info(f"Comparison report: {report}")


if __name__ == "__main__":
    # Run examples
    asyncio.run(example_direct_usage())
    logger.info("=" * 50)
    
    asyncio.run(example_legacy_compatibility())
    logger.info("=" * 50)
    
    asyncio.run(example_feature_toggle_usage())
    logger.info("=" * 50)
    
    asyncio.run(example_gradual_rollout())