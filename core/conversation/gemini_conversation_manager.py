"""
Gemini-powered conversation manager for clinical trials chatbot.

This module handles the complete conversation flow using Gemini
for intent detection, entity extraction, state management, and response generation.
"""

import json
import logging
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
import re
from datetime import datetime

from core.conversation.context import ConversationContext
from core.conversation.understanding.gemini_intent_detector import GeminiIntentDetector, GeminiDetectedIntent
from core.database import db
from models.schemas import ConversationState
from core.prescreening.gemini_prescreening_manager import GeminiPrescreeningManager
from core.services.gemini_service import gemini_service

logger = logging.getLogger(__name__)


@dataclass
class ConversationAction:
    """Represents an action to take in the conversation"""
    action_type: str
    parameters: Dict[str, Any]
    response_text: str
    new_state: Optional[str] = None
    collect_entities: Optional[List[str]] = None
    metadata: Dict[str, Any] = None


class GeminiConversationManager:
    """
    Gemini-powered conversation manager that handles the complete
    conversation flow with intent detection, state management, and responses.
    """
    
    def __init__(self, api_key: str = None):
        self.gemini = gemini_service
        self.intent_detector = GeminiIntentDetector()
        self.prescreening_manager = GeminiPrescreeningManager()
        self.conversation_functions = self._define_conversation_functions()

    def _check_existing_prescreening_session(self, session_id: str, condition: str = None, trial_id: int = None) -> Dict[str, Any]:
        """
        Check if there's already a prescreening session for this conversation.

        Now checks BOTH in-progress AND completed sessions to prevent duplicates.

        Args:
            session_id: Session identifier
            condition: Condition to match (optional)
            trial_id: Specific trial ID to check (optional)

        Returns:
            Existing session dict or None
        """
        try:
            query = """
                SELECT ps.*, ct.trial_name, ct.conditions
                FROM prescreening_sessions ps
                LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
                WHERE ps.session_id = %s
                AND (%s IS NULL OR ps.trial_id = %s)
                ORDER BY ps.started_at DESC
                LIMIT 1
            """
            results = db.execute_query(query, (session_id, trial_id, trial_id))

            if results and len(results) > 0:
                session = results[0]
                from datetime import datetime, timedelta

                if session.get('started_at'):
                    start_time = session['started_at']
                    if isinstance(start_time, str):
                        start_time = datetime.fromisoformat(start_time)

                    time_since_start = datetime.now() - start_time
                    status = session.get('status')

                    logger.info(f"{'='*80}")
                    logger.info(f"🔍 DEDUPLICATION CHECK - Session: {session_id}")
                    logger.info(f"   Trial ID: {session.get('trial_id')}")
                    logger.info(f"   Status: {status}")
                    logger.info(f"   Started: {start_time}")
                    logger.info(f"   Time since start: {time_since_start}")
                    logger.info(f"{'='*80}")

                    # Check if completed within last 24 hours (prevent duplicate prescreening)
                    if status == 'completed':
                        if time_since_start < timedelta(hours=24):
                            logger.warning(f"🚫 DUPLICATE BLOCKED: User already completed prescreening for trial {session.get('trial_id')}")
                            logger.warning(f"   Completed at: {start_time}")
                            return session
                        else:
                            logger.info(f"✅ Completed session is >24h old, allowing new prescreening")

                    # Check if in-progress within last hour (offer to resume)
                    elif time_since_start < timedelta(hours=1):
                        logger.info(f"♻️  IN-PROGRESS SESSION: Offering to resume")
                        return session
                    else:
                        logger.info(f"⏰ In-progress session is >1h old, treating as abandoned")

            return None
        except Exception as e:
            logger.error(f"Error checking existing prescreening session: {str(e)}")
            return None
    
    def _handle_existing_prescreening_session(self, existing_session: Dict[str, Any], context: ConversationContext) -> Dict[str, Any]:
        """Handle the case where a prescreening session already exists"""
        try:
            # Get current progress
            session_id = existing_session['session_id']
            trial_name = existing_session.get('trial_name', 'this trial')
            condition = existing_session.get('conditions', existing_session.get('condition', 'the condition'))
            status = existing_session.get('status')

            # Get answered questions count
            answered_count = db.execute_query("""
                SELECT COUNT(*) as count
                FROM prescreening_answers
                WHERE session_id = %s
            """, (session_id,))

            answered = answered_count[0]['count'] if answered_count else 0
            total_questions = existing_session.get('total_questions', 6)

            logger.info(f"📋 HANDLING EXISTING SESSION:")
            logger.info(f"   Status: {status}")
            logger.info(f"   Answered: {answered}/{total_questions}")

            # Handle completed prescreening (DUPLICATE PREVENTION)
            if status == 'completed' or answered >= total_questions:
                logger.warning(f"🚫 DUPLICATE BLOCKED: Prescreening already completed")
                response = (
                    f"You already completed prescreening for this {condition} trial!\n\n"
                    f"Our research team has your information and will contact you within 1-2 business days.\n\n"
                    f"Would you like to search for other trials?"
                )
                return {
                    "response": response,
                    "new_state": "completed",
                    "metadata": {
                        "duplicate_blocked": True,
                        "existing_session_id": existing_session['id'],
                        "trial_id": existing_session['trial_id']
                    }
                }

            # Handle in-progress prescreening (RESUME OPTION)
            else:
                logger.info(f"♻️  OFFERING RESUME: In-progress session found")
                response = f"I notice we started checking your eligibility for {self._get_condition_based_trial_reference({'conditions': condition}, condition)} earlier (answered {answered} of {total_questions} questions). Would you like to:\n\n"
                response += f"1. Continue where we left off\n"
                response += f"2. Start over with fresh information\n"
                response += f"3. Look for different trials instead"

                return {
                    "response": response,
                    "new_state": "prescreening_resume_choice",
                    "metadata": {
                        "existing_session_id": existing_session['id'],
                        "answered_questions": answered,
                        "total_questions": total_questions,
                        "trial_id": existing_session['trial_id']
                    }
                }
            
        except Exception as e:
            logger.error(f"Error handling existing prescreening session: {str(e)}")
            # Fall back to starting new session
            return None

    def _get_condition_based_trial_reference(self, trial_data: Dict[str, Any], default_condition: str = None) -> str:
        """Generate a user-friendly condition-based reference for a trial instead of using trial name"""
        condition = trial_data.get('conditions') or default_condition
        if condition:
            # Clean up condition name and make it user-friendly
            condition_clean = condition.lower().strip()
            if 'gout' in condition_clean:
                return "the gout trial"
            elif 'migraine' in condition_clean:
                return "the migraine trial"
            elif 'diabetes' in condition_clean or 'diabetic' in condition_clean:
                if 'gastroparesis' in condition_clean:
                    return "the diabetic gastroparesis trial"
                else:
                    return "the diabetes trial"
            elif 'cancer' in condition_clean:
                return "the cancer trial"
            elif 'heart' in condition_clean or 'cardiac' in condition_clean:
                return "the heart disease trial"
            else:
                return f"the {condition_clean} trial"
        return "this clinical trial"
    
    def _define_conversation_functions(self) -> List[Dict[str, Any]]:
        """Define OpenAI functions for conversation management"""
        return [
            {
                "name": "manage_conversation",
                "description": "Manage the conversation flow and determine next actions for clinical trials assistance",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action_type": {
                            "type": "string",
                            "enum": [
                                "search_trials",
                                "ask_for_location",
                                "ask_for_condition",
                                "start_prescreening",
                                "continue_prescreening",
                                "provide_trial_info",
                                "handle_eligibility_question",
                                "general_response",
                                "clarify_request"
                            ],
                            "description": "The type of action to take"
                        },
                        "parameters": {
                            "type": "object",
                            "description": "Parameters for the action",
                            "properties": {
                                "condition": {"type": "string"},
                                "location": {"type": "string"},
                                "trial_id": {"type": "string"},
                                "question_type": {"type": "string"},
                                "search_filters": {"type": "object"}
                            }
                        },
                        "response_text": {
                            "type": "string",
                            "description": "The response text to send to the user"
                        },
                        "new_state": {
                            "type": "string",
                            "enum": [
                                "initial",
                                "awaiting_location",
                                "awaiting_condition",
                                "trials_shown",
                                "prescreening_active",
                                "prescreening_complete",
                                "awaiting_clarification"
                            ],
                            "description": "The new conversation state"
                        },
                        "collect_entities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Entities to collect from the user's next response"
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Explanation of why this action was chosen"
                        }
                    },
                    "required": ["action_type", "parameters", "response_text", "reasoning"]
                }
            }
        ]

    def _is_lead_campaign_session(self, context: ConversationContext) -> bool:
        """Check if this is a lead campaign session"""
        return context.metadata.get('lead_campaign', False)

    async def _handle_lead_campaign_initial_response(
        self,
        message: str,
        detected_intent,
        context: ConversationContext
    ) -> Dict[str, Any]:
        """
        Handle initial response in lead campaign flow
        Routes to prescreening on positive response, ends gracefully on negative
        """

        message_lower = message.lower().strip()

        # Detect positive responses
        positive_keywords = ['yes', 'sure', 'interested', 'tell me more', 'ok', 'yeah', 'please']
        is_positive = any(keyword in message_lower for keyword in positive_keywords)

        # Detect negative responses
        negative_keywords = ['no', 'not interested', 'no thanks', 'stop', 'unsubscribe']
        is_negative = any(keyword in message_lower for keyword in negative_keywords)

        if is_positive:
            logger.info(f"[LEAD-CAMPAIGN] Positive response, starting prescreening")

            # Clear the awaiting flag
            context.metadata['awaiting_prescreening_interest'] = False

            # Start prescreening using trial_id from context
            trial_id = context.metadata.get('trial_id')

            if not trial_id:
                logger.error(f"[LEAD-CAMPAIGN] Missing trial_id in context for session {context.session_id}")
                return {
                    "response": "I apologize, but I'm having trouble loading the trial information. Our team will reach out to you directly.",
                    "new_state": "error",
                    "metadata": {"error": "missing_trial_id"}
                }

            return await self._start_prescreening_for_trial(trial_id, context)

        elif is_negative:
            logger.info(f"[LEAD-CAMPAIGN] Negative response, ending conversation")

            return {
                "response": "I understand. Thank you for your time! If you change your mind in the future, please feel free to reach out to us at (404) 355-8779.",
                "new_state": "conversation_ended",
                "metadata": {"lead_declined": True}
            }

        else:
            # Unclear response - ask for clarification
            trial_name = context.metadata.get('trial_name', 'this clinical trial')

            return {
                "response": f"Would you be interested in answering a few quick questions about {trial_name}?\n\nPlease reply YES if interested, or NO if not interested.",
                "new_state": context.conversation_state,
                "metadata": {"clarification_requested": True}
            }

    async def _start_prescreening_for_trial(
        self,
        trial_id: int,
        context: ConversationContext
    ) -> Dict[str, Any]:
        """
        Start prescreening for specific trial (used by lead campaigns)
        Bypasses trial search since trial_id is pre-populated in context
        """

        logger.info(f"[LEAD-CAMPAIGN] Starting prescreening for trial_id={trial_id}")

        try:
            # Start prescreening session
            questions, trial_name = self.prescreening_manager.start_prescreening(
                trial_id=trial_id,
                session_id=context.session_id,
                user_id=context.user_id,
                condition=context.focus_condition,
                location=context.focus_location
            )

            if not questions:
                logger.error(f"[LEAD-CAMPAIGN] No questions returned for trial {trial_id}")
                return {
                    "response": "I'm having trouble loading the prescreening questions. Our team will contact you directly to discuss this trial.",
                    "new_state": "error",
                    "metadata": {"error": "no_questions"}
                }

            # Store in context
            context.prescreening_data = {
                'trial_id': trial_id,
                'trial_name': trial_name,
                'questions': [self._serialize_question(q) for q in questions],
                'current_question_index': 0,
                'total_questions': len(questions),
                'answers': []
            }

            # Get first question
            first_question = questions[0]

            response = f"Great! Let's get started.\n\n{first_question.question_text}"

            if len(questions) > 1:
                response += f"\n\n(Question 1 of {len(questions)})"

            logger.info(f"[LEAD-CAMPAIGN] ✅ Prescreening started with {len(questions)} questions")

            return {
                "response": response,
                "new_state": "prescreening_active",
                "metadata": {
                    "prescreening_started": True,
                    "trial_id": trial_id,
                    "total_questions": len(questions)
                }
            }

        except Exception as e:
            logger.error(f"[LEAD-CAMPAIGN] ❌ Error starting prescreening: {e}", exc_info=True)
            return {
                "response": "I apologize for the technical issue. Our team will reach out to you directly about this opportunity.",
                "new_state": "error",
                "metadata": {"error": str(e)}
            }

    async def process_message(self, message: str, context: ConversationContext) -> Dict[str, Any]:
        """
        Process a user message and return the appropriate response and actions.
        
        Args:
            message: User's input message
            context: Current conversation context
            
        Returns:
            Dictionary with response, new state, and metadata
        """
        try:
            # Step 1: Detect intent and extract entities
            detected_intent = await self.intent_detector.detect_intent(message, context)

            # LEAD CAMPAIGN: Check if this is initial response to lead campaign
            if self._is_lead_campaign_session(context):
                awaiting_interest = context.metadata.get('awaiting_prescreening_interest', False)
                if awaiting_interest:
                    logger.info(f"[LEAD-CAMPAIGN] Handling initial response for lead campaign")
                    return await self._handle_lead_campaign_initial_response(message, detected_intent, context)

            # Log entity extraction details
            logger.info(f"ENTITY_PARSING: Intent: {detected_intent.intent_type.value} (confidence: {detected_intent.confidence:.2f})")
            
            if detected_intent.entities:
                logger.info(f"ENTITY_PARSING: Extracted entities - Condition: {detected_intent.entities.condition}, Location: {detected_intent.entities.location}, Age: {detected_intent.entities.age}")
                
                # Validate entity formats for database compatibility
                if detected_intent.entities.condition and len(detected_intent.entities.condition) > 100:
                    logger.warning(f"ENTITY_PARSING: Condition too long for DB ({len(detected_intent.entities.condition)} chars): {detected_intent.entities.condition[:50]}...")
                
                if detected_intent.entities.location and len(detected_intent.entities.location) > 100:
                    logger.warning(f"ENTITY_PARSING: Location too long for DB ({len(detected_intent.entities.location)} chars): {detected_intent.entities.location[:50]}...")
                    
                # Ensure age is int before comparison (may come as string from entity extractor)
                age_value = detected_intent.entities.age
                if age_value:
                    try:
                        age_int = int(age_value) if isinstance(age_value, str) else age_value
                        if age_int < 0 or age_int > 120:
                            logger.warning(f"ENTITY_PARSING: Unusual age value: {age_value}")
                    except (ValueError, TypeError):
                        logger.warning(f"ENTITY_PARSING: Could not parse age value: {age_value}")
            else:
                logger.debug(f"ENTITY_PARSING: No entities extracted from message: '{message[:100]}...'")
            
            # Step 2: Analyze user sentiment and communication style
            # user_analysis = await self._analyze_user_style(message, context)
            user_analysis = {"sentiment": "neutral", "communication_style": "standard", "complexity_preference": "moderate"}
            
            # Step 3: Determine conversation action using OpenAI
            conversation_action = await self._determine_conversation_action(
                message, detected_intent, context, user_analysis
            )
            
            # Step 4: Execute the action with personalization
            result = await self._execute_conversation_action(
                conversation_action, detected_intent, context, user_analysis, message
            )
            
            return {
                "response": result.get("response", "I'm here to help with clinical trials. How can I assist you?"),
                "new_state": result.get("new_state", context.conversation_state),
                "quick_replies": result.get("quick_replies"),  # 🎯 FIX: Pass through quick_replies from handlers
                "intent": {
                    "type": detected_intent.intent_type.value,
                    "confidence": detected_intent.confidence,
                    "entities": asdict(detected_intent.entities),
                    "next_action": detected_intent.next_action,
                    "reasoning": detected_intent.reasoning
                },
                "metadata": {
                    "processing_method": "gemini_conversation_manager",
                    "action_type": conversation_action.action_type,
                    "entities_collected": asdict(detected_intent.entities),
                    "conversation_action": asdict(conversation_action),
                    "user_analysis": user_analysis,
                    **result.get("metadata", {})
                }
            }
            
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            return self._fallback_response(message, context)
    
    async def _determine_conversation_action(
        self, 
        message: str, 
        detected_intent: GeminiDetectedIntent, 
        context: ConversationContext,
        user_analysis: Dict[str, Any] = None
    ) -> ConversationAction:
        """Use OpenAI to determine the best conversation action"""
        try:
            # Handle special states that don't need OpenAI routing
            if context.conversation_state == "prescreening_review":
                return ConversationAction(
                    action_type="handle_prescreening_review",
                    parameters={},
                    response_text="",
                    new_state=None,
                    collect_entities=False,
                    metadata={"reasoning": "User in prescreening review state"}
                )

            if context.conversation_state == "awaiting_booking_confirmation":
                return ConversationAction(
                    action_type="handle_booking_confirmation",
                    parameters={},
                    response_text="",
                    new_state=None,
                    collect_entities=False,
                    metadata={"reasoning": "User confirming booking"}
                )

            # Handle alternative condition selection
            # Handle nearest location confirmation (new state)
            if context.conversation_state == "awaiting_location_confirmation":
                message_lower = message.lower()

                if any(word in message_lower for word in ['yes', 'yeah', 'sure', 'ok', 'please', 'show']):
                    # User accepts suggested nearby location
                    suggested_location = context.metadata.get('suggested_location')
                    condition = context.metadata.get('condition')

                    if suggested_location and condition:
                        trials = await self._find_trials_in_database(condition, suggested_location)

                        if trials:
                            response = self._format_trials_response(trials, condition, suggested_location)
                            context.last_shown_trials = trials[:3]

                            return {
                                "response": response,
                                "new_state": "trials_shown",
                                "metadata": {
                                    "trials_found": len(trials),
                                    "accepted_suggested_location": True
                                }
                            }

                elif any(word in message_lower for word in ['no', 'nope', 'too far', 'different']):
                    return {
                        "response": "I understand. Would you like to try a different location, or search for a different condition?",
                        "new_state": "idle",
                        "metadata": {"declined_suggested_location": True}
                    }

            if context.conversation_state == "awaiting_alternative_selection":
                return ConversationAction(
                    action_type="handle_alternative_selection",
                    parameters={},
                    response_text="",
                    new_state=None,
                    collect_entities=False,
                    metadata={"reasoning": "User selecting from alternative trial conditions"}
                )

            # Handle all booking detail collection states
            booking_states = ["collecting_booking_name", "collecting_booking_phone",
                              "collecting_booking_email", "collecting_booking_dob"]
            if context.conversation_state in booking_states:
                return ConversationAction(
                    action_type="handle_booking_details",
                    parameters={},
                    response_text="",
                    new_state=None,
                    collect_entities=False,
                    metadata={"reasoning": f"Collecting booking details: {context.conversation_state}"}
                )

            if context.conversation_state == "requesting_preferred_times":
                return ConversationAction(
                    action_type="handle_preferred_times",
                    parameters={},
                    response_text="",
                    new_state=None,
                    collect_entities=False,
                    metadata={"reasoning": "Collecting preferred times with Gemini"}
                )

            if context.conversation_state == "prescreening_complete":
                # Check if user is asking a general question (not related to contact collection)
                # These should be answered instead of continuing contact flow
                message_lower = message.lower()
                general_question_keywords = [
                    "travel", "reimburse", "pay", "compensat", "stipend", "money",
                    "how far", "how long", "how often", "what if", "can i",
                    "what are the", "tell me about", "explain", "what does",
                    "side effect", "risk", "safe", "duration", "requirement"
                ]
                is_general_question = any(kw in message_lower for kw in general_question_keywords)

                # Also check if it looks like a new search request
                search_keywords = ["find", "search", "look for", "trials for", "studies for", "different"]
                is_new_search = any(kw in message_lower for kw in search_keywords)

                if is_general_question or is_new_search:
                    logger.info(f"User asking general question during prescreening_complete state - routing to normal flow")
                    # Don't return early - let it fall through to normal Gemini routing
                    pass
                else:
                    return ConversationAction(
                        action_type="handle_post_prescreening",
                        parameters={},
                        response_text="",
                        new_state=None,
                        collect_entities=False,
                        metadata={"reasoning": "User in prescreening complete state"}
                    )
            
            # Build context for conversation management
            system_prompt = self._build_conversation_system_prompt(context)
            
            # Build the conversation management prompt with user analysis
            user_prompt = self._build_conversation_user_prompt(message, detected_intent, context, user_analysis)
            
            # Call Gemini to determine the best action
            full_prompt = f"""{system_prompt}

{user_prompt}

Based on the user's message and context, determine the best conversation action. Return a JSON object with:
- action_type: one of {[f["parameters"]["properties"]["action_type"]["enum"] for f in self.conversation_functions if f["name"] == "manage_conversation"][0]}
- parameters: object with relevant parameters
- response_text: the response to send to the user
- new_state: optional new conversation state
- collect_entities: optional array of entities to collect
- reasoning: explanation of why this action was chosen
"""
            
            result = await self.gemini.extract_json(full_prompt, "")
            
            if result and "action_type" in result:
                logger.info(f"Gemini conversation action: {result.get('action_type', 'unknown')}")
                return ConversationAction(
                    action_type=result.get("action_type", "general_response"),
                    parameters=result.get("parameters", {}),
                    response_text=result.get("response_text", "How can I help you with clinical trials?"),
                    new_state=result.get("new_state"),
                    collect_entities=result.get("collect_entities"),
                    metadata={"reasoning": result.get("reasoning", "Gemini conversation management")}
                )
            
            # Fallback if function calling fails
            return self._fallback_conversation_action(detected_intent, context)
            
        except Exception as e:
            logger.error(f"Error determining conversation action: {str(e)}")
            return self._fallback_conversation_action(detected_intent, context)
    
    def _build_conversation_system_prompt(self, context: ConversationContext) -> str:
        """Build system prompt for conversation management"""
        return f"""You are an expert clinical trials conversation manager. Your role is to determine the best action to take in a conversation about clinical trials.

Current conversation context:
- State: {context.conversation_state or 'initial'}
- User's condition: {context.focus_condition or 'unknown'}
- User's location: {context.focus_location or 'unknown'}
- Conversation history: {len(context.conversation_history) if context.conversation_history else 0} turns

Your responsibilities:
1. Determine the most appropriate action based on the user's intent and current context
2. Generate a helpful, empathetic response
3. Manage conversation state transitions
4. Collect necessary information (condition, location) before searching trials
5. Guide users through eligibility prescreening when appropriate

Key principles:
- Always be helpful and empathetic
- Never provide medical advice
- Collect condition and location before searching trials
- Offer eligibility checks when showing trials
- Keep responses conversational and supportive
- Handle context switches gracefully

Action guidelines:
- search_trials: Only when you have both condition and location
- ask_for_location: When you have condition but need location
- ask_for_condition: When you have location but need condition
- start_prescreening: When user wants eligibility check
- handle_eligibility_question: When user asks about eligibility
- general_response: For general questions or clarifications
"""
    
    def _build_conversation_user_prompt(
        self, 
        message: str, 
        detected_intent: GeminiDetectedIntent, 
        context: ConversationContext,
        user_analysis: Dict[str, Any] = None
    ) -> str:
        """Build user prompt for conversation management"""
        user_analysis_text = ""
        if user_analysis:
            user_analysis_text = f"""
User Analysis:
- Sentiment: {user_analysis.get('sentiment', 'neutral')} (confidence: {user_analysis.get('sentiment_confidence', 0.0):.2f})
- Communication style: {user_analysis.get('communication_style', 'standard')}
- Complexity preference: {user_analysis.get('complexity_preference', 'moderate')}
- Emotional state: {user_analysis.get('emotional_indicators', [])}
"""

        return f"""User message: "{message}"

Detected intent: {detected_intent.intent_type.value}
Confidence: {detected_intent.confidence}
Extracted entities: {asdict(detected_intent.entities)}
OpenAI reasoning: {detected_intent.reasoning}
{user_analysis_text}
Based on the user's message, detected intent, user analysis, and current conversation context, determine the best action to take and generate an appropriate response.

Consider:
1. What information do we still need?
2. What is the user's primary goal?
3. How can we best help them progress toward their goal?
4. What would be the most natural next step?
5. How should we adapt our tone/complexity based on their communication style and emotional state?

Provide a specific action, parameters, and a warm, helpful response that moves the conversation forward constructively while matching their communication preferences."""
    
    async def _execute_conversation_action(
        self, 
        action: ConversationAction, 
        detected_intent: GeminiDetectedIntent, 
        context: ConversationContext,
        user_analysis: Dict[str, Any] = None,
        message: str = None
    ) -> Dict[str, Any]:
        """Execute the determined conversation action"""
        try:
            if action.action_type == "search_trials":
                return await self._search_trials_semantic(action, detected_intent, context, user_analysis)
            elif action.action_type == "ask_for_location":
                return await self._ask_for_location(action, detected_intent, context)
            elif action.action_type == "ask_for_condition":
                return await self._ask_for_condition(action, detected_intent, context)
            elif action.action_type == "start_prescreening":
                return await self._start_prescreening_with_explanations(action, detected_intent, context, user_analysis)
            elif action.action_type == "handle_eligibility_question":
                return await self._handle_eligibility_question(action, detected_intent, context)
            elif action.action_type == "provide_trial_info":
                return await self._provide_trial_info(action, detected_intent, context)
            elif action.action_type == "continue_prescreening":
                return await self._process_prescreening_answer(action, detected_intent, context, user_analysis, message)
            elif action.action_type == "handle_prescreening_review":
                return await self._handle_prescreening_review(action, detected_intent, context, message)
            elif action.action_type == "handle_post_prescreening":
                return await self._handle_post_prescreening(action, detected_intent, context, message)
            elif action.action_type == "handle_booking_confirmation":
                return await self._handle_booking_confirmation(context, message)
            elif action.action_type == "handle_booking_details":
                return await self._handle_booking_details(context, message)
            elif action.action_type == "handle_preferred_times":
                return await self._handle_preferred_times(context, message)
            elif action.action_type == "handle_alternative_selection":
                return await self._handle_alternative_selection(context, message)
            else:
                return await self._general_response(action, detected_intent, context)
                
        except Exception as e:
            logger.error(f"Error executing conversation action {action.action_type}: {str(e)}")
            return {
                "response": "I apologize, but I encountered an error. Let me help you find clinical trials. What condition are you interested in?",
                "new_state": "initial",
                "metadata": {"error": str(e)}
            }
    
    async def _search_trials(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext) -> Dict[str, Any]:
        """Search for clinical trials"""
        logger.info(f"{'='*80}")
        logger.info(f"🔍 SEARCH TRIALS - Session: {context.session_id}")
        logger.info(f"   Intent condition: {detected_intent.entities.condition}")
        logger.info(f"   Intent location: {detected_intent.entities.location}")
        logger.info(f"   Action condition: {action.parameters.get('condition')}")
        logger.info(f"   Action location: {action.parameters.get('location')}")
        logger.info(f"   Context condition (fallback): {context.focus_condition}")
        logger.info(f"   Context location (fallback): {context.focus_location}")
        logger.info(f"   Conversation state: {context.conversation_state}")
        logger.info(f"   Last shown trials: {len(context.last_shown_trials) if context.last_shown_trials else 0}")
        logger.info(f"{'='*80}")

        condition = action.parameters.get("condition") or detected_intent.entities.condition or context.focus_condition
        location = action.parameters.get("location") or detected_intent.entities.location or context.focus_location

        logger.info(f"📊 RESOLVED VALUES:")
        logger.info(f"   Final condition: {condition}")
        logger.info(f"   Condition type: {type(condition)}")
        logger.info(f"   Final location: {location}")

        if not condition or not location:
            logger.warning(f"⚠️  MISSING INFO: condition={condition}, location={location}")
            return {
                "response": "I need both your condition and location to search for trials. " +
                          ("What condition are you interested in?" if not condition else "What's your location?"),
                "new_state": "awaiting_condition" if not condition else "awaiting_location",
                "metadata": {"missing_info": "condition" if not condition else "location"}
            }

        # Handle multiple conditions
        if isinstance(condition, list) and len(condition) > 1:
            logger.info(f"🔬 MULTI-CONDITION DETECTED: {condition}")
            return await self._handle_multiple_conditions(condition, location, context)

        # Convert list with single item to string
        if isinstance(condition, list) and len(condition) == 1:
            condition = condition[0]
            logger.info(f"   → Single condition from list: {condition}")

        # Search trials in database using hybrid semantic+keyword search
        trials = await self._find_trials_in_database(condition, location)
        logger.info(f"✅ Search results: found {len(trials)} trials for condition='{condition}', location='{location}'")
        
        if trials:
            response = self._format_trials_response(trials, condition, location)

            # CRITICAL FIX: Save trials to context so prescreening can access them
            context.last_shown_trials = trials[:3]  # Save top 3 trials
            logger.info(f"✅ Saved {len(trials[:3])} trials to context.last_shown_trials")

            return {
                "response": response,
                "new_state": "trials_shown",
                "metadata": {
                    "trials_found": len(trials),
                    "condition": condition,
                    "location": location,
                    "trials_data": trials[:3]
                }
            }
        else:
            # No trials found - suggest nearest location with trials
            from core.services.trial_search import trial_search

            # First, try to find nearest location with trials for this condition
            nearest = await trial_search.find_nearest_location_with_trials(
                condition=condition,
                requested_location=location
            )

            if nearest:
                # Found trials in nearby location
                response_text = f"I couldn't find any trials for {condition} in {location}.\n\n"

                if nearest['distance_miles']:
                    response_text += f"However, the nearest location with {condition} trials is **{nearest['nearest_location']}**, about {nearest['distance_miles']} miles away. "
                else:
                    response_text += f"However, we do have {condition} trials available in **{nearest['nearest_location']}**. "

                response_text += f"We have {nearest['trial_count']} trial{'s' if nearest['trial_count'] != 1 else ''} there.\n\n"
                response_text += f"Is that too far, or would you like to see the trials in {nearest['nearest_location']}?"

                # Store in both metadata and state_data for access
                return {
                    "response": response_text,
                    "new_state": "awaiting_location_confirmation",
                    "metadata": {
                        "no_trials_in_requested_location": True,
                        "requested_location": location,
                        "suggested_location": nearest['nearest_location'],
                        "distance_miles": nearest['distance_miles'],
                        "condition": condition
                    },
                    "state_data_updates": {
                        "suggested_location": nearest['nearest_location'],
                        "requested_location": location,
                        "condition": condition,
                        "distance_miles": nearest['distance_miles']
                    }
                }

            # No nearby trials found - show other conditions in same location
            from core.services.trial_search import TrialSearchService
            search_service = TrialSearchService()

            other_trials = search_service.search_trials(
                condition=None,  # All conditions
                location=location
            )

            response_text = f"I couldn't find any trials for {condition} in {location} at the moment.\n\n"

            # If other trials exist in this location, show them grouped by investigator
            if other_trials and len(other_trials) > 0:

                # Group by investigator and collect unique conditions
                by_investigator = {}
                all_conditions = set()
                for trial in other_trials:
                    inv_name = trial.get("investigator_name", "Unknown")
                    if inv_name not in by_investigator:
                        by_investigator[inv_name] = {}

                    # Group by condition under each investigator
                    conditions = trial.get("conditions", "").split(',')
                    for cond in conditions:
                        cond = cond.strip()
                        if cond:
                            all_conditions.add(cond)
                            if cond not in by_investigator[inv_name]:
                                by_investigator[inv_name][cond] = 0
                            by_investigator[inv_name][cond] += 1

                response_text += f"However, we do have other trials available in {location}:\n\n"
                for investigator, conditions in by_investigator.items():
                    response_text += f"**{investigator}**\n"
                    for condition_name, count in sorted(conditions.items()):
                        response_text += f"  • {condition_name} ({count} trial{'s' if count > 1 else ''})\n"
                    response_text += "\n"

                # List conditions explicitly for selection
                sorted_conditions = sorted(list(all_conditions))
                if len(sorted_conditions) == 1:
                    response_text += f"Would you like to check your eligibility for **{sorted_conditions[0]}**?"
                elif len(sorted_conditions) == 2:
                    response_text += f"Which condition would you like to check: **{sorted_conditions[0]}** or **{sorted_conditions[1]}**?"
                else:
                    # More than 2 conditions - list with commas
                    conditions_str = ", ".join([f"**{c}**" for c in sorted_conditions[:-1]]) + f", or **{sorted_conditions[-1]}**"
                    response_text += f"Which condition would you like to check: {conditions_str}?"

                # Store alternative trials and conditions in metadata for next turn
                return {
                    "response": response_text,
                    "new_state": "awaiting_alternative_selection",
                    "metadata": {
                        "no_trials_found": True,
                        "original_condition": condition,
                        "location": location,
                        "alternative_conditions": sorted_conditions,
                        "alternative_trials": other_trials
                    }
                }
            else:
                response_text += "Would you like to try a different location?"

                return {
                    "response": response_text,
                    "new_state": "initial",
                    "metadata": {"no_trials_found": True, "condition": condition, "location": location, "suggested_alternatives": False}
                }
    
    async def _ask_for_location(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext) -> Dict[str, Any]:
        """Ask user for their location"""
        return {
            "response": action.response_text,
            "new_state": "awaiting_location",
            "metadata": {"asking_for": "location"}
        }
    
    async def _ask_for_condition(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext) -> Dict[str, Any]:
        """Ask user for their condition"""
        return {
            "response": action.response_text,
            "new_state": "awaiting_condition",
            "metadata": {"asking_for": "condition"}
        }
    
    async def _start_prescreening(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext) -> Dict[str, Any]:
        """Start eligibility prescreening"""
        # Get the first prescreening question
        first_question = "What is your age?"  # Simplified for now
        
        return {
            "response": f"Great! I'll help you check your eligibility. {first_question}",
            "new_state": "prescreening_active",
            "metadata": {"prescreening_started": True, "current_question": "age"}
        }
    
    async def _handle_eligibility_question(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext) -> Dict[str, Any]:
        """Handle eligibility-related questions"""
        if context.conversation_state == "trials_shown":
            return await self._start_prescreening(action, detected_intent, context)
        else:
            return {
                "response": action.response_text,
                "new_state": "awaiting_condition",
                "metadata": {"eligibility_question": True}
            }
    
    async def _provide_trial_info(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext) -> Dict[str, Any]:
        """Provide information about trials"""
        return {
            "response": action.response_text,
            "new_state": context.conversation_state,  # Keep current state
            "metadata": {"provided_info": True}
        }
    
    async def _general_response(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext) -> Dict[str, Any]:
        """Handle general responses"""
        return {
            "response": action.response_text,
            "new_state": context.conversation_state or "initial",
            "metadata": {"general_response": True}
        }
    
    async def _find_trials_in_database(self, condition: str, location: str) -> List[Dict[str, Any]]:
        """Find trials in the database using the TrialSearchService with semantic search and metro area expansion"""
        try:
            # Import the trial search service here to avoid circular imports
            from core.services.trial_search import trial_search

            # First try direct search
            results = await trial_search.search_trials_hybrid(condition, location)

            if results:
                logger.info(f"Found {len(results)} trials for {condition} in {location}")
                return results

            # If no results, try metro area expansion
            # This allows "St. Louis" to find trials in Wildwood, Town and Country, etc.
            logger.info(f"No direct results for {condition} in {location}, trying metro area expansion...")
            results = await trial_search.search_trials_hybrid(condition, location)

            if results:
                logger.info(f"Metro expansion found {len(results)} trials for {condition} near {location}")
                return results

            return []

        except Exception as e:
            logger.error(f"Database error: {str(e)}")
            # Return empty list instead of mock data
            return []
    
    async def _handle_multiple_conditions(self, conditions: List[str], location: str, context: ConversationContext) -> Dict[str, Any]:
        """
        Handle when user mentions multiple conditions.

        Instead of searching for array as string, search each condition separately
        and show what's available.

        Args:
            conditions: List of condition names
            location: Location to search
            context: Conversation context

        Returns:
            Response dict with available trials per condition
        """
        logger.info(f"🔬 HANDLING MULTIPLE CONDITIONS:")
        logger.info(f"   Conditions: {conditions}")
        logger.info(f"   Location: {location}")

        available_conditions = []

        for condition in conditions:
            trials = await self._find_trials_in_database(condition, location)
            if trials:
                available_conditions.append({
                    'condition': condition,
                    'count': len(trials),
                    'trials': trials[:3]  # Top 3
                })
                logger.info(f"   ✅ {condition}: {len(trials)} trials found")
            else:
                logger.info(f"   ❌ {condition}: 0 trials found")

        if available_conditions:
            # Found trials for at least one condition
            response = f"I found trials for multiple conditions in {location}:\n\n"
            for item in available_conditions:
                response += f"**{item['condition'].title()}:** {item['count']} trial(s)\n"
            response += "\nWhich condition would you like to explore first?"

            # Save all available trials to context
            all_trials = []
            for item in available_conditions:
                all_trials.extend(item['trials'])
            context.last_shown_trials = all_trials

            return {
                "response": response,
                "new_state": "awaiting_condition_selection",
                "metadata": {
                    "multi_condition_search": True,
                    "available_conditions": [item['condition'] for item in available_conditions],
                    "trials_found": len(all_trials)
                }
            }
        else:
            # No trials found for any condition
            logger.warning(f"❌ NO TRIALS for any condition: {conditions}")
            response = f"I couldn't find trials for any of those conditions ({', '.join(conditions)}) in {location} at the moment.\n\n"
            response += "Would you like to try:\n"
            response += "1. A different location?\n"
            response += "2. A different condition?"

            return {
                "response": response,
                "new_state": "awaiting_search_choice",
                "metadata": {
                    "multi_condition_search": True,
                    "no_results": True,
                    "searched_conditions": conditions
                }
            }

    def _format_trials_response(self, trials: List[Dict[str, Any]], condition: str, location: str) -> str:
        """Format trials response grouped by physician and condition"""
        if not trials:
            return f"I couldn't find any trials for {condition} in {location} at the moment. Would you like to try a different condition or location?"
        
        # Group trials by investigator
        by_investigator = {}
        for trial in trials:
            investigator = trial.get('investigator_name', 'Unknown Investigator')
            if investigator not in by_investigator:
                by_investigator[investigator] = []
            by_investigator[investigator].append(trial)
        
        # Start response
        response = f"Great! I found {len(trials)} clinical trial(s) available in {location}:\n\n"
        
        # Format by investigator with cleaner presentation
        for investigator, investigator_trials in by_investigator.items():
            # Check if investigator name already has title prefix
            if investigator.lower().startswith(('dr.', 'dr ', 'doctor')):
                response += f"**{investigator}**\n"
            else:
                response += f"**Dr. {investigator}**\n"
            
            # Group this investigator's trials by condition
            by_condition = {}
            for trial in investigator_trials:
                condition_name = trial.get('conditions', 'Unknown Condition').strip()
                if condition_name not in by_condition:
                    by_condition[condition_name] = []
                by_condition[condition_name].append(trial)
            
            # Format conditions for this investigator
            for cond_name, cond_trials in by_condition.items():
                trial_count = len(cond_trials)
                trial_word = "trial" if trial_count == 1 else "trials"
                response += f"  • {cond_name} ({trial_count} {trial_word})\n"
            
            response += "\n"
        
        # Add call to action
        response += "Would you like to:\n"
        response += "1. Check your eligibility for any of these trials?\n"
        response += "2. Learn more about a specific condition?\n"
        response += "3. See trials in a different location?"
        
        return response
    
    def _fallback_conversation_action(self, detected_intent: GeminiDetectedIntent, context: ConversationContext) -> ConversationAction:
        """Fallback conversation action when OpenAI fails"""
        return ConversationAction(
            action_type="general_response",
            parameters={},
            response_text="I'm here to help you find clinical trials. What condition are you interested in?",
            new_state="initial",
            metadata={"fallback": True}
        )
    
    def _fallback_response(self, message: str, context: ConversationContext) -> Dict[str, Any]:
        """Fallback response when everything fails"""
        return {
            "response": "I apologize, but I'm having trouble processing your request. Could you please tell me what medical condition you're interested in finding trials for?",
            "new_state": "initial",
            "intent": {"type": "general_query", "confidence": 0.5},
            "metadata": {"fallback": True, "error": True}
        }
    
    async def _analyze_user_style(self, message: str, context: ConversationContext) -> Dict[str, Any]:
        """Analyze user's communication style and sentiment using OpenAI"""
        try:
            # Build conversation history for analysis
            history_text = ""
            if context.conversation_history:
                recent_history = context.conversation_history[-3:]  # Last 3 turns
                for turn in recent_history:
                    history_text += f"User: {turn.get('user_message', '')}\nBot: {turn.get('bot_response', '')}\n"
            
            analysis_prompt = f"""Analyze the user's communication style and emotional state from their messages.

Current message: "{message}"

Recent conversation history:
{history_text}

Analyze and provide:
1. Sentiment (positive/negative/neutral/anxious/frustrated/hopeful)
2. Communication style (formal/casual/direct/detailed/brief)
3. Complexity preference (simple/moderate/technical)
4. Emotional indicators (worried/excited/confused/confident/urgent)
5. Overall confidence level in communicating about medical topics

Respond with a JSON object containing these assessments."""

            analysis_text = await self.gemini.generate_text(f"""You are an expert in communication analysis. Analyze user messages to understand their communication style, emotional state, and preferences to help personalize responses.

{analysis_prompt}""", max_tokens=500)
            
            # Try to parse as JSON, fallback to text analysis
            try:
                analysis = json.loads(analysis_text)
            except:
                # Fallback parsing
                analysis = self._parse_analysis_fallback(analysis_text)
            
            # Add confidence scores
            analysis["sentiment_confidence"] = 0.8  # Default confidence
            analysis["analysis_timestamp"] = context.last_updated.isoformat() if context.last_updated else datetime.now().isoformat()
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing user style: {str(e)}")
            return {
                "sentiment": "neutral",
                "communication_style": "standard", 
                "complexity_preference": "moderate",
                "emotional_indicators": [],
                "sentiment_confidence": 0.5
            }
    
    def _parse_analysis_fallback(self, analysis_text: str) -> Dict[str, Any]:
        """Fallback parsing for user analysis"""
        analysis = {
            "sentiment": "neutral",
            "communication_style": "standard",
            "complexity_preference": "moderate", 
            "emotional_indicators": []
        }
        
        text_lower = analysis_text.lower()
        
        # Sentiment detection
        if any(word in text_lower for word in ["positive", "happy", "excited", "hopeful"]):
            analysis["sentiment"] = "positive"
        elif any(word in text_lower for word in ["negative", "frustrated", "upset", "angry"]):
            analysis["sentiment"] = "negative"
        elif any(word in text_lower for word in ["anxious", "worried", "concerned"]):
            analysis["sentiment"] = "anxious"
            
        # Communication style
        if any(word in text_lower for word in ["formal", "professional"]):
            analysis["communication_style"] = "formal"
        elif any(word in text_lower for word in ["casual", "informal"]):
            analysis["communication_style"] = "casual"
        elif any(word in text_lower for word in ["direct", "brief"]):
            analysis["communication_style"] = "direct"
            
        return analysis
    
    async def _search_trials_semantic(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext, user_analysis: Dict[str, Any] = None) -> Dict[str, Any]:
        """Search for clinical trials using semantic matching with embeddings"""
        condition = action.parameters.get("condition") or detected_intent.entities.condition or context.focus_condition
        location = action.parameters.get("location") or detected_intent.entities.location or context.focus_location
        
        if not condition or not location:
            return {
                "response": "I need both your condition and location to search for trials. " + 
                          ("What condition are you interested in?" if not condition else "What's your location?"),
                "new_state": "awaiting_condition" if not condition else "awaiting_location",
                "metadata": {"missing_info": "condition" if not condition else "location"}
            }
        
        try:
            # Get semantic embeddings for the condition
            condition_embedding = await self._get_condition_embedding(condition)
            
            # Search trials using semantic similarity
            trials = await self._find_trials_semantic(condition, location, condition_embedding)
            
            logger.info(f"Semantic search found {len(trials)} trials for {condition} in {location}")
            
            if trials:
                # Use the same formatting as regular search
                response = self._format_trials_response(trials, condition, location)

                # CRITICAL FIX: Save trials to context so prescreening can access them
                context.last_shown_trials = trials[:3]  # Save top 3 trials
                logger.info(f"✅ Saved {len(trials[:3])} trials to context.last_shown_trials")

                return {
                    "response": response,
                    "new_state": "trials_shown",
                    "metadata": {
                        "trials_found": len(trials),
                        "condition": condition,
                        "location": location,
                        "trials_data": trials[:3],
                        "semantic_search": True
                    }
                }
            else:
                logger.info(f"No semantic trials found, falling back to regular search")
                # Fallback to regular search if semantic search finds nothing
                return await self._search_trials(action, detected_intent, context)
                
        except Exception as e:
            logger.error(f"Error in semantic trial search: {str(e)}")
            # Fallback to original search
            return await self._search_trials(action, detected_intent, context)
    
    async def _get_condition_embedding(self, condition: str) -> List[float]:
        """Get Gemini embedding for medical condition"""
        try:
            embeddings = await self.gemini.generate_embeddings([f"Medical condition: {condition}. Clinical trial research."])
            return embeddings[0] if embeddings else []
        except Exception as e:
            logger.error(f"Error getting embedding: {str(e)}")
            return []
    
    async def _find_trials_semantic(self, condition: str, location: str, condition_embedding: List[float]) -> List[Dict[str, Any]]:
        """Find trials using semantic similarity"""
        try:
            # CRITICAL FIX: Search with condition filter first, not all location trials
            # This prevents showing unrelated trials (e.g., Alopecia when searching for Hidradenitis)
            condition_trials = await self._find_trials_in_database(condition, location)

            if not condition_trials or not condition_embedding:
                # Fallback to keyword search
                return await self._find_trials_in_database(condition, location)
            
            # Calculate semantic similarity for each trial
            semantic_trials = []
            for trial in condition_trials:
                trial_text = f"{trial.get('conditions', '')} {trial.get('trial_name', '')}"
                
                try:
                    # Get embedding for trial
                    trial_embeddings = await self.gemini.generate_embeddings([f"Clinical trial: {trial_text}"])
                    trial_embedding = trial_embeddings[0]
                    
                    # Calculate cosine similarity
                    similarity = self._cosine_similarity(condition_embedding, trial_embedding)
                    
                    if similarity > 0.7:  # Similarity threshold
                        trial["similarity_score"] = similarity
                        semantic_trials.append(trial)
                        
                except Exception as e:
                    logger.error(f"Error processing trial embedding: {str(e)}")
                    continue
            
            # Sort by similarity and return top results
            semantic_trials.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)
            return semantic_trials[:5]
            
        except Exception as e:
            logger.error(f"Error in semantic search: {str(e)}")
            return await self._find_trials_in_database(condition, location)
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors"""
        try:
            if not vec1 or not vec2:
                return 0.0
                
            vec1_np = np.array(vec1)
            vec2_np = np.array(vec2)
            
            dot_product = np.dot(vec1_np, vec2_np)
            norm1 = np.linalg.norm(vec1_np)
            norm2 = np.linalg.norm(vec2_np)
            
            if norm1 == 0 or norm2 == 0:
                return 0.0
                
            return dot_product / (norm1 * norm2)
        except Exception:
            return 0.0
    
    def _get_response_style(self, user_analysis: Dict[str, Any] = None) -> Dict[str, Any]:
        """Determine response style based on user analysis"""
        if not user_analysis:
            return {"tone": "professional", "complexity": "moderate", "length": "standard"}
        
        style = {}
        
        # Tone based on sentiment
        sentiment = user_analysis.get("sentiment", "neutral")
        if sentiment in ["anxious", "frustrated"]:
            style["tone"] = "reassuring"
        elif sentiment == "positive":
            style["tone"] = "enthusiastic"
        else:
            style["tone"] = "professional"
        
        # Complexity based on preference
        complexity = user_analysis.get("complexity_preference", "moderate")
        style["complexity"] = complexity
        
        # Length based on communication style
        comm_style = user_analysis.get("communication_style", "standard")
        if comm_style == "brief":
            style["length"] = "concise"
        elif comm_style == "detailed":
            style["length"] = "comprehensive"
        else:
            style["length"] = "standard"
        
        return style
    
    def _format_trial_results(self, trials: List[Dict[str, Any]], condition: str, location: str, response_style: Dict[str, Any]) -> str:
        """Format trial results based on user's communication preferences"""
        tone = response_style.get("tone", "professional")
        complexity = response_style.get("complexity", "moderate")
        length = response_style.get("length", "standard")
        
        # Opening based on tone
        if tone == "reassuring":
            opening = f"I understand this can feel overwhelming. I found {len(trials)} clinical trial(s) for {condition} in {location} that might help:"
        elif tone == "enthusiastic":
            opening = f"Great news! I found {len(trials)} promising clinical trial(s) for {condition} in {location}:"
        else:
            opening = f"I found {len(trials)} clinical trial(s) for {condition} in {location}:"
        
        response = opening + "\n\n"
        
        # Format trials based on complexity and length preferences
        for i, trial in enumerate(trials[:3], 1):
            trial_ref = self._get_condition_based_trial_reference(trial).title()
            response += f"**{i}. {trial_ref}**\n"
            
            if complexity in ["moderate", "technical"]:
                # Add condition and location info
                response += f"Condition: {trial.get('conditions', 'Not specified')}\n"
                response += f"Location: {trial.get('site_location', 'Not specified')}\n"
                if trial.get('investigator_name'):
                    response += f"Investigator: {trial.get('investigator_name')}\n"
                        
                if complexity == "technical" and trial.get('similarity_score'):
                    response += f"Match confidence: {trial['similarity_score']:.1%}\n"
            
            if length == "comprehensive" and trial.get('conditions'):
                response += f"Conditions: {trial['conditions']}\n"
            
            response += "\n"
        
        # Closing based on tone
        if tone == "reassuring":
            response += "Would you like me to gently walk you through checking if you might be eligible for any of these trials? I'll explain everything clearly."
        elif tone == "enthusiastic":
            response += "This looks promising! Would you like me to check if you might be eligible for any of these trials?"
        else:
            response += "Would you like me to check if you might be eligible for any of these trials?"
        
        return response
    
    async def _start_prescreening_with_explanations(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext, user_analysis: Dict[str, Any] = None) -> Dict[str, Any]:
        """Start eligibility prescreening with OpenAI prescreening manager"""
        try:
            logger.info(f"{'='*80}")
            logger.info(f"▶️  START PRESCREENING - Session: {context.session_id}")
            logger.info(f"   Condition: {context.focus_condition}")
            logger.info(f"   Location: {context.focus_location}")
            logger.info(f"   State: {context.conversation_state}")
            logger.info(f"   Last shown trials: {len(context.last_shown_trials) if context.last_shown_trials else 0}")
            logger.info(f"{'='*80}")

            # Check if already in prescreening to prevent loops
            if context.conversation_state in ["prescreening_active", "awaiting_age", "awaiting_diagnosis", "awaiting_medications"]:
                logger.info("✅ Already in prescreening, continuing current flow")
                return await self._continue_prescreening(context)

            # Get trial ID from context - NO HARDCODED FALLBACKS
            trial_id = None

            # Try to get trial ID from last shown trials
            if hasattr(context, 'last_shown_trials') and context.last_shown_trials:
                # CRITICAL FIX: Find trial matching focus_condition, not just first trial
                focus_condition = context.focus_condition.lower() if context.focus_condition else ""

                matching_trial = None
                for trial in context.last_shown_trials:
                    trial_conditions = trial.get("conditions", "").lower()
                    # Check if trial conditions match focus condition
                    if focus_condition in trial_conditions or trial_conditions in focus_condition:
                        matching_trial = trial
                        break

                # IMPROVED FIX: Only use trial if it matches the condition
                # Do NOT fall back to first trial if no match - that causes wrong trial selection
                if matching_trial and matching_trial.get("id"):
                    trial_id = matching_trial["id"]
                    logger.info(f"Using trial ID {trial_id} from last shown trials (matched condition: {focus_condition})")

            # Check for existing prescreening sessions AFTER determining trial_id
            # This prevents duplicate prescreening for the SAME trial
            existing_session = self._check_existing_prescreening_session(
                context.session_id,
                context.focus_condition,
                trial_id  # Pass trial_id for specific deduplication
            )
            if existing_session:
                logger.info(f"Found existing prescreening session for trial {existing_session.get('trial_id')}")
                return self._handle_existing_prescreening_session(existing_session, context)
            else:
                if not trial_id:
                    logger.warning(f"No matching trial found in last_shown_trials for condition: {focus_condition}")

            # If no trial from last shown trials, try to find trial by condition/location
            if not trial_id:
                condition = detected_intent.entities.condition or context.focus_condition
                location = detected_intent.entities.location or context.focus_location
                
                if condition and location:
                    # Search for trials matching condition/location
                    matching_trials = await self._find_trials_in_database(condition, location)
                    if matching_trials and len(matching_trials) > 0:
                        # CRITICAL VALIDATION: Verify the returned trial actually matches the user's condition
                        # This prevents wrong trial selection when search service returns irrelevant results
                        first_trial = matching_trials[0]
                        trial_conditions = first_trial.get("conditions", "").lower()
                        focus_condition_lower = condition.lower()

                        # Check if there's a reasonable match between trial and user's condition
                        is_relevant_match = (
                            focus_condition_lower in trial_conditions or
                            trial_conditions in focus_condition_lower or
                            any(word in trial_conditions for word in focus_condition_lower.split() if len(word) > 3)
                        )

                        if is_relevant_match:
                            trial_id = first_trial.get("id")
                            logger.info(f"✅ Found trial ID {trial_id} by searching for condition='{condition}', location='{location}'")
                            logger.info(f"   Trial conditions: '{first_trial.get('conditions')}' - VALIDATED as relevant match")
                        else:
                            # Search returned trials but none are relevant to user's condition
                            logger.warning(f"⚠️  Search returned trials for condition='{condition}', location='{location}'")
                            logger.warning(f"   But first trial has conditions: '{first_trial.get('conditions')}'")
                            logger.warning(f"   No relevant match found - not using this trial")
                            # trial_id remains None, will trigger error below
            
            # If still no trial_id found, return error - DO NOT DEFAULT TO TRIAL 11
            if not trial_id:
                logger.error("No trial ID found - cannot start prescreening without specific trial")

                # Provide helpful message based on context
                if condition and location:
                    response_msg = f"I searched for {condition} trials in {location}, but couldn't find any available studies that match. Would you like to:\n\n1. Search in a different location\n2. Try a different condition\n3. See what trials are available near you"
                else:
                    response_msg = "I need to know which specific trial you'd like to check eligibility for. Could you please search for trials first, or let me know what condition and location you're interested in?"

                return {
                    "response": response_msg,
                    "new_state": "initial",
                    "metadata": {"prescreening_error": "no_trial_id_found", "searched_condition": condition, "searched_location": location}
                }
            
            # Initialize prescreening with OpenAI manager
            logger.info(f"[PRESCREENING] Starting prescreening with trial_id={trial_id}")

            # Extract user info from context
            user_id = context.user_id or "anonymous"
            session_id = context.session_id
            condition = detected_intent.entities.condition or context.focus_condition
            location = detected_intent.entities.location or context.focus_location

            # 🔥 PASS DATABASE PARAMETERS TO PRESCREENING MANAGER
            questions, trial_name = self.prescreening_manager.start_prescreening(
                trial_id, session_id, user_id, condition, location
            )

            # CRITICAL VALIDATION: Log trial details to verify correct trial is being used
            logger.info(f"[PRESCREENING] Trial selected: ID={trial_id}, Name='{trial_name}', User's condition='{condition}'")
            logger.info(f"[PRESCREENING] Generated {len(questions)} questions for this trial")
            
            if not questions:
                return {
                    "response": "I'm sorry, I couldn't find prescreening questions for this trial. Let me help you search for other trials.",
                    "new_state": "initial",
                    "metadata": {"prescreening_error": "no_questions_found"}
                }
            
            # *** DATABASE INTEGRATION: Session creation handled by prescreening_manager.start_prescreening() ***
            # No need to create prescreening session here - it's already created by the prescreening manager
            logger.info(f"✅ Prescreening session created by prescreening_manager for session {context.session_id}")
            
            # Initialize prescreening data in context
            if not hasattr(context, 'prescreening_data') or not context.prescreening_data:
                context.prescreening_data = {}
            
            # Convert questions to serializable format
            serializable_questions = []
            for q in questions:
                serializable_questions.append({
                    "criterion_id": q.criterion_id,
                    "question_text": q.question_text,
                    "criterion_type": q.criterion_type,
                    "category": q.category,
                    "expected_answer_type": q.expected_answer_type,
                    "evaluation_hint": q.evaluation_hint
                })
            
            context.prescreening_data.update({
                "trial_id": trial_id,
                "trial_name": trial_name,
                "questions": serializable_questions,
                "current_question_index": 0,
                "answers": []
            })
            
            # Get the first question
            first_question = questions[0]
            
            response_style = self._get_response_style(user_analysis)
            
            if response_style.get("tone") == "reassuring":
                intro = "I'll help you understand if you might be eligible. Don't worry - these questions help us find the right fit for you."
            else:
                intro = "Great! I'll help you check your eligibility."
            
            # Use condition-based display name for user-facing message, but keep actual trial_name for storage
            trial_display_name = self._get_condition_based_trial_reference(
                {'conditions': condition}, 
                context.focus_condition or 'clinical'
            )
            response = f"{intro}\n\nI need to ask you a few questions about {trial_display_name}.\n\n**Question 1 of {len(questions)}:** {first_question.question_text}"
            
            return {
                "response": response,
                "new_state": "prescreening_active",
                "metadata": {
                    "prescreening_started": True,
                    "trial_id": trial_id,
                    "trial_name": trial_name,
                    "current_question_index": 0,
                    "total_questions": len(questions),
                    "current_question": first_question.question_text,
                    "expected_answer_type": first_question.expected_answer_type
                }
            }
            
        except Exception as e:
            logger.error(f"Error starting prescreening with explanations: {str(e)}")
            # Do NOT fallback to hardcoded questions - return error instead
            return {
                "response": f"I'm having trouble loading the prescreening questions for this trial. Please try searching for trials again, or contact support if this continues.",
                "new_state": "initial",
                "metadata": {"prescreening_error": "failed_to_load_questions", "error_details": str(e)}
            }
    
    async def _continue_prescreening(self, context: ConversationContext) -> Dict[str, Any]:
        """Continue prescreening flow based on current state - ONLY use dynamic questions"""
        prescreening_data = getattr(context, 'prescreening_data', {})
        
        # If we have prescreening data with questions, continue the dynamic flow
        if prescreening_data and prescreening_data.get('questions'):
            questions = prescreening_data.get('questions', [])
            current_index = prescreening_data.get('current_question_index', 0)
            
            if current_index < len(questions):
                current_question_data = questions[current_index]
                progress_text = f"**Question {current_index + 1} of {len(questions)}:** {current_question_data['question_text']}"
                return {
                    "response": progress_text,
                    "new_state": "prescreening_active",
                    "metadata": {
                        "current_question_index": current_index,
                        "total_questions": len(questions),
                        "expected_answer_type": current_question_data['expected_answer_type']
                    }
                }
            else:
                # All questions answered, complete prescreening
                return await self._complete_prescreening_evaluation(context)
        else:
            # No valid prescreening data - restart the flow
            logger.error("No valid prescreening data found for continuation")
            return {
                "response": "I seem to have lost track of where we were in the prescreening. Let me start over. Please search for trials first.",
                "new_state": "initial", 
                "metadata": {"prescreening_error": "no_dynamic_data_for_continuation"}
            }
    
    def _get_trial_criteria(self, context: ConversationContext) -> List[str]:
        """Get trial eligibility criteria"""
        # Simplified - in real implementation, get from database based on context.trial_id
        return [
            "Age between 18-75 years",
            "Diagnosed with the target condition",
            "No serious heart conditions",
            "Not pregnant or nursing",
            "Able to attend regular appointments"
        ]
    
    async def _generate_eligibility_explanation(self, criteria: List[str], user_analysis: Dict[str, Any] = None) -> str:
        """Generate plain English explanation of eligibility criteria"""
        try:
            complexity = user_analysis.get("complexity_preference", "moderate") if user_analysis else "moderate"
            
            criteria_text = "\n".join([f"- {criterion}" for criterion in criteria])
            
            explanation_prompt = f"""Generate a clear, plain English explanation of these clinical trial eligibility criteria for a patient:

{criteria_text}

Target complexity level: {complexity}
- simple: Use everyday language, short sentences, avoid medical jargon
- moderate: Use clear language with some medical terms explained
- technical: Use appropriate medical terminology with context

Explain:
1. Why these criteria exist (patient safety and study validity)
2. What each criterion means in practical terms
3. Reassure that these are standard requirements

Keep it supportive and informative, not intimidating."""

            return await self.gemini.generate_text(f"""You are a helpful clinical trials coordinator who explains eligibility criteria in an accessible, reassuring way.

{explanation_prompt}""", max_tokens=600)
            
        except Exception as e:
            logger.error(f"Error generating eligibility explanation: {str(e)}")
            return "Clinical trials have specific eligibility criteria to ensure participant safety and study accuracy. I'll ask you a few questions to see if this trial might be a good fit for you."
    
    def _get_answer_acknowledgment(self, parsed_answer) -> str:
        """Get appropriate acknowledgment for a parsed answer"""
        if parsed_answer.confidence >= 0.8:
            if parsed_answer.interpretation == "yes":
                return "Thank you."
            elif parsed_answer.interpretation == "no":
                return "I understand."
            elif parsed_answer.interpretation == "number":
                return "Got it."
            else:
                return "Thank you for that information."
        else:
            return "I noted that down."

    def _generate_response_summary(self, answer_objects: List, prescreening_data: Dict[str, Any]) -> str:
        """Generate a detailed summary of user responses with status indicators"""
        summary_lines = []
        
        for answer in answer_objects:
            # Extract key information from the answer
            question = answer.question_text
            user_response = answer.user_response
            parsed_value = answer.parsed_value
            confidence = answer.confidence
            
            # Determine status indicator and display format
            status_icon, display_text = self._format_answer_summary(question, user_response, parsed_value, confidence)
            
            summary_lines.append(f"{status_icon} {display_text}")
        
        return "\n".join(summary_lines)
    
    def _format_answer_summary(self, question: str, user_response: str, parsed_value: str, confidence: float) -> tuple:
        """Format a single answer for the summary with appropriate status indicator"""
        question_lower = question.lower()
        
        # BMI/Height/Weight responses
        if any(keyword in question_lower for keyword in ["bmi", "body mass index", "height", "weight"]):
            return self._format_bmi_summary(question, user_response, parsed_value, confidence)
        
        # Age responses
        elif "age" in question_lower:
            return self._format_age_summary(question, user_response, parsed_value, confidence)
        
        # Numeric responses (flares, episodes, etc.)
        elif any(keyword in question_lower for keyword in ["flare", "episode", "occurrence", "how many"]):
            return self._format_numeric_summary(question, user_response, parsed_value, confidence)
        
        # Yes/No responses
        elif any(keyword in question_lower for keyword in ["have you", "are you", "do you"]):
            return self._format_yes_no_summary(question, user_response, parsed_value, confidence)
        
        # Default format
        else:
            return self._format_generic_summary(question, user_response, parsed_value, confidence)
    
    def _format_bmi_summary(self, question: str, user_response: str, parsed_value: str, confidence: float) -> tuple:
        """Format BMI/height/weight summary with calculated values"""
        # Try to parse height and weight from user response
        hw_data = self.prescreening_manager._parse_height_weight(user_response)
        
        if hw_data["height_cm"] and hw_data["weight_kg"]:
            bmi = self.prescreening_manager._calculate_bmi(hw_data["height_cm"], hw_data["weight_kg"])
            
            if bmi:
                height_ft = int(hw_data['height_cm'] / 2.54 / 12)
                height_in = int((hw_data['height_cm'] / 2.54) % 12)
                weight_lbs = int(hw_data['weight_kg'] / 0.453592)
                
                if confidence > 0.7:
                    status = "✓"
                    display = f"BMI: {bmi:.1f} (Height: {height_ft}'{height_in}\", Weight: {weight_lbs} lbs)"
                else:
                    status = "?"
                    display = f"BMI: {bmi:.1f} (Height: {height_ft}'{height_in}\", Weight: {weight_lbs} lbs) - needs verification"
                
                return status, display
        
        # Fallback if parsing failed
        return "?", f"Height/Weight: {user_response} - needs clarification"
    
    def _format_age_summary(self, question: str, user_response: str, parsed_value: str, confidence: float) -> tuple:
        """Format age summary"""
        try:
            age_value = float(parsed_value) if parsed_value else None
            if age_value and confidence > 0.7:
                return "✓", f"Age: {int(age_value)}"
            else:
                return "?", f"Age: {user_response} - needs clarification"
        except (ValueError, TypeError):
            return "?", f"Age: {user_response} - needs clarification"
    
    def _format_numeric_summary(self, question: str, user_response: str, parsed_value: str, confidence: float) -> tuple:
        """Format numeric responses (flares, episodes, etc.)"""
        try:
            numeric_value = float(parsed_value) if parsed_value else None
            if numeric_value is not None and confidence > 0.7:
                # Extract the key term from question
                if "flare" in question.lower():
                    term = "flares"
                elif "episode" in question.lower():
                    term = "episodes"
                elif "occurrence" in question.lower():
                    term = "occurrences"
                else:
                    term = "count"
                
                return "✓", f"{int(numeric_value)} {term} in specified period"
            else:
                return "?", f"Episodes/Count: {user_response} - needs clarification"
        except (ValueError, TypeError):
            return "?", f"Episodes/Count: {user_response} - needs clarification"
    
    def _format_yes_no_summary(self, question: str, user_response: str, parsed_value: str, confidence: float) -> tuple:
        """Format yes/no responses"""
        response_lower = user_response.lower().strip()
        
        if confidence > 0.7:
            if any(word in response_lower for word in ["yes", "y", "correct", "right", "true"]):
                # Extract key condition from question
                condition = self._extract_condition_from_question(question)
                return "✓", f"{condition}: Yes"
            elif any(word in response_lower for word in ["no", "n", "false", "wrong", "incorrect"]):
                condition = self._extract_condition_from_question(question)
                return "✓", f"{condition}: No"
        
        condition = self._extract_condition_from_question(question)
        return "?", f"{condition}: {user_response} - needs clarification"
    
    def _format_generic_summary(self, question: str, user_response: str, parsed_value: str, confidence: float) -> tuple:
        """Format generic responses"""
        if confidence > 0.7:
            return "✓", f"Response: {user_response}"
        else:
            return "?", f"Response: {user_response} - needs clarification"
    
    def _extract_condition_from_question(self, question: str) -> str:
        """Extract the key condition/topic from a question"""
        question_lower = question.lower()
        
        # Common medical conditions
        conditions = {
            "gout": "Gout diagnosis",
            "migraine": "Migraine diagnosis", 
            "diabetes": "Diabetes",
            "allerg": "Allergies",
            "medication": "Current medications",
            "pregnant": "Pregnancy status",
            "kidney": "Kidney function",
            "liver": "Liver function"
        }
        
        for keyword, condition in conditions.items():
            if keyword in question_lower:
                return condition
        
        # Generic fallback
        if "have you" in question_lower:
            return "Medical history"
        elif "are you" in question_lower:
            return "Current status"
        elif "do you" in question_lower:
            return "Current condition"
        else:
            return "Response"

    async def _handle_prescreening_review(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext, message: str) -> Dict[str, Any]:
        """Handle user choices during prescreening review phase"""
        try:
            metadata = getattr(context, 'metadata', {})
            user_choice = message.strip()
            
            if user_choice == "1":
                # Continue with evaluation using current responses
                eligibility_result_data = metadata.get("eligibility_result", {})
                summary = metadata.get("summary", "")
                
                # Reconstruct response with evaluation
                response = f"Thanks for completing the prescreening!\n\n"
                
                # Format eligibility result
                if isinstance(eligibility_result_data, dict):
                    response += eligibility_result_data.get("summary_text", "Evaluation completed.")
                else:
                    response += str(eligibility_result_data)
                
                return {
                    "response": response,
                    "new_state": "prescreening_complete",
                    "metadata": {
                        "prescreening_complete": True,
                        "eligibility_determined": True
                    }
                }
                
            elif user_choice == "2":
                # Allow user to edit responses
                response = "Which response would you like to clarify or edit?\n\n"
                response += "Please tell me which question you'd like to answer again, or describe what you'd like to change.\n\n"
                response += "For example: 'I want to change my age' or 'Let me clarify my height and weight'"
                
                return {
                    "response": response,
                    "new_state": "prescreening_editing",
                    "metadata": {
                        "editing_mode": True,
                        "original_summary": metadata.get("summary", ""),
                        "pending_evaluation": True
                    }
                }
            
            else:
                # Invalid choice, ask again
                return {
                    "response": "Please respond with '1' to continue with the current evaluation or '2' to make changes to your responses.",
                    "new_state": "prescreening_review",
                    "metadata": metadata
                }
                
        except Exception as e:
            logger.error(f"Error handling prescreening review: {str(e)}")
            return {
                "response": "I had trouble processing your choice. Let me continue with the evaluation based on your current responses.",
                "new_state": "prescreening_complete",
                "metadata": {"error": str(e)}
            }

    async def _handle_post_prescreening(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext, message: str) -> Dict[str, Any]:
        """Handle user messages after prescreening is complete - includes contact collection"""
        try:
            from core.services.contact_collection_service import contact_collection_service
            
            metadata = getattr(context, 'metadata', {})
            context_data = getattr(context, 'state_data', {})
            message_lower = message.lower().strip()
            
            # Get current contact collection state
            current_state = contact_collection_service.get_contact_collection_state(context_data)
            
            # If this is the first time in post-prescreening and contact collection not started
            if contact_collection_service.should_start_contact_collection(metadata):
                # CHECK: Is this a lead campaign with pre-populated contact data?
                is_lead_campaign = metadata.get('lead_campaign', False)
                contact_partial_data = metadata.get('contact_partial_data', {})

                if is_lead_campaign and contact_partial_data:
                    # Lead campaign with contact info - skip collection, send completion message
                    logger.info(f"[LEAD-CAMPAIGN] Skipping contact collection - info already provided")

                    overall_status = metadata.get("overall_status", "pending")
                    location = context.focus_location or "our office"

                    # Generate lead-specific completion message
                    if overall_status == "likely_eligible":
                        completion_msg = f"Great news! Based on your responses, you appear to qualify for this trial.\n\nA coordinator from {location} will contact you within 1-2 business days to discuss next steps and schedule your screening visit.\n\nThank you for your interest in clinical research!"
                    elif overall_status == "likely_ineligible":
                        completion_msg = f"Thank you for completing the questionnaire. While this specific trial may not be a match based on your responses, we may have other opportunities available.\n\nA coordinator will reach out within 1-2 business days to discuss alternative trials that may be suitable.\n\nThank you for your time!"
                    else:
                        completion_msg = f"Thank you for completing the prescreening! We need to review your responses with our medical team.\n\nA coordinator from {location} will contact you within 1-2 business days to discuss your results.\n\nThank you for your interest!"

                    # Mark contact collection as complete
                    updated_context_data = contact_collection_service.update_contact_collection_state(
                        context_data,
                        contact_collection_service.STATES['CONTACT_COMPLETE'],
                        contact_collection_initiated=True
                    )

                    return {
                        "response": completion_msg,
                        "new_state": "contact_complete",
                        "metadata": {**metadata, "contact_collection_initiated": True, "contact_skipped_lead_campaign": True},
                        "state_data_updates": updated_context_data
                    }

                # Regular contact collection flow for non-lead campaigns
                # Get eligibility status from metadata
                overall_status = metadata.get("overall_status", "pending")
                trial_name = metadata.get("trial_name")

                # Map overall_status to our eligibility categories
                if overall_status == "likely_eligible":
                    eligibility_status = "eligible"
                elif overall_status == "potentially_eligible":
                    eligibility_status = "eligible"  # Treat as eligible for contact collection
                elif overall_status == "likely_ineligible":
                    eligibility_status = "ineligible"
                else:
                    eligibility_status = "pending"

                # Generate invitation message
                invitation_message = contact_collection_service.get_contact_invitation_message(
                    eligibility_status, context.focus_condition
                )

                # Update context to indicate contact collection initiated
                updated_context_data = contact_collection_service.update_contact_collection_state(
                    context_data,
                    contact_collection_service.STATES['AWAITING_CONTACT_CONSENT'],
                    contact_collection_initiated=True,
                    eligibility_status=eligibility_status
                )

                return {
                    "response": invitation_message,
                    "new_state": "prescreening_complete",
                    "metadata": {**metadata, "contact_collection_initiated": True},
                    "state_data_updates": updated_context_data
                }
            
            # Handle contact collection flow states
            elif current_state == contact_collection_service.STATES['AWAITING_CONTACT_CONSENT']:
                consent_given, response_message, next_state = contact_collection_service.process_consent_response(message)

                if consent_given is True:
                    # User consented - update state
                    updated_context_data = contact_collection_service.update_contact_collection_state(
                        context_data, next_state, contact_consent_given=True
                    )

                    # Check if user provided first name directly (next_state will be COLLECTING_LAST_NAME)
                    if next_state == contact_collection_service.STATES['COLLECTING_LAST_NAME']:
                        # Extract and save the first name they provided
                        first_name = contact_collection_service._extract_name(message)
                        if first_name:
                            updated_context_data['contact_partial_data']['first_name'] = first_name

                elif consent_given is False:
                    # User declined - thank them and end contact collection
                    updated_context_data = contact_collection_service.update_contact_collection_state(
                        context_data, next_state, contact_consent_given=False
                    )
                else:
                    # Unclear response - ask for clarification, stay in same state
                    updated_context_data = context_data

                return {
                    "response": response_message,
                    "new_state": "prescreening_complete",
                    "metadata": metadata,
                    "state_data_updates": updated_context_data
                }
            
            elif current_state == contact_collection_service.STATES['COLLECTING_FIRST_NAME']:
                is_valid, response_message, next_state = contact_collection_service.collect_first_name(message)
                
                if is_valid:
                    first_name = contact_collection_service._extract_name(message)
                    updated_context_data = contact_collection_service.update_contact_collection_state(
                        context_data, next_state
                    )
                    updated_context_data['contact_partial_data']['first_name'] = first_name
                else:
                    updated_context_data = context_data
                
                return {
                    "response": response_message,
                    "new_state": "prescreening_complete",
                    "metadata": metadata,
                    "state_data_updates": updated_context_data
                }
            
            elif current_state == contact_collection_service.STATES['COLLECTING_LAST_NAME']:
                is_valid, response_message, next_state = contact_collection_service.collect_last_name(message)
                
                if is_valid:
                    last_name = contact_collection_service._extract_name(message)
                    updated_context_data = contact_collection_service.update_contact_collection_state(
                        context_data, next_state
                    )
                    updated_context_data['contact_partial_data']['last_name'] = last_name
                else:
                    updated_context_data = context_data
                
                return {
                    "response": response_message,
                    "new_state": "prescreening_complete",
                    "metadata": metadata,
                    "state_data_updates": updated_context_data
                }
            
            elif current_state == contact_collection_service.STATES['COLLECTING_PHONE']:
                is_valid, response_message, next_state = contact_collection_service.collect_phone_number(message)
                
                if is_valid:
                    phone = contact_collection_service._extract_phone_number(message)
                    updated_context_data = contact_collection_service.update_contact_collection_state(
                        context_data, next_state
                    )
                    updated_context_data['contact_partial_data']['phone_number'] = phone
                else:
                    updated_context_data = context_data
                    
                return {
                    "response": response_message,
                    "new_state": "prescreening_complete",
                    "metadata": metadata,
                    "state_data_updates": updated_context_data
                }
            
            elif current_state == contact_collection_service.STATES['COLLECTING_EMAIL']:
                is_valid, response_message, next_state = contact_collection_service.collect_email(message)
                
                if is_valid:
                    email = contact_collection_service._extract_email(message)
                    updated_context_data = contact_collection_service.update_contact_collection_state(
                        context_data, next_state
                    )
                    updated_context_data['contact_partial_data']['email'] = email
                    
                    # Generate confirmation message
                    contact_data = updated_context_data.get('contact_partial_data', {})
                    eligibility_status = updated_context_data.get('eligibility_status', 'pending')
                    confirmation_msg = contact_collection_service.generate_confirmation_message(
                        contact_data, eligibility_status
                    )
                    
                    return {
                        "response": confirmation_msg,
                        "new_state": "prescreening_complete",
                        "metadata": metadata,
                        "state_data_updates": updated_context_data
                    }
                else:
                    updated_context_data = context_data
                    
                return {
                    "response": response_message,
                    "new_state": "prescreening_complete",
                    "metadata": metadata,
                    "state_data_updates": updated_context_data
                }
            
            elif current_state == contact_collection_service.STATES['VALIDATING_CONTACT']:
                # User is confirming or correcting contact information
                if any(phrase in message_lower for phrase in ["yes", "y", "correct", "looks good", "that's right", "confirmed"]):
                    # User confirmed - save to database and complete
                    contact_data = context_data.get('contact_partial_data', {})
                    eligibility_status = context_data.get('eligibility_status', 'pending')
                    trial_name = context_data.get('trial_name')
                    
                    # Save to database
                    session_id = context.session_id
                    prescreening_session_id = metadata.get('prescreening_session_id')
                    
                    saved = contact_collection_service.save_contact_information(
                        session_id, contact_data, eligibility_status, prescreening_session_id
                    )
                    
                    if saved:
                        # Send comprehensive conversation report since both prescreening and contact collection are complete
                        try:
                            from core.services.email_service import email_service
                            import asyncio
                            # Send conversation report to scheduler@ with CC to dashboard@
                            asyncio.create_task(
                                email_service.send_conversation_report(session_id)
                            )
                            logger.info(f"Triggered conversation report for completed session {session_id}")
                        except Exception as e:
                            logger.error(f"Failed to send conversation report: {str(e)}")
                            # Don't fail the contact collection if email fails
                        # Generate completion message (use condition for privacy, not trial name)
                        condition = context.focus_condition or "clinical"
                        completion_message = contact_collection_service.generate_completion_message(
                            eligibility_status, trial_name=None, condition=condition
                        )
                        
                        updated_context_data = contact_collection_service.update_contact_collection_state(
                            context_data, contact_collection_service.STATES['CONTACT_COMPLETE'],
                            contact_collection_completed=True
                        )
                        
                        return {
                            "response": completion_message,
                            "new_state": "prescreening_complete",
                            "metadata": {**metadata, "contact_collection_completed": True},
                            "state_data_updates": updated_context_data
                        }
                    else:
                        return {
                            "response": "I apologize, but there was an error saving your contact information. Please try again or contact us directly.",
                            "new_state": "prescreening_complete",
                            "metadata": metadata,
                            "state_data_updates": context_data
                        }
                        
                elif any(phrase in message_lower for phrase in ["no", "n", "incorrect", "wrong", "fix", "change", "correct"]):
                    # User wants to make corrections - restart collection
                    response = "No problem! Let's collect your information again. What is your first name?"
                    updated_context_data = contact_collection_service.update_contact_collection_state(
                        context_data, contact_collection_service.STATES['COLLECTING_FIRST_NAME']
                    )
                    # Clear partial data
                    updated_context_data['contact_partial_data'] = {}
                    
                    return {
                        "response": response,
                        "new_state": "prescreening_complete",
                        "metadata": metadata,
                        "state_data_updates": updated_context_data
                    }
                else:
                    # Unclear response
                    return {
                        "response": "Please respond with 'yes' if the information is correct, or 'no' if you need to make corrections.",
                        "new_state": "prescreening_complete",
                        "metadata": metadata,
                        "state_data_updates": context_data
                    }
            
            # Handle legacy post-prescreening requests (for users who completed contact collection or declined)
            elif current_state in [contact_collection_service.STATES['CONTACT_COMPLETE'], contact_collection_service.STATES['CONTACT_DECLINED']]:
                # Fall back to original post-prescreening handler logic
                pass  # Will continue to original logic below
            
            # Original post-prescreening logic for users who haven't gone through contact collection
            # or need other assistance after completing contact collection
            if any(phrase in message_lower for phrase in ["contact", "coordinator", "phone", "email", "information"]):
                # User wants contact information
                trial_id = metadata.get("trial_id")
                if trial_id:
                    # Get trial contact information from database
                    from core.database import db
                    trial_info = db.execute_query("""
                        SELECT protocol_number, trial_name, investigator_name, phone, email, location
                        FROM clinical_trials 
                        WHERE id = %s
                    """, (trial_id,))
                    
                    if trial_info:
                        trial = trial_info[0]
                        response = f"Here's the contact information for the {trial['trial_name']} trial:\n\n"
                        response += f"Principal Investigator: {trial['investigator_name']}\n"
                        response += f"Phone: {trial['phone']}\n"
                        response += f"Email: {trial['email']}\n"
                        response += f"Location: {trial['location']}\n\n"
                        response += "I recommend mentioning that you completed an initial eligibility screening and are interested in learning more about the study."
                    else:
                        response = "I'm sorry, I couldn't retrieve the contact information. Please contact the study coordinator directly or visit the trial registry for more details."
                else:
                    response = "I don't have the specific trial information available. Please contact the study coordinator directly."
                
                return {
                    "response": response,
                    "new_state": "prescreening_complete",
                    "metadata": metadata
                }
                
            elif any(phrase in message_lower for phrase in ["other trials", "different trials", "more trials", "search", "other studies"]):
                # User wants to search for other trials
                condition = context.focus_condition or "clinical"
                response = f"I'd be happy to help you search for other {condition} trials. Let me find what's available in your area."
                
                return {
                    "response": response,
                    "new_state": "initial",
                    "metadata": {"searching_other_trials": True}
                }
                
            elif any(phrase in message_lower for phrase in ["yes", "y", "interested", "want to", "would like"]):
                # User is confirming interest - provide next steps
                overall_status = metadata.get("overall_status", "unknown")
                
                if overall_status == "likely_eligible":
                    response = "Great! Since you appear to be eligible, the next step would be to contact the trial coordinator for a more detailed screening. Would you like me to provide the contact information?"
                elif overall_status == "likely_ineligible":
                    condition = context.focus_condition or "clinical"
                    response = f"I understand you're still interested. While this particular trial may not be the best fit, there could be other {condition} trials that might work better for you. Would you like me to search for other options?"
                else:
                    response = "I recommend speaking with the trial coordinator to discuss your specific situation in more detail. They can provide the most accurate assessment. Would you like me to provide the contact information?"
                    
                return {
                    "response": response,
                    "new_state": "prescreening_complete",
                    "metadata": metadata
                }
                
            elif any(phrase in message_lower for phrase in ["no", "n", "not interested", "don't want", "thanks"]):
                # User is declining - offer alternatives
                response = "That's completely understandable. Is there anything else I can help you with? I can:\n\n"
                response += "• Search for other clinical trials\n"
                response += "• Provide general information about clinical trial participation\n"
                response += "• Help you understand different types of studies\n\n"
                response += "Just let me know how I can assist you!"
                
                return {
                    "response": response,
                    "new_state": "initial",
                    "metadata": {"post_prescreening_decline": True}
                }
                
            else:
                # General post-prescreening response
                overall_status = metadata.get("overall_status", "unknown")
                trial_id = metadata.get("trial_id")
                
                response = "Thank you for completing the prescreening! Based on your responses, "
                
                if overall_status == "likely_eligible":
                    response += "you appear to meet the basic eligibility criteria. The next step would be to contact the trial coordinator for a more detailed screening.\n\n"
                    response += "Would you like me to provide the contact information?"
                elif overall_status == "likely_ineligible":
                    condition = context.focus_condition or "clinical"
                    response += f"this particular trial may not be the best fit. However, there might be other {condition} trials that could work better for you.\n\n"
                    response += "Would you like me to search for other trials?"
                else:
                    response += "I recommend speaking with the trial coordinator to discuss your specific situation.\n\n"
                    response += "Would you like me to provide the contact information?"
                
                return {
                    "response": response,
                    "new_state": "prescreening_complete", 
                    "metadata": metadata
                }
                
        except Exception as e:
            logger.error(f"Error handling post-prescreening: {str(e)}")
            return {
                "response": "Thank you for completing the prescreening. How else can I help you with clinical trials?",
                "new_state": "initial",
                "metadata": {"post_prescreening_error": str(e)}
            }

    async def _complete_prescreening_evaluation(self, context: ConversationContext) -> Dict[str, Any]:
        """Complete prescreening and provide eligibility assessment using OpenAI manager"""
        try:
            prescreening_data = getattr(context, 'prescreening_data', {})
            trial_id = prescreening_data.get("trial_id")
            
            # If no trial_id in prescreening data, try to get from last shown trials
            if not trial_id and hasattr(context, 'last_shown_trials') and context.last_shown_trials:
                trial_info = context.last_shown_trials[0]
                trial_id = trial_info.get("id")
                logger.info(f"Using trial ID {trial_id} from last shown trials for completion")
            
            # If still no trial_id found, return error - DO NOT DEFAULT TO TRIAL 11
            if not trial_id:
                logger.error("No trial ID found for prescreening completion - cannot evaluate without specific trial")
                return {
                    "response": "I had trouble determining which trial we were evaluating. Let me help you search for trials again.",
                    "new_state": "initial",
                    "metadata": {"prescreening_error": "no_trial_id_for_completion"}
                }
            answers = prescreening_data.get("answers", [])
            
            if not answers:
                return {
                    "response": "It seems we don't have any answers recorded. Let me start the prescreening process again.",
                    "new_state": "initial",
                    "metadata": {"prescreening_error": "no_answers"}
                }
            
            # Recreate PrescreeningAnswer objects for OpenAI manager
            from core.prescreening.gemini_prescreening_manager import PrescreeningAnswer
            answer_objects = []
            for answer_data in answers:
                answer_obj = PrescreeningAnswer(
                    criterion_id=answer_data["criterion_id"],
                    question_text=answer_data["question_text"],
                    user_response=answer_data["user_response"],
                    parsed_value=answer_data["parsed_value"],
                    interpretation=answer_data["interpretation"],
                    confidence=answer_data["confidence"]
                )
                answer_objects.append(answer_obj)
            
            # Evaluate eligibility using OpenAI manager
            logger.info(f"📊 Starting eligibility evaluation for trial {trial_id} with {len(answer_objects)} answers")
            eligibility_result = await self.prescreening_manager.evaluate_eligibility(trial_id, answer_objects)
            logger.info(f"📊 Eligibility evaluation complete: status={eligibility_result.overall_status}, inclusion={eligibility_result.inclusion_met}/{eligibility_result.inclusion_total}")

            # Start building response with eligibility summary
            response = f"Thanks for completing the prescreening!\n\n"
            response += eligibility_result.summary_text

            # ✨ CHECK FOR AVAILABILITY FIRST (before clarification check)
            # This ensures eligible patients see availability even if some answers are uncertain
            availability_shown = False

            # CRITICAL DEBUG: Use ERROR level for immediate log visibility
            logger.error(f"🔍 AVAILABILITY CHECK STARTING - Status: {eligibility_result.overall_status}")

            if eligibility_result.overall_status in ["likely_eligible", "potentially_eligible"]:
                logger.error(f"✓ Overall status check passed - proceeding to criteria check")
                # Calculate inclusion percentage
                inclusion_percentage = (eligibility_result.inclusion_met / eligibility_result.inclusion_total * 100) if eligibility_result.inclusion_total > 0 else 0

                logger.error(f"📊 Eligibility Stats: {eligibility_result.inclusion_met}/{eligibility_result.inclusion_total} ({inclusion_percentage:.1f}%)")

                # CRITICAL DEBUG: Log all condition values before check
                logger.error(f"🎯 Availability Criteria Check:")
                logger.error(f"   - Inclusion %: {inclusion_percentage:.1f}% (need ≥60%)")
                logger.error(f"   - Trial ID: {trial_id}")
                logger.error(f"   - Context focus_location: '{context.focus_location}'")
                logger.error(f"   - All conditions met: {inclusion_percentage >= 60 and trial_id and context.focus_location}")

                # Only show availability for strong matches (≥60% inclusion criteria met)
                if inclusion_percentage >= 60 and trial_id and context.focus_location:
                    try:
                        from core.services.crio_availability_service import CRIOAvailabilityService

                        logger.error(f"🔍 Checking availability for trial {trial_id} in {context.focus_location}")

                        # Normalize location by removing common extra words
                        clean_location = context.focus_location
                        noise_words = [' for ', ' For ', ' in ', ' In ', ' at ', ' At ',
                                       ' gout', ' Gout', ' trial', ' Trial', ' trials', ' Trials',
                                       ' psoriasis', ' Psoriasis', ' diabetes', ' Diabetes']
                        for word in noise_words:
                            clean_location = clean_location.replace(word, ' ')
                        clean_location = ' '.join(clean_location.split())  # Normalize whitespace

                        logger.error(f"   Normalized location: '{context.focus_location}' → '{clean_location}'")

                        # CRITICAL FIX: Get site from trial_investigators to ensure we check the RIGHT site
                        # This guarantees the trial is actually available at the site we're checking
                        site_query = db.execute_query("""
                            SELECT
                                ti.site_id,
                                ti.investigator_name,
                                ti.site_location,
                                sc.site_name,
                                sc.coordinator_email,
                                sc.coordinator_user_key,
                                sc.address,
                                sc.city,
                                sc.state,
                                sc.zip_code
                            FROM trial_investigators ti
                            JOIN site_coordinators sc ON ti.site_id = sc.site_id
                            WHERE ti.trial_id = %s
                            AND ti.site_location ILIKE %s
                            AND ti.site_id IS NOT NULL
                            LIMIT 1
                        """, (trial_id, f"%{clean_location}%"))

                        if site_query:
                            site_info = site_query[0]
                            site_id = site_info['site_id']
                            coordinator_email = site_info['coordinator_email']

                            logger.error(f"✅ Found trial at site {site_id} ({site_info['site_name']}) with {site_info['investigator_name']}")

                            if coordinator_email:

                                # Fetch availability using shared CRIO session
                                # Get MORE slots initially so we can select diverse options
                                availability_service = CRIOAvailabilityService()
                                all_available_slots = availability_service.get_next_available_slots(
                                    site_id=site_id,
                                    study_id=str(trial_id),
                                    coordinator_email=coordinator_email,
                                    num_slots=15,  # Fetch more to enable diversity selection
                                    days_ahead=14
                                )

                                # Select 3 DIVERSE slots spanning different half-days
                                # This ensures variety: e.g., 12/31 AM, 12/31 PM, 1/1 AM
                                from core.conversation.slot_diversity import select_diverse_slots, format_slot_diversity_summary
                                availability_slots = select_diverse_slots(all_available_slots, num_slots=3)

                                if availability_slots:
                                    diversity_summary = format_slot_diversity_summary(availability_slots)
                                    logger.error(f"✅ Selected {len(availability_slots)} DIVERSE slots: {diversity_summary}")
                                    logger.error(f"   (from {len(all_available_slots)} total available slots)")

                                    # Add availability to response
                                    # Note: For web chat, quick_replies buttons will show instead of this text
                                    # This text serves as fallback for text-only interfaces (SMS, etc.)
                                    slots_text = "\n".join([
                                        f"   • {slot['display']}"
                                        for slot in availability_slots[:3]
                                    ])

                                    response += f"\n\nI can see availability at {site_info['site_name']}.\n\nWould you like to book an appointment? Please click an availability below.\n\n{slots_text}\n\n• Reply **'yes'** to book the first slot\n• Or reply **'2'** or **'3'** to select a different time"
                                    availability_shown = True

                                    # Store booking context for handler
                                    context.presented_slots = availability_slots
                                    context.booking_site_info = site_info
                                    context.booking_trial_id = trial_id

                                    # 🐛 DEBUG: Confirm booking attributes were set
                                    logger.error(f"🔧 BOOKING ATTRIBUTES SET in _complete_prescreening_evaluation:")
                                    logger.error(f"   Session: {context.session_id}")
                                    logger.error(f"   presented_slots: {len(context.presented_slots)} slots")
                                    logger.error(f"   booking_site_info site_name: {site_info.get('site_name')}")
                                    logger.error(f"   booking_trial_id: {trial_id}")
                                else:
                                    logger.error("ℹ️  No availability slots returned from CRIO")
                            else:
                                logger.warning(f"⚠️  No coordinator email found for site {site_id}")
                        else:
                            logger.warning(f"⚠️  Trial {trial_id} not found at any site in {context.focus_location}, or site_id not mapped")

                    except Exception as e:
                        logger.error(f"❌ Error checking availability: {e}", exc_info=True)
                        logger.warning(f"⚠️  Falling back to contact collection due to availability check exception")
                        # Don't fail - just continue without availability
                elif inclusion_percentage < 60:
                    logger.error(f"⚠️  Not showing availability: inclusion percentage ({inclusion_percentage:.1f}%) below 60% threshold")
                elif not context.focus_location:
                    logger.error(f"⚠️  Not showing availability: location not specified (focus_location={context.focus_location})")
                elif not trial_id:
                    logger.error(f"⚠️  Not showing availability: trial_id not found (trial_id={trial_id})")
            else:
                logger.error(f"⚠️  Not showing availability: overall_status is '{eligibility_result.overall_status}' (need likely_eligible or potentially_eligible)")

            # FINAL SUMMARY LOG
            logger.error(f"📋 AVAILABILITY CHECK COMPLETE - Shown: {availability_shown}")

            # CRITICAL FIX: Initiate contact collection IMMEDIATELY after eligibility results
            # This prevents double-asking and provides smooth flow
            from core.services.contact_collection_service import contact_collection_service

            # Determine eligibility status for contact collection
            if eligibility_result.overall_status == "likely_eligible":
                eligibility_status = "eligible"
            elif eligibility_result.overall_status == "potentially_eligible":
                eligibility_status = "eligible"
            elif eligibility_result.overall_status == "likely_ineligible":
                eligibility_status = "ineligible"
            else:
                eligibility_status = "pending"

            # Add availability OR contact collection invitation (not both)
            if not availability_shown:
                # Use CONDITION for privacy, never trial name
                condition = context.focus_condition or "clinical trial"
                contact_invitation = contact_collection_service.get_contact_invitation_message(
                    eligibility_status, condition
                )
                response += "\n\n" + contact_invitation

            # *** DATABASE INTEGRATION: Mark prescreening session as completed ***
            try:
                # Get user_id from context
                user_id = 'anonymous'
                if hasattr(context, 'user_id') and context.user_id:
                    user_id = context.user_id
                elif hasattr(context, 'state_data') and context.state_data and context.state_data.get('user_id'):
                    user_id = context.state_data.get('user_id')
                
                # 🔥 CALL THE NEW PRESCREENING MANAGER METHOD
                self.prescreening_manager.complete_prescreening_session(
                    context.session_id, user_id, eligibility_result.overall_status
                )
                
            except Exception as db_error:
                logger.error(f"❌ Failed to update prescreening session status: {str(db_error)}")
                # Continue with response even if DB update fails
            # *** END DATABASE INTEGRATION ***

            # Setup contact collection state ONLY if NOT in booking flow
            state_data_updates = {}
            if not availability_shown:
                logger.info(f"📞 No availability shown - initializing contact collection flow for session {context.session_id}")
                state_data_updates = contact_collection_service.update_contact_collection_state(
                    getattr(context, 'state_data', {}),
                    contact_collection_service.STATES['AWAITING_CONTACT_CONSENT'],
                    contact_collection_initiated=True,
                    eligibility_status=eligibility_status,
                    trial_name=context.focus_condition  # Use condition, not actual trial name
                )
            else:
                logger.info(f"📅 Availability shown - entering BOOKING FLOW for session {context.session_id}")

            final_state = "awaiting_booking_confirmation" if availability_shown else "prescreening_complete"
            logger.info(f"🔄 State transition: prescreening_active → {final_state} (availability_shown={availability_shown})")

            # Prepare quick reply buttons for slot selection (if availability shown)
            quick_replies = None
            if availability_shown and context.presented_slots:
                quick_replies = []
                for i, slot in enumerate(context.presented_slots[:3], 1):
                    # Format: "Friday, January 2\n8:00 AM" (date on line 1, time on line 2)
                    from datetime import datetime
                    dt = datetime.fromisoformat(slot['datetime'])
                    date_line = dt.strftime("%A, %B %-d")  # "Friday, January 2"
                    time_line = dt.strftime("%-I:%M %p")    # "8:00 AM"
                    button_label = f"{date_line}\n{time_line}"

                    quick_replies.append({
                        "label": button_label,
                        "value": str(i) if i > 1 else "yes",  # "yes" for first, "2"/"3" for others
                        "type": "slot_selection"
                    })
                logger.error(f"🎯 QUICK REPLIES GENERATED: {len(quick_replies)} buttons")
                logger.error(f"   Buttons: {[qr['label'] for qr in quick_replies]}")
            else:
                logger.error(f"⚠️  Quick replies NOT generated - availability_shown={availability_shown}, presented_slots={len(context.presented_slots) if context.presented_slots else 0}")

            return {
                "response": response,
                "new_state": final_state,
                "quick_replies": quick_replies,  # Interactive buttons for frontend
                "metadata": {
                    "prescreening_complete": True,
                    "contact_collection_initiated": not availability_shown,  # Only True if NOT booking
                    "trial_id": trial_id,
                    "overall_status": eligibility_result.overall_status,
                    "inclusion_met": eligibility_result.inclusion_met,
                    "inclusion_total": eligibility_result.inclusion_total,
                    "exclusion_met": eligibility_result.exclusion_met,
                    "exclusion_total": eligibility_result.exclusion_total,
                    "detailed_results": eligibility_result.detailed_results,
                    "collected_answers": [
                        {
                            "question": answer["question_text"],
                            "response": answer["user_response"],
                            "interpretation": answer["interpretation"],
                            "confidence": answer["confidence"]
                        } for answer in answers
                    ]
                },
                "state_data_updates": state_data_updates
            }
            
        except Exception as e:
            logger.error(f"Error completing prescreening evaluation: {str(e)}")
            return {
                "response": "I had trouble evaluating your eligibility. Let me help you search for other trials or contact the study coordinator directly.",
                "new_state": "initial",
                "metadata": {"prescreening_error": str(e)}
            }
    
    async def _process_prescreening_answer(self, action: ConversationAction, detected_intent: GeminiDetectedIntent, context: ConversationContext, user_analysis: Dict[str, Any] = None, message: str = None) -> Dict[str, Any]:
        """Process an answer during prescreening flow using OpenAI prescreening manager"""
        try:
            logger.info(f"🔵 PROCESSING PRESCREENING ANSWER - Session: {context.session_id}, Message: '{message}'")

            # Ensure prescreening_data exists
            if not hasattr(context, 'prescreening_data') or not context.prescreening_data:
                logger.error("❌ No prescreening data found in context")
                return await self._start_prescreening_with_explanations(action, detected_intent, context, user_analysis)
            
            prescreening_data = context.prescreening_data
            questions = prescreening_data.get("questions", [])
            current_index = prescreening_data.get("current_question_index", 0)
            
            if current_index >= len(questions):
                logger.error("Current question index out of range")
                return await self._complete_prescreening_evaluation(context)
            
            current_question_data = questions[current_index]
            user_response = message or "yes"
            
            # Recreate PrescreeningQuestion object for OpenAI manager
            from core.prescreening.gemini_prescreening_manager import PrescreeningQuestion
            current_question = PrescreeningQuestion(
                criterion_id=current_question_data["criterion_id"],
                question_text=current_question_data["question_text"],
                criterion_type=current_question_data["criterion_type"],
                category=current_question_data["category"],
                expected_answer_type=current_question_data["expected_answer_type"],
                evaluation_hint=current_question_data["evaluation_hint"]
            )
            
            # Check if we're awaiting confirmation
            metadata = getattr(context, 'metadata', {})
            if metadata.get("awaiting_confirmation"):
                response_lower = user_response.lower().strip()
                if response_lower in ["yes", "y", "correct", "right"]:
                    # Use the validated data from previous validation
                    if metadata.get("validation_data"):
                        # Override user_response with validated data for processing
                        validation_data = metadata["validation_data"]
                        if "bmi" in validation_data:
                            user_response = f"{validation_data['display_height']}, {validation_data['display_weight']}"
                        elif "numeric_value" in validation_data:
                            user_response = str(int(validation_data["numeric_value"]))
                        elif "answer" in validation_data:
                            user_response = validation_data["answer"]
                    
                    # Clear confirmation state
                    context.metadata = {k: v for k, v in metadata.items() if k not in ["awaiting_confirmation", "validation_data"]}
                else:
                    # User wants to correct their response, re-validate
                    pass  # Continue with normal validation flow
            
            # Get the actual TrialCriterion object for validation
            criterion = self.prescreening_manager._get_criterion_by_id(current_question_data["criterion_id"])
            if not criterion:
                logger.error(f"Could not find criterion with ID {current_question_data['criterion_id']}")
                return {
                    "response": "I had trouble processing your answer. There seems to be an issue with the prescreening system. Please try searching for trials again.",
                    "new_state": "initial",
                    "metadata": {"prescreening_error": "criterion_not_found"}
                }
            
            # Validate the user's response first
            validation_result = self.prescreening_manager._validate_user_response(criterion, user_response)
            
            # Handle validation failures or confirmation needs
            if not validation_result["is_valid"]:
                return {
                    "response": validation_result["feedback_message"] + (f"\n\n{validation_result['suggested_format']}" if validation_result["suggested_format"] else ""),
                    "new_state": "prescreening_active",
                    "metadata": {
                        "validation_error": True,
                        "awaiting_correction": True,
                        "current_question_index": current_index
                    }
                }
            
            elif validation_result["needs_confirmation"]:
                # Ask for confirmation with parsed data
                confirmation_msg = validation_result["feedback_message"]
                if validation_result.get("parsed_data") and "bmi" in validation_result["parsed_data"]:
                    confirmation_msg += "\n\nPlease respond 'yes' to confirm or provide your correct height and weight."
                else:
                    confirmation_msg += "\n\nPlease respond 'yes' to confirm or provide the correct information."
                
                return {
                    "response": confirmation_msg,
                    "new_state": "prescreening_active", 
                    "metadata": {
                        "awaiting_confirmation": True,
                        "validation_data": validation_result["parsed_data"],
                        "current_question_index": current_index
                    }
                }
            
            # Parse the user's answer using OpenAI
            parsed_answer = await self.prescreening_manager.parse_answer(current_question, user_response)
            
            # Store the answer in serializable format
            if not prescreening_data.get("answers"):
                prescreening_data["answers"] = []
            prescreening_data["answers"].append({
                "criterion_id": parsed_answer.criterion_id,
                "question_text": parsed_answer.question_text,
                "user_response": parsed_answer.user_response,
                "parsed_value": parsed_answer.parsed_value,
                "interpretation": parsed_answer.interpretation,
                "confidence": parsed_answer.confidence
            })
            
            # *** DATABASE INTEGRATION: Save prescreening answer ***
            try:
                # Get user_id from context - try multiple sources
                user_id = 'anonymous'  # Default fallback
                if hasattr(context, 'user_id') and context.user_id:
                    user_id = context.user_id
                elif hasattr(context, 'state_data') and context.state_data and context.state_data.get('user_id'):
                    user_id = context.state_data.get('user_id')
                
                logger.info(f"Saving prescreening answer: user_id={user_id}, session_id={context.session_id}, question='{parsed_answer.question_text[:50]}...', answer='{parsed_answer.user_response[:30]}...'")
                
                # 🔥 CALL THE NEW PRESCREENING MANAGER METHOD
                self.prescreening_manager.save_prescreening_answer(
                    context.session_id, user_id, current_question, user_response, parsed_answer
                )
                
                logger.info("✅ Successfully saved prescreening answer to database")
                
            except Exception as db_error:
                logger.error(f"❌ Failed to save prescreening answer to database: {str(db_error)}")
                # Continue with prescreening even if DB save fails
            # *** END DATABASE INTEGRATION ***
            
            # Move to next question or complete prescreening
            next_index = current_index + 1
            
            if next_index < len(questions):
                # Continue with next question
                next_question_data = questions[next_index]
                prescreening_data["current_question_index"] = next_index
                
                # Acknowledge the answer and ask next question
                acknowledgment = self._get_answer_acknowledgment(parsed_answer)
                response = f"{acknowledgment}\n\n{next_question_data['question_text']}"
                
                return {
                    "response": response,
                    "new_state": "prescreening_active",
                    "metadata": {
                        "current_question_index": next_index,
                        "total_questions": len(questions),
                        "current_question": next_question_data['question_text'],
                        "expected_answer_type": next_question_data['expected_answer_type'],
                        "parsed_answer": {
                            "interpretation": parsed_answer.interpretation,
                            "confidence": parsed_answer.confidence
                        }
                    }
                }
            else:
                # All questions answered, complete prescreening
                return await self._complete_prescreening_evaluation(context)
                
        except Exception as e:
            logger.error(f"❌ CRITICAL: Error processing prescreening answer: {str(e)}")
            logger.error(f"   Session: {context.session_id}")
            logger.error(f"   Message: {message}")
            logger.error(f"   Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()}")
            return {
                "response": "I had trouble processing your answer. There seems to be an issue with the prescreening system. Please try searching for trials again.",
                "new_state": "initial",
                "metadata": {
                    "prescreening_error": "failed_to_process_answer",
                    "error_details": str(e),
                    "error_type": type(e).__name__
                }
            }

    # ========================================================================
    # BOOKING FLOW HANDLERS (Added for end-to-end booking)
    # ========================================================================

    async def _handle_booking_confirmation(self, context: ConversationContext, message: str) -> Dict[str, Any]:
        """Handle user confirming they want to book an appointment"""
        message_lower = message.lower().strip()

        logger.info(f"🎯 BOOKING CONFIRMATION - Session: {context.session_id}, Message: '{message}'")

        # Verify booking context exists (presented_slots is now always defined, check if populated)
        if not context.presented_slots:
            logger.error(f"❌ CRITICAL: presented_slots empty in context for session {context.session_id}")
            logger.error(f"   This should have been set when availability was shown")
            return {
                "response": "I'm sorry, I seem to have lost the availability information. Let me provide coordinator contact information instead.",
                "new_state": "prescreening_complete",
                "metadata": {"error": "missing_booking_context"}
            }

        logger.info(f"   ✅ Booking context verified: {len(context.presented_slots)} slots available")

        # Check if user selected specific slot number
        slot_index = self._parse_slot_selection(message_lower, len(context.presented_slots))

        # Detect booking confirmation (explicit words OR slot number selection)
        is_booking_confirmation = (
            any(word in message_lower for word in ['yes', 'sure', 'please', 'book', 'schedule', 'ok', 'okay']) or
            slot_index is not None  # Selecting a slot number counts as confirmation
        )

        if is_booking_confirmation:
            # Use selected slot index, or determine it now
            if slot_index is None:
                slot_index = self._parse_slot_selection(message_lower, len(context.presented_slots))

            if slot_index is not None:
                context.selected_slot = context.presented_slots[slot_index]
                logger.info(f"   User selected slot {slot_index + 1}: {context.selected_slot['display']}")
            else:
                context.selected_slot = context.presented_slots[0]  # Default to first
                logger.info(f"   Defaulting to first slot: {context.selected_slot['display']}")

            # Start collecting booking details
            context.booking_data = {}
            return {
                "response": f"Perfect! I'll schedule you for {context.selected_slot['display']}. I just need a few details.\n\nWhat is your full name?",
                "new_state": "collecting_booking_name",
                "metadata": {"booking_in_progress": True}
            }

        elif any(phrase in message_lower for phrase in ["don't work", "dont work", "doesn't work", "different time", "other time", "another time", "not available"]):
            # User wants different times - ask for preferences
            logger.info("   User wants different times, asking for preferences")

            # Check if we have early slots (within 72 hours) to gently encourage
            from datetime import datetime, timedelta
            now = datetime.now()
            early_slots = [s for s in context.presented_slots if datetime.fromisoformat(s['datetime']) < now + timedelta(hours=72)]

            if early_slots and not getattr(context, 'early_booking_encouraged', False):
                # Gentle one-time encouragement with trial availability context
                context.early_booking_encouraged = True
                early_options = "\n".join([f"   • {slot['display']}" for slot in early_slots[:2]])

                return {
                    "response": f"""I understand those times might not be ideal. I do have some openings as early as tomorrow:

{early_options}

Since trials can fill up quickly, would one of these earlier times work for your schedule? If not, I completely understand - what days or times work best for you?

For example, you can say 'weekday mornings' or 'Tuesday or Wednesday afternoon'""",
                    "new_state": "requesting_preferred_times",
                    "metadata": {"early_encouragement_shown": True}
                }
            else:
                # Ask for preferences without encouragement
                return {
                    "response": """I understand. What days or times work best for your schedule?

For example, you can say:
• "Weekday mornings"
• "Tuesday or Wednesday afternoon"
• "Anytime after 3pm"

I'll find the best available times that match your preference.""",
                    "new_state": "requesting_preferred_times",
                    "metadata": {}
                }

        elif any(word in message_lower for word in ['no', 'not now', 'later', 'contact']):
            # User declined booking - transition to contact collection
            logger.info("   User declined booking, transitioning to contact collection")

            from core.services.contact_collection_service import contact_collection_service

            # Get eligibility status from context
            eligibility_status = "eligible"  # They were shown availability, so they're eligible

            # Get contact invitation message
            contact_invitation = contact_collection_service.get_contact_invitation_message(
                eligibility_status,
                context.focus_condition or "clinical trial"
            )

            # Set up contact collection state
            state_data_updates = contact_collection_service.update_contact_collection_state(
                getattr(context, 'state_data', {}),
                contact_collection_service.STATES['AWAITING_CONTACT_CONSENT'],
                contact_collection_initiated=True,
                eligibility_status=eligibility_status,
                trial_name=context.focus_condition
            )

            return {
                "response": f"No problem! {contact_invitation}",
                "new_state": "prescreening_complete",
                "metadata": {"booking_declined": True},
                "state_data_updates": state_data_updates
            }

        else:
            # Unclear response
            logger.info("   Unclear response, asking again")
            return {
                "response": "Would you like to schedule an appointment? Reply 'yes' to book, or I can provide coordinator contact information.",
                "new_state": "awaiting_booking_confirmation",
                "metadata": {}
            }

    def _parse_slot_selection(self, message: str, num_slots: int) -> Optional[int]:
        """Parse slot selection from user message"""
        if '1' in message or 'first' in message:
            return 0
        elif '2' in message or 'second' in message and num_slots > 1:
            return 1
        elif '3' in message or 'third' in message and num_slots > 2:
            return 2
        return None

    async def _handle_booking_details(self, context: ConversationContext, message: str) -> Dict[str, Any]:
        """Collect patient details for booking"""
        logger.info(f"🎯 BOOKING DETAILS COLLECTION - State: {context.conversation_state}, Message: '{message}'")

        if context.conversation_state == "collecting_booking_name":
            # Collect name
            context.booking_data['name'] = message.strip()
            first_name = context.booking_data['name'].split()[0] if context.booking_data['name'].split() else "there"
            logger.info(f"   Collected name: {context.booking_data['name']}")

            return {
                "response": f"Thank you, {first_name}. What's your phone number?",
                "new_state": "collecting_booking_phone",
                "metadata": {"booking_in_progress": True}
            }

        elif context.conversation_state == "collecting_booking_phone":
            # Collect and validate phone
            phone = self._extract_phone_number(message)

            if not phone:
                logger.info(f"   Invalid phone number: {message}")
                return {
                    "response": "I couldn't recognize that phone number. Please enter a 10-digit phone number (e.g., 555-123-4567):",
                    "new_state": "collecting_booking_phone",
                    "metadata": {"validation_error": True}
                }

            context.booking_data['phone'] = phone
            logger.info(f"   Collected phone: {phone}")

            return {
                "response": "Great! And what's your email address?",
                "new_state": "collecting_booking_email",
                "metadata": {"booking_in_progress": True}
            }

        elif context.conversation_state == "collecting_booking_email":
            # Collect and validate email
            email = self._extract_email(message)

            if not email:
                logger.info(f"   Invalid email: {message}")
                return {
                    "response": "That doesn't look like a valid email. Please enter your email address (e.g., name@example.com):",
                    "new_state": "collecting_booking_email",
                    "metadata": {"validation_error": True}
                }

            context.booking_data['email'] = email
            logger.info(f"   Collected email: {email}")

            return {
                "response": "Perfect! Finally, what's your date of birth? (Please use MM/DD/YYYY format)",
                "new_state": "collecting_booking_dob",
                "metadata": {"booking_in_progress": True}
            }

        elif context.conversation_state == "collecting_booking_dob":
            # Collect and validate date of birth
            dob = self._extract_date(message)

            if not dob:
                logger.info(f"   Invalid DOB: {message}")
                return {
                    "response": "I need your date of birth in MM/DD/YYYY format. For example: 01/15/1985",
                    "new_state": "collecting_booking_dob",
                    "metadata": {"validation_error": True}
                }

            context.booking_data['dob'] = dob
            logger.info(f"   Collected DOB: {dob}")
            logger.info(f"   All details collected, creating appointment...")

            # All details collected - create appointment
            return await self._create_appointment(context)

        else:
            # Unknown state
            logger.error(f"   Unknown booking state: {context.conversation_state}")
            return {
                "response": "I'm sorry, something went wrong with the booking process. Let me provide coordinator contact information instead.",
                "new_state": "prescreening_complete",
                "metadata": {"booking_error": "unknown_state"}
            }

    def _extract_phone_number(self, text: str) -> Optional[str]:
        """Extract and validate phone number from text"""
        import re
        # Remove all non-digit characters
        digits = re.sub(r'\D', '', text)

        # Accept 10 digits or 11 digits starting with 1
        if len(digits) == 10:
            # Format as (XXX) XXX-XXXX
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        elif len(digits) == 11 and digits[0] == '1':
            # Remove leading 1 and format
            digits = digits[1:]
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"

        return None

    def _extract_email(self, text: str) -> Optional[str]:
        """Extract and validate email from text"""
        import re
        # Basic email regex
        match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text.lower())
        return match.group(0) if match else None

    def _extract_date(self, text: str) -> Optional[str]:
        """Extract and validate date from text (MM/DD/YYYY format)"""
        import re
        from datetime import datetime

        # Try MM/DD/YYYY format
        match = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', text)

        if match:
            month, day, year = match.groups()

            # Validate date
            try:
                # Convert to date object to validate
                date_obj = datetime(int(year), int(month), int(day))

                # Check reasonable birth year (between 1900 and current year - 18)
                current_year = datetime.now().year
                if int(year) < 1900 or int(year) > current_year - 18:
                    logger.warning(f"   Date of birth year {year} out of reasonable range")
                    return None

                # Return in YYYY-MM-DD format for database
                return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
            except ValueError:
                # Invalid date
                return None

        return None

    async def _create_appointment(self, context: ConversationContext) -> Dict[str, Any]:
        """Store pending booking and notify coordinator (simplified flow - no CRIO creation)"""
        logger.info(f"🎯 STORING PENDING BOOKING")
        logger.info(f"   Patient: {context.booking_data.get('name')}")
        logger.info(f"   Slot: {context.selected_slot.get('display')}")
        logger.info(f"   Site: {context.booking_site_info.get('site_name')}")

        # Database debug logging (since Cloud Run logs don't work)
        try:
            db.execute_update("""
                CREATE TABLE IF NOT EXISTS debug_booking_flow (
                    id SERIAL PRIMARY KEY,
                    session_id VARCHAR(100),
                    step VARCHAR(50),
                    success BOOLEAN,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            db.execute_update("INSERT INTO debug_booking_flow (session_id, step, success) VALUES (%s, %s, %s)",
                            (context.session_id, 'create_appointment_called', True))
        except:
            pass

        try:
            # Parse appointment datetime
            if isinstance(context.selected_slot['datetime'], str):
                appointment_dt = datetime.fromisoformat(context.selected_slot['datetime'])
            else:
                appointment_dt = context.selected_slot['datetime']

            # Store patient contact info (UPSERT - update if exists from contact collection)
            contact_result = db.execute_insert_returning("""
                INSERT INTO patient_contact_info
                (session_id, first_name, last_name, email, phone_number, date_of_birth,
                 eligibility_status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (session_id) DO UPDATE SET
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    email = EXCLUDED.email,
                    phone_number = EXCLUDED.phone_number,
                    date_of_birth = EXCLUDED.date_of_birth,
                    eligibility_status = EXCLUDED.eligibility_status
                RETURNING id
            """, (
                context.session_id,
                context.booking_data['name'].split()[0],  # First name
                ' '.join(context.booking_data['name'].split()[1:]) or context.booking_data['name'],  # Last name
                context.booking_data['email'],
                context.booking_data['phone'],
                context.booking_data['dob'],
                'eligible'
            ))

            contact_id = contact_result['id']  # FIX: Access as dict, not list

            # Debug log
            try:
                db.execute_update("INSERT INTO debug_booking_flow (session_id, step, success) VALUES (%s, %s, %s)",
                                (context.session_id, 'contact_info_saved', True))
            except:
                pass

            # Store pending appointment (with placeholders since not created in CRIO yet)
            placeholder_appointment_id = f"PENDING_{context.session_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            placeholder_patient_id = f"PENDING_PATIENT_{context.session_id}"

            appointment_result = db.execute_insert_returning("""
                INSERT INTO appointments
                (crio_appointment_id, crio_patient_id, session_id, site_id, study_id, visit_id,
                 coordinator_email, appointment_date, status, notes, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
            """, (
                placeholder_appointment_id,
                placeholder_patient_id,
                context.session_id,
                context.booking_site_info['site_id'],
                str(context.booking_trial_id),
                'recruitment',
                context.booking_site_info['coordinator_email'],
                appointment_dt,
                'scheduled',  # Use valid status value
                f"PENDING COORDINATOR CONFIRMATION - Chatbot booking request - Patient: {context.booking_data['name']}, Phone: {context.booking_data['phone']}"
            ))

            appointment_id = appointment_result['id']  # FIX: Access as dict, not list

            # Debug log
            try:
                db.execute_update("INSERT INTO debug_booking_flow (session_id, step, success) VALUES (%s, %s, %s)",
                                (context.session_id, 'appointment_saved', True))
            except:
                pass

            logger.info(f"✅ Pending booking stored - Appointment ID: {appointment_id}")

            # Send email notifications (patient and coordinator)
            from core.services.email_service import email_service
            from core.services.sms_service import sms_service

            # Format full address from site_info (used for both emails)
            site_address = None
            if context.booking_site_info.get('address'):
                address_parts = [
                    context.booking_site_info.get('address', ''),
                    context.booking_site_info.get('city', ''),
                    context.booking_site_info.get('state', ''),
                    context.booking_site_info.get('zip_code', '')
                ]
                # Filter out empty parts and join
                site_address = ', '.join([p for p in address_parts if p])

            # Note: Email sending is non-blocking - errors won't fail the booking
            try:
                # Send appointment confirmation email to patient
                patient_email = context.booking_data.get('email')
                if patient_email:
                    await email_service.send_appointment_confirmation(
                        session_id=context.session_id,
                        patient_email=patient_email,
                        patient_name=context.booking_data['name'],
                        appointment_datetime=appointment_dt,
                        site_name=context.booking_site_info['site_name'],
                        site_address=site_address
                    )
                    logger.info(f"📧 Sent appointment confirmation email to {patient_email}")
                else:
                    logger.warning(f"No email address available for patient - skipping email confirmation")

            except Exception as email_error:
                logger.error(f"Patient email notification error: {email_error}")
                # Continue anyway - booking is stored

            # Send coordinator notification email
            try:
                # Get trial name if available
                trial_name = None
                if hasattr(context, 'booking_trial_id') and context.booking_trial_id:
                    trial_query = db.execute_query(
                        "SELECT trial_name FROM clinical_trials WHERE id = %s",
                        (context.booking_trial_id,)
                    )
                    if trial_query:
                        trial_name = trial_query[0]['trial_name']

                await email_service.send_coordinator_booking_notification(
                    session_id=context.session_id,
                    patient_name=context.booking_data['name'],
                    patient_email=context.booking_data.get('email'),
                    patient_phone=context.booking_data['phone'],
                    patient_dob=context.booking_data.get('dob'),
                    appointment_datetime=appointment_dt,
                    site_name=context.booking_site_info['site_name'],
                    site_address=site_address,
                    trial_id=context.booking_trial_id if hasattr(context, 'booking_trial_id') else None,
                    trial_name=trial_name,
                    eligibility_status='eligible'
                )
                logger.info(f"📧 Sent coordinator booking notification to mmorris@delricht.com")

            except Exception as coordinator_email_error:
                logger.error(f"Coordinator email notification error: {coordinator_email_error}")
                # Continue anyway - booking is stored

            # Send confirmation SMS to patient (fully non-blocking)
            try:
                patient_message = f"""Thank you for your booking request!

We've received your request for:
📅 {context.selected_slot['display']}
🏥 {context.booking_site_info['site_name']}

A coordinator will text you shortly to confirm your appointment.

Reply STOP to opt out."""

                # Try to send SMS - completely non-blocking, all exceptions caught
                try:
                    await sms_service.send_sms(
                        to_phone=context.booking_data['phone'],
                        message=patient_message,
                        session_id=context.session_id
                    )
                    logger.info(f"✅ Confirmation SMS sent to patient: {context.booking_data['phone']}")
                except Exception as sms_error:
                    # Any SMS error is non-critical
                    logger.warning(f"SMS send failed (non-critical): {sms_error}")
                    # Continue - booking still succeeds
            except Exception as outer_sms_error:
                # Even constructing the message failed - just continue
                logger.warning(f"SMS preparation failed (non-critical): {outer_sms_error}")
                pass

            # Debug log
            try:
                db.execute_update("INSERT INTO debug_booking_flow (session_id, step, success) VALUES (%s, %s, %s)",
                                (context.session_id, 'returning_success', True))
            except:
                pass

            return {
                "response": f"""✅ **Your booking has been submitted!**

📅 **Requested Time**: {context.selected_slot['display']}
🏥 **Location**: {context.booking_site_info['site_name']}

**We've sent a text confirmation to {context.booking_data['phone']}. A coordinator will contact you shortly to finalize your appointment.**

Is there anything else I can help you with?""",
                "new_state": "booking_complete",
                "metadata": {
                    "appointment_id": appointment_id,
                    "contact_id": contact_id,
                    "booking_pending": True
                }
            }

        except Exception as e:
            # Debug log exception
            try:
                db.execute_update("INSERT INTO debug_booking_flow (session_id, step, success, error_message) VALUES (%s, %s, %s, %s)",
                                (context.session_id, 'exception_caught', False, str(e)[:500]))
            except:
                pass

            logger.error(f"❌ Exception storing pending booking: {e}", exc_info=True)
            return {
                "response": f"""I encountered an error processing your booking request.

Let me provide coordinator contact information:

📧 **Email**: {context.booking_site_info.get('coordinator_email', 'coordinator@delricht.com')}
🏥 **Site**: {context.booking_site_info.get('site_name', 'Clinical Research Site')}
📞 **Phone**: (918) 400-3939

Please call to schedule your appointment for {context.selected_slot['display']}.""",
                "new_state": "booking_failed",
                "metadata": {"booking_exception": str(e)}
            }

    async def _handle_preferred_times(self, context: ConversationContext, message: str) -> Dict[str, Any]:
        """Handle user's preferred time request using Gemini to parse preferences"""
        logger.info(f"🕐 PREFERRED TIMES - Session: {context.session_id}, Message: '{message}'")

        try:
            # Use Gemini to extract time preferences from natural language
            preference_prompt = f"""Extract scheduling preferences from this message: "{message}"

Return JSON with:
- time_of_day: "morning" (before 12pm), "afternoon" (12pm-5pm), "evening" (after 5pm), or null
- days_of_week: array of day names ["Monday", "Tuesday", etc.] or null for any day
- specific_time: specific time if mentioned (e.g., "3pm") or null

Examples:
- "Weekday mornings" → {{"time_of_day": "morning", "days_of_week": ["Monday","Tuesday","Wednesday","Thursday","Friday"]}}
- "Tuesday or Thursday afternoon" → {{"time_of_day": "afternoon", "days_of_week": ["Tuesday","Thursday"]}}
- "Anytime after 3pm" → {{"time_of_day": "afternoon", "specific_time": "3pm"}}"""

            preferences = await self.gemini.extract_json(preference_prompt, "")

            logger.info(f"   Extracted preferences: {preferences}")

            # Fetch more availability with filters
            from core.services.crio_availability_service import CRIOAvailabilityService
            from datetime import datetime, timedelta

            availability_service = CRIOAvailabilityService()
            all_slots = availability_service.get_next_available_slots(
                site_id=context.booking_site_info['site_id'],
                study_id=str(context.booking_trial_id),
                coordinator_email=context.booking_site_info['coordinator_email'],
                num_slots=20,  # Get more to filter
                days_ahead=14
            )

            if not all_slots:
                return {
                    "response": "I'm sorry, I don't see any availability matching your preferences in the next 2 weeks. Would you like me to provide coordinator contact information so they can help find a time that works?",
                    "new_state": "prescreening_complete",
                    "metadata": {}
                }

            # Filter by preferences
            filtered_slots = all_slots

            # Filter by time of day
            time_pref = preferences.get('time_of_day')
            if time_pref == 'morning':
                filtered_slots = [s for s in filtered_slots if datetime.fromisoformat(s['datetime']).hour < 12]
            elif time_pref == 'afternoon':
                filtered_slots = [s for s in filtered_slots if 12 <= datetime.fromisoformat(s['datetime']).hour < 17]
            elif time_pref == 'evening':
                filtered_slots = [s for s in filtered_slots if datetime.fromisoformat(s['datetime']).hour >= 17]

            # Filter by days of week
            days_pref = preferences.get('days_of_week')
            if days_pref:
                filtered_slots = [
                    s for s in filtered_slots
                    if datetime.fromisoformat(s['datetime']).strftime('%A') in days_pref
                ]

            if not filtered_slots:
                return {
                    "response": f"""I don't see any availability matching those specific preferences. Here are the next available times:

{chr(10).join([f"   • {s['display']}" for s in all_slots[:3]])}

Would any of these work, or would you like coordinator contact information?""",
                    "new_state": "awaiting_booking_confirmation",
                    "metadata": {}
                }

            # Show filtered slots
            slots_text = "\n".join([f"   • {s['display']}" for s in filtered_slots[:3]])

            # Update context with new slots
            context.presented_slots = filtered_slots[:3]

            return {
                "response": f"""Based on your preferences, here are the best available times:

{slots_text}

Would you like to schedule one of these times?""",
                "new_state": "awaiting_booking_confirmation",
                "metadata": {"filtered_by_preferences": True}
            }

        except Exception as e:
            logger.error(f"Error processing preferred times: {e}", exc_info=True)
            # Fallback: show original slots
            return {
                "response": f"""Let me show you the next available times:

{chr(10).join([f"   • {s['display']}" for s in context.presented_slots[:3]])}

Would you like to schedule one of these?""",
                "new_state": "awaiting_booking_confirmation",
                "metadata": {}
            }

    async def _handle_alternative_selection(self, context: ConversationContext, message: str) -> Dict[str, Any]:
        """Handle user selecting from alternative trial conditions"""
        logger.info(f"🎯 ALTERNATIVE SELECTION - Session: {context.session_id}, Message: '{message}'")

        try:
            # Get alternative conditions and trials from metadata
            alternative_conditions = context.metadata.get('alternative_conditions', [])
            alternative_trials = context.metadata.get('alternative_trials', [])
            location = context.metadata.get('location', context.focus_location)

            if not alternative_conditions or not alternative_trials:
                logger.error("No alternative conditions or trials in metadata")
                return {
                    "response": "I'm sorry, I lost track of which trials were available. Could you please search again?",
                    "new_state": "initial",
                    "metadata": {}
                }

            logger.info(f"   Available alternatives: {alternative_conditions}")
            logger.info(f"   User message: '{message}'")

            # Parse user's selection
            user_message_lower = message.lower().strip()
            selected_condition = None

            # Check for exact or partial match
            for condition in alternative_conditions:
                condition_lower = condition.lower()
                # Exact match
                if user_message_lower == condition_lower:
                    selected_condition = condition
                    break
                # Partial match (user typed part of condition name)
                elif condition_lower in user_message_lower or user_message_lower in condition_lower:
                    selected_condition = condition
                    break
                # Check individual words (e.g., user says "fibromyalgia" for "Chronic Fibromyalgia")
                elif any(word in condition_lower for word in user_message_lower.split() if len(word) > 3):
                    selected_condition = condition
                    break

            # If only one condition and user said "yes" or similar
            if not selected_condition and len(alternative_conditions) == 1:
                affirmative_words = ['yes', 'y', 'yeah', 'yep', 'sure', 'ok', 'okay']
                if user_message_lower in affirmative_words:
                    selected_condition = alternative_conditions[0]

            if not selected_condition:
                # Couldn't determine selection - ask again
                if len(alternative_conditions) == 1:
                    response = f"Would you like to check your eligibility for **{alternative_conditions[0]}**? (yes/no)"
                elif len(alternative_conditions) == 2:
                    response = f"Which condition: **{alternative_conditions[0]}** or **{alternative_conditions[1]}**?"
                else:
                    conditions_str = ", ".join([f"**{c}**" for c in alternative_conditions[:-1]]) + f", or **{alternative_conditions[-1]}**"
                    response = f"Which condition would you like to check: {conditions_str}?"

                return {
                    "response": f"I didn't catch that. {response}",
                    "new_state": "awaiting_alternative_selection",
                    "metadata": context.metadata
                }

            logger.info(f"✅ Selected condition: {selected_condition}")

            # Filter trials to only those matching the selected condition
            matching_trials = []
            for trial in alternative_trials:
                trial_conditions = trial.get('conditions', '')
                if selected_condition in trial_conditions:
                    matching_trials.append(trial)

            if not matching_trials:
                logger.error(f"No trials found for selected condition: {selected_condition}")
                return {
                    "response": f"I'm sorry, I couldn't find the trials for {selected_condition}. Let me search again for you.",
                    "new_state": "initial",
                    "metadata": {}
                }

            logger.info(f"   Found {len(matching_trials)} trials for {selected_condition}")

            # Update context with selected condition and trials
            context.focus_condition = selected_condition
            context.focus_location = location
            context.last_shown_trials = matching_trials[:10]  # Store up to 10

            # Format trials response
            response = self._format_trials_response(matching_trials, selected_condition, location)

            return {
                "response": response,
                "new_state": "trials_shown",
                "metadata": {
                    "trials_found": len(matching_trials),
                    "from_alternative_selection": True,
                    "original_search_failed": True
                }
            }

        except Exception as e:
            logger.error(f"Error handling alternative selection: {e}", exc_info=True)
            return {
                "response": "I'm sorry, I encountered an error. Could you please search for trials again?",
                "new_state": "initial",
                "metadata": {"error": str(e)}
            }


