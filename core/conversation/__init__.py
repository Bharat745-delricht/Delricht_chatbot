"""
Core conversation handling system.

This package provides a modular conversation management system with:
- State management and orchestration
- Intent understanding and entity extraction  
- Context management and persistence
- Handler-based request processing
- Pipeline-based conversation flow
- Integration adapters for backward compatibility
- Feature toggles for gradual rollout
"""

from .orchestration import (
    ConversationStateManager,
    ConversationFlowController,
    TransitionRules,
)
from .context import (
    ContextManager,
    ConversationContext,
    ContextStorage,
)
from .understanding import (
    IntentDetector,
    IntentType,
    EntityExtractor,
    EntityType,
    ContextAnalyzer,
)
from .pipeline import (
    ConversationProcessor,
    ProcessingResult,
    MiddlewarePipeline,
)
from .handlers import (
    HandlerRegistry,
    BaseHandler,
)
from .integration import (
    ConversationSystemAdapter,
    Feature,
    get_feature_toggle,
    is_feature_enabled,
)

__all__ = [
    # Orchestration
    'ConversationStateManager',
    'ConversationFlowController',
    'TransitionRules',
    
    # Context
    'ContextManager',
    'ConversationContext',
    'ContextStorage',
    
    # Understanding
    'IntentDetector',
    'IntentType',
    'EntityExtractor',
    'EntityType',
    'ContextAnalyzer',
    
    # Pipeline
    'ConversationProcessor',
    'ProcessingResult',
    'MiddlewarePipeline',
    
    # Handlers
    'HandlerRegistry',
    'BaseHandler',
    
    # Integration
    'ConversationSystemAdapter',
    'Feature',
    'get_feature_toggle',
    'is_feature_enabled',
]