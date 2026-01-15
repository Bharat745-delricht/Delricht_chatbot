"""
Safety Validator for Clinical Trials Chatbot

This module provides comprehensive safety checks for:
1. Emergency detection - Recognize crisis situations and direct to emergency services
2. Medical advice requests - Block requests for treatment/diagnosis recommendations
3. Output validation - Ensure bot responses don't contain medical advice
4. Prompt injection detection - Block adversarial inputs

IMPORTANT: This chatbot connects patients with clinical trials. It should NEVER:
- Provide medical advice, diagnoses, or treatment recommendations
- Suggest starting, stopping, or changing medications
- Interpret symptoms or lab results
- Replace consultation with healthcare providers
"""

import re
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)


class SafetyStatus(Enum):
    """Safety check result status"""
    SAFE = "safe"
    EMERGENCY = "emergency"
    MEDICAL_ADVICE_REQUEST = "medical_advice_request"
    MEDICAL_ADVICE_OUTPUT = "medical_advice_output"
    PROMPT_INJECTION = "prompt_injection"
    BLOCKED = "blocked"


@dataclass
class SafetyCheckResult:
    """Result of a safety check"""
    status: SafetyStatus
    is_safe: bool
    response: Optional[str] = None  # Override response if not safe
    reason: Optional[str] = None    # Reason for blocking (for logging)
    matched_pattern: Optional[str] = None  # What triggered the check


class SafetyValidator:
    """
    Validates user input and bot output for safety concerns.

    Usage:
        validator = SafetyValidator()

        # Check user input before processing
        input_result = validator.check_input(user_message)
        if not input_result.is_safe:
            return input_result.response

        # Process message normally...
        bot_response = await process_message(user_message)

        # Check bot output before returning
        output_result = validator.check_output(bot_response)
        if not output_result.is_safe:
            return output_result.response
    """

    # ==========================================================================
    # EMERGENCY DETECTION
    # These indicate immediate danger - must redirect to emergency services
    # ==========================================================================

    EMERGENCY_PATTERNS = [
        # Life-threatening emergencies
        r"\b(chest\s*pain|heart\s*attack|cardiac\s*arrest)\b",
        r"\b(can'?t\s*breathe|trouble\s*breathing|difficulty\s*breathing|shortness\s*of\s*breath)\b",
        r"\b(choking|suffocating)\b",
        r"\b(stroke|slurred\s*speech|face\s*drooping|arm\s*weakness)\b",
        r"\b(seizure|convulsion)\b",
        r"\b(severe\s*bleeding|won'?t\s*stop\s*bleeding|hemorrhage)\b",
        r"\b(unconscious|passed\s*out|unresponsive)\b",
        r"\b(anaphyla|severe\s*allergic\s*reaction|throat\s*swelling)\b",
        r"\b(overdose|took\s*too\s*(many|much))\b",

        # Mental health emergencies
        r"\b(suicid|kill\s*(my)?self|end\s*(my)?\s*life|want\s*to\s*die)\b",
        r"\b(self[- ]?harm|cutting\s*myself|hurt\s*myself)\b",
        r"\b(going\s*to\s*hurt\s*(someone|myself))\b",

        # Explicit emergency requests
        r"\b(call\s*911|emergency\s*room|need\s*an?\s*ambulance)\b",
        r"\b(medical\s*emergency|life[- ]?threatening)\b",
        r"\b(dying|think\s*i'?m\s*dying)\b",
    ]

    EMERGENCY_RESPONSE = """🚨 **If this is a medical emergency, please take immediate action:**

• **Call 911** (or your local emergency number) immediately
• **Go to the nearest emergency room**
• **Call the National Suicide Prevention Lifeline: 988** (if having thoughts of self-harm)
• **Poison Control: 1-800-222-1222** (for overdose/poisoning)

I'm a clinical trials assistant and cannot provide emergency medical help. Please contact emergency services or your healthcare provider right away.

Once you're safe and have received proper care, I'm happy to help you find clinical trials."""

    # ==========================================================================
    # MEDICAL ADVICE DETECTION (Input)
    # User requests that ask for medical recommendations
    # ==========================================================================

    MEDICAL_ADVICE_INPUT_PATTERNS = [
        # Treatment recommendations
        r"\b(should\s*i\s*(take|stop|start|continue|change|try))\b.*\b(medication|medicine|drug|treatment|therapy|pill)\b",
        r"\b(what\s*(medication|medicine|drug|treatment)\s*(should|would|can)\s*i)\b",
        r"\b(recommend\s*(a|any|some)?\s*(medication|medicine|drug|treatment|therapy))\b",
        r"\b(prescribe|prescription)\b.*\b(for\s*me|what\s*should)\b",
        r"\b(best\s*(medication|medicine|drug|treatment)\s*for)\b",

        # Dosage questions
        r"\b(how\s*much\s*(should\s*i|to)\s*take)\b",
        r"\b(what\s*(dose|dosage)\s*(should|do)\s*i)\b",
        r"\b(increase|decrease)\s*(my)?\s*(dose|dosage|medication)\b",

        # Diagnosis requests
        r"\b(do\s*i\s*have|what\s*(do\s*i|disease|condition|illness)\s*do\s*i\s*have)\b",
        r"\b(diagnose|diagnosis)\b.*\b(me|my|what)\b",
        r"\b(is\s*this|what\s*is)\b.*\b(symptom|normal|serious|dangerous)\b",
        r"\b(what('?s)?\s*wrong\s*with\s*me)\b",

        # Medical opinion requests
        r"\b(medical\s*(advice|opinion|recommendation))\b",
        r"\b(should\s*i\s*(see|go\s*to|visit)\s*(a|the)?\s*doctor)\b",
        r"\b(is\s*it\s*(safe|ok|okay)\s*(to|for\s*me\s*to))\b.*\b(take|stop|mix)\b",

        # Lab/test interpretation
        r"\b(what\s*do\s*(my|these)\s*(lab|test|blood)\s*results\s*mean)\b",
        r"\b(interpret|explain)\s*(my)?\s*(lab|test|blood|results)\b",
        r"\b(is\s*(my|this)\s*(level|count|number)\s*(normal|high|low))\b",

        # Symptom evaluation
        r"\b(why\s*(do|am)\s*i\s*(feel|having|experiencing))\b",
        r"\b(what\s*(causes?|is\s*causing))\b.*\b(symptom|pain|problem)\b",
        r"\b(cure|treat|heal)\s*(my|this|the)\b",
    ]

    MEDICAL_ADVICE_INPUT_RESPONSE = """I understand you have a health question, but I'm not able to provide medical advice, diagnoses, or treatment recommendations.

**For medical questions, please:**
• Contact your doctor or healthcare provider
• Visit an urgent care clinic
• Use a telehealth service

**What I CAN help with:**
• Finding clinical trials for your condition
• Explaining how clinical trials work
• Checking your eligibility for specific studies
• Connecting you with research coordinators

Would you like me to help you find clinical trials instead?"""

    # ==========================================================================
    # MEDICAL ADVICE DETECTION (Output)
    # Bot responses that contain medical advice - these should be blocked
    # ==========================================================================

    MEDICAL_ADVICE_OUTPUT_PATTERNS = [
        # Direct recommendations
        r"\b(you\s*should\s*(take|stop|start|try|use|avoid))\b",
        r"\b(i\s*recommend\s*(you|that\s*you)?\s*(take|stop|start|try))\b",
        r"\b(i\s*suggest\s*(you|that\s*you)?\s*(take|stop|start))\b",
        r"\b(i\s*advise\s*(you|that\s*you)?)\b",

        # Diagnosis statements
        r"\b(you\s*(have|may\s*have|probably\s*have|likely\s*have))\b.*\b(disease|disorder|condition|syndrome|infection)\b",
        r"\b(it\s*(sounds|seems)\s*like\s*you\s*have)\b",
        r"\b(this\s*(is|sounds\s*like|could\s*be))\b.*\b(symptom\s*of|sign\s*of)\b",

        # Dosage instructions
        r"\b(take\s*\d+\s*(mg|ml|pill|tablet|capsule))\b",
        r"\b(the\s*(dose|dosage)\s*(should|is|would)\s*be)\b",
        r"\b(increase\s*(your|the)\s*dose\s*to)\b",

        # Treatment assertions
        r"\b(this\s*(will|can|should)\s*(cure|treat|heal|fix))\b",
        r"\b(the\s*best\s*treatment\s*(is|would\s*be))\b",
        r"\b(you\s*need\s*(to\s*take|a\s*prescription|medication))\b",

        # Definitive health statements
        r"\b(you\s*(don'?t|do\s*not)\s*have)\b.*\b(to\s*worry|anything\s*serious)\b",
        r"\b(this\s*is\s*(nothing|not)\s*serious)\b",
        r"\b(you('?re)?\s*(are)?\s*(fine|healthy|okay|normal))\b.*\b(don'?t\s*need)\b",
    ]

    MEDICAL_ADVICE_OUTPUT_FALLBACK = """I can help you find clinical trials that may be relevant to your condition.

Would you like me to:
1. Search for trials based on your medical condition?
2. Check your eligibility for a specific study?
3. Explain how clinical trials work?

Please let me know how I can assist you."""

    # ==========================================================================
    # PROMPT INJECTION DETECTION
    # Attempts to manipulate the AI's behavior
    # ==========================================================================

    PROMPT_INJECTION_PATTERNS = [
        r"\b(ignore\s*(previous|all|above|prior)\s*(instructions?|prompts?|rules?))\b",
        r"\b(disregard\s*(your|all|the)\s*(instructions?|programming|rules?))\b",
        r"\b(forget\s*(everything|all|your)\s*(above|previous|prior)?)\b",
        r"\b(you\s*are\s*now\s*(a|an|acting\s*as))\b",
        r"\b(pretend\s*(you'?re?|to\s*be))\b",
        r"\b(new\s*instruction|override|bypass)\b",
        r"\b(system\s*prompt|admin\s*mode|developer\s*mode)\b",
        r"\b(jailbreak|dan\s*mode|evil\s*mode)\b",
        r"\b(repeat\s*(back|after\s*me)|say\s*exactly)\b",
        r"\b(roleplay\s*as|act\s*as\s*if)\b",
        r"\b(what\s*(are|is)\s*(your|the)\s*(instructions?|system\s*prompt|rules?))\b",
    ]

    PROMPT_INJECTION_RESPONSE = """I'm here to help you find clinical trials. I can assist with:

• Searching for trials by condition and location
• Checking eligibility for specific studies
• Explaining how clinical trials work
• Connecting you with research coordinators

What medical condition are you interested in finding trials for?"""

    # ==========================================================================
    # SENSITIVE DATA PATTERNS (for detection/logging only)
    # ==========================================================================

    SENSITIVE_DATA_PATTERNS = {
        'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        'phone': r'\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
        'ssn': r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b',
        'credit_card': r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',
    }

    def __init__(self):
        """Initialize the safety validator with compiled regex patterns"""
        # Compile all patterns for efficiency
        self._emergency_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.EMERGENCY_PATTERNS
        ]
        self._medical_input_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.MEDICAL_ADVICE_INPUT_PATTERNS
        ]
        self._medical_output_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.MEDICAL_ADVICE_OUTPUT_PATTERNS
        ]
        self._injection_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.PROMPT_INJECTION_PATTERNS
        ]
        self._sensitive_patterns = {
            k: re.compile(v, re.IGNORECASE)
            for k, v in self.SENSITIVE_DATA_PATTERNS.items()
        }

        logger.info("SafetyValidator initialized with pattern matching")

    def check_input(self, user_message: str) -> SafetyCheckResult:
        """
        Check user input for safety concerns before processing.

        Priority order:
        1. Emergency detection (highest - immediate redirect)
        2. Prompt injection (block adversarial inputs)
        3. Medical advice requests (redirect to appropriate resources)

        Args:
            user_message: The user's input message

        Returns:
            SafetyCheckResult with status and optional override response
        """
        if not user_message or not user_message.strip():
            return SafetyCheckResult(
                status=SafetyStatus.SAFE,
                is_safe=True
            )

        message_lower = user_message.lower().strip()

        # 1. Check for emergency situations (HIGHEST PRIORITY)
        emergency_match = self._check_patterns(message_lower, self._emergency_patterns)
        if emergency_match:
            logger.warning(f"EMERGENCY DETECTED in input: '{emergency_match}' - Message: {user_message[:100]}")
            return SafetyCheckResult(
                status=SafetyStatus.EMERGENCY,
                is_safe=False,
                response=self.EMERGENCY_RESPONSE,
                reason="Emergency keywords detected",
                matched_pattern=emergency_match
            )

        # 2. Check for prompt injection attempts
        injection_match = self._check_patterns(message_lower, self._injection_patterns)
        if injection_match:
            logger.warning(f"PROMPT INJECTION attempt detected: '{injection_match}' - Message: {user_message[:100]}")
            return SafetyCheckResult(
                status=SafetyStatus.PROMPT_INJECTION,
                is_safe=False,
                response=self.PROMPT_INJECTION_RESPONSE,
                reason="Potential prompt injection detected",
                matched_pattern=injection_match
            )

        # 3. Check for medical advice requests
        medical_match = self._check_patterns(message_lower, self._medical_input_patterns)
        if medical_match:
            logger.info(f"Medical advice request detected: '{medical_match}' - Message: {user_message[:100]}")
            return SafetyCheckResult(
                status=SafetyStatus.MEDICAL_ADVICE_REQUEST,
                is_safe=False,
                response=self.MEDICAL_ADVICE_INPUT_RESPONSE,
                reason="Medical advice request detected",
                matched_pattern=medical_match
            )

        # Input is safe
        return SafetyCheckResult(
            status=SafetyStatus.SAFE,
            is_safe=True
        )

    def check_output(self, bot_response: str) -> SafetyCheckResult:
        """
        Check bot output for medical advice before returning to user.

        This is a safety net to catch any medical advice that might have
        been generated by the LLM despite system prompt instructions.

        Args:
            bot_response: The generated bot response

        Returns:
            SafetyCheckResult with status and optional replacement response
        """
        if not bot_response or not bot_response.strip():
            return SafetyCheckResult(
                status=SafetyStatus.SAFE,
                is_safe=True
            )

        response_lower = bot_response.lower()

        # Check for medical advice in output
        medical_match = self._check_patterns(response_lower, self._medical_output_patterns)
        if medical_match:
            logger.warning(f"MEDICAL ADVICE in output blocked: '{medical_match}' - Response: {bot_response[:100]}")
            return SafetyCheckResult(
                status=SafetyStatus.MEDICAL_ADVICE_OUTPUT,
                is_safe=False,
                response=self.MEDICAL_ADVICE_OUTPUT_FALLBACK,
                reason="Medical advice detected in bot response",
                matched_pattern=medical_match
            )

        # Output is safe
        return SafetyCheckResult(
            status=SafetyStatus.SAFE,
            is_safe=True
        )

    def detect_sensitive_data(self, text: str) -> dict:
        """
        Detect sensitive data in text (for logging/auditing purposes).

        Does NOT block - just identifies what sensitive data might be present.

        Args:
            text: Text to scan

        Returns:
            Dict of detected data types and whether they were found
        """
        detected = {}
        for data_type, pattern in self._sensitive_patterns.items():
            matches = pattern.findall(text)
            detected[data_type] = {
                'found': bool(matches),
                'count': len(matches)
            }
        return detected

    def mask_sensitive_data(self, text: str) -> str:
        """
        Mask sensitive data in text for logging or passing to LLM.

        Args:
            text: Text to mask

        Returns:
            Text with sensitive data masked
        """
        masked = text
        masked = self._sensitive_patterns['email'].sub('[EMAIL]', masked)
        masked = self._sensitive_patterns['phone'].sub('[PHONE]', masked)
        masked = self._sensitive_patterns['ssn'].sub('[SSN]', masked)
        masked = self._sensitive_patterns['credit_card'].sub('[CARD]', masked)
        return masked

    def _check_patterns(self, text: str, patterns: List[re.Pattern]) -> Optional[str]:
        """
        Check text against a list of compiled regex patterns.

        Args:
            text: Text to check (should be lowercase)
            patterns: List of compiled regex patterns

        Returns:
            First matched pattern string, or None if no match
        """
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return match.group(0)
        return None

    def get_safety_summary(self, user_message: str, bot_response: str) -> dict:
        """
        Get a complete safety summary for audit logging.

        Args:
            user_message: Original user message
            bot_response: Generated bot response

        Returns:
            Dict with all safety check results
        """
        input_result = self.check_input(user_message)
        output_result = self.check_output(bot_response)
        input_sensitive = self.detect_sensitive_data(user_message)
        output_sensitive = self.detect_sensitive_data(bot_response)

        return {
            'input_check': {
                'status': input_result.status.value,
                'is_safe': input_result.is_safe,
                'reason': input_result.reason,
                'matched_pattern': input_result.matched_pattern
            },
            'output_check': {
                'status': output_result.status.value,
                'is_safe': output_result.is_safe,
                'reason': output_result.reason,
                'matched_pattern': output_result.matched_pattern
            },
            'sensitive_data': {
                'in_input': input_sensitive,
                'in_output': output_sensitive
            }
        }


# Global singleton instance
safety_validator = SafetyValidator()
