"""
Context analysis for understanding user messages.

This module analyzes the conversation context to help with intent detection
and entity extraction, providing insights about referential language and
implicit meanings.
"""

import re
import logging
from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

from core.conversation.context import ConversationContext

logger = logging.getLogger(__name__)


@dataclass
class ContextualClue:
    """Represents a contextual clue found in the message or context"""
    clue_type: str  # "referential", "continuation", "clarification", etc.
    confidence: float
    evidence: str
    inferred_data: Dict[str, Any]


class ContextAnalyzer:
    """
    Analyzes conversation context to extract implicit information.
    
    This class helps understand user messages by analyzing:
    - Referential language ("that trial", "the study")
    - Continuation patterns
    - Implicit references to previous topics
    - Context-dependent meanings
    """
    
    # Referential words that indicate reference to previous content
    REFERENTIAL_WORDS = {
        "that", "this", "the", "it", "they", "those", "these",
        "same", "previous", "mentioned", "above", "earlier"
    }
    
    # Continuation indicators
    CONTINUATION_WORDS = {
        "also", "and", "plus", "additionally", "furthermore",
        "another", "other", "else", "more"
    }
    
    # Clarification indicators
    CLARIFICATION_PATTERNS = [
        r"i mean(?:t)?",
        r"what i mean(?:t)? (?:is|was)",
        r"to clarify",
        r"specifically",
        r"in particular",
        r"especially"
    ]
    
    def analyze_context(self, message: str, context: ConversationContext) -> List[ContextualClue]:
        """
        Analyze message in context to find contextual clues.
        
        Args:
            message: User message
            context: Conversation context
            
        Returns:
            List of contextual clues found
        """
        clues = []
        message_lower = message.lower()
        
        # Check for referential language
        referential_clues = self._analyze_referential_language(message_lower, context)
        clues.extend(referential_clues)
        
        # Check for continuation patterns
        continuation_clues = self._analyze_continuation_patterns(message_lower, context)
        clues.extend(continuation_clues)
        
        # Check for clarifications
        clarification_clues = self._analyze_clarifications(message_lower, context)
        clues.extend(clarification_clues)
        
        # Check for implicit references
        implicit_clues = self._analyze_implicit_references(message_lower, context)
        clues.extend(implicit_clues)
        
        # Analyze timing patterns
        timing_clues = self._analyze_timing_patterns(context)
        clues.extend(timing_clues)
        
        return clues
    
    def _analyze_referential_language(self, message: str, 
                                    context: ConversationContext) -> List[ContextualClue]:
        """Analyze referential words in the message"""
        clues = []
        
        # Check for referential words
        found_referentials = [word for word in self.REFERENTIAL_WORDS if word in message.split()]
        
        if not found_referentials:
            return clues
        
        # Analyze what the referentials might refer to
        if "trial" in message or "study" in message:
            # Referring to a trial
            if context.last_shown_trials:
                # User likely referring to recently shown trials
                clues.append(ContextualClue(
                    clue_type="referential_trial",
                    confidence=0.9,
                    evidence=f"Uses '{found_referentials[0]}' with 'trial'",
                    inferred_data={
                        "referring_to": "last_shown_trials",
                        "trials": context.last_shown_trials
                    }
                ))
            elif context.focus_condition:
                # Might be referring to trials for their condition
                clues.append(ContextualClue(
                    clue_type="referential_trial_condition",
                    confidence=0.8,
                    evidence=f"Uses '{found_referentials[0]}' with 'trial' and has focus condition",
                    inferred_data={
                        "condition": context.focus_condition,
                        "location": context.focus_location
                    }
                ))
        
        # Check for location references
        if any(word in message for word in ["location", "place", "city", "there"]):
            if context.mentioned_locations:
                last_location = list(context.mentioned_locations)[-1]
                clues.append(ContextualClue(
                    clue_type="referential_location",
                    confidence=0.85,
                    evidence=f"Referential word with location context",
                    inferred_data={"location": last_location}
                ))
        
        # Check for condition references
        if any(word in message for word in ["condition", "disease", "illness"]) or \
           (found_referentials and not any(word in message for word in ["trial", "location"])):
            if context.mentioned_conditions:
                last_condition = list(context.mentioned_conditions)[-1]
                clues.append(ContextualClue(
                    clue_type="referential_condition",
                    confidence=0.8,
                    evidence=f"Referential word with condition context",
                    inferred_data={"condition": last_condition}
                ))
        
        return clues
    
    def _analyze_continuation_patterns(self, message: str, 
                                     context: ConversationContext) -> List[ContextualClue]:
        """Analyze if message continues from previous topic"""
        clues = []
        
        # Check for continuation words
        found_continuations = [word for word in self.CONTINUATION_WORDS if word in message.split()]
        
        if found_continuations and context.conversation_history:
            last_turn = context.conversation_history[-1]
            last_topic = self._extract_topic_from_turn(last_turn)
            
            if last_topic:
                clues.append(ContextualClue(
                    clue_type="continuation",
                    confidence=0.85,
                    evidence=f"Uses continuation word '{found_continuations[0]}'",
                    inferred_data={
                        "continuing_topic": last_topic,
                        "continuation_word": found_continuations[0]
                    }
                ))
        
        # Check for implicit continuation (short message after bot question)
        if len(message.split()) <= 3 and context.conversation_history:
            last_response = context.conversation_history[-1].get("bot_response", "").lower()
            if "?" in last_response:
                clues.append(ContextualClue(
                    clue_type="answer_continuation",
                    confidence=0.9,
                    evidence="Short message following bot question",
                    inferred_data={
                        "answering_question": True,
                        "question": last_response
                    }
                ))
        
        return clues
    
    def _analyze_clarifications(self, message: str, 
                              context: ConversationContext) -> List[ContextualClue]:
        """Analyze clarification patterns"""
        clues = []
        
        for pattern in self.CLARIFICATION_PATTERNS:
            if re.search(pattern, message):
                # User is clarifying something
                if context.conversation_history:
                    # They're likely clarifying their last message
                    last_user_msg = None
                    for turn in reversed(context.conversation_history):
                        if turn.get("user_message"):
                            last_user_msg = turn["user_message"]
                            break
                    
                    if last_user_msg:
                        clues.append(ContextualClue(
                            clue_type="clarification",
                            confidence=0.9,
                            evidence=f"Clarification pattern: {pattern}",
                            inferred_data={
                                "clarifying_message": last_user_msg,
                                "pattern": pattern
                            }
                        ))
                break
        
        return clues
    
    def _analyze_implicit_references(self, message: str, 
                                   context: ConversationContext) -> List[ContextualClue]:
        """Analyze implicit references based on context"""
        clues = []
        
        # Check for implicit trial references
        if "eligible" in message or "qualify" in message:
            if context.last_shown_trials and "trial" not in message:
                # Likely asking about eligibility for shown trials
                clues.append(ContextualClue(
                    clue_type="implicit_trial_reference",
                    confidence=0.85,
                    evidence="Eligibility question without trial mention",
                    inferred_data={
                        "likely_about": "last_shown_trials",
                        "trials": context.last_shown_trials
                    }
                ))
        
        # Check for location assumptions
        if "trials" in message and "in" not in message:
            if context.focus_location:
                # User might assume we know their location
                clues.append(ContextualClue(
                    clue_type="implicit_location",
                    confidence=0.8,
                    evidence="Trial query without location",
                    inferred_data={"assumed_location": context.focus_location}
                ))
        
        # Check for condition assumptions
        if any(word in message for word in ["trial", "study", "research"]):
            if context.focus_condition and context.focus_condition not in message:
                # User might assume we remember their condition
                clues.append(ContextualClue(
                    clue_type="implicit_condition",
                    confidence=0.75,
                    evidence="Trial query without condition mention",
                    inferred_data={"assumed_condition": context.focus_condition}
                ))
        
        return clues
    
    def _analyze_timing_patterns(self, context: ConversationContext) -> List[ContextualClue]:
        """Analyze timing patterns in conversation"""
        clues = []
        
        if not context.conversation_history or len(context.conversation_history) < 2:
            return clues
        
        # Check for rapid responses (likely engaged user)
        recent_timestamps = []
        for turn in context.conversation_history[-5:]:  # Last 5 turns
            if turn.get("timestamp"):
                recent_timestamps.append(turn["timestamp"])
        
        if len(recent_timestamps) >= 2:
            # Calculate average response time
            time_diffs = []
            for i in range(1, len(recent_timestamps)):
                diff = recent_timestamps[i] - recent_timestamps[i-1]
                if isinstance(diff, timedelta):
                    time_diffs.append(diff.total_seconds())
            
            if time_diffs:
                avg_response_time = sum(time_diffs) / len(time_diffs)
                if avg_response_time < 30:  # Less than 30 seconds average
                    clues.append(ContextualClue(
                        clue_type="rapid_engagement",
                        confidence=0.7,
                        evidence=f"Average response time: {avg_response_time:.1f}s",
                        inferred_data={
                            "engagement_level": "high",
                            "likely_continuing_flow": True
                        }
                    ))
        
        return clues
    
    def _extract_topic_from_turn(self, turn: Dict[str, Any]) -> Optional[str]:
        """Extract the main topic from a conversation turn"""
        bot_response = turn.get("bot_response", "").lower()
        
        # Look for trial mentions
        if "trial" in bot_response:
            # Extract condition from trial mention
            match = re.search(r"(\w+)\s+trial", bot_response)
            if match:
                return f"{match.group(1)}_trial"
        
        # Look for location mentions
        if "location" in bot_response or "where" in bot_response:
            return "location"
        
        # Look for condition mentions
        if "condition" in bot_response or "diagnosis" in bot_response:
            return "condition"
        
        # Look for eligibility mentions
        if "eligible" in bot_response or "qualify" in bot_response:
            return "eligibility"
        
        return None
    
    def infer_missing_information(self, message: str, context: ConversationContext,
                                clues: List[ContextualClue]) -> Dict[str, Any]:
        """
        Infer missing information from context and clues.
        
        Args:
            message: User message
            context: Conversation context
            clues: Contextual clues found
            
        Returns:
            Dictionary of inferred information
        """
        inferred = {}
        
        # Process each clue type
        for clue in clues:
            if clue.clue_type == "referential_trial":
                if clue.confidence >= 0.8:
                    inferred["referring_to_trials"] = clue.inferred_data.get("trials", [])
                    
            elif clue.clue_type == "referential_location":
                if clue.confidence >= 0.8:
                    inferred["likely_location"] = clue.inferred_data.get("location")
                    
            elif clue.clue_type == "referential_condition":
                if clue.confidence >= 0.8:
                    inferred["likely_condition"] = clue.inferred_data.get("condition")
                    
            elif clue.clue_type == "implicit_location":
                if "likely_location" not in inferred:
                    inferred["likely_location"] = clue.inferred_data.get("assumed_location")
                    
            elif clue.clue_type == "implicit_condition":
                if "likely_condition" not in inferred:
                    inferred["likely_condition"] = clue.inferred_data.get("assumed_condition")
        
        # Additional inference based on message patterns
        if "eligible" in message.lower() and context.last_shown_trials:
            if "specific_trial" not in inferred:
                # Infer they're asking about eligibility for shown trials
                inferred["eligibility_context"] = "last_shown_trials"
        
        return inferred
    
    def get_context_summary(self, context: ConversationContext) -> Dict[str, Any]:
        """
        Get a summary of relevant context information.
        
        Args:
            context: Conversation context
            
        Returns:
            Summary of context
        """
        summary = {
            "has_focus_condition": bool(context.focus_condition),
            "has_focus_location": bool(context.focus_location),
            "has_shown_trials": bool(context.last_shown_trials),
            "conversation_length": len(context.conversation_history),
            "mentioned_conditions_count": len(context.mentioned_conditions),
            "mentioned_locations_count": len(context.mentioned_locations),
        }
        
        # Add recent activity summary
        if context.conversation_history:
            last_turn = context.conversation_history[-1]
            summary["last_user_message"] = last_turn.get("user_message", "")
            summary["last_bot_response_preview"] = last_turn.get("bot_response", "")[:100]
            
            # Check if bot asked a question
            if "?" in last_turn.get("bot_response", ""):
                summary["bot_asked_question"] = True
                summary["awaiting_response"] = True
        
        # Add state information
        if context.conversation_state:
            summary["current_state"] = context.conversation_state
            summary["in_prescreening"] = "PRESCREENING" in context.conversation_state or \
                                       "AWAITING" in context.conversation_state
        
        return summary