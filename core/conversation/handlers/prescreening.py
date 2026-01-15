"""
Handler for prescreening flow management.

This handler processes prescreening answers and manages the flow of
eligibility questions and evaluations.
"""

import logging
from typing import Dict, Any, List, Optional

from core.conversation.handlers.base import BaseHandler, HandlerResponse
from core.conversation.understanding import IntentType, DetectedIntent, ExtractedEntity, EntityType
from core.conversation.context import ConversationContext
from core.conversation.orchestration import ConversationStateManager
from core.eligibility.prescreening_controller import PrescreeningController
from core.chat.answer_parser import AnswerParser
from core.chat.sync_gemini_responder import SyncGeminiResponder
from core.services.trial_search import trial_search
from models.schemas import ConversationState, EligibilityResult, PrescreeningSession
from core.conversation.state_config import state_config

logger = logging.getLogger(__name__)


class PrescreeningHandler(BaseHandler):
    """
    Handles prescreening flow and answers.
    
    This handler manages:
    - Processing prescreening answers
    - Advancing through prescreening questions
    - Handling interruptions and clarifications
    - Evaluating eligibility results
    """
    
    def __init__(self):
        super().__init__()
        self.prescreening_controller = PrescreeningController()
        self.answer_parser = AnswerParser()
        self.gemini_responder = SyncGeminiResponder()
    
    def _get_condition_trial_reference(self, condition: str) -> str:
        """Generate a user-friendly condition-based reference for a trial"""
        if not condition:
            return "Clinical Trial"
        
        condition_clean = condition.lower().strip()
        if 'gout' in condition_clean:
            return "Gout Trial"
        elif 'migraine' in condition_clean:
            return "Migraine Trial" 
        elif 'diabetes' in condition_clean or 'diabetic' in condition_clean:
            if 'gastroparesis' in condition_clean:
                return "Diabetic Gastroparesis Trial"
            else:
                return "Diabetes Trial"
        elif 'cancer' in condition_clean:
            return "Cancer Trial"
        elif 'heart' in condition_clean or 'cardiac' in condition_clean:
            return "Heart Disease Trial"
        else:
            return f"{condition.title()} Trial"
    
    def can_handle(self, intent: DetectedIntent, context: ConversationContext) -> bool:
        """Check if this handler can process the intent"""
        # Handle all answer types for both prescreening and trial search setup
        answer_intents = [
            IntentType.AGE_ANSWER,
            IntentType.YES_NO_ANSWER,
            IntentType.NUMBER_ANSWER,
            IntentType.CONDITION_ANSWER,
            IntentType.LOCATION_ANSWER,
            IntentType.MEDICATION_ANSWER,
            IntentType.QUESTION_DURING_PRESCREENING,
        ]
        
        if intent.intent_type not in answer_intents:
            return False
        
        # Handle prescreening states
        in_prescreening = context.conversation_state in [
            ConversationState.PRESCREENING_ACTIVE.value,
            ConversationState.AWAITING_AGE.value,
            ConversationState.AWAITING_DIAGNOSIS.value,
            ConversationState.AWAITING_MEDICATIONS.value,
            ConversationState.AWAITING_FLARES.value,
        ]
        
        # Handle trial search setup states (location/condition gathering)
        in_trial_setup = context.conversation_state in [
            ConversationState.AWAITING_LOCATION.value,
            ConversationState.AWAITING_CONDITION.value,
        ]
        
        # Handle completion state for YES_NO_ANSWER (prescreening confirmations)
        completing_prescreening = (
            context.conversation_state == ConversationState.COMPLETED.value and 
            intent.intent_type == IntentType.YES_NO_ANSWER
        )
        
        # FIXED: Accept prescreening, trial setup, or YES_NO completion states
        return in_prescreening or in_trial_setup or completing_prescreening
    
    def handle(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
              context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle prescreening answer, trial setup answer, or question"""
        
        logger.info(f"PrescreeningHandler processing {intent.intent_type} in state {context.conversation_state}")
        
        # Handle questions during prescreening
        if intent.intent_type == IntentType.QUESTION_DURING_PRESCREENING:
            return self._handle_prescreening_question(entities, context, state_manager)
        
        # Handle YES_NO_ANSWER in COMPLETED state (prescreening confirmation)
        if (context.conversation_state == ConversationState.COMPLETED.value and 
            intent.intent_type == IntentType.YES_NO_ANSWER):
            return self._handle_prescreening_completion(intent, entities, context, state_manager)
        
        # Check if this is trial search setup (location/condition gathering) or active prescreening
        in_prescreening = context.conversation_state in [
            ConversationState.PRESCREENING_ACTIVE.value,
            ConversationState.AWAITING_AGE.value,
            ConversationState.AWAITING_DIAGNOSIS.value,
            ConversationState.AWAITING_MEDICATIONS.value,
            ConversationState.AWAITING_FLARES.value,
        ]
        
        if in_prescreening or context.prescreening_data:
            # Active prescreening - use existing prescreening logic
            answer_value = self._extract_answer_value(intent, entities, context)
            
            if answer_value is None:
                return self._handle_unclear_answer(intent, context)
            
            return self._process_prescreening_answer(answer_value, context, state_manager)
        else:
            # Trial setup - handle location/condition answers for trial search OR eligibility requests
            if intent.intent_type == IntentType.LOCATION_ANSWER:
                return self._handle_location_answer(intent, entities, context, state_manager)
            elif intent.intent_type == IntentType.CONDITION_ANSWER:
                return self._handle_condition_answer(intent, entities, context, state_manager)
            elif intent.intent_type == IntentType.YES_NO_ANSWER:
                # Handle eligibility requests like "Yes" after "Would you like to check eligibility?"
                return self._handle_eligibility_confirmation(intent, entities, context, state_manager)
            else:
                # Fallback for other answers during trial setup
                return HandlerResponse(
                    success=False,
                    message="I didn't understand that answer. Could you please rephrase?",
                    actions=[],
                    metadata={"error": "unsupported_answer_type_in_trial_setup"}
                )
    
    def _extract_answer_value(self, intent: DetectedIntent,
                            entities: Dict[EntityType, ExtractedEntity],
                            context: ConversationContext) -> Optional[Any]:
        """Extract the answer value based on intent type"""
        
        raw_message = intent.original_message or intent.matched_pattern or ""
        logger.info(f"Extracting answer from: '{raw_message}' for intent {intent.intent_type} in state {context.conversation_state}")
        
        # First try entity-based extraction
        if intent.intent_type == IntentType.AGE_ANSWER:
            logger.info(f"Entities available for age: {list(entities.keys())}")
            if EntityType.AGE in entities:
                age_value = entities[EntityType.AGE].value
                logger.info(f"Found age entity: {age_value}")
                return age_value
            elif EntityType.NUMBER in entities:
                number_value = entities[EntityType.NUMBER].value
                logger.info(f"Found number entity for age: {number_value}")
                return number_value
            else:
                logger.warning(f"AGE_ANSWER intent but no AGE or NUMBER entities found. Available: {list(entities.keys())}")
                
        elif intent.intent_type == IntentType.YES_NO_ANSWER:
            if EntityType.BOOLEAN in entities:
                logger.info(f"Found boolean entity: {entities[EntityType.BOOLEAN].normalized_value}")
                return entities[EntityType.BOOLEAN].normalized_value
                
        elif intent.intent_type == IntentType.NUMBER_ANSWER:
            if EntityType.NUMBER in entities:
                logger.info(f"Found number entity: {entities[EntityType.NUMBER].value}")
                return entities[EntityType.NUMBER].value
                
        elif intent.intent_type == IntentType.MEDICATION_ANSWER:
            if EntityType.MEDICATION in entities:
                return entities[EntityType.MEDICATION].value
            elif EntityType.BOOLEAN in entities:
                # Might be answering yes/no to medication question
                return entities[EntityType.BOOLEAN].normalized_value
                
        elif intent.intent_type == IntentType.CONDITION_ANSWER:
            if EntityType.CONDITION in entities:
                return entities[EntityType.CONDITION].normalized_value
                
        elif intent.intent_type == IntentType.LOCATION_ANSWER:
            if EntityType.LOCATION in entities:
                return entities[EntityType.LOCATION].normalized_value
        
        # Enhanced fallback parsing - try multiple approaches
        logger.info(f"No entity found, trying pattern extraction from: '{raw_message}'")
        
        # Try answer parser with context
        if context.current_question_key:
            try:
                parsed = self.answer_parser.parse_answer(raw_message, context.current_question_key)
                if parsed is not None:
                    logger.info(f"Answer parser extracted: {parsed}")
                    return parsed
            except Exception as e:
                logger.debug(f"Answer parser failed: {e}")
        
        # Try direct pattern extraction based on current state
        current_state = context.conversation_state
        
        if current_state == ConversationState.AWAITING_AGE.value:
            # Extract age from messages like "I'm 35", "35 years old", "I am 35", or just "35"
            import re
            logger.info(f"Trying age extraction from: '{raw_message}'")
            # More comprehensive age pattern matching
            age_patterns = [
                r"(?:i'?m|i am|im)\s+(\d{1,3})",  # "I'm 35", "I am 35"
                r"(\d{1,3})\s*(?:years?\s*old)?",  # "35", "35 years old"
                r"age\s*(?:is|:)?\s*(\d{1,3})",   # "age is 35", "age: 35"
            ]
            
            for pattern in age_patterns:
                age_match = re.search(pattern, raw_message.lower())
                if age_match:
                    age = int(age_match.group(1))
                    if 1 <= age <= 120:  # Reasonable age range
                        logger.info(f"Extracted age {age} from message: {raw_message}")
                        return age
        
        elif current_state == ConversationState.AWAITING_DIAGNOSIS.value:
            # Extract yes/no from various formats
            message_lower = raw_message.lower()
            if any(word in message_lower for word in ["yes", "yeah", "yep", "y", "i have", "i do"]):
                return True
            elif any(word in message_lower for word in ["no", "nope", "n", "i don't", "i haven't"]):
                return False
        
        elif current_state in [ConversationState.AWAITING_MEDICATIONS.value, ConversationState.AWAITING_FLARES.value]:
            # Extract numbers or yes/no
            import re
            # Try number first
            number_match = re.search(r'\b(\d+)\b', raw_message)
            if number_match:
                return int(number_match.group(1))
            
            # Try yes/no
            message_lower = raw_message.lower()
            if any(word in message_lower for word in ["yes", "yeah", "yep", "y"]):
                return True
            elif any(word in message_lower for word in ["no", "nope", "n"]):
                return False
        
        # Final fallback: try intent metadata
        if "answer" in intent.metadata:
            return intent.metadata["answer"]
        
        return None
    
    def _process_prescreening_answer(self, answer_value: Any,
                                   context: ConversationContext,
                                   state_manager: ConversationStateManager) -> HandlerResponse:
        """Process a prescreening answer and advance the flow"""
        
        try:
            current_state = context.conversation_state
            logger.info(f"Processing prescreening answer: {answer_value} in state: {current_state}")
            
            # Store the answer in context
            if not hasattr(context, 'prescreening_answers'):
                context.prescreening_answers = {}
            
            # Map state to question type and store answer
            if current_state == ConversationState.AWAITING_AGE.value:
                logger.info(f"Processing age answer: {answer_value}")
                context.prescreening_answers['age'] = answer_value
                
                # Validate age
                if answer_value < 18:
                    logger.info(f"User age {answer_value} is under 18, disqualifying")
                    return HandlerResponse(
                        success=True,
                        message="I appreciate your interest in clinical trials. Unfortunately, you must be at least 18 years old to participate in most clinical trials. Please speak with your healthcare provider about other treatment options that may be appropriate for you.",
                        metadata={"prescreening_complete": True, "eligible": False, "disqualification_reason": "age_under_18"},
                        next_state=ConversationState.COMPLETED.value
                    )
                
                next_question = "Have you been diagnosed with gout by a physician?"
                next_state = ConversationState.AWAITING_DIAGNOSIS
                
            elif current_state == ConversationState.AWAITING_DIAGNOSIS.value:
                logger.info(f"Processing diagnosis answer: {answer_value}")
                context.prescreening_answers['diagnosis'] = answer_value
                if answer_value:  # If yes to diagnosis
                    next_question = "Are you currently taking any medications for gout?"
                    next_state = ConversationState.AWAITING_MEDICATIONS
                else:
                    # If no diagnosis, they're not eligible
                    logger.info("User has no gout diagnosis, disqualifying")
                    return HandlerResponse(
                        success=True,
                        message="Based on your responses, you may not be eligible for this gout trial since you haven't been diagnosed with gout. Please consult with a healthcare provider about your symptoms.",
                        metadata={"prescreening_complete": True, "eligible": False, "disqualification_reason": "no_diagnosis"},
                        next_state=ConversationState.COMPLETED.value
                    )
                    
            elif current_state == ConversationState.AWAITING_MEDICATIONS.value:
                logger.info(f"Processing medications answer: {answer_value}")
                context.prescreening_answers['medications'] = answer_value
                next_question = "How many gout flare-ups have you experienced in the past 12 months? Please provide a number."
                next_state = ConversationState.AWAITING_FLARES
                
            elif current_state == ConversationState.AWAITING_FLARES.value:
                logger.info(f"Processing flares answer: {answer_value}")
                context.prescreening_answers['flares'] = answer_value
                # Complete prescreening
                return self._complete_prescreening(context, state_manager)
                
            elif current_state == ConversationState.PRESCREENING_ACTIVE.value:
                # Handle generic prescreening active state
                logger.warning(f"In PRESCREENING_ACTIVE state, transitioning to AWAITING_AGE")
                next_question = "What is your age?"
                next_state = ConversationState.AWAITING_AGE
                
            else:
                # Unknown state
                logger.error(f"Unknown prescreening state: {current_state}")
                return HandlerResponse(
                    success=False,
                    message="I encountered an error processing your answer. Let's try again.",
                    metadata={"error": "unknown_prescreening_state", "state": current_state}
                )
            
            # Transition to next state
            if next_state:
                state_manager.transition_to(next_state, reason="Prescreening progression")
            
            return HandlerResponse(
                success=True,
                message=next_question,
                metadata={
                    "prescreening_active": True,
                    "answers_collected": len(context.prescreening_answers),
                    "current_question": next_state.value if next_state else current_state
                },
                next_state=next_state.value if next_state else None
            )
            
        except Exception as e:
            logger.error(f"Error processing prescreening answer: {str(e)}")
            return HandlerResponse(
                success=False,
                message="I encountered an error processing your answer. Let's try again.",
                metadata={"error": str(e)}
            )
    
    def _complete_prescreening(self, context: ConversationContext, 
                             state_manager: ConversationStateManager) -> HandlerResponse:
        """Complete prescreening and evaluate eligibility"""
        
        # Simple eligibility logic based on collected answers
        answers = getattr(context, 'prescreening_answers', {})
        
        age = answers.get('age', 0)
        diagnosis = answers.get('diagnosis', False)
        flares = answers.get('flares', 0)
        
        # Basic eligibility criteria for gout trial
        eligible = True
        reasons = []
        
        if age < 18:
            eligible = False
            reasons.append("Must be 18 years or older")
        
        if not diagnosis:
            eligible = False
            reasons.append("Must have physician diagnosis of gout")
        
        if flares < 2:
            eligible = False
            reasons.append("Must have had at least 2 gout flare-ups in the past 12 months")
        
        # Transition to completed state
        state_manager.transition_to(ConversationState.COMPLETED, reason="Prescreening completed")
        
        if eligible:
            message = (
                f"Great news! Based on your responses, you appear to meet the initial eligibility criteria "
                f"for this gout trial. You are {age} years old, have been diagnosed with gout, and have "
                f"experienced {flares} flare-ups in the past year.\n\n"
                "The next step would be to contact the research team for a more detailed screening. "
                "Would you like me to provide the contact information?"
            )
        else:
            message = (
                f"Based on your responses, you may not meet the eligibility criteria for this trial. "
                f"Reasons: {', '.join(reasons)}.\n\n"
                "Please consult with your healthcare provider about other treatment options or trials "
                "that might be appropriate for you."
            )
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={
                "prescreening_complete": True,
                "eligible": eligible,
                "eligibility_reasons": reasons if not eligible else [],
                "collected_answers": answers
            },
            next_state=ConversationState.COMPLETED.value
        )
    
    def _handle_prescreening_complete(self, result: Dict[str, Any],
                                    context: ConversationContext,
                                    state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle completion of prescreening"""
        
        # Evaluate eligibility
        collected_data = result.get("collected_data", {})
        trial_id = context.trial_id
        
        # For now, create a simple eligibility result based on collected data
        # In production, this would use the prescreening_controller's evaluate method
        eligibility_result = self._simple_eligibility_evaluation(
            trial_id=trial_id,
            user_responses=collected_data
        )
        
        # Generate enhanced response
        base_message = self._format_eligibility_result(eligibility_result, context)
        
        # Enhance with OpenAI if available
        try:
            enhanced_message = self.gemini_responder.enhance_eligibility_result(
                base_result=base_message,
                eligibility=eligibility_result,
                trial_name=context.trial_name,
                condition=context.focus_condition
            )
            message = enhanced_message
        except:
            message = base_message
        
        # Transition to completed state
        state_manager.transition_to(
            ConversationState.COMPLETED,
            reason="Prescreening completed"
        )
        
        # Update context
        actions = [
            {
                "type": "update_context",
                "data": {
                    "prescreening_active": False,
                    "prescreening_complete": True,
                    "eligibility_result": {
                        "eligible": eligibility_result.eligible,
                        "confidence": eligibility_result.confidence,
                        "criteria_met": eligibility_result.criteria_met,
                        "criteria_not_met": eligibility_result.criteria_not_met
                    }
                }
            },
            {
                "type": "log_eligibility_result",
                "data": {
                    "trial_id": trial_id,
                    "eligible": eligibility_result.eligible,
                    "confidence": eligibility_result.confidence
                }
            }
        ]
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={
                "prescreening_complete": True,
                "eligible": eligibility_result.eligible,
                "confidence": eligibility_result.confidence
            },
            next_state=ConversationState.COMPLETED.value,
            actions=actions
        )
    
    def _handle_prescreening_question(self, entities: Dict[EntityType, ExtractedEntity],
                                    context: ConversationContext,
                                    state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle questions asked during prescreening"""
        
        # User is asking a question during prescreening
        message = "I understand you have a question. Let me help with that, "
        message += "and then we can continue with the eligibility check.\n\n"
        message += "What would you like to know?"
        
        # Note: In a full implementation, this would parse the question
        # and provide relevant information before continuing
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={
                "handling_question": True,
                "will_continue_prescreening": True
            }
        )
    
    def _handle_unclear_answer(self, intent: DetectedIntent,
                             context: ConversationContext) -> HandlerResponse:
        """Handle unclear or unparseable answers"""
        
        current_state = context.conversation_state
        
        # Provide state-specific clarification
        if current_state == ConversationState.AWAITING_AGE.value:
            message = "I need to know your age for this trial. Please tell me how old you are."
        elif current_state == ConversationState.AWAITING_DIAGNOSIS.value:
            message = "Please answer with 'yes' or 'no' - have you been diagnosed with this condition?"
        elif current_state == ConversationState.AWAITING_MEDICATIONS.value:
            message = "Are you currently taking any medications? Please answer 'yes' or 'no'."
        elif current_state == ConversationState.AWAITING_FLARES.value:
            message = "How many flares or episodes have you had? Please provide a number."
        else:
            message = "I didn't quite understand your answer. Could you please rephrase it?"
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={"needs_clarification": True}
        )
    
    def _get_state_for_question(self, question_key: Optional[str]) -> Optional[ConversationState]:
        """Get appropriate state for question type"""
        return state_config.get_state_for_question(question_key)
    
    def _format_eligibility_result(self, result: EligibilityResult,
                                 context: ConversationContext) -> str:
        """Format eligibility result for display"""
        
        # Use condition-based reference instead of trial name
        trial_ref = "this clinical trial"
        if context.focus_condition:
            condition_clean = context.focus_condition.lower().strip()
            if 'gout' in condition_clean:
                trial_ref = "the gout trial"
            elif 'migraine' in condition_clean:
                trial_ref = "the migraine trial"
            elif 'diabetes' in condition_clean or 'diabetic' in condition_clean:
                if 'gastroparesis' in condition_clean:
                    trial_ref = "the diabetic gastroparesis trial"
                else:
                    trial_ref = "the diabetes trial"
            else:
                trial_ref = f"the {condition_clean} trial"
        
        if result.eligible:
            message = f"✅ **Good news!** Based on your responses, you appear to be eligible for {trial_ref}.\n\n"
            
            if result.confidence < 1.0:
                message += f"**Confidence level:** {int(result.confidence * 100)}%\n\n"
            
            message += "**Criteria you meet:**\n"
            for criterion in result.criteria_met[:5]:  # Show top 5
                message += f"- {criterion}\n"
            
            if len(result.criteria_unknown) > 0:
                message += f"\n**Note:** There are {len(result.criteria_unknown)} additional criteria "
                message += "that will need to be verified by the study team.\n"
            
            message += f"\n{result.next_steps}"
            
        else:
            message = f"Based on your responses, you may not be eligible for {trial_ref} at this time.\n\n"
            
            if result.criteria_not_met:
                message += "**Criteria not met:**\n"
                for criterion in result.criteria_not_met[:3]:  # Show top 3
                    message += f"- {criterion}\n"
            
            message += f"\n{result.recommendation}\n"
            
            # Suggest alternatives
            message += "\nWould you like me to help you find other trials you might qualify for?"
        
        return message
    
    def _simple_eligibility_evaluation(self, trial_id: int, 
                                     user_responses: Dict[str, Any]) -> EligibilityResult:
        """Simple eligibility evaluation based on basic criteria"""
        
        # Basic eligibility checks
        criteria_met = []
        criteria_not_met = []
        criteria_unknown = []
        
        # Age check
        age = user_responses.get("age")
        if age:
            if 18 <= age <= 75:
                criteria_met.append("Age between 18 and 75 years")
            else:
                criteria_not_met.append("Age requirement (18-75 years)")
        else:
            criteria_unknown.append("Age verification")
        
        # Diagnosis check
        diagnosis = user_responses.get("diagnosis")
        if diagnosis is True:
            criteria_met.append("Confirmed diagnosis of condition")
        elif diagnosis is False:
            criteria_not_met.append("Must have confirmed diagnosis")
        else:
            criteria_unknown.append("Diagnosis confirmation")
        
        # Determine eligibility
        eligible = len(criteria_not_met) == 0 and len(criteria_met) > 0
        confidence = len(criteria_met) / (len(criteria_met) + len(criteria_not_met) + len(criteria_unknown)) if (len(criteria_met) + len(criteria_not_met) + len(criteria_unknown)) > 0 else 0.5
        
        # Create result
        return EligibilityResult(
            eligible=eligible,
            confidence=confidence,
            criteria_met=criteria_met,
            criteria_not_met=criteria_not_met,
            criteria_unknown=criteria_unknown,
            next_steps="The study team will contact you within 2-3 business days to discuss next steps." if eligible else "",
            recommendation="Don't worry - there may be other trials that are a better fit for you." if not eligible else ""
        )
    
    # ========== TRIAL SETUP METHODS (from AnswerHandler) ==========
    
    def _handle_location_answer(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
                               context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle location answer for trial search setup"""
        
        # Extract location from entities
        location = None
        if EntityType.LOCATION in entities:
            location = entities[EntityType.LOCATION].normalized_value
        else:
            # Try to extract from the raw message
            location = intent.metadata.get('raw_location', intent.matched_pattern)
        
        if not location:
            return HandlerResponse(
                success=False,
                message="I didn't catch the location. Could you please tell me what city or state you're in?",
                actions=[],
                metadata={"error": "location_not_found"}
            )
        
        # Update context with location
        context.focus_location = location
        context.mentioned_locations.add(location)
        
        # Determine next step based on what we have
        if context.focus_condition:
            # We have both condition and location
            # Check if this is for eligibility check or trial search
            if hasattr(context, 'metadata') and context.metadata.get('eligibility_intent'):
                # Start prescreening flow
                return self._start_eligibility_prescreening(context.focus_condition, location, context, state_manager)
            else:
                # Search for trials
                return self._search_trials_and_respond(context, state_manager)
        else:
            # We have location but need condition
            state_manager.transition_to(ConversationState.AWAITING_CONDITION)
            return HandlerResponse(
                success=True,
                message=f"Great! I'll look for clinical trials in {location}. What medical condition are you interested in?",
                actions=[],
                metadata={"location_captured": location}
            )
    
    def _handle_condition_answer(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
                                context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle condition answer for trial search setup"""
        
        # Extract condition from entities
        condition = None
        if EntityType.CONDITION in entities:
            condition = entities[EntityType.CONDITION].normalized_value
        else:
            # Try to extract from raw message
            condition = intent.metadata.get('raw_condition', intent.matched_pattern)
        
        if not condition:
            return HandlerResponse(
                success=False,
                message="I didn't catch the medical condition. Could you please tell me what condition you're interested in?",
                actions=[],
                metadata={"error": "condition_not_found"}
            )
        
        # Update context with condition
        context.focus_condition = condition
        context.mentioned_conditions.add(condition)
        
        # Determine next step based on what we have and intent
        if context.focus_location:
            # We have both condition and location
            # Check if this is for eligibility check or trial search
            if hasattr(context, 'metadata') and context.metadata.get('eligibility_intent'):
                # Start prescreening flow
                return self._start_eligibility_prescreening(condition, context.focus_location, context, state_manager)
            else:
                # Search for trials
                return self._search_trials_and_respond(context, state_manager)
        else:
            # We have condition but need location
            state_manager.transition_to(ConversationState.AWAITING_LOCATION)
            
            # Adjust message based on intent
            if hasattr(context, 'metadata') and context.metadata.get('eligibility_intent'):
                message = f"Great! I'll help you check your eligibility for {condition} trials. What city or state are you located in?"
            else:
                message = f"Thanks! I'll look for {condition} trials. What city or state are you located in?"
            
            return HandlerResponse(
                success=True,
                message=message,
                actions=[],
                metadata={"condition_captured": condition}
            )
    
    def _search_trials_and_respond(self, context: ConversationContext, 
                                  state_manager: ConversationStateManager) -> HandlerResponse:
        """Search for trials with condition and location"""
        
        try:
            # Search for trials
            trials = trial_search.search_trials(
                condition=context.focus_condition,
                location=context.focus_location
            )
            
            if not trials:
                return HandlerResponse(
                    success=True,
                    message=f"I couldn't find any clinical trials for {context.focus_condition} in {context.focus_location}. Would you like me to search in nearby areas?",
                    actions=[],
                    metadata={"trials_found": 0}
                )
            
            # Store trials in context
            context.last_shown_trials = trials[:5]  # Show max 5
            
            # Format response
            message = f"I found {len(trials)} clinical trial{'s' if len(trials) > 1 else ''} for {context.focus_condition} in {context.focus_location}:\n\n"
            
            for i, trial in enumerate(trials[:3], 1):  # Show first 3
                # Use condition-based reference instead of trial name
                condition = trial.get('conditions', context.focus_condition or 'Clinical')
                condition_ref = self._get_condition_trial_reference(condition)
                message += f"**{i}. {condition_ref}**\n"
                if trial.get('brief_summary'):
                    message += f"   {trial['brief_summary'][:150]}...\n"
                message += "\n"
            
            if len(trials) > 3:
                message += f"... and {len(trials) - 3} more trials.\n\n"
            
            message += "Would you like me to check if you might be eligible for any of these trials?"
            
            # Transition to trial selection state
            state_manager.transition_to(ConversationState.TRIALS_SHOWN)
            
            return HandlerResponse(
                success=True,
                message=message,
                actions=[],
                metadata={
                    "trials_found": len(trials),
                    "condition": context.focus_condition,
                    "location": context.focus_location
                }
            )
            
        except Exception as e:
            logger.error(f"Error searching trials: {str(e)}")
            return HandlerResponse(
                success=False,
                message="I encountered an error while searching for trials. Please try again.",
                actions=[],
                metadata={"error": "search_failed"}
            )
    
    def _start_eligibility_prescreening(self, condition: str, location: str, 
                                       context: ConversationContext,
                                       state_manager: ConversationStateManager) -> HandlerResponse:
        """Start eligibility prescreening flow for a condition and location"""
        
        try:
            # Search for matching trials
            trials = trial_search.search_trials(condition=condition, location=location)
            
            if not trials:
                return HandlerResponse(
                    success=True,
                    message=f"I couldn't find any clinical trials for {condition} in {location}. Would you like me to search in nearby areas?",
                    actions=[],
                    metadata={"no_trials_found": True}
                )
            
            # Get the first matching trial for prescreening
            trial = trials[0]
            trial_id = trial.get('id')
            trial_ref = self._get_condition_trial_reference(trial.get('conditions', condition))
            
            logger.info(f"Starting prescreening for trial {trial_id}: {trial_ref}")
            
            # Store trial info in context
            context.current_trial = {
                'id': trial_id,
                'name': trial_ref,
                'condition': condition,
                'location': location
            }
            
            # Initialize prescreening data
            if not context.prescreening_data:
                context.prescreening_data = {}
            
            # Transition to prescreening state and ask first question
            state_manager.transition_to(ConversationState.AWAITING_AGE, reason="Starting eligibility prescreening")
            
            message = f"Great! I'll help you check your eligibility for the {condition} trial in {location}. "
            message += "Let me ask you a few questions. First, what is your age?"
            
            return HandlerResponse(
                success=True,
                message=message,
                actions=[],
                metadata={
                    "prescreening_started": True,
                    "trial_id": trial_id,
                    "prescreening_step": "age"
                }
            )
            
        except Exception as e:
            logger.error(f"Error starting prescreening: {str(e)}")
            return HandlerResponse(
                success=False,
                message="I encountered an error while setting up the eligibility check. Please try again.",
                actions=[],
                metadata={"error": "prescreening_setup_failed"}
            )
    
    def _handle_eligibility_confirmation(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
                                        context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle eligibility confirmation like 'Yes' after 'Would you like to check eligibility?'"""
        
        # Extract yes/no response
        response = None
        if EntityType.BOOLEAN in entities:
            response = entities[EntityType.BOOLEAN].normalized_value
        else:
            # Parse from message
            raw_message = intent.matched_pattern or intent.metadata.get("raw_message", "")
            message_lower = raw_message.lower().strip()
            
            if any(word in message_lower for word in ["yes", "yeah", "yep", "y", "sure", "ok", "okay"]):
                response = True
            elif any(word in message_lower for word in ["no", "nope", "n", "not now"]):
                response = False
        
        if response is True:
            # User wants to check eligibility
            # We should have trial information from context
            if hasattr(context, 'last_shown_trials') and context.last_shown_trials:
                # Use the trial that was just shown
                trial = context.last_shown_trials[0]
                condition = trial.get('conditions', 'the trial')
                location = context.focus_location or "your area"
                
                return self._start_eligibility_prescreening(condition, location, context, state_manager)
            
            elif context.focus_condition and context.focus_location:
                # Use the condition and location from context
                return self._start_eligibility_prescreening(context.focus_condition, context.focus_location, context, state_manager)
            
            else:
                # Need more information
                return HandlerResponse(
                    success=True,
                    message="I'd be happy to check your eligibility. What condition are you interested in, and what's your location?",
                    actions=[],
                    metadata={"eligibility_request": True}
                )
        
        elif response is False:
            # User doesn't want to check eligibility
            return HandlerResponse(
                success=True,
                message="No problem! Is there anything else I can help you with regarding clinical trials?",
                actions=[],
                metadata={"eligibility_declined": True}
            )
        
        else:
            # Unclear response
            return HandlerResponse(
                success=False,
                message="I didn't understand that answer. Would you like me to check your eligibility for this trial? Please answer yes or no.",
                actions=[],
                metadata={"unclear_eligibility_response": True}
            )
    
    def _handle_prescreening_completion(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
                                       context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle YES_NO_ANSWER when prescreening is completed"""
        
        logger.info("Handling prescreening completion with YES_NO_ANSWER")
        
        # Extract the yes/no response
        answer_value = self._extract_answer_value(intent, entities, context)
        
        if answer_value is True:
            # User wants to start prescreening
            message = (
                "Great! I'll help you check your eligibility. "
                "Let me start by asking a few questions.\n\n"
                "First, what is your age?"
            )
            
            # Transition to awaiting age
            state_manager.transition_to(
                ConversationState.AWAITING_AGE,
                reason="Starting prescreening flow"
            )
            
            # Initialize prescreening data
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "prescreening_data": {
                            "started": True,
                            "current_question": "age"
                        }
                    }
                },
                {
                    "type": "log_prescreening_start",
                    "data": {
                        "user_confirmed": True,
                        "trial_context": bool(context.trial_id or context.trial_name)
                    }
                }
            ]
            
            return HandlerResponse(
                success=True,
                message=message,
                next_state=ConversationState.AWAITING_AGE.value,
                actions=actions,
                metadata={"prescreening_started": True}
            )
            
        elif answer_value is False:
            # User doesn't want prescreening
            message = (
                "No problem! If you change your mind later, just let me know and "
                "I can help you check your eligibility for clinical trials.\n\n"
                "Is there anything else I can help you with?"
            )
            
            # Reset to idle state
            state_manager.transition_to(
                ConversationState.IDLE,
                reason="User declined prescreening"
            )
            
            return HandlerResponse(
                success=True,
                message=message,
                next_state=ConversationState.IDLE.value,
                metadata={"prescreening_declined": True}
            )
            
        else:
            # Unclear response - ask again
            message = (
                "I didn't understand that answer. "
                "Would you like me to check your eligibility for clinical trials? "
                "Please answer yes or no."
            )
            
            return HandlerResponse(
                success=True,
                message=message,
                metadata={"unclear_completion_response": True}
            )