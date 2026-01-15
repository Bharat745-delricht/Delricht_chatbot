"""
State-aware intent detection module.

This module provides pure intent detection that considers conversation state
and context to improve classification accuracy, especially for contextual responses.
"""

import re
import logging
from typing import Dict, Any, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

from models.schemas import ConversationState
from core.conversation.context import ConversationContext
from core.conversation.state_config import state_config

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    """All supported intent types"""
    # Core intents
    ELIGIBILITY = "eligibility"
    ELIGIBILITY_SPECIFIC_TRIAL = "eligibility_specific_trial"
    TRIAL_INFO_REQUEST = "trial_info_request"
    TRIAL_SEARCH = "trial_search"
    PERSONAL_CONDITION = "personal_condition"
    LOCATION_SEARCH = "location_search"
    TRIAL_INTEREST = "trial_interest"
    ELIGIBILITY_FOLLOWUP = "eligibility_followup"
    
    # Answer intents during prescreening
    AGE_ANSWER = "age_answer"
    YES_NO_ANSWER = "yes_no_answer"
    NUMBER_ANSWER = "number_answer"
    CONDITION_ANSWER = "condition_answer"
    LOCATION_ANSWER = "location_answer"
    MEDICATION_ANSWER = "medication_answer"
    
    # Contextual intents
    QUESTION_DURING_PRESCREENING = "question_during_prescreening"
    ELIGIBILITY_FOR_SHOWN_TRIAL = "eligibility_for_shown_trial"
    
    # General
    GENERAL_QUERY = "general_query"


@dataclass
class IntentPattern:
    """Represents an intent pattern with metadata"""
    pattern: str
    intent_type: IntentType
    confidence: float = 0.9
    requires_entity: Optional[str] = None
    valid_states: Optional[Set[ConversationState]] = None


@dataclass
class DetectedIntent:
    """Result of intent detection"""
    intent_type: IntentType
    confidence: float
    matched_pattern: Optional[str] = None
    trigger_prescreening: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    original_message: Optional[str] = None


