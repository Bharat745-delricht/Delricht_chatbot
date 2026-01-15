"""Integration components for the new conversation system"""

from .adapter import (
    ConversationSystemAdapter,
    LegacyCompatibilityAdapter,
    ParallelExecutionAdapter
)
from .feature_toggle import (
    Feature,
    FeatureState,
    FeatureToggle,
    get_feature_toggle,
    is_feature_enabled
)
from .migration import (
    ConversationMigrator,
    SystemCutoverManager,
    MigrationStatus,
    MigrationReport
)

__all__ = [
    # Adapters
    'ConversationSystemAdapter',
    'LegacyCompatibilityAdapter',
    'ParallelExecutionAdapter',
    
    # Feature toggles
    'Feature',
    'FeatureState',
    'FeatureToggle',
    'get_feature_toggle',
    'is_feature_enabled',
    
    # Migration
    'ConversationMigrator',
    'SystemCutoverManager',
    'MigrationStatus',
    'MigrationReport',
]