"""
Gemini-powered intent detection using structured prompts.

This module replaces the regex-heavy pattern matching with Gemini's
structured JSON responses for more robust and maintainable intent detection.
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

from core.conversation.context import ConversationContext
from models.schemas import ConversationState
from core.services.gemini_service import gemini_service

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    """Supported intent types for Gemini structured responses"""
    # Primary intents
    ELIGIBILITY = "eligibility"
    TRIAL_SEARCH = "trial_search"
    PERSONAL_CONDITION = "personal_condition"
    LOCATION_SEARCH = "location_search"
    TRIAL_INFO_REQUEST = "trial_info_request"
    
    # Answer intents during prescreening
    AGE_ANSWER = "age_answer"
    YES_NO_ANSWER = "yes_no_answer"
    NUMBER_ANSWER = "number_answer"
    CONDITION_ANSWER = "condition_answer"
    LOCATION_ANSWER = "location_answer"
    MEDICATION_ANSWER = "medication_answer"
    
    # Follow-up intents
    ELIGIBILITY_FOLLOWUP = "eligibility_followup"
    TRIAL_INTEREST = "trial_interest"
    
    # General
    GENERAL_QUERY = "general_query"
    GENERAL_INQUIRY = "general_inquiry"  # Alternative general intent returned by Gemini


@dataclass
class ExtractedEntities:
    """Structured entity extraction results"""
    condition: Optional[str] = None
    location: Optional[str] = None
    age: Optional[int] = None
    medication: Optional[str] = None
    number: Optional[int] = None
    trial_name: Optional[str] = None
    boolean_answer: Optional[bool] = None
    confidence_scores: Optional[Dict[str, float]] = None


@dataclass
class GeminiDetectedIntent:
    """Gemini-powered intent detection result"""
    intent_type: IntentType
    confidence: float
    entities: ExtractedEntities
    next_action: str
    reasoning: str
    trigger_prescreening: bool = False
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class GeminiIntentDetector:
    """
    Gemini-powered intent detection using structured prompts.
    
    This replaces the regex-heavy pattern matching with structured
    Gemini JSON responses for more robust intent detection.
    """
    
    def __init__(self):
        self.gemini = gemini_service
        self.intent_types = [intent.value for intent in IntentType]
    
    def _build_intent_detection_prompt(self, context: ConversationContext) -> str:
        """Build structured prompt for Gemini intent detection"""
        base_prompt = """You are an expert clinical trials assistant specialized in intent detection and entity extraction.

Your role is to accurately detect what users want and extract relevant information from their messages.

Key context about clinical trials:
- Users may ask about eligibility for trials
- They may mention medical conditions they have
- They may specify geographic locations
- They may be answering prescreening questions
- They may want general information about trials or conditions

Available intent types:
- eligibility: User asking about trial eligibility
- trial_search: User searching for clinical trials
- personal_condition: User mentioning their medical condition
- location_search: User providing or asking about location
- trial_info_request: User asking for specific trial information
- age_answer: User providing their age (during prescreening)
- yes_no_answer: User providing yes/no answer
- number_answer: User providing numerical answer
- condition_answer: User providing condition information
- location_answer: User providing location information
- medication_answer: User providing medication information
- eligibility_followup: Follow-up questions about eligibility
- trial_interest: User expressing interest in a trial
- general_query: General questions or conversation

Available next actions:
- ask_for_location: Ask user for their location
- ask_for_condition: Ask user about their medical condition
- search_trials: Search for relevant trials
- start_prescreening: Begin eligibility assessment
- continue_prescreening: Continue ongoing prescreening
- provide_trial_info: Provide information about trials
- clarify_intent: Ask for clarification
- general_response: Provide general response

Current conversation context:"""
        
        # Add conversation state context
        if context.conversation_state:
            base_prompt += f"\n- Current conversation state: {context.conversation_state}"
        
        # Add what we already know
        if context.focus_condition:
            base_prompt += f"\n- User's condition: {context.focus_condition}"
        
        if context.focus_location:
            base_prompt += f"\n- User's location: {context.focus_location}"
        
        # Add recent conversation history context
        if context.conversation_history:
            base_prompt += f"\n- Recent conversation history available"
        
        # Add state-specific guidance
        if context.conversation_state:
            if context.conversation_state == ConversationState.AWAITING_LOCATION.value:
                base_prompt += """

CRITICAL LOCATION INTELLIGENCE: User was just asked for their location. This is HIGH PRIORITY.

Location Recognition Patterns:
- Single words: "Tulsa" → location_answer (extract: Tulsa)
- City, State: "Dallas, Texas" → location_answer (extract: Dallas, Texas)
- Casual mentions: "I'm in Phoenix" → location_answer (extract: Phoenix)
- Follow-up references: "Tulsa, like I said" → location_answer (extract: Tulsa)
- State names: "Oklahoma" → location_answer (extract: Oklahoma)

ALWAYS extract location even from:
- Standalone city names without context words
- Abbreviations (TX, CA, NY)
- Informal phrases ("I live in...")
- Reference phrases ("...like I mentioned")

If ANY geographic location is mentioned, classify as location_answer and extract it.
"""
            elif context.conversation_state == ConversationState.AWAITING_CONDITION.value:
                base_prompt += """

IMPORTANT: User was just asked about their medical condition. Treat responses as condition answers.
Examples: "diabetes" = condition_answer, "I have gout" = condition_answer
"""
            elif "prescreening" in context.conversation_state.lower():
                base_prompt += """

IMPORTANT: User is currently in prescreening. Interpret responses as answers to eligibility questions.
Numbers could be age, frequency, or other medical data depending on context.
"""
        
        base_prompt += """

Be accurate and consider the conversation context when detecting intent and extracting entities.

Extract entities carefully:
- condition: Medical condition mentioned (full name, including modifiers)
- location: Geographic location with ENHANCED DETECTION:
  * Cities: Dallas, Tulsa, Phoenix, Atlanta, etc.
  * States: Texas, Oklahoma, California, NY, TX, etc.
  * Compound: "Dallas Texas", "Tulsa, OK", "Los Angeles, CA"
  * Casual: "I'm in...", "I live in...", "near...", "around..."
  * References: "Tulsa, like I said", "...as I mentioned"
  * Even standalone words if they're known locations
- age: Age in any format (convert written numbers to numeric)
- medication: Medication or treatment mentioned  
- number: Any numerical value (flares, frequency, dosage, duration, etc.)
- trial_name: Specific trial name or identifier
- boolean_answer: Yes/no answer in various formats
- confidence_scores: Optional confidence scores for entities

