"""
Input and output validators for the conversation pipeline.

This module provides validation functions to ensure data integrity
throughout the processing pipeline.
"""

import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from core.conversation.understanding import IntentType
from models.schemas import ConversationState


class InputValidator:
    """Validates input data for the conversation pipeline"""
    
    # Maximum allowed message length
    MAX_MESSAGE_LENGTH = 1000
    
    # Minimum message length (to avoid empty messages)
    MIN_MESSAGE_LENGTH = 1
    
    # Valid session ID pattern (alphanumeric + hyphens/underscores)
    SESSION_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')
    
    # Maximum session ID length
    MAX_SESSION_ID_LENGTH = 128
    
    @classmethod
    def validate_message(cls, message: Any) -> Tuple[bool, Optional[str]]:
        """
        Validate user message.
        
        Args:
            message: Message to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(message, str):
            return False, "Message must be a string"
        
        # Check length
        if len(message) < cls.MIN_MESSAGE_LENGTH:
            return False, "Message cannot be empty"
            
        if len(message) > cls.MAX_MESSAGE_LENGTH:
            return False, f"Message too long (max {cls.MAX_MESSAGE_LENGTH} characters)"
        
        # Check for potentially harmful content (basic check)
        if cls._contains_harmful_patterns(message):
            return False, "Message contains invalid content"
        
        return True, None
    
    @classmethod
    def validate_session_id(cls, session_id: Any) -> Tuple[bool, Optional[str]]:
        """
        Validate session ID.
        
        Args:
            session_id: Session ID to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(session_id, str):
            return False, "Session ID must be a string"
        
        if not session_id:
            return False, "Session ID cannot be empty"
        
        if len(session_id) > cls.MAX_SESSION_ID_LENGTH:
            return False, f"Session ID too long (max {cls.MAX_SESSION_ID_LENGTH} characters)"
        
        if not cls.SESSION_ID_PATTERN.match(session_id):
            return False, "Session ID contains invalid characters"
        
        return True, None
    
    @classmethod
    def validate_user_id(cls, user_id: Any) -> Tuple[bool, Optional[str]]:
        """
        Validate user ID.
        
        Args:
            user_id: User ID to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if user_id is None:
            return True, None  # User ID is optional
        
        if not isinstance(user_id, str):
            return False, "User ID must be a string"
        
        if len(user_id) > cls.MAX_SESSION_ID_LENGTH:
            return False, f"User ID too long (max {cls.MAX_SESSION_ID_LENGTH} characters)"
        
        return True, None
    
    @classmethod
    def validate_request(cls, data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate complete request data.
        
        Args:
            data: Request data dictionary
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        # Validate message
        is_valid, error = cls.validate_message(data.get("message"))
        if not is_valid:
            errors.append(f"Message: {error}")
        
        # Validate session ID
        is_valid, error = cls.validate_session_id(data.get("session_id"))
        if not is_valid:
            errors.append(f"Session ID: {error}")
        
        # Validate user ID if present
        is_valid, error = cls.validate_user_id(data.get("user_id"))
        if not is_valid:
            errors.append(f"User ID: {error}")
        
        return len(errors) == 0, errors
    
    @classmethod
    def _contains_harmful_patterns(cls, message: str) -> bool:
        """Check for potentially harmful patterns (basic implementation)"""
        # This is a placeholder - in production, use more sophisticated checks
        harmful_patterns = [
            r'<script',  # Script tags
            r'javascript:',  # JavaScript URLs
            r'data:text/html',  # Data URLs
            r'\x00',  # Null bytes
        ]
        
        message_lower = message.lower()
        for pattern in harmful_patterns:
            if re.search(pattern, message_lower):
                return True
        
        return False


class ContextValidator:
    """Validates conversation context data"""
    
    @classmethod
    def validate_conversation_state(cls, state: Any) -> Tuple[bool, Optional[str]]:
        """
        Validate conversation state.
        
        Args:
            state: State to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if state is None:
            return True, None  # State can be None initially
        
        if not isinstance(state, str):
            return False, "Conversation state must be a string"
        
        # Check if it's a valid state
        valid_states = [s.value for s in ConversationState]
        if state not in valid_states:
            return False, f"Invalid conversation state: {state}"
        
        return True, None
    
    @classmethod
    def validate_context_data(cls, context: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate context data structure.
        
        Args:
            context: Context dictionary
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        # Validate conversation state
        is_valid, error = cls.validate_conversation_state(
            context.get("conversation_state")
        )
        if not is_valid:
            errors.append(f"State: {error}")
        
        # Validate data types for key fields
        if "focus_condition" in context and context["focus_condition"] is not None:
            if not isinstance(context["focus_condition"], str):
                errors.append("Focus condition must be a string")
        
        if "focus_location" in context and context["focus_location"] is not None:
            if not isinstance(context["focus_location"], str):
                errors.append("Focus location must be a string")
        
        if "trial_id" in context and context["trial_id"] is not None:
            if not isinstance(context["trial_id"], (int, str)):
                errors.append("Trial ID must be a number or string")
        
        # Validate collections
        if "mentioned_conditions" in context:
            if not isinstance(context["mentioned_conditions"], (list, set)):
                errors.append("Mentioned conditions must be a list or set")
        
        if "mentioned_locations" in context:
            if not isinstance(context["mentioned_locations"], (list, set)):
                errors.append("Mentioned locations must be a list or set")
        
        return len(errors) == 0, errors


class OutputValidator:
    """Validates output data from the pipeline"""
    
    @classmethod
    def validate_response(cls, response: Any) -> Tuple[bool, Optional[str]]:
        """
        Validate response message.
        
        Args:
            response: Response to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(response, str):
            return False, "Response must be a string"
        
        if not response:
            return False, "Response cannot be empty"
        
        # Check response length (more lenient than input)
        if len(response) > 5000:
            return False, "Response too long"
        
        return True, None
    
    @classmethod
    def validate_intent(cls, intent: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate intent structure.
        
        Args:
            intent: Intent dictionary
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        if not isinstance(intent, dict):
            return False, ["Intent must be a dictionary"]
        
        # Required fields
        if "type" not in intent:
            errors.append("Intent type is required")
        else:
            # Validate intent type
            if not isinstance(intent["type"], str):
                errors.append("Intent type must be a string")
            else:
                valid_types = [t.value for t in IntentType]
                if intent["type"] not in valid_types:
                    errors.append(f"Invalid intent type: {intent['type']}")
        
        if "confidence" not in intent:
            errors.append("Intent confidence is required")
        else:
            # Validate confidence
            if not isinstance(intent["confidence"], (int, float)):
                errors.append("Intent confidence must be a number")
            elif not 0 <= intent["confidence"] <= 1:
                errors.append("Intent confidence must be between 0 and 1")
        
        return len(errors) == 0, errors
    
    @classmethod
    def validate_processing_result(cls, result: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate complete processing result.
        
        Args:
            result: Processing result dictionary
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        # Required fields
        required_fields = ["success", "response"]
        for field in required_fields:
            if field not in result:
                errors.append(f"Missing required field: {field}")
        
        # Validate success flag
        if "success" in result and not isinstance(result["success"], bool):
            errors.append("Success must be a boolean")
        
        # Validate response
        if "response" in result:
            is_valid, error = cls.validate_response(result["response"])
            if not is_valid:
                errors.append(f"Response: {error}")
        
        # Validate intent if present
        if "intent" in result and result["intent"]:
            is_valid, intent_errors = cls.validate_intent(result["intent"])
            if not is_valid:
                errors.extend([f"Intent: {e}" for e in intent_errors])
        
        # Validate processing time if present
        if "processing_time_ms" in result:
            if not isinstance(result["processing_time_ms"], (int, float)):
                errors.append("Processing time must be a number")
            elif result["processing_time_ms"] < 0:
                errors.append("Processing time cannot be negative")
        
        return len(errors) == 0, errors


class SecurityValidator:
    """Validates data for security concerns"""
    
    # Patterns that might indicate injection attempts
    SUSPICIOUS_PATTERNS = [
        r'(?i)(\b(union|select|insert|update|delete|drop|create|alter)\b.*\b(from|where|table|database)\b)',  # SQL
        r'(?i)<\s*script',  # Script injection
        r'(?i)javascript\s*:',  # JavaScript protocol
        r'(?i)on\w+\s*=',  # Event handlers
        r'\$\{.*\}',  # Template injection
        r'{{.*}}',  # Template injection
        r'%\{.*\}',  # Template injection
    ]
    
    @classmethod
    def validate_input_security(cls, message: str) -> Tuple[bool, Optional[str]]:
        """
        Validate message for security concerns.
        
        Args:
            message: Message to validate
            
        Returns:
            Tuple of (is_safe, security_concern)
        """
        # Check for suspicious patterns
        for pattern in cls.SUSPICIOUS_PATTERNS:
            if re.search(pattern, message):
                return False, "Message contains potentially unsafe content"
        
        # Check for excessive special characters (might indicate encoding attacks)
        special_char_ratio = sum(1 for c in message if not c.isalnum() and not c.isspace()) / len(message)
        if special_char_ratio > 0.5:
            return False, "Message contains excessive special characters"
        
        return True, None
    
    @classmethod
    def sanitize_output(cls, response: str) -> str:
        """
        Sanitize response for safe display.
        
        Args:
            response: Response to sanitize
            
        Returns:
            Sanitized response
        """
        # Basic HTML entity encoding for safety
        replacements = {
            '<': '&lt;',
            '>': '&gt;',
            '&': '&amp;',
            '"': '&quot;',
            "'": '&#x27;',
        }
        
        for char, replacement in replacements.items():
            response = response.replace(char, replacement)
        
        return response