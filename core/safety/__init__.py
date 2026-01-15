"""
Safety module for the clinical trials chatbot.

Provides input/output validation to prevent:
- Medical advice generation
- Emergency situation mishandling
- Sensitive data exposure
- Prompt injection attacks
"""

from .safety_validator import SafetyValidator, SafetyCheckResult

__all__ = ['SafetyValidator', 'SafetyCheckResult']