Return a JSON object with:
- intent: The detected intent type
- confidence: Confidence score (0.0 to 1.0)
- entities: Object with extracted entity values
- next_action: The next action to take
- reasoning: Brief explanation of the detection
- trigger_prescreening: Whether to trigger prescreening (boolean)
"""
        
        return base_prompt
    
    async def detect_intent(self, message: str, context: ConversationContext) -> GeminiDetectedIntent:
        """
        Detect intent and extract entities using hybrid approach: deterministic + AI fallback.
        
        Args:
            message: User's input message
            context: Current conversation context
            
        Returns:
            GeminiDetectedIntent with structured results
        """
        try:
            # Step 1: Try deterministic entity extraction first
            deterministic_entities = self._extract_entities_deterministic(message, context)
            
            # Step 2: Build context-aware system prompt for AI
            system_prompt = self._build_intent_detection_prompt(context)
            
            # Add conversation history context
            full_prompt = system_prompt
            if context.conversation_history:
                full_prompt += "\n\nRecent conversation history:\n"
                recent_history = context.conversation_history[-3:]
                for turn in recent_history:
                    if turn.get("user_message"):
                        full_prompt += f"User: {turn['user_message']}\n"
                    if turn.get("bot_response"):
                        full_prompt += f"Assistant: {turn['bot_response']}\n"
            
            # Step 3: Include deterministic results in prompt to guide AI
            if deterministic_entities:
                full_prompt += f"\n\nDeterministic entity extraction found: {deterministic_entities}"
                full_prompt += "\nConsider these findings but verify with your own analysis."
            
            full_prompt += f"\n\nCurrent user message: {message}\n\nDetect intent and extract entities from this message."
            
            # Step 4: Call Gemini for structured JSON response
            result = await self.gemini.extract_json(full_prompt, "")
            
            if result:
                # Step 5: Merge deterministic + AI results (deterministic takes priority)
                gemini_result = self._parse_gemini_result(result, message)
                return self._merge_entity_results(deterministic_entities, gemini_result)
            
            # Step 6: Fallback if Gemini fails - use deterministic results
            return self._fallback_with_deterministic(message, context, deterministic_entities)
            
        except Exception as e:
            logger.error(f"Hybrid intent detection error: {str(e)}")
            # Even if everything fails, try deterministic extraction
            deterministic_entities = self._extract_entities_deterministic(message, context)
            return self._fallback_with_deterministic(message, context, deterministic_entities)
    
    
    def _parse_gemini_result(self, result: Dict[str, Any], original_message: str) -> GeminiDetectedIntent:
        """Parse Gemini JSON result into structured intent"""
        try:
            # Extract intent
            intent_str = result.get("intent", "general_query")
            intent_type = IntentType(intent_str)
            
            # Extract entities
            entities_data = result.get("entities", {})
            entities = ExtractedEntities(
                condition=entities_data.get("condition"),
                location=entities_data.get("location"),
                age=entities_data.get("age"),
                medication=entities_data.get("medication"),
                number=entities_data.get("number"),
                trial_name=entities_data.get("trial_name"),
                boolean_answer=entities_data.get("boolean_answer"),
                confidence_scores=entities_data.get("confidence_scores", {})
            )
            
            return GeminiDetectedIntent(
                intent_type=intent_type,
                confidence=result.get("confidence", 0.8),
                entities=entities,
                next_action=result.get("next_action", "general_response"),
                reasoning=result.get("reasoning", "Gemini structured response"),
                trigger_prescreening=result.get("trigger_prescreening", False),
                metadata={
                    "original_message": original_message,
                    "gemini_result": result,
                    "extraction_method": "gemini_structured_response"
                }
            )
            
        except Exception as e:
            logger.error(f"Error parsing Gemini result: {str(e)}")
            return self._fallback_detection(original_message, None)
    
    def _extract_entities_deterministic(self, message: str, context: ConversationContext) -> Dict[str, Any]:
        """Extract entities using deterministic regex patterns (high precision)"""
        entities = {}
        message_lower = message.lower().strip()
        
        # Location extraction with high-precision patterns
        # FIXED: Removed non-greedy '?' to capture full multi-word locations like "New Orleans"
        location_patterns = [
            # Direct trial search patterns with location (highest priority)
            (r"(?:are there|any)\s+trials?\s+(?:in|at|near)\s+([a-zA-Z][a-zA-Z\s]{2,30})(?:[,\.!?]|$)", 1),
            (r"trials?\s+(?:in|at|near)\s+([a-zA-Z][a-zA-Z\s]{2,30})(?:[,\.!?]|$)", 1),
            # "Like I said" pattern (common follow-up)
            (r"([a-zA-Z\s]+),?\s+like\s+i\s+said", 1),
            # Simple standalone location when expecting location
            (r"^([a-zA-Z][a-zA-Z\s]{1,30})$", 1) if context.conversation_state == "AWAITING_LOCATION" else None
        ]
        
        # Filter out None patterns
        location_patterns = [p for p in location_patterns if p is not None]
        
        for pattern, group_idx in location_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                location = match.group(group_idx).strip()
                # Basic validation - not a medical condition or generic phrase
                if location.lower() not in ['trials', 'studies', 'research', 'available']:
                    entities['location'] = location.title()
                    break
        
        # Age extraction
        age_patterns = [
            (r"(?:i'?m |i am |age )(\d+)(?:\s*years?(?:\s*old)?)?", 1),
            (r"^(\d+)$", 1) if context.conversation_state in ["AWAITING_AGE", "prescreening_active"] else None
        ]
        age_patterns = [p for p in age_patterns if p is not None]
        
        for pattern, group_idx in age_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                try:
                    age = int(match.group(group_idx))
                    if 0 <= age <= 150:
                        entities['age'] = age
                        break
                except ValueError:
                    continue
        
        # Boolean responses (yes/no)
        if re.match(r"^(?:yes|yeah|yep|yup|sure|okay|ok|y)$", message_lower):
            entities['boolean_answer'] = True
        elif re.match(r"^(?:no|nope|nah|n)$", message_lower):
            entities['boolean_answer'] = False
        
        # Number extraction for medical context
        number_match = re.search(r"(\d+)\s*(?:times?|flares?|attacks?|episodes?|per)", message)
        if number_match:
            entities['number'] = int(number_match.group(1))
        
        return entities
    
    def _merge_entity_results(self, deterministic: Dict[str, Any], gemini_result: GeminiDetectedIntent) -> GeminiDetectedIntent:
        """Merge deterministic and AI results, giving priority to deterministic"""
        
        # Start with Gemini result
        merged_entities = gemini_result.entities
        
        # Override with deterministic results (higher precision)
        if deterministic.get('location'):
            merged_entities.location = deterministic['location']
        if deterministic.get('age') is not None:
            merged_entities.age = deterministic['age']
        if deterministic.get('boolean_answer') is not None:
            merged_entities.boolean_answer = deterministic['boolean_answer']
        if deterministic.get('number') is not None:
            merged_entities.number = deterministic['number']
        
        # Update confidence if deterministic found entities
        if deterministic:
            gemini_result.confidence = min(0.95, gemini_result.confidence + 0.1)
        
        # Add metadata about deterministic findings
        gemini_result.metadata.update({
            "deterministic_entities": deterministic,
            "extraction_method": "hybrid_deterministic_ai"
        })
        
        return gemini_result
    
    def _fallback_with_deterministic(self, message: str, context: ConversationContext, 
                                   deterministic: Dict[str, Any]) -> GeminiDetectedIntent:
        """Fallback using deterministic results when Gemini fails"""
        
        # Try to infer intent from deterministic entities and context
        intent_type = IntentType.GENERAL_QUERY
        next_action = "general_response"
        trigger_prescreening = False
        
        # Use context to guide intent detection
        if context.conversation_state == "AWAITING_LOCATION" and deterministic.get('location'):
            intent_type = IntentType.LOCATION_ANSWER
            next_action = "search_trials"
        elif context.conversation_state == "AWAITING_AGE" and deterministic.get('age'):
            intent_type = IntentType.AGE_ANSWER
            next_action = "continue_prescreening"
        elif context.conversation_state and "prescreening" in context.conversation_state.lower():
            if deterministic.get('boolean_answer') is not None:
                intent_type = IntentType.YES_NO_ANSWER
                next_action = "continue_prescreening"
            elif deterministic.get('number'):
                intent_type = IntentType.NUMBER_ANSWER
                next_action = "continue_prescreening"
        else:
            # Use message content to guess intent
            message_lower = message.lower()
            if "trials" in message_lower and deterministic.get('location'):
                intent_type = IntentType.TRIAL_SEARCH
                next_action = "search_trials"
            elif any(word in message_lower for word in ["eligible", "qualify"]):
                intent_type = IntentType.ELIGIBILITY
                next_action = "start_prescreening"
                trigger_prescreening = True
        
        # Build entities from deterministic results
        entities = ExtractedEntities(
            location=deterministic.get('location'),
            age=deterministic.get('age'),
            number=deterministic.get('number'),
            boolean_answer=deterministic.get('boolean_answer')
        )
        
        return GeminiDetectedIntent(
            intent_type=intent_type,
            confidence=0.7 if deterministic else 0.5,
            entities=entities,
            next_action=next_action,
            reasoning=f"Fallback detection using deterministic entities: {list(deterministic.keys())}",
            trigger_prescreening=trigger_prescreening,
            metadata={
                "original_message": message,
                "deterministic_entities": deterministic,
                "extraction_method": "fallback_deterministic"
            }
        )

    def _fallback_detection(self, message: str, context: Optional[ConversationContext]) -> GeminiDetectedIntent:
        """Fallback intent detection when Gemini fails"""
        # Simple fallback logic
        message_lower = message.lower()
        
        # Check for common patterns
        if any(word in message_lower for word in ["eligible", "qualify", "eligibility"]):
            intent_type = IntentType.ELIGIBILITY
            next_action = "start_prescreening"
            trigger_prescreening = True
        elif any(word in message_lower for word in ["i have", "diagnosed with", "suffer from"]):
            intent_type = IntentType.PERSONAL_CONDITION
            next_action = "ask_for_location"
            trigger_prescreening = True
        elif any(word in message_lower for word in ["trials", "studies", "research"]):
            intent_type = IntentType.TRIAL_SEARCH
            next_action = "search_trials"
            trigger_prescreening = False
        else:
            intent_type = IntentType.GENERAL_QUERY
            next_action = "general_response"
            trigger_prescreening = False
        
        return GeminiDetectedIntent(
            intent_type=intent_type,
            confidence=0.6,  # Lower confidence for fallback
            entities=ExtractedEntities(),
            next_action=next_action,
            reasoning="Fallback detection due to Gemini error",
            trigger_prescreening=trigger_prescreening,
            metadata={
                "original_message": message,
                "extraction_method": "fallback"
            }
        )