class IntentDetector:
    """
    State-aware intent detection system.
    
    This class performs pure intent detection, considering conversation state
    to improve accuracy for contextual responses.
    """
    
    def __init__(self):
        self._initialize_patterns()
        self._initialize_typo_corrections()
        
    def _initialize_patterns(self):
        """Initialize all intent patterns"""
        # Eligibility patterns
        self.eligibility_patterns = [
            IntentPattern(r"(?:am i|would i be|could i be) eligible", IntentType.ELIGIBILITY),
            IntentPattern(r"(?:do i|would i|can i) qualify", IntentType.ELIGIBILITY),
            IntentPattern(r"can i (?:join|participate|enroll)", IntentType.ELIGIBILITY),
            IntentPattern(r"check (?:my )?eligibility", IntentType.ELIGIBILITY),
            IntentPattern(r"(?:am i|would i be) a (?:good )?candidate", IntentType.ELIGIBILITY),
            IntentPattern(r"(?:am i|would i be) suitable", IntentType.ELIGIBILITY),
            IntentPattern(r"meet (?:the )?criteria", IntentType.ELIGIBILITY),
            IntentPattern(r"qualified for", IntentType.ELIGIBILITY),
            IntentPattern(r"right for this (?:trial|study)", IntentType.ELIGIBILITY),
            IntentPattern(r"would this (?:trial|study) work for me", IntentType.ELIGIBILITY),
            # Enhanced eligibility patterns
            IntentPattern(r"i want to check if i'?m eligible", IntentType.ELIGIBILITY, confidence=0.95),
            IntentPattern(r"check if i'?m eligible", IntentType.ELIGIBILITY, confidence=0.95),
            IntentPattern(r"see if i'?m eligible", IntentType.ELIGIBILITY, confidence=0.95),
            IntentPattern(r"find out if i'?m eligible", IntentType.ELIGIBILITY, confidence=0.95),
            # Fixed: Handle simple "am i eligible" without question mark or extra words
            IntentPattern(r"^am i eligible", IntentType.ELIGIBILITY, confidence=0.95),
            IntentPattern(r"^am i eligibile", IntentType.ELIGIBILITY, confidence=0.95),  # Common typo
            # Handle eligibility for specific trials
            IntentPattern(r"am i eligible (?:for )?(?:the )?([a-zA-Z\s]+) trial", IntentType.ELIGIBILITY_SPECIFIC_TRIAL, requires_entity="condition"),
        ]
        
        # Personal condition patterns
        self.condition_patterns = [
            IntentPattern(r"i have (.+)", IntentType.PERSONAL_CONDITION, requires_entity="condition"),
            IntentPattern(r"i'?ve been diagnosed with (.+)", IntentType.PERSONAL_CONDITION, requires_entity="condition"),
            IntentPattern(r"i suffer from (.+)", IntentType.PERSONAL_CONDITION, requires_entity="condition"),
            IntentPattern(r"my (?:condition is|diagnosis is) (.+)", IntentType.PERSONAL_CONDITION, requires_entity="condition"),
            IntentPattern(r"i'?m being treated for (.+)", IntentType.PERSONAL_CONDITION, requires_entity="condition"),
            IntentPattern(r"i was diagnosed with (.+)", IntentType.PERSONAL_CONDITION, requires_entity="condition"),
        ]
        
        # Trial interest patterns - more specific to avoid overlap with info requests
        self.trial_interest_patterns = [
            IntentPattern(r"(?:i want to|i'd like to|i would like to) (?:participate|join|enroll)", IntentType.TRIAL_INTEREST),
            IntentPattern(r"(?:i'm|i am) interested in (?:participating|joining|enrolling)", IntentType.TRIAL_INTEREST),
            IntentPattern(r"(?:i'm|i am) interested in (?:clinical )?trials?", IntentType.TRIAL_INTEREST),
            IntentPattern(r"how (?:can|do) i (?:join|participate|enroll)", IntentType.TRIAL_INTEREST),
            IntentPattern(r"(?:looking for|want to find) (?:a )?(?:clinical )?trials?", IntentType.TRIAL_INTEREST),
            IntentPattern(r"what trials? (?:are available|do you have)", IntentType.TRIAL_INTEREST),
            IntentPattern(r"tell me about (?:the |your )?(?:available )?trials?(?:\s+for\s+me)?", IntentType.TRIAL_INTEREST),
            IntentPattern(r"want to (?:participate|join|enroll) in (?:a |an )?(?:clinical )?trial", IntentType.TRIAL_INTEREST),
            # Trial search without location patterns (HIGH PRIORITY)
            IntentPattern(r"show me ([a-zA-Z\s]+) trials?", IntentType.TRIAL_SEARCH, confidence=0.9),
            IntentPattern(r"find ([a-zA-Z\s]+) trials?", IntentType.TRIAL_SEARCH, confidence=0.9),
            IntentPattern(r"search for ([a-zA-Z\s]+) trials?", IntentType.TRIAL_SEARCH, confidence=0.9),
            IntentPattern(r"([a-zA-Z\s]+) trials? available", IntentType.TRIAL_SEARCH, confidence=0.9),
            # Generic "show me trials" should be trial interest
            IntentPattern(r"show me (?:the |your )?(?:available )?trials?$", IntentType.TRIAL_INTEREST),
            # Fixed: Handle "I'm interested in the [condition] trial"
            IntentPattern(r"(?:i'm|i am) interested in (?:the )?([a-zA-Z\s]+) trial", IntentType.ELIGIBILITY_SPECIFIC_TRIAL, requires_entity="condition", confidence=0.9),
        ]
        
        # Trial info patterns - specific to requesting information about conditions/trials
        self.trial_info_patterns = [
            # Simple command patterns for trial details
            IntentPattern(r"^(?:more )?(?:details?|info|information)$", IntentType.TRIAL_INFO_REQUEST, confidence=0.95),
            IntentPattern(r"^(?:get|show|tell me) (?:more )?(?:details?|info|information)$", IntentType.TRIAL_INFO_REQUEST, confidence=0.9),
            # Specific trial patterns with explicit "trial" keyword
            IntentPattern(r"tell me (?:more )?about the ([a-zA-Z\s]+) trial", IntentType.TRIAL_INFO_REQUEST, requires_entity="condition"),
            IntentPattern(r"(?:more )?(?:info|information|details) (?:about|on) the ([a-zA-Z\s]+) trial", IntentType.TRIAL_INFO_REQUEST, requires_entity="condition"),
            IntentPattern(r"what (?:is|about) the ([a-zA-Z\s]+) trial", IntentType.TRIAL_INFO_REQUEST, requires_entity="condition"),
            IntentPattern(r"(?:can you|could you) tell me about the ([a-zA-Z\s]+) trial", IntentType.TRIAL_INFO_REQUEST, requires_entity="condition"),
            # Condition info patterns - must NOT contain "trials", "available", "your"
            IntentPattern(r"(?:can you )?tell me (?:more )?about ([a-zA-Z\s]{3,20})(?:\s+(?:condition|disease|treatment))(?!\s+trials?)", IntentType.TRIAL_INFO_REQUEST, requires_entity="condition"),
            IntentPattern(r"(?:more )?(?:info|information|details) (?:about|on) ([a-zA-Z\s]{3,20})(?:\s+(?:condition|disease|treatment))(?!\s+trials?)", IntentType.TRIAL_INFO_REQUEST, requires_entity="condition"),
            IntentPattern(r"what (?:is|about|causes|treats) ([a-zA-Z\s]{3,20})(?:\s+(?:condition|disease))?", IntentType.TRIAL_INFO_REQUEST, requires_entity="condition"),
            IntentPattern(r"learn more about ([a-zA-Z\s]{3,20})(?:\s+(?:condition|disease|treatment))(?!\s+trials?)", IntentType.TRIAL_INFO_REQUEST, requires_entity="condition"),
            IntentPattern(r"explain ([a-zA-Z\s]{3,20})(?:\s+to me)?", IntentType.TRIAL_INFO_REQUEST, requires_entity="condition"),
            # Specific condition names without context (but not trial-related words)
            IntentPattern(r"(?:tell me about|what is|explain) (?!.*(?:trials?|available|your))([a-zA-Z\s]{3,20})$", IntentType.TRIAL_INFO_REQUEST, requires_entity="condition"),
        ]
        
        # Trial search patterns - specific combinations of condition + location (HIGHER PRIORITY than location alone)
        self.trial_search_patterns = [
            # Pattern: "[condition] trials/studies in [location]"
            IntentPattern(r"([a-zA-Z\s]+)\s+trials?\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            IntentPattern(r"([a-zA-Z\s]+)\s+studies\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            IntentPattern(r"([a-zA-Z\s]+)\s+research\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            # Pattern: "trials/studies for [condition] in [location]"
            IntentPattern(r"trials?\s+for\s+([a-zA-Z\s]+)\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            IntentPattern(r"studies\s+for\s+([a-zA-Z\s]+)\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            IntentPattern(r"research\s+for\s+([a-zA-Z\s]+)\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            # Pattern: "find/search [condition] trials/studies in [location]"
            IntentPattern(r"(?:find|search|look for)\s+([a-zA-Z\s]+)\s+trials?\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            IntentPattern(r"(?:find|search|look for)\s+([a-zA-Z\s]+)\s+studies\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            IntentPattern(r"(?:find|search|look for)\s+trials?\s+for\s+([a-zA-Z\s]+)\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            IntentPattern(r"(?:find|search|look for)\s+studies\s+for\s+([a-zA-Z\s]+)\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            # Pattern: "clinical trials/studies for [condition] in [location]"
            IntentPattern(r"clinical\s+trials?\s+for\s+([a-zA-Z\s]+)\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            IntentPattern(r"clinical\s+studies\s+for\s+([a-zA-Z\s]+)\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            IntentPattern(r"([a-zA-Z\s]+)\s+clinical\s+trials?\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
            IntentPattern(r"([a-zA-Z\s]+)\s+clinical\s+studies\s+(?:in|near|around)\s+([a-zA-Z\s]+)", IntentType.TRIAL_SEARCH, confidence=0.95),
        ]
        
        # Location patterns (LOWER PRIORITY - only when no condition is mentioned)
        self.location_patterns = [
            IntentPattern(r"trials? (?:in|near|around) ([a-zA-Z\s]+)", IntentType.LOCATION_SEARCH, requires_entity="location"),
            IntentPattern(r"studies (?:in|near|around) ([a-zA-Z\s]+)", IntentType.LOCATION_SEARCH, requires_entity="location"),
            IntentPattern(r"(?:i'?m |i am |im )?(?:in|from|based in|located in) ([a-zA-Z\s]+)", IntentType.LOCATION_SEARCH, requires_entity="location"),
            IntentPattern(r"(?:i |i'?m |i am |im )?(?:live|living) in ([a-zA-Z\s]+)", IntentType.LOCATION_SEARCH, requires_entity="location"),
        ]
        
        # Follow-up patterns
        self.followup_patterns = [
            IntentPattern(r"tell me more", IntentType.ELIGIBILITY_FOLLOWUP, confidence=0.85),
            IntentPattern(r"more (?:information|info|details)", IntentType.ELIGIBILITY_FOLLOWUP, confidence=0.85),
            IntentPattern(r"what else", IntentType.ELIGIBILITY_FOLLOWUP, confidence=0.85),
            IntentPattern(r"continue", IntentType.ELIGIBILITY_FOLLOWUP, confidence=0.85),
        ]
        
        # Answer patterns
        self.answer_patterns = [
            IntentPattern(r"^(?:yes|no|yeah|nope|y|n)$", IntentType.YES_NO_ANSWER, confidence=0.95),
            IntentPattern(r"^(?:i'?m |i am )?\d+(?:\s*years?(?:\s*old)?)?$", IntentType.AGE_ANSWER, confidence=0.95),
            IntentPattern(r"^\d+$", IntentType.AGE_ANSWER, confidence=0.9),  # Simple numbers could be ages in prescreening
            IntentPattern(r"(?:times?|flares?|attacks?) (?:per|a|in)", IntentType.NUMBER_ANSWER, confidence=0.9),
        ]
        
        # Organize patterns by priority for efficient matching
        self.pattern_groups = [
            # High priority - specific patterns
            self.eligibility_patterns,
            self.trial_info_patterns,
            self.condition_patterns,
            self.trial_search_patterns,  # NEW: Higher priority than location_patterns
            self.trial_interest_patterns,
            self.location_patterns,
            self.followup_patterns,
            self.answer_patterns,
        ]
    
    def _initialize_typo_corrections(self):
        """Initialize common typo corrections"""
        self.typo_corrections = {
            "cehck": "check",
            "chekc": "check", 
            "elegible": "eligible",
            "eligable": "eligible",
            "trails": "trials",
            "trail": "trial",
            "diabeties": "diabetes",
            "diabetis": "diabetes",
            "eleigible": "eligible",
            "qualifiy": "qualify",
            "paricipate": "participate",
            "enrol": "enroll",
        }
    
    def detect_intent(self, message: str, context: ConversationContext) -> DetectedIntent:
        """
        Detect intent from message considering conversation state.
        
        Args:
            message: User message
            context: Conversation context
            
        Returns:
            Detected intent with confidence
        """
        # Preprocess message
        processed_message = self._preprocess_message(message)
        
        # HIGHEST PRIORITY: Check contextual responses for special contexts (like trial info)
        if self._is_contextual_response(processed_message, context):
            contextual_intent = self._detect_contextual_intent(processed_message, context)
            if contextual_intent:
                # Prioritize contextual intent for trial info responses OR eligibility questions
                if (context.just_showed_trial_info and contextual_intent.confidence >= 0.9) or \
                   (contextual_intent.metadata.get("from_eligibility_question") and contextual_intent.confidence >= 0.9):
                    return contextual_intent
        
        # Check state-specific intent detection for awaiting states  
        state_intent = self._detect_state_specific_intent(processed_message, context)
        if state_intent and state_intent.confidence >= 0.8:
            # Don't override pattern matching for complex messages that might be new intents
            # Only boost confidence for simple contextual answers
            if (len(processed_message.split()) <= 3 and 
                context.conversation_state in [
                    ConversationState.AWAITING_LOCATION.value,
                    ConversationState.AWAITING_CONDITION.value,
                    ConversationState.AWAITING_FLARES.value,
                    ConversationState.AWAITING_AGE.value,
                    ConversationState.AWAITING_DIAGNOSIS.value,
                    ConversationState.AWAITING_MEDICATIONS.value
                ]):
                # Extra boost for AWAITING_LOCATION to prevent condition misclassification
                if context.conversation_state == ConversationState.AWAITING_LOCATION.value:
                    state_intent.confidence = 0.98  # Very high confidence for location
                else:
                    state_intent.confidence = 0.95  # High confidence to override pattern matching
                return state_intent
        
        # Then perform general pattern matching for high-confidence patterns
        pattern_intent = self._detect_pattern_based_intent(processed_message, context)
        if pattern_intent and pattern_intent.confidence >= 0.9:
            return pattern_intent
        
        # Check if we're awaiting a specific response type (if not already checked)
        if self._is_contextual_response(processed_message, context):
            contextual_intent = self._detect_contextual_intent(processed_message, context)
            if contextual_intent:
                return contextual_intent
        
        # Return pattern intent if we found one with lower confidence
        if pattern_intent:
            return pattern_intent
        
        # Default to general query
        return DetectedIntent(
            intent_type=IntentType.GENERAL_QUERY,
            confidence=0.5,
            trigger_prescreening=False,
            original_message=message
        )
    
    def _preprocess_message(self, message: str) -> str:
        """Preprocess message for intent detection"""
        processed = message.lower().strip()
        
        # Apply typo corrections
        for typo, correction in self.typo_corrections.items():
            processed = processed.replace(typo, correction)
            
        return processed
    
    def _detect_state_specific_intent(self, message: str, 
                                     context: ConversationContext) -> Optional[DetectedIntent]:
        """Detect intent based on current conversation state"""
        current_state = context.conversation_state
        
        if not current_state:
            return None
            
        # Get expected intent for current state
        expected_intent_str = state_config.get_expected_intent_for_state(current_state)
        if not expected_intent_str:
            return None
        
        # Convert string back to IntentType
        try:
            expected_intent = IntentType(expected_intent_str)
        except ValueError:
            return None
        
        # Validate message matches expected intent type
        if expected_intent == IntentType.AGE_ANSWER:
            if re.search(r"\d+", message):
                return DetectedIntent(
                    intent_type=IntentType.AGE_ANSWER,
                    confidence=0.95,
                    metadata={"in_prescreening": True},
                    original_message=message
                )
                
        elif expected_intent == IntentType.YES_NO_ANSWER:
            if re.search(r"^(?:yes|no|yeah|nope|y|n|sure|ok|okay)", message):
                return DetectedIntent(
                    intent_type=IntentType.YES_NO_ANSWER,
                    confidence=0.95,
                    metadata={"in_prescreening": True},
                    original_message=message
                )
                
        elif expected_intent == IntentType.NUMBER_ANSWER:
            # For NUMBER_ANSWER in AWAITING_FLARES, prioritize over AGE_ANSWER
            if re.search(r"\d+", message):
                return DetectedIntent(
                    intent_type=IntentType.NUMBER_ANSWER,
                    confidence=0.98,  # Very high confidence to override AGE_ANSWER pattern
                    metadata={"in_prescreening": True, "awaiting_flares": True, "state_specific": True},
                    original_message=message
                )
                
        elif expected_intent in [IntentType.CONDITION_ANSWER, IntentType.LOCATION_ANSWER]:
            # For condition/location, check if it looks like a reasonable answer
            words = message.split()
            if len(words) <= 5:  # Short responses likely answers
                # Enhanced validation for location answers
                if expected_intent == IntentType.LOCATION_ANSWER:
                    # PRIORITY: Any single word in AWAITING_LOCATION should be treated as location
                    if len(words) == 1:
                        return DetectedIntent(
                            intent_type=IntentType.LOCATION_ANSWER,
                            confidence=0.98,  # Very high confidence for single words
                            metadata={"in_prescreening": True, "awaiting_location": True, "single_word_location": True},
                            original_message=message
                        )
                    # Check for explicit location context words
                    location_indicators = ['in', 'from', 'near', 'at', 'city', 'state', 'county', 'live', 'located']
                    has_location_context = any(word in message.lower() for word in location_indicators)
                    
                    # Check for common location patterns (city/state names)
                    has_location_pattern = bool(re.search(r'\b(?:new\s+\w+|[\w\s]{2,}\s+(?:city|state|county|texas|california|florida|york|jersey))\b', message.lower()))
                    
                    # Check if it's a capitalized word(s) that looks like a place name
                    looks_like_place = bool(re.search(r'^(?:i.{0,5}\s+)?(?:in\s+|from\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s*$', message))
                    
                    # Enhanced: Single capitalized word in AWAITING_LOCATION state should be treated as location
                    single_capitalized_word = bool(re.search(r'^[A-Z][a-z]+$', message.strip()))
                    
                    # Common US city/state names pattern
                    common_locations = bool(re.search(r'\b(?:boston|new\s*york|california|texas|florida|chicago|atlanta|seattle|denver|phoenix|philadelphia|michigan|ohio|tulsa|houston|dallas|miami|orlando)\b', message.lower()))
                    
                    # Enhanced: "I'm in [Location]" patterns in AWAITING_LOCATION should be LOCATION_ANSWER
                    im_in_location = bool(re.search(r"i'?m\s+in\s+[A-Za-z]+", message, re.IGNORECASE))
                    live_in_location = bool(re.search(r"(?:live|living)\s+in\s+[A-Za-z]+", message, re.IGNORECASE))
                    from_location = bool(re.search(r"(?:from|based\s+in)\s+[A-Za-z]+", message, re.IGNORECASE))
                    
                    if has_location_context or has_location_pattern or looks_like_place or single_capitalized_word or common_locations or im_in_location or live_in_location or from_location:
                        return DetectedIntent(
                            intent_type=IntentType.LOCATION_ANSWER,
                            confidence=0.9,
                            metadata={"in_prescreening": True, "awaiting_location": True}
                        )
                else:
                    # For condition answers, be more permissive
                    # But still apply single word priority for AWAITING_CONDITION
                    if expected_intent == IntentType.CONDITION_ANSWER and len(words) == 1:
                        return DetectedIntent(
                            intent_type=IntentType.CONDITION_ANSWER,
                            confidence=0.98,  # High confidence for single word conditions
                            metadata={"in_prescreening": True, "single_word_condition": True},
                            original_message=message
                        )
                    return DetectedIntent(
                        intent_type=expected_intent,
                        confidence=0.85,
                        metadata={"in_prescreening": True}
                    )
        
        return None
    
    def _is_contextual_response(self, message: str, context: ConversationContext) -> bool:
        """Check if message is likely a contextual response"""
        # Check for explicit awaiting flags
        if any([
            context.state_data.get("awaiting_location"),
            context.state_data.get("awaiting_condition"),
            context.state_data.get("awaiting_trial_specification"),
            context.just_showed_trial_info,  # Add trial info context
        ]):
            return True
        
        # Check if last bot message was a question
        if context.conversation_history:
            last_turn = context.conversation_history[-1]
            last_response = last_turn.get("bot_response", "").lower()
            
            # NEW: Check for eligibility question patterns
            if self._detect_eligibility_question_in_response(last_response):
                return True
            
            question_indicators = [
                "which location", "what location", "where are you",
                "which trial", "what condition", "which condition",
                "could you tell me", "could you please", "can you tell me"
            ]
            
            if any(indicator in last_response for indicator in question_indicators):
                # Short messages after questions are likely answers
                if len(message.split()) <= 5:
                    return True
        
        return False
    
    def _detect_eligibility_question_in_response(self, bot_response: str) -> bool:
        """Check if bot response contains an eligibility check question"""
        eligibility_patterns = [
            r"would you like to check if you(?:'re|'re)? eligible",
            r"would you like me to check (?:if |whether )?you(?:'re|'re)? eligible",
            r"want to check (?:your )?eligibility",
            r"interested in checking (?:if |whether )?you qualify",
            r"shall we check if you(?:'re|'re)? eligible",
            r"let me check if you(?:'re|'re)? eligible",
            r"would you like to see if you qualify",
            r"want me to check if you qualify",
            r"check if you might be eligible",
            r"see if you(?:'re|'re)? eligible for this trial",
            r"determine (?:if |whether )?you(?:'re|'re)? eligible"
        ]
        
        for pattern in eligibility_patterns:
            if re.search(pattern, bot_response):
                return True
        
        return False
    
    def _detect_contextual_intent(self, message: str, 
                                 context: ConversationContext) -> Optional[DetectedIntent]:
        """Detect intent for contextual responses"""
        # Check if user just saw trial info OR if last response had eligibility question
        last_had_eligibility_question = False
        
        if context.conversation_history:
            last_turn = context.conversation_history[-1]
            last_response = last_turn.get("bot_response", "")
            last_had_eligibility_question = self._detect_eligibility_question_in_response(last_response.lower())
        
        if context.just_showed_trial_info or last_had_eligibility_question:
            # Check for affirmative responses or eligibility keywords
            yes_patterns = r'\b(yes|yeah|yep|sure|ok|okay|eligible|eligibility)\b'
            if re.search(yes_patterns, message, re.IGNORECASE):
                return DetectedIntent(
                    intent_type=IntentType.ELIGIBILITY,
                    confidence=0.95,
                    metadata={
                        "contextual": True,
                        "from_trial_info": context.just_showed_trial_info,
                        "from_eligibility_question": last_had_eligibility_question
                    }
                )
        
        # If awaiting location
        if context.state_data.get("awaiting_location"):
            return DetectedIntent(
                intent_type=IntentType.LOCATION_ANSWER,
                confidence=0.9,
                metadata={
                    "contextual": True,
                    "awaiting_type": "location"
                }
            )
        
        # If awaiting condition
        if context.state_data.get("awaiting_condition"):
            return DetectedIntent(
                intent_type=IntentType.CONDITION_ANSWER,
                confidence=0.9,
                metadata={
                    "contextual": True,
                    "awaiting_type": "condition"
                }
            )
        
        # If awaiting trial specification
        if context.state_data.get("awaiting_trial_specification"):
            # Could be either condition or location
            if context.focus_location and not context.focus_condition:
                return DetectedIntent(
                    intent_type=IntentType.CONDITION_ANSWER,
                    confidence=0.85,
                    metadata={
                        "contextual": True,
                        "awaiting_type": "trial_specification"
                    }
                )
            else:
                return DetectedIntent(
                    intent_type=IntentType.TRIAL_INFO_REQUEST,
                    confidence=0.85,
                    metadata={
                        "contextual": True,
                        "awaiting_type": "trial_specification"
                    }
                )
        
        # Check conversation history for context
        if context.conversation_history:
            last_response = context.conversation_history[-1].get("bot_response", "").lower()
            
            # Location question
            if any(phrase in last_response for phrase in ["which location", "what location"]):
                return DetectedIntent(
                    intent_type=IntentType.LOCATION_ANSWER,
                    confidence=0.85,
                    metadata={"contextual": True, "inferred_from": "last_response"}
                )
            
            # Condition/trial question
            if any(phrase in last_response for phrase in ["which trial", "what condition"]):
                return DetectedIntent(
                    intent_type=IntentType.CONDITION_ANSWER,
                    confidence=0.85,
                    metadata={"contextual": True, "inferred_from": "last_response"}
                )
        
        return None
    
    def _detect_pattern_based_intent(self, message: str, 
                                    context: ConversationContext) -> Optional[DetectedIntent]:
        """Detect intent using pattern matching"""
        # Check each pattern group in priority order
        for pattern_group in self.pattern_groups:
            for pattern in pattern_group:
                if isinstance(pattern, IntentPattern):
                    match = re.search(pattern.pattern, message)
                    if match:
                        # Check if pattern is valid for current state
                        if pattern.valid_states and context.conversation_state:
                            if ConversationState(context.conversation_state) not in pattern.valid_states:
                                continue
                        
                        # Determine if this should trigger prescreening
                        trigger_prescreening = pattern.intent_type in [
                            IntentType.ELIGIBILITY,
                            IntentType.ELIGIBILITY_SPECIFIC_TRIAL,
                            IntentType.PERSONAL_CONDITION,
                        ]
                        
                        return DetectedIntent(
                            intent_type=pattern.intent_type,
                            confidence=pattern.confidence,
                            matched_pattern=pattern.pattern,
                            trigger_prescreening=trigger_prescreening,
                            metadata={"pattern_match": True},
                            original_message=message
                        )
        
        # Special case: Check for condition + location combination
        if self._has_condition_location_combo(message, context):
            return DetectedIntent(
                intent_type=IntentType.TRIAL_SEARCH,
                confidence=0.85,
                trigger_prescreening=False,
                metadata={"inferred": "condition_location_combo"}
            )
        
        # Special case: Single word that might be a condition
        if len(message.split()) <= 3 and context.focus_location:
            # This could be a condition name
            return DetectedIntent(
                intent_type=IntentType.TRIAL_SEARCH,
                confidence=0.7,
                trigger_prescreening=False,
                metadata={"inferred": "short_message_with_location"}
            )
        
        return None
    
    def _has_condition_location_combo(self, message: str, context: ConversationContext) -> bool:
        """Check if message contains both condition and location information"""
        # Check for location keywords
        has_location_keyword = any(word in message.lower() for word in [
            " in ", " near ", " around ", " at "
        ]) or bool(re.search(r"[A-Z][a-z]+", message))  # Capitalized words
        
        if not has_location_keyword:
            return False
        
        # Check for medical condition keywords using condition registry
        try:
            from core.services.condition_registry import condition_registry
            words = message.lower().split()
            
            # Check if any word or phrase is a medical condition
            for i, word in enumerate(words):
                # Check single words
                if condition_registry.is_medical_condition(word):
                    return True
                
                # Check two-word combinations
                if i < len(words) - 1:
                    two_word = f"{word} {words[i+1]}"
                    if condition_registry.is_medical_condition(two_word):
                        return True
                
                # Check three-word combinations for complex condition names
                if i < len(words) - 2:
                    three_word = f"{word} {words[i+1]} {words[i+2]}"
                    if condition_registry.is_medical_condition(three_word):
                        return True
            
            return False
            
        except ImportError:
            # Fallback to hardcoded list if registry not available
            has_condition_keyword = any(word in message.lower() for word in [
                "diabetes", "cancer", "heart", "lung", "kidney", "liver",
                "arthritis", "asthma", "copd", "ibs", "gout", "migraine",
                "depression", "anxiety", "hypertension", "obesity"
            ])
            return has_condition_keyword
    
    def get_valid_intents_for_state(self, state: ConversationState) -> List[IntentType]:
        """Get list of valid intent types for a given state"""
        intent_strings = state_config.get_valid_intents_for_state(state)
        # Convert string values back to IntentType enums
        valid_intents = []
        for intent_str in intent_strings:
            try:
                valid_intents.append(IntentType(intent_str))
            except ValueError:
                # Skip invalid intent strings
                continue
        return valid_intents if valid_intents else [IntentType.GENERAL_QUERY]