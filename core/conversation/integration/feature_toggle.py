"""
Feature toggle system for gradual migration.

This module provides feature toggles that allow gradual rollout of the new
conversation system and easy rollback if issues are detected.
"""

import os
import json
import logging
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class FeatureState(str, Enum):
    """Feature toggle states"""
    OFF = "off"
    ON = "on"
    PERCENTAGE = "percentage"  # Gradual rollout
    USER_LIST = "user_list"   # Specific users only
    A_B_TEST = "a_b_test"     # A/B testing


class Feature(str, Enum):
    """Available feature toggles"""
    NEW_CONVERSATION_SYSTEM = "new_conversation_system"
    NEW_INTENT_DETECTION = "new_intent_detection"
    NEW_ENTITY_EXTRACTION = "new_entity_extraction"
    NEW_STATE_MANAGEMENT = "new_state_management"
    NEW_HANDLERS = "new_handlers"
    PARALLEL_EXECUTION = "parallel_execution"
    ENHANCED_LOGGING = "enhanced_logging"
    RESPONSE_CACHING = "response_caching"


class FeatureToggle:
    """
    Manages feature toggles for the conversation system.
    
    Features can be controlled via:
    - Environment variables
    - Configuration file
    - Runtime API calls
    - Database settings
    """
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file
        self.features: Dict[Feature, Dict[str, Any]] = {}
        self._load_defaults()
        self._load_from_env()
        if config_file:
            self._load_from_file(config_file)
            
        # Callbacks for feature changes
        self.change_callbacks: Dict[Feature, List[Callable]] = {}
        
    def _load_defaults(self):
        """Load default feature settings"""
        # Default all features to OFF for safety
        for feature in Feature:
            self.features[feature] = {
                "state": FeatureState.OFF,
                "percentage": 0,
                "user_list": [],
                "metadata": {
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "updated_by": "system"
                }
            }
    
    def _load_from_env(self):
        """Load feature settings from environment variables"""
        for feature in Feature:
            env_key = f"FEATURE_{feature.value.upper()}"
            env_value = os.environ.get(env_key)
            
            if env_value:
                if env_value.lower() in ["true", "on", "1"]:
                    self.set_feature(feature, FeatureState.ON)
                elif env_value.lower() in ["false", "off", "0"]:
                    self.set_feature(feature, FeatureState.OFF)
                elif env_value.isdigit():
                    # Percentage rollout
                    percentage = int(env_value)
                    self.set_feature(feature, FeatureState.PERCENTAGE, percentage=percentage)
    
    def _load_from_file(self, config_file: str):
        """Load feature settings from configuration file"""
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                
            for feature_name, settings in config.get("features", {}).items():
                try:
                    feature = Feature(feature_name)
                    state = FeatureState(settings.get("state", "off"))
                    
                    self.features[feature].update({
                        "state": state,
                        "percentage": settings.get("percentage", 0),
                        "user_list": settings.get("user_list", []),
                        "metadata": settings.get("metadata", {})
                    })
                except ValueError:
                    logger.warning(f"Unknown feature in config: {feature_name}")
                    
        except Exception as e:
            logger.error(f"Failed to load feature config from {config_file}: {str(e)}")
    
    def is_enabled(self, feature: Feature, user_id: Optional[str] = None,
                  session_id: Optional[str] = None) -> bool:
        """
        Check if a feature is enabled.
        
        Args:
            feature: Feature to check
            user_id: Optional user ID for user-specific features
            session_id: Optional session ID for A/B testing
            
        Returns:
            True if feature is enabled for this context
        """
        if feature not in self.features:
            return False
            
        settings = self.features[feature]
        state = settings["state"]
        
        if state == FeatureState.OFF:
            return False
        elif state == FeatureState.ON:
            return True
        elif state == FeatureState.PERCENTAGE:
            # Use session ID for consistent experience
            if session_id:
                # Simple hash-based percentage (deterministic)
                hash_value = hash(f"{feature.value}:{session_id}") % 100
                return hash_value < settings["percentage"]
            else:
                # Random for no session
                import random
                return random.randint(0, 99) < settings["percentage"]
        elif state == FeatureState.USER_LIST:
            return user_id in settings["user_list"]
        elif state == FeatureState.A_B_TEST:
            # Simple A/B test based on session ID
            if session_id:
                return hash(session_id) % 2 == 0
            else:
                return False
                
        return False
    
    def set_feature(self, feature: Feature, state: FeatureState,
                   percentage: Optional[int] = None,
                   user_list: Optional[List[str]] = None,
                   updated_by: str = "api"):
        """
        Set a feature toggle state.
        
        Args:
            feature: Feature to set
            state: New state
            percentage: Percentage for gradual rollout
            user_list: List of users for user-specific rollout
            updated_by: Who is making the change
        """
        if feature not in self.features:
            self.features[feature] = {"metadata": {}}
            
        old_state = self.features[feature].get("state")
        
        self.features[feature].update({
            "state": state,
            "metadata": {
                "updated_at": datetime.now().isoformat(),
                "updated_by": updated_by,
                "previous_state": old_state
            }
        })
        
        if percentage is not None:
            self.features[feature]["percentage"] = max(0, min(100, percentage))
            
        if user_list is not None:
            self.features[feature]["user_list"] = user_list
            
        # Save to file if configured
        if self.config_file:
            self._save_to_file()
            
        # Trigger callbacks
        self._trigger_callbacks(feature, old_state, state)
        
        logger.info(
            f"Feature {feature.value} changed from {old_state} to {state}",
            extra={
                "feature": feature.value,
                "old_state": old_state,
                "new_state": state,
                "updated_by": updated_by
            }
        )
    
    def gradual_rollout(self, feature: Feature, target_percentage: int,
                       increment: int = 10, updated_by: str = "gradual_rollout"):
        """
        Gradually increase feature rollout percentage.
        
        Args:
            feature: Feature to roll out
            target_percentage: Target percentage
            increment: Percentage to increase
            updated_by: Who is making the change
        """
        current = self.features.get(feature, {}).get("percentage", 0)
        new_percentage = min(current + increment, target_percentage, 100)
        
        self.set_feature(
            feature,
            FeatureState.PERCENTAGE,
            percentage=new_percentage,
            updated_by=updated_by
        )
        
        return new_percentage
    
    def rollback_feature(self, feature: Feature, updated_by: str = "rollback"):
        """
        Rollback a feature to OFF state.
        
        Args:
            feature: Feature to rollback
            updated_by: Who is making the change
        """
        self.set_feature(feature, FeatureState.OFF, updated_by=updated_by)
    
    def register_callback(self, feature: Feature, callback: Callable):
        """
        Register a callback for feature changes.
        
        Args:
            feature: Feature to monitor
            callback: Function to call on change
        """
        if feature not in self.change_callbacks:
            self.change_callbacks[feature] = []
            
        self.change_callbacks[feature].append(callback)
    
    def _trigger_callbacks(self, feature: Feature, old_state: Any, new_state: Any):
        """Trigger callbacks for feature change"""
        if feature in self.change_callbacks:
            for callback in self.change_callbacks[feature]:
                try:
                    callback(feature, old_state, new_state)
                except Exception as e:
                    logger.error(f"Error in feature toggle callback: {str(e)}")
    
    def _save_to_file(self):
        """Save current settings to configuration file"""
        if not self.config_file:
            return
            
        try:
            config = {
                "features": {
                    feature.value: {
                        "state": settings["state"].value,
                        "percentage": settings.get("percentage", 0),
                        "user_list": settings.get("user_list", []),
                        "metadata": settings.get("metadata", {})
                    }
                    for feature, settings in self.features.items()
                }
            }
            
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save feature config: {str(e)}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status of all features"""
        return {
            feature.value: {
                "state": settings["state"].value,
                "percentage": settings.get("percentage", 0),
                "user_count": len(settings.get("user_list", [])),
                "metadata": settings.get("metadata", {})
            }
            for feature, settings in self.features.items()
        }
    
    def get_enabled_features(self, user_id: Optional[str] = None,
                           session_id: Optional[str] = None) -> List[str]:
        """Get list of enabled features for a given context"""
        enabled = []
        
        for feature in Feature:
            if self.is_enabled(feature, user_id, session_id):
                enabled.append(feature.value)
                
        return enabled


# Global feature toggle instance
_feature_toggle = None


def get_feature_toggle() -> FeatureToggle:
    """Get global feature toggle instance"""
    global _feature_toggle
    
    if _feature_toggle is None:
        # Initialize with environment-based config file if set
        config_file = os.environ.get("FEATURE_TOGGLE_CONFIG")
        _feature_toggle = FeatureToggle(config_file)
        
    return _feature_toggle


def is_feature_enabled(feature: Feature, user_id: Optional[str] = None,
                      session_id: Optional[str] = None) -> bool:
    """Convenience function to check if a feature is enabled"""
    return get_feature_toggle().is_enabled(feature, user_id, session_id)