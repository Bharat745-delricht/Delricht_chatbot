"""
Entity extraction module with intent-specific rules.

This module extracts entities (conditions, locations, etc.) from user messages
using intent-specific extraction strategies and normalization.
"""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple, Set
from dataclasses import dataclass
from enum import Enum

from core.services.condition_normalizer import condition_normalizer
from core.services.condition_registry import condition_registry
from .intent_detector import IntentType, DetectedIntent
from core.conversation.context import ConversationContext

logger = logging.getLogger(__name__)


class EntityType(str, Enum):
    """Types of entities that can be extracted"""
    CONDITION = "condition"
    LOCATION = "location"
    AGE = "age"
    TRIAL_ID = "trial_id"
    TRIAL_NAME = "trial_name"
    MEDICATION = "medication"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DURATION = "duration"


@dataclass
class ExtractedEntity:
    """Represents an extracted entity"""
    entity_type: EntityType
    value: Any
    normalized_value: Optional[Any] = None
    confidence: float = 1.0
    source: str = "direct"  # "direct", "inferred", "context"
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class EntityExtractor:
    """
    Extracts and normalizes entities from user messages.
    
    This class uses intent-specific strategies to extract entities,
    applying appropriate normalization and validation.
    """
    
    def __init__(self):
        self._initialize_patterns()
        
    def _initialize_patterns(self):
        """Initialize extraction patterns"""
        # Location patterns - more precise to avoid false positives
        # FIXED: Removed non-greedy '?' to capture full multi-word locations like "New Orleans"
        self.location_patterns = [
            # Enhanced: Direct trial search patterns with location (highest priority)
            (r"(?:are there|any)\s+trials?\s+(?:in|at|near)\s+([a-zA-Z][a-zA-Z\s]{2,30})(?:[,\.!?]|$)", 1),
            (r"trials?\s+(?:in|at|near)\s+([a-zA-Z][a-zA-Z\s]{2,30})(?:[,\.!?]|$)", 1),
            (r"stud(?:y|ies)\s+(?:in|at|near)\s+([a-zA-Z][a-zA-Z\s]{2,30})(?:[,\.!?]|$)", 1),
            # Enhanced: Question patterns that include location implicitly
            (r"trials?\s+(?:available\s+)?in\s+([a-zA-Z][a-zA-Z\s]{2,30})(?:[,\.!?]|$)", 1),
            (r"what.+trials?.+in\s+([a-zA-Z][a-zA-Z\s]{2,30})(?:[,\.!?]|$)", 1),
            # Available in pattern (common for trial searches)
            (r"available\s+in\s+([a-zA-Z][a-zA-Z\s]{2,30})(?:[,\.!?]|$)", 1),
            # Location keywords with word boundaries
            (r"\b(?:in|at|near|around)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*?)(?:[,\.!?]|$)", 1),
            (r"(?:from|based in|located in)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*?)(?:[,\.!?]|$)", 1),
            (r"(?:live|living) in\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*?)(?:[,\.!?]|$)", 1),
            # Enhanced: Like I said pattern (common follow-up)
            (r"([a-zA-Z\s]+),?\s+like\s+i\s+said", 1),
            # Standalone location (case insensitive)
            (r"^([a-zA-Z][a-zA-Z\s]{1,30})$", 1),
        ]
        
        # Condition patterns  
        self.condition_patterns = [
            # Personal condition statements
            (r"i have (.+?)(?:[,\.!?]|$)", 1),
            (r"i'?ve been diagnosed with (.+?)(?:[,\.!?]|$)", 1),
            (r"i suffer from (.+?)(?:[,\.!?]|$)", 1),
            (r"my (?:condition|diagnosis) is (.+?)(?:[,\.!?]|$)", 1),
            (r"i'?m being treated for (.+?)(?:[,\.!?]|$)", 1),
            (r"i was diagnosed with (.+?)(?:[,\.!?]|$)", 1),
            # Trial-specific conditions (including studies and research)
            (r"the ([a-zA-Z\s]+) (?:trials?|studies|research)", 1),
            (r"([a-zA-Z\s]+) (?:trials?|studies|research) (?:in|near|around|for)", 1),
            (r"(?:trials?|studies|research) for ([a-zA-Z\s]+)", 1),
            (r"eligible for (?:the )?([a-zA-Z\s]+) (?:trials?|studies)", 1),
            (r"(?:find|search|look for) ([a-zA-Z\s]+) (?:trials?|studies|research)", 1),
            (r"clinical (?:trials?|studies|research) for ([a-zA-Z\s]+)", 1),
            (r"([a-zA-Z\s]+) clinical (?:trials?|studies|research)", 1),
            # Studies-specific patterns
            (r"([a-zA-Z\s]+) studies (?:in|near|around)", 1),
            (r"studies (?:for|on|about) ([a-zA-Z\s]+)", 1),
            (r"research (?:for|on|about) ([a-zA-Z\s]+)", 1),
            # Information request patterns
            (r"tell me (?:more )?about (?:the )?([a-zA-Z\s]+?)(?:\s+(?:condition|disease|trial|study))?(?:[,\.!?]|$)", 1),
            (r"what is (?:the )?([a-zA-Z\s]+?)(?:\s+(?:condition|disease))?(?:[,\.!?]|$)", 1),
            (r"explain (?:the )?([a-zA-Z\s]+?)(?:\s+to me)?(?:[,\.!?]|$)", 1),
            (r"(?:more )?(?:info|information|details) (?:about|on) (?:the )?([a-zA-Z\s]+?)(?:\s+(?:condition|disease))?(?:[,\.!?]|$)", 1),
            (r"learn more about (?:the )?([a-zA-Z\s]+?)(?:\s+(?:condition|disease))?(?:[,\.!?]|$)", 1),
        ]
        
        # Age patterns
        self.age_patterns = [
            (r"(?:i'?m |i am )?(\d+)(?:\s*years?(?:\s*old)?)?", 1),
            (r"age(?:d)?\s*(?:is\s*)?(\d+)", 1),
            (r"(\d+)\s*y/?o", 1),
            (r"^(\d+)$", 1),  # Just a number in age context
        ]
        
        # Number patterns
        self.number_patterns = [
            (r"(\d+)\s*(?:times?|x)", 1),
            (r"(\d+)\s*(?:flares?|attacks?|episodes?)", 1),
            (r"(?:about|around|approximately)?\s*(\d+)", 1),
            (r"^(\d+)$", 1),
        ]
        
        # Boolean patterns
        self.yes_patterns = [
            r"^(?:yes|yeah|yep|yup|sure|okay|ok|definitely|absolutely|correct)$",
            r"^y$",
            r"that'?s (?:right|correct)",
            r"i do",
            r"i am",
        ]
        
        self.no_patterns = [
            r"^(?:no|nope|nah|not really|negative|incorrect)$",
            r"^n$", 
            r"that'?s (?:wrong|incorrect)",
            r"i don'?t",
            r"i'?m not",
        ]
        
        # Medication patterns
        self.medication_patterns = [
            (r"(?:i take|i'?m taking|taking) ([a-zA-Z\s,]+)", 1),
            (r"(?:on|using) ([a-zA-Z\s,]+) (?:medication|medicine|drug)", 1),
            (r"([a-zA-Z]+) (?:\d+\s*mg|\d+mg)", 1),  # Drug name with dosage
        ]
        
        # Trial reference patterns
        self.trial_patterns = [
            (r"trial #?(\d+)", 1),
            (r"study #?(\d+)", 1),
            (r"protocol ([A-Z0-9\-]+)", 1),
            (r"NCT(\d+)", 1),  # ClinicalTrials.gov ID
        ]
    
    def extract_entities(self, message: str, intent: DetectedIntent,
                        context: ConversationContext) -> Dict[EntityType, ExtractedEntity]:
        """
        Extract entities using two-phase approach: intent-specific then opportunistic.
        
        Args:
            message: User message
            intent: Detected intent
            context: Conversation context
            
        Returns:
            Dictionary mapping entity types to extracted entities
        """
        entities = {}
        
        # Phase 1: Intent-specific extraction (high confidence, targeted)
        entities.update(self._extract_intent_specific_entities(message, intent, context))
        
        # Phase 2: Opportunistic extraction of other entities (medium confidence)
        # Only extract entities we don't already have
        additional_entities = self._extract_additional_entities(message, context, exclude=entities.keys())
        entities.update(additional_entities)
        
        # Phase 3: Apply context-based inference if needed
        entities = self._apply_context_inference(entities, intent, context)
        
        return entities
    
    def _extract_intent_specific_entities(self, message: str, intent: DetectedIntent,
                                         context: ConversationContext) -> Dict[EntityType, ExtractedEntity]:
        """
        Phase 1: Extract entities specific to the detected intent (high confidence).
        """
        entities = {}
        
        # Use intent-specific extraction strategies
        if intent.intent_type == IntentType.LOCATION_ANSWER:
            entities.update(self._extract_location_answer(message, context))
            
        elif intent.intent_type == IntentType.CONDITION_ANSWER:
            entities.update(self._extract_condition_answer(message, context))
            
        elif intent.intent_type == IntentType.AGE_ANSWER:
            entities.update(self._extract_age(message))
            
        elif intent.intent_type == IntentType.YES_NO_ANSWER:
            entities.update(self._extract_boolean(message))
            
        elif intent.intent_type == IntentType.NUMBER_ANSWER:
            entities.update(self._extract_number(message))
            
        elif intent.intent_type == IntentType.MEDICATION_ANSWER:
            entities.update(self._extract_medications(message))
            
        elif intent.intent_type in [IntentType.TRIAL_INFO_REQUEST, IntentType.TRIAL_SEARCH]:
            # Extract both condition and location
            entities.update(self._extract_condition(message, context))
            entities.update(self._extract_location(message, context))
            
        elif intent.intent_type == IntentType.PERSONAL_CONDITION:
            entities.update(self._extract_condition(message, context))
            
        elif intent.intent_type == IntentType.LOCATION_SEARCH:
            entities.update(self._extract_location(message, context))
            
        else:
            # For unspecified intents, try general extraction
            entities.update(self._extract_all_entities(message, context))
        
        return entities
    
    def _extract_additional_entities(self, message: str, context: ConversationContext,
                                   exclude: set) -> Dict[EntityType, ExtractedEntity]:
        """
        Phase 2: Opportunistically extract other entities not already found (medium confidence).
        """
        entities = {}
        
        # Only extract entity types we don't already have
        if EntityType.CONDITION not in exclude:
            condition_entities = self._extract_condition(message, context)
            # Lower confidence for opportunistic extraction
            for entity in condition_entities.values():
                entity.confidence = max(0.5, entity.confidence - 0.2)
                entity.source = "opportunistic"
            entities.update(condition_entities)
        
        if EntityType.LOCATION not in exclude:
            location_entities = self._extract_location(message, context)
            # Lower confidence for opportunistic extraction
            for entity in location_entities.values():
                entity.confidence = max(0.5, entity.confidence - 0.2)
                entity.source = "opportunistic"
            entities.update(location_entities)
        
        if EntityType.TRIAL_ID not in exclude:
            trial_entities = self._extract_trial_reference(message)
            # Lower confidence for opportunistic extraction
            for entity in trial_entities.values():
                entity.confidence = max(0.5, entity.confidence - 0.2)
                entity.source = "opportunistic"
            entities.update(trial_entities)
        
        # Don't opportunistically extract age/numbers/medications unless in appropriate context
        # These are too context-sensitive for general opportunistic extraction
        
        return entities
    
    def _extract_location(self, message: str, context: ConversationContext) -> Dict[EntityType, ExtractedEntity]:
        """Extract location entity"""
        entities = {}
        
        # Try each location pattern
        for pattern, group_idx in self.location_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                location = match.group(group_idx).strip()
                
                # Filter out common false positives
                false_positives = [
                    'trials', 'clinical trials', 'studies', 'research',
                    'participate', 'join', 'enroll', 'available', 'hello',
                    'trials are available', 'tell me about your trials',
                    'what trials', 'which trials', 'any trials',
                    'clinical trial', 'medical research'
                ]
                
                if location.lower() in false_positives:
                    continue
                
                # Check if it contains false positive phrases
                if any(fp in location.lower() for fp in false_positives):
                    continue
                
                # Validate the extracted location text itself is not a condition
                # (Don't check the entire message - that would reject valid multi-entity messages)
                if not condition_registry.is_medical_condition(location.lower()) and not self._is_likely_condition(location):
                    # Clean up location text
                    location = self._clean_location_text(location)
                    
                    # Skip if empty after cleanup
                    if not location:
                        continue
                    
                    # Double-check after cleaning - make sure it's still not a condition
                    if self._is_likely_condition(location):
                        continue
                    
                    # Normalize location
                    location = self._normalize_location(location)
                    
                    # Final validation - reasonable location length
                    if len(location) <= 50 and len(location.split()) <= 4:
                        entities[EntityType.LOCATION] = ExtractedEntity(
                            entity_type=EntityType.LOCATION,
                            value=location,
                            normalized_value=location,
                            confidence=0.9,
                            source="direct",
                            metadata={"pattern": pattern}
                        )
                        break
        
        return entities
    
    def _extract_location_answer(self, message: str, context: ConversationContext) -> Dict[EntityType, ExtractedEntity]:
        """Extract location from a contextual answer"""
        entities = {}
        
        # In location answer context, even single words can be locations
        message_clean = message.strip()
        
        # Skip if it's a yes/no response
        if self._is_boolean_response(message_clean):
            return entities
        
        # Check if it's a known medical condition first
        if condition_registry.is_medical_condition(message_clean.lower()):
            # This is a condition, not a location
            return entities
        
        # Normalize and accept as location
        location = self._normalize_location(message_clean)
        
        entities[EntityType.LOCATION] = ExtractedEntity(
            entity_type=EntityType.LOCATION,
            value=location,
            normalized_value=location,
            confidence=0.85,
            source="contextual",
            metadata={"context": "awaiting_location"}
        )
        
        return entities
    
    def _extract_condition(self, message: str, context: ConversationContext) -> Dict[EntityType, ExtractedEntity]:
        """Extract medical condition entity"""
        entities = {}
        
        # Try condition patterns
        for pattern, group_idx in self.condition_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                condition = match.group(group_idx).strip()
                
                # Clean up extracted condition
                condition = self._clean_condition_text(condition)
                
                # Skip if condition is empty after cleanup
                if not condition:
                    continue
                
                # Normalize condition
                normalized = condition_normalizer.normalize_condition(condition)
                
                entities[EntityType.CONDITION] = ExtractedEntity(
                    entity_type=EntityType.CONDITION,
                    value=condition,
                    normalized_value=normalized,
                    confidence=0.9,
                    source="direct",
                    metadata={"pattern": pattern}
                )
                break
        
        # If no pattern match, check for standalone condition
        if not entities and len(message.split()) <= 3:
            normalized = condition_normalizer.normalize_condition(message)
            if condition_registry.is_medical_condition(message.lower()) or normalized != message:
                entities[EntityType.CONDITION] = ExtractedEntity(
                    entity_type=EntityType.CONDITION,
                    value=message,
                    normalized_value=normalized,
                    confidence=0.8,
                    source="inferred",
                    metadata={"method": "short_message"}
                )
        
        return entities
    
    def _extract_condition_answer(self, message: str, context: ConversationContext) -> Dict[EntityType, ExtractedEntity]:
        """Extract condition from a contextual answer"""
        entities = {}
        
        # In condition answer context, normalize and accept
        condition = message.strip().lower()
        
        # Remove common prefixes
        condition = re.sub(r"^(i have |i've been diagnosed with |i suffer from )", "", condition)
        condition = re.sub(r"^(a |an |the )", "", condition)
        
        # Normalize
        normalized = condition_normalizer.normalize_condition(condition)
        
        entities[EntityType.CONDITION] = ExtractedEntity(
            entity_type=EntityType.CONDITION,
            value=condition,
            normalized_value=normalized,
            confidence=0.85,
            source="contextual",
            metadata={"context": "awaiting_condition"}
        )
        
        return entities
    
    def _extract_age(self, message: str) -> Dict[EntityType, ExtractedEntity]:
        """Extract age entity"""
        entities = {}
        
        for pattern, group_idx in self.age_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                age_str = match.group(group_idx)
                try:
                    age = int(age_str)
                    if 0 <= age <= 150:  # Reasonable age range
                        entities[EntityType.AGE] = ExtractedEntity(
                            entity_type=EntityType.AGE,
                            value=age,
                            normalized_value=age,
                            confidence=0.95,
                            source="direct",
                            metadata={"pattern": pattern}
                        )
                        break
                except ValueError:
                    continue
        
        return entities
    
    def _extract_boolean(self, message: str) -> Dict[EntityType, ExtractedEntity]:
        """Extract boolean (yes/no) entity"""
        entities = {}
        message_lower = message.lower().strip()
        
        # Check yes patterns
        for pattern in self.yes_patterns:
            if re.match(pattern, message_lower):
                entities[EntityType.BOOLEAN] = ExtractedEntity(
                    entity_type=EntityType.BOOLEAN,
                    value="yes",
                    normalized_value=True,
                    confidence=0.95,
                    source="direct"
                )
                return entities
        
        # Check no patterns
        for pattern in self.no_patterns:
            if re.match(pattern, message_lower):
                entities[EntityType.BOOLEAN] = ExtractedEntity(
                    entity_type=EntityType.BOOLEAN,
                    value="no",
                    normalized_value=False,
                    confidence=0.95,
                    source="direct"
                )
                return entities
        
        return entities
    
    def _extract_number(self, message: str) -> Dict[EntityType, ExtractedEntity]:
        """Extract number entity"""
        entities = {}
        
        for pattern, group_idx in self.number_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                number_str = match.group(group_idx)
                try:
                    number = int(number_str)
                    entities[EntityType.NUMBER] = ExtractedEntity(
                        entity_type=EntityType.NUMBER,
                        value=number,
                        normalized_value=number,
                        confidence=0.9,
                        source="direct",
                        metadata={"pattern": pattern}
                    )
                    break
                except ValueError:
                    continue
        
        return entities
    
    def _extract_medications(self, message: str) -> Dict[EntityType, ExtractedEntity]:
        """Extract medication entities"""
        entities = {}
        medications = []
        
        for pattern, group_idx in self.medication_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                med_string = match.group(group_idx)
                # Split by commas or "and"
                med_list = re.split(r"[,\s]+and\s+|,\s*", med_string)
                medications.extend([med.strip() for med in med_list if med.strip()])
        
        if medications:
            entities[EntityType.MEDICATION] = ExtractedEntity(
                entity_type=EntityType.MEDICATION,
                value=medications,
                normalized_value=medications,
                confidence=0.85,
                source="direct"
            )
        
        return entities
    
    def _extract_all_entities(self, message: str, context: ConversationContext) -> Dict[EntityType, ExtractedEntity]:
        """Extract all possible entities from message"""
        entities = {}
        
        # Try to extract each entity type
        entities.update(self._extract_location(message, context))
        entities.update(self._extract_condition(message, context))
        entities.update(self._extract_trial_reference(message))
        
        # Don't extract age/numbers unless in appropriate context
        if context.conversation_state in ["AWAITING_AGE", "AWAITING_FLARES"]:
            entities.update(self._extract_age(message))
            entities.update(self._extract_number(message))
        
        return entities
    
    def _extract_trial_reference(self, message: str) -> Dict[EntityType, ExtractedEntity]:
        """Extract trial ID or reference"""
        entities = {}
        
        for pattern, group_idx in self.trial_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                trial_ref = match.group(group_idx)
                entities[EntityType.TRIAL_ID] = ExtractedEntity(
                    entity_type=EntityType.TRIAL_ID,
                    value=trial_ref,
                    normalized_value=trial_ref,
                    confidence=0.9,
                    source="direct",
                    metadata={"pattern": pattern}
                )
                break
        
        return entities
    
    def _apply_context_inference(self, entities: Dict[EntityType, ExtractedEntity],
                               intent: DetectedIntent, 
                               context: ConversationContext) -> Dict[EntityType, ExtractedEntity]:
        """Apply context-based inference to fill missing entities"""
        
        # If looking for trials but missing location, infer from context
        if intent.intent_type in [IntentType.TRIAL_SEARCH, IntentType.TRIAL_INFO_REQUEST]:
            if EntityType.LOCATION not in entities and context.focus_location:
                entities[EntityType.LOCATION] = ExtractedEntity(
                    entity_type=EntityType.LOCATION,
                    value=context.focus_location,
                    normalized_value=context.focus_location,
                    confidence=0.8,
                    source="context",
                    metadata={"inferred_from": "focus_location"}
                )
            
            if EntityType.CONDITION not in entities and context.focus_condition:
                entities[EntityType.CONDITION] = ExtractedEntity(
                    entity_type=EntityType.CONDITION,
                    value=context.focus_condition,
                    normalized_value=context.focus_condition,
                    confidence=0.8,
                    source="context",
                    metadata={"inferred_from": "focus_condition"}
                )
        
        return entities
    
    def _normalize_location(self, location: str) -> str:
        """Normalize location name"""
        # Capitalize each word
        words = location.split()
        normalized = " ".join(word.capitalize() for word in words)
        
        # Handle common abbreviations
        abbreviations = {
            "Ny": "New York",
            "Nyc": "New York City",
            "La": "Los Angeles",
            "Sf": "San Francisco",
            "Dc": "Washington DC",
        }
        
        for abbr, full in abbreviations.items():
            if normalized == abbr:
                return full
        
        return normalized
    
    def _is_likely_condition(self, text: str) -> bool:
        """Check if text is likely a medical condition"""
        text_lower = text.lower().strip()
        
        # Check condition registry
        if condition_registry.is_medical_condition(text_lower):
            return True
        
        # Check if normalization changes it (indicates it's a known condition)
        normalized = condition_normalizer.normalize_condition(text_lower)
        if normalized != text_lower:
            return True
        
        # Check for medical keywords
        medical_keywords = [
            "disease", "syndrome", "disorder", "cancer", "diabetes",
            "arthritis", "asthma", "copd", "ibs", "gout", "fungus",
            "infection", "pain", "ache", "inflammation", "chronic"
        ]
        
        return any(keyword in text_lower for keyword in medical_keywords)
    
    def _is_boolean_response(self, text: str) -> bool:
        """Check if text is a yes/no response"""
        text_lower = text.lower().strip()
        
        all_boolean_patterns = self.yes_patterns + self.no_patterns
        for pattern in all_boolean_patterns:
            if re.match(pattern, text_lower):
                return True
        
        return False
    
    def _clean_condition_text(self, condition: str) -> str:
        """Clean up extracted condition text"""
        # Remove common command verbs
        condition = re.sub(r"^(find|search|look for|looking for|show me|get)\s+", "", condition, re.IGNORECASE)
        
        # Remove trailing location indicators that might have been captured
        condition = re.sub(r"\s+(in|near|around|at)\s+[a-zA-Z\s]+$", "", condition, re.IGNORECASE)
        
        # Remove common prefixes/suffixes
        condition = re.sub(r"^(a |an |the |some |any )", "", condition, re.IGNORECASE)
        condition = re.sub(r"[.,!?]+$", "", condition)
        
        # Handle special cases
        if condition.lower() in ["clinical", "trials", "studies", "research"]:
            return ""  # These aren't conditions
        
        return condition.strip()
    
    def _clean_location_text(self, location: str) -> str:
        """Clean up extracted location text"""
        # Remove "near me" phrases
        location = re.sub(r"\b(near\s+)?me\s+(in|at)?\s*", "", location, re.IGNORECASE)
        
        # Remove common prefixes
        location = re.sub(r"^(in|at|near|around)\s+", "", location, re.IGNORECASE)
        
        # Clean up extra whitespace
        location = re.sub(r"\s+", " ", location).strip()
        
        return location
    
    def get_entities_summary(self, entities: Dict[EntityType, ExtractedEntity]) -> Dict[str, Any]:
        """Get a summary of extracted entities"""
        summary = {}
        
        for entity_type, entity in entities.items():
            summary[entity_type.value] = {
                "value": entity.value,
                "normalized": entity.normalized_value,
                "confidence": entity.confidence,
                "source": entity.source
            }
        
        return summary