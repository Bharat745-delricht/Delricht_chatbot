"""Database-integrated prescreening controller using existing schema"""
import logging
import json
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime
from core.database import db
from core.chat.answer_parser import AnswerParser
from core.eligibility.question_templates import QuestionTemplates
from core.eligibility.criteria_parser import CriteriaParser
from models.schemas import ConversationState, EligibilityResult

logger = logging.getLogger(__name__)


class PrescreeningController:
    """Prescreening controller that uses existing database tables"""
    
    def __init__(self):
        self.parser = AnswerParser()
        self.templates = QuestionTemplates()
        self.criteria_parser = CriteriaParser()
    
    def _build_question_flow(self, condition: str, trial_id: int) -> List[str]:
        """Build dynamic question flow based on condition and trial requirements"""
        logger.info(f"[DEBUG] Building question flow for condition={condition}, trial_id={trial_id}")
        
        # Base questions for all conditions
        flow = ["age", "diagnosis", "medications"]
        logger.info(f"[DEBUG] Base flow: {flow}")
        
        # Add condition-specific questions from templates
        condition_questions = self.templates.get_condition_questions(condition)
        logger.info(f"[DEBUG] Found {len(condition_questions)} condition-specific questions for {condition}")
        for q in condition_questions:
            flow.append(q.key)
            logger.info(f"[DEBUG] Added condition question: {q.key} - {q.text[:50]}...")
        
        # Add trial-specific questions from trial_criteria
        try:
            criteria_questions = self.criteria_parser.get_trial_criteria_questions(trial_id)
            logger.info(f"[DEBUG] Found {len(criteria_questions)} trial-specific questions for trial {trial_id}")
            for q in criteria_questions:
                if q.key not in flow:  # Avoid duplicates
                    flow.append(q.key)
                    # Store the question in templates for later retrieval
                    self.templates.questions[q.key] = q
                    logger.info(f"[DEBUG] Added trial-specific question: {q.key} - {q.text[:50]}...")
        except Exception as e:
            logger.warning(f"Failed to load trial criteria questions: {str(e)}")
        
        # Add final common questions only if needed
        flow.append("other_conditions")
        
        logger.info(f"Final question flow for {condition} trial {trial_id}: {flow}")
        return flow
    
    async def start_prescreening(
        self, 
        user_id: str,
        session_id: str, 
        trial_id: Optional[int] = None,
        condition: Optional[str] = None,
        location: Optional[str] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """Start a new prescreening session using existing DB schema"""
        
        # Check if session already exists
        existing = db.execute_query("""
            SELECT id, status, questions_answered, total_questions
            FROM prescreening_sessions
            WHERE user_id = %s AND session_id = %s AND status = 'in_progress'
            ORDER BY started_at DESC
            LIMIT 1
        """, (user_id, session_id))
        
        if existing:
            # Resume existing session
            ps_id = existing[0]['id']
            return await self._continue_prescreening(ps_id, user_id, session_id)
        
        # Determine what we need to ask
        if not condition and not trial_id:
            # Need condition first
            question = self.templates.get_question("condition")
            intro = "I'd be happy to help you find clinical trials! "
            state = ConversationState.AWAITING_CONDITION
        elif not location:
            # Need location
            question = self.templates.get_question("location")
            intro = f"I'll help you check eligibility for {condition} trials. "
            state = ConversationState.AWAITING_LOCATION
        else:
            # Have condition and location, find matching trials
            if not trial_id:
                trials = self._find_matching_trials(condition, location)
                if trials:
                    # Use the first matching trial
                    trial_id = trials[0]['id']
                    return await self._initialize_prescreening(
                        user_id, session_id, trial_id, condition, location
                    )
                else:
                    # No matching trials found
                    return f"I couldn't find any {condition} trials in {location}. Would you like to search in a different location?", {
                        "no_trials_found": True,
                        "condition": condition,
                        "location": location
                    }
            else:
                # Have specific trial_id, start prescreening
                return await self._initialize_prescreening(
                    user_id, session_id, trial_id, condition, location
                )
        
        # Create prescreening session
        logger.info(f"Creating prescreening session for user_id={user_id}, trial_id={trial_id}")
        try:
            result = db.execute_query("""
                INSERT INTO prescreening_sessions 
                (user_id, session_id, trial_id, condition, location, status)
                VALUES (%s, %s, %s, %s, %s, 'in_progress')
                RETURNING id
            """, (user_id, session_id, trial_id, condition, location))
            logger.info(f"Prescreening session insert result: {result}")
            ps_id = result[0]['id']
            logger.info(f"Created prescreening session with id: {ps_id}")
        except Exception as e:
            logger.error(f"Error creating prescreening session: {str(e)}")
            logger.error(f"Parameters: user_id={user_id}, session_id={session_id}, trial_id={trial_id}")
            raise
        
        # Update context
        self._update_context(session_id, {
            "prescreening_active": True,
            "prescreening_session_id": ps_id,
            "current_state": state.value,
            "trial_id": trial_id,
            "condition": condition,
            "location": location
        })
        
        return intro + question.text, {"prescreening_session_id": ps_id, "state": state.value}
    
    async def handle_answer(
        self, 
        user_id: str,
        session_id: str,
        answer: str
    ) -> Tuple[str, Dict[str, Any]]:
        """Process an answer in the prescreening flow"""
        
        # Get active prescreening session
        session_data = db.execute_query("""
            SELECT ps.*, ct.trial_name, ct.conditions as trial_conditions
            FROM prescreening_sessions ps
            LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
            WHERE ps.user_id = %s AND ps.session_id = %s 
            AND ps.status = 'in_progress'
            ORDER BY ps.started_at DESC
            LIMIT 1
        """, (user_id, session_id))
        
        if not session_data:
            return "I don't have an active prescreening session. Would you like to check your eligibility for a trial?", {}
        
        session = session_data[0]
        ps_id = session['id']
        
        # Get context to determine current state
        context = self._get_context(session_id)
        try:
            current_state = ConversationState(context.get("current_state", ConversationState.IDLE.value))
        except ValueError:
            # If state is invalid, default to IDLE
            current_state = ConversationState.IDLE
        
        # Handle based on state
        if current_state == ConversationState.AWAITING_CONDITION:
            parsed = self.parser.parse_condition(answer)
            if parsed:
                # Update session with condition
                db.execute_update("""
                    UPDATE prescreening_sessions 
                    SET condition = %s
                    WHERE id = %s
                """, (parsed, ps_id))
                
                # Now ask for location
                question = self.templates.get_question("location")
                self._update_context(session_id, {"current_state": ConversationState.AWAITING_LOCATION.value})
                return f"Great! We have several {parsed} clinical trials available. {question.text}", {"state": "awaiting_location"}
            else:
                # Couldn't parse condition, ask for clarification
                return ("I'm not sure I understood which condition you're referring to. " +
                        "Could you please specify a medical condition like gout, diabetes, " +
                        "hypertension, or migraine?"), {"state": "awaiting_condition"}
                
        elif current_state == ConversationState.AWAITING_LOCATION:
            parsed = self.parser.parse_location(answer)
            if parsed:
                # Update session with location
                db.execute_update("""
                    UPDATE prescreening_sessions 
                    SET location = %s
                    WHERE id = %s
                """, (parsed, ps_id))
                
                # Find matching trials
                trials = self._find_matching_trials(session['condition'], parsed)
                if trials:
                    # Use first matching trial
                    trial_id = trials[0]['id']
                    db.execute_update("""
                        UPDATE prescreening_sessions 
                        SET trial_id = %s
                        WHERE id = %s
                    """, (trial_id, ps_id))
                    
                    # Start actual prescreening
                    return await self._initialize_prescreening(
                        user_id, session_id, trial_id, 
                        session['condition'], parsed
                    )
                else:
                    return f"I couldn't find any {session['condition']} trials in {parsed}. Would you like to search in a different location?", {}
            else:
                # Couldn't parse location, ask for clarification
                return ("I couldn't understand the location. Could you please tell me " +
                        "which city or state you're located in?"), {"state": "awaiting_location"}
        
        else:
            # Regular prescreening question
            return await self._process_prescreening_answer(
                ps_id, user_id, session_id, answer, context
            )
    
    async def _initialize_prescreening(
        self, 
        user_id: str,
        session_id: str,
        trial_id: int,
        condition: str,
        location: str
    ) -> Tuple[str, Dict[str, Any]]:
        """Initialize prescreening with dynamic question flow"""
        logger.info(f"[DEBUG] Initializing prescreening: user={user_id}, session={session_id}, trial_id={trial_id}, condition={condition}, location={location}")
        
        # Get trial info
        trial = db.execute_query("""
            SELECT * FROM clinical_trials WHERE id = %s
        """, (trial_id,))[0]
        logger.info(f"[DEBUG] Found trial: {trial['title'][:50]}... for condition: {trial['condition']}")
        
        # Build dynamic question flow for this condition/trial
        question_flow = self._build_question_flow(condition, trial_id)
        logger.info(f"[DEBUG] Built question flow with {len(question_flow)} questions: {question_flow}")
        
        # Get or create prescreening session
        existing = db.execute_query("""
            SELECT id FROM prescreening_sessions
            WHERE user_id = %s AND session_id = %s AND trial_id = %s
            AND status = 'in_progress'
        """, (user_id, session_id, trial_id))
        
        if existing:
            ps_id = existing[0]['id']
            logger.info(f"Using existing prescreening session: {ps_id}")
        else:
            logger.info(f"Creating new prescreening session for trial_id={trial_id}, questions={len(question_flow)}")
            try:
                result = db.execute_query("""
                    INSERT INTO prescreening_sessions 
                    (user_id, session_id, trial_id, condition, location, status, total_questions)
                    VALUES (%s, %s, %s, %s, %s, 'in_progress', %s)
                    RETURNING id
                """, (user_id, session_id, trial_id, condition, location, len(question_flow)))
                logger.info(f"Prescreening session insert result: {result}")
                ps_id = result[0]['id']
                logger.info(f"Created prescreening session with id: {ps_id}")
            except Exception as e:
                logger.error(f"Error creating prescreening session: {str(e)}")
                logger.error(f"Parameters: user_id={user_id}, trial_id={trial_id}, questions={len(question_flow)}")
                raise
        
        # Update context with dynamic tracking
        self._update_context(session_id, {
            "prescreening_active": True,
            "prescreening_session_id": ps_id,
            "current_state": "prescreening_active",
            "trial_id": trial_id,
            "question_flow": question_flow,
            "current_question_index": 0
        })
        
        # Get first question
        first_question_key = question_flow[0]
        question = self.templates.get_question(first_question_key, condition=condition)
        
        intro = f"I'll help you determine if you might be eligible for the {condition} trial in {location}. "
        intro += "Let me ask you a few questions to better understand your situation.\n\n"
        
        return intro + question.text, {
            "prescreening_session_id": ps_id,
            "trial_id": trial_id,
            "state": "prescreening_active",
            "current_question": first_question_key
        }
    
    async def _process_prescreening_answer(
        self,
        ps_id: int,
        user_id: str,
        session_id: str,
        answer: str,
        context: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        """Process a prescreening answer using dynamic question flow"""
        
        # Get current question from dynamic flow
        question_flow = context.get("question_flow", [])
        current_index = context.get("current_question_index", 0)
        
        if current_index >= len(question_flow):
            # No more questions, complete prescreening
            return await self._complete_prescreening(ps_id, user_id, session_id)
        
        current_question_key = question_flow[current_index]
        
        # Get session info for condition
        session = db.execute_query("""
            SELECT condition, trial_id FROM prescreening_sessions WHERE id = %s
        """, (ps_id,))[0]
        
        # Get current question to determine answer type
        current_question = self.templates.get_question(current_question_key, condition=session['condition'])
        
        if not current_question:
            # Skip this question and move to next
            context["current_question_index"] = current_index + 1
            self._update_context(session_id, context)
            return await self._get_next_question(
                ps_id, user_id, session_id, session['condition'], context, {}
            )
        
        answer_type = current_question.type
        
        # Parse answer with enhanced metadata tracking
        parsed_value = self.parser.parse(answer, answer_type)
        
        # Determine auto-evaluation metadata
        auto_evaluated = False
        confidence_score = 0.0
        evaluation_method = 'manual'
        calculation_details = {}
        
        # Track specific auto-evaluation scenarios
        if answer_type == 'numeric' and parsed_value is not None:
            auto_evaluated = True
            confidence_score = 0.9
            evaluation_method = 'numeric_comparison'
            calculation_details = {
                'input_text': answer,
                'parsed_value': parsed_value,
                'auto_parsed': True
            }
        elif current_question_key == 'age' and parsed_value is not None:
            auto_evaluated = True
            confidence_score = 0.95
            evaluation_method = 'age_extraction'
            calculation_details = {
                'input_text': answer,
                'extracted_age': parsed_value,
                'auto_parsed': True
            }
        elif answer_type == 'boolean' and parsed_value is not None:
            auto_evaluated = True
            confidence_score = 0.85
            evaluation_method = 'boolean_classification'
            calculation_details = {
                'input_text': answer,
                'classification': parsed_value,
                'auto_parsed': True
            }
        
        # Log health metrics for BMI-related calculations
        if current_question_key in ['height', 'weight'] and parsed_value is not None:
            self._log_health_metrics(
                session_id, user_id, current_question_key, 
                parsed_value, answer
            )
        
        # Store answer with enhanced metadata
        db.execute_update("""
            INSERT INTO prescreening_answers
            (user_id, session_id, trial_id, condition, location, 
             question_id, question_text, question_type, user_answer, parsed_value,
             auto_evaluated, confidence_score, evaluation_method, calculation_details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (user_id, session_id, session['trial_id'], session['condition'], 
              context.get('location'), current_question_key, current_question.text, answer_type, 
              answer, str(parsed_value), auto_evaluated, confidence_score, evaluation_method,
              json.dumps(calculation_details)))
        
        # Update questions answered
        db.execute_update("""
            UPDATE prescreening_sessions 
            SET questions_answered = questions_answered + 1
            WHERE id = %s
        """, (ps_id,))
        
        # Handle parsing failures
        if parsed_value is None and current_question.required:
            return current_question.clarification_text.format(
                condition=session['condition']
            ), {"state": "prescreening_active", "current_question": current_question_key}
        
        # Check for early exit conditions
        if current_question_key == "diagnosis" and not parsed_value:
            # No diagnosis, end prescreening
            return await self._complete_prescreening(ps_id, user_id, session_id)
        
        # Acknowledge and move to next question
        acknowledgment = self._acknowledge_answer(current_question_key, parsed_value)
        
        # Update question index
        context["current_question_index"] = current_index + 1
        self._update_context(session_id, context)
        
        # Get next question
        return await self._get_next_question(
            ps_id, user_id, session_id, session['condition'], context, 
            {"answer": answer, "acknowledgment": acknowledgment}
        )
    
    async def _get_next_question(
        self, 
        ps_id: int,
        user_id: str,
        session_id: str,
        condition: str,
        context: Dict[str, Any],
        metadata: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        """Get next question in the dynamic flow"""
        
        question_flow = context.get("question_flow", [])
        current_index = context.get("current_question_index", 0)
        
        # Check if we've completed all questions
        if current_index >= len(question_flow):
            return await self._complete_prescreening(ps_id, user_id, session_id)
        
        # Get next question
        next_question_key = question_flow[current_index]
        next_question = self.templates.get_question(next_question_key, condition=condition)
        
        if not next_question:
            # Skip to next question if this one isn't found
            context["current_question_index"] = current_index + 1
            self._update_context(session_id, context)
            return await self._get_next_question(
                ps_id, user_id, session_id, condition, context, metadata
            )
        
        # Build response with acknowledgment if available
        response = metadata.get("acknowledgment", "")
        if response:
            response += " "
        response += next_question.text
        
        return response, {
            "state": "prescreening_active",
            "current_question": next_question_key,
            "question_index": current_index,
            "total_questions": len(question_flow)
        }
    
    async def _evaluate_eligibility(self, ps_id: int, trial_id: int) -> EligibilityResult:
        """Evaluate eligibility based on collected answers and trial criteria"""
        
        # Get all answers
        answers = db.execute_query("""
            SELECT question_id, parsed_value
            FROM prescreening_answers
            WHERE session_id = (SELECT session_id FROM prescreening_sessions WHERE id = %s)
            ORDER BY answered_at
        """, (ps_id,))
        
        # Build answer dict
        answer_dict = {a['question_id']: a['parsed_value'] for a in answers}
        
        criteria_met = []
        criteria_not_met = []
        criteria_unknown = []
        
        # Get trial criteria from database
        criteria = db.execute_query("""
            SELECT criterion_type, criterion_text, parsed_json
            FROM trial_criteria
            WHERE trial_id = %s 
            AND is_required = true
            AND parsed_json != '{"field": "unparsed"}'::jsonb
        """, (trial_id,))
        
        # Evaluate each criterion
        for criterion in criteria:
            try:
                parsed = criterion['parsed_json']
                field = parsed.get('field')
                criterion_type = criterion['criterion_type']
                
                if field == 'age':
                    age = answer_dict.get('age')
                    if age is not None:
                        # Handle both string and int from parser
                        try:
                            age_val = int(age) if isinstance(age, str) else age
                            min_age = parsed.get('value', [18, 99])[0]
                            max_age = parsed.get('value', [18, 99])[1]
                            
                            if min_age <= age_val <= max_age:
                                criteria_met.append(f"✓ Age {age_val} (within {min_age}-{max_age} years)")
                            else:
                                criteria_not_met.append(f"✗ Age {age_val} (requires {min_age}-{max_age} years)")
                        except (ValueError, TypeError):
                            criteria_unknown.append("? Age could not be evaluated")
                
                elif field == 'diagnosis':
                    diagnosis_answer = answer_dict.get('diagnosis') == 'True'
                    if criterion_type == 'inclusion':
                        if diagnosis_answer:
                            criteria_met.append(f"✓ Confirmed diagnosis of {parsed.get('value', 'condition')}")
                        else:
                            criteria_not_met.append(f"✗ No confirmed diagnosis")
                    else:  # exclusion
                        if not diagnosis_answer:
                            criteria_met.append(f"✓ No diagnosis of {parsed.get('value', 'condition')}")
                        else:
                            criteria_not_met.append(f"✗ Has diagnosis of {parsed.get('value', 'condition')}")
                
                elif field == 'condition_count':
                    count = answer_dict.get('condition_count', answer_dict.get('gout_flares'))
                    if count is not None:
                        try:
                            # Handle both string and int/float
                            count_val = float(count) if isinstance(count, str) else count
                            count_val = int(count_val)  # Convert to int for display
                            required = parsed.get('value', 2)
                            operator = parsed.get('operator', 'greater_than_or_equal')
                            
                            if operator == 'greater_than_or_equal' and count_val >= required:
                                criteria_met.append(f"✓ {count_val} flares/episodes (≥{required} required)")
                            elif operator == 'greater_than' and count_val > required:
                                criteria_met.append(f"✓ {count_val} flares/episodes (>{required} required)")
                            else:
                                criteria_not_met.append(f"✗ {count_val} flares/episodes (requires {operator} {required})")
                        except (ValueError, TypeError):
                            criteria_unknown.append("? Condition count could not be evaluated")
                
                elif field == 'kidney_stones' and criterion_type == 'exclusion':
                    kidney_stones = answer_dict.get('kidney_stones')
                    if kidney_stones == 'False':
                        criteria_met.append("✓ No kidney stones in past 6 months")
                    elif kidney_stones == 'True':
                        criteria_not_met.append("✗ Had kidney stones in past 6 months")
                
                elif field == 'condition':
                    # Handle other medical conditions
                    condition_name = parsed.get('value', '')
                    answer_key = f"has_{condition_name.replace(' ', '_').lower()}"
                    has_condition = answer_dict.get(answer_key) == 'True'
                    
                    if criterion_type == 'inclusion':
                        if has_condition:
                            criteria_met.append(f"✓ Has {condition_name}")
                        else:
                            criteria_not_met.append(f"✗ Does not have {condition_name}")
                    else:  # exclusion
                        if not has_condition:
                            criteria_met.append(f"✓ Does not have {condition_name}")
                        else:
                            criteria_not_met.append(f"✗ Has {condition_name} (exclusion criterion)")
                
                elif field == 'lab_value':
                    test_name = parsed.get('test')
                    lab_answer = answer_dict.get(f'lab_{test_name}')
                    if lab_answer and lab_answer.lower() != 'unknown':
                        # Parse lab value logic would go here
                        criteria_unknown.append(f"? {test_name} value provided but needs verification")
                    
            except Exception as e:
                logger.warning(f"Failed to evaluate criterion: {str(e)}")
        
        # Calculate result
        eligible = len(criteria_not_met) == 0 and len(criteria_met) >= 2
        confidence = len(criteria_met) / (len(criteria_met) + len(criteria_not_met)) if criteria_met else 0.0
        
        # Update session
        result_text = "eligible" if eligible else "not eligible"
        summary = f"Based on prescreening: {len(criteria_met)} criteria met, {len(criteria_not_met)} not met"
        
        db.execute_update("""
            UPDATE prescreening_sessions 
            SET eligibility_result = %s,
                eligibility_summary = %s,
                status = 'completed',
                completed_at = NOW()
            WHERE id = %s
        """, (result_text, summary, ps_id))
        
        if eligible:
            recommendation = "You appear to meet the basic eligibility criteria."
            next_steps = "The next step would be to schedule a screening visit to confirm your eligibility."
        else:
            recommendation = "Based on your responses, you may not meet all the eligibility criteria."
            next_steps = "However, there may be other trials that could be suitable for you."
        
        return EligibilityResult(
            eligible=eligible,
            confidence=confidence,
            criteria_met=criteria_met,
            criteria_not_met=criteria_not_met,
            criteria_unknown=criteria_unknown,
            recommendation=recommendation,
            next_steps=next_steps
        )
    
    def _find_matching_trials(self, condition: str, location: str) -> List[Dict[str, Any]]:
        """Find trials matching condition and location with multi-trial completion optimization"""
        from core.services.trial_search import trial_search, MultiTrialCompletionSelector
        
        logger.info(f"[DEBUG] Finding trials for condition='{condition}', location='{location}'")
        
        # Use enhanced search with multi-trial detection
        search_result = trial_search.search_trials_with_multi_trial_detection(
            condition, location, session_id=None
        )
        
        if not search_result["trials"]:
            logger.info(f"[DEBUG] No trials found for {condition} in {location}")
            return []
        
        # Handle multi-trial scenario with completion rate optimization
        if search_result["requires_multi_trial_logic"]:
            logger.info(f"MULTI_TRIAL_SCENARIO: {len(search_result['trials'])} trials found for {condition} in {location}")
            
            # Use completion rate selector
            selector = MultiTrialCompletionSelector()
            selection_result = selector.select_from_multiple_trials(
                search_result["trials"], condition, location
            )
            
            # Log the multi-trial selection
            logger.info(f"COMPLETION_OPTIMIZED_SELECTION: {selection_result['selection_reasoning']}")
            
            # Return the selected trial as first in list (maintains backward compatibility)
            selected = selection_result["selected_trial"]
            other_trials = [t for t in search_result["trials"] if t['id'] != selected['id']]
            return [selected] + other_trials[:4]  # Return selected + up to 4 alternatives
        
        else:
            # Single trial - use standard flow
            logger.info(f"[DEBUG] Single trial scenario: {len(search_result['trials'])} trials")
            return search_result["trials"]
    
    def _get_context(self, session_id: str) -> Dict[str, Any]:
        """Get conversation context"""
        results = db.execute_query("""
            SELECT context_data FROM conversation_context
            WHERE session_id = %s AND active = true
            LIMIT 1
        """, (session_id,))
        
        if results and results[0]['context_data']:
            return results[0]['context_data']
        return {}
    
    def _update_context(self, session_id: str, updates: Dict[str, Any]):
        """Update conversation context (fixed constraint reference)"""
        db.execute_update("""
            INSERT INTO conversation_context (session_id, user_id, context_data, active)
            VALUES (%s, %s, %s, true)
            ON CONFLICT (session_id)
            DO UPDATE SET 
                context_data = conversation_context.context_data || %s,
                active = EXCLUDED.active,
                updated_at = NOW()
        """, (session_id, 'system', json.dumps(updates), json.dumps(updates)))
    
    def _acknowledge_answer(self, question_key: str, answer: Any) -> str:
        """Generate acknowledgment for an answer"""
        acknowledgments = {
            "age": "Thank you.",
            "diagnosis": "I see." if answer else "Understood.",
            "medications": "Got it.",
            "location": "Great!",
            "condition": "Excellent.",
        }
        return acknowledgments.get(question_key, "Thank you.")
    
    async def _complete_prescreening(
        self,
        ps_id: int,
        user_id: str,
        session_id: str
    ) -> Tuple[str, Dict[str, Any]]:
        """Complete prescreening and evaluate eligibility"""
        
        # Get session info including location
        session = db.execute_query("""
            SELECT id, trial_id, condition, location FROM prescreening_sessions WHERE id = %s
        """, (ps_id,))[0]
        
        # Evaluate eligibility
        result = await self._evaluate_eligibility(ps_id, session['trial_id'])
        
        # Clear prescreening state from context
        self._update_context(session_id, {
            "prescreening_active": False,
            "current_state": "idle",
            "question_flow": None,
            "current_question_index": None
        })
        
        # Format result with session info including ID
        return self._format_eligibility_result(result, session), {
            "state": "completed",
            "eligible": result.eligible,
            "prescreening_session_id": ps_id
        }
    
    def _format_eligibility_result(self, result: EligibilityResult, session: Dict[str, Any]) -> str:
        """Format eligibility result for display"""
        response = "Based on your responses:\n"
        
        for criterion in result.criteria_met:
            response += f"{criterion}\n"
        
        for criterion in result.criteria_not_met:
            response += f"{criterion}\n"
        
        response += f"\n{result.recommendation}\n\n{result.next_steps}"
        
        if result.eligible:
            # Get location from session
            location_result = db.execute_query("""
                SELECT location FROM prescreening_sessions WHERE id = %s
            """, (session.get('id', 0),))
            
            user_location = location_result[0]['location'] if location_result else None
            
            # Get investigator info for the user's location
            if user_location:
                investigators = db.execute_query("""
                    SELECT investigator_name, site_location
                    FROM trial_investigators
                    WHERE trial_id = %s
                    AND LOWER(site_location) LIKE LOWER(%s)
                    LIMIT 1
                """, (session['trial_id'], f"%{user_location}%"))
            else:
                # Fallback if no location
                investigators = db.execute_query("""
                    SELECT investigator_name, site_location
                    FROM trial_investigators
                    WHERE trial_id = %s
                    LIMIT 1
                """, (session['trial_id'],))
            
            if investigators:
                inv = investigators[0]
                response += f"\n\nWould you like contact information for {inv['investigator_name']}'s team in {inv['site_location']}?"
        
        return response
    
    def _log_health_metrics(self, session_id: str, user_id: str, metric_type: str, 
                           calculated_value: Any, input_text: str):
        """Log health metrics for BMI calculations and other health data"""
        try:
            # Determine units and calculation method
            units = None
            calculation_method = 'auto_parsed'
            
            if metric_type == 'height':
                units = 'cm'  # Assume cm for now
            elif metric_type == 'weight':
                units = 'kg'  # Assume kg for now
            elif metric_type == 'age':
                units = 'years'
                
            # Convert to float for storage
            try:
                numeric_value = float(calculated_value)
            except (ValueError, TypeError):
                numeric_value = 0.0
                calculation_method = 'manual_entry'
            
            # Store in health_metrics table
            db.execute_update("""
                INSERT INTO health_metrics 
                (session_id, user_id, metric_type, calculated_value, input_text, 
                 units, calculation_method)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (session_id, user_id, metric_type, numeric_value, 
                  input_text, units, calculation_method))
                  
        except Exception as e:
            logger.warning(f"Failed to log health metric: {str(e)}")
    
    async def _continue_prescreening(
        self, 
        ps_id: int,
        user_id: str,
        session_id: str
    ) -> Tuple[str, Dict[str, Any]]:
        """Continue an existing prescreening session"""
        
        # Get session info
        session = db.execute_query("""
            SELECT condition, trial_id, questions_answered, total_questions
            FROM prescreening_sessions WHERE id = %s
        """, (ps_id,))[0]
        
        # Get existing context or rebuild it
        context = self._get_context(session_id)
        
        # If no question flow in context, rebuild it
        if "question_flow" not in context or not context.get("question_flow"):
            question_flow = self._build_question_flow(session['condition'], session['trial_id'])
            context["question_flow"] = question_flow
            context["current_question_index"] = session['questions_answered']
            context["prescreening_active"] = True
            context["prescreening_session_id"] = ps_id
            context["current_state"] = "prescreening_active"
            self._update_context(session_id, context)
        
        # Get current question
        question_flow = context["question_flow"]
        current_index = context.get("current_question_index", session['questions_answered'])
        
        if current_index >= len(question_flow):
            return await self._complete_prescreening(ps_id, user_id, session_id)
        
        current_question_key = question_flow[current_index]
        question = self.templates.get_question(current_question_key, condition=session['condition'])
        
        return f"Let's continue where we left off. {question.text}", {
            "prescreening_session_id": ps_id,
            "state": "prescreening_active",
            "current_question": current_question_key,
            "question_index": current_index,
            "total_questions": len(question_flow)
        }