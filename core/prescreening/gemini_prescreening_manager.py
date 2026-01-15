"""
Gemini-powered prescreening system for clinical trials.

This system uses Gemini to generate natural questions from database criteria,
parse user responses flexibly, and evaluate eligibility intelligently.
"""

import json
import logging
import re
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from core.database import db
from core.services.gemini_service import gemini_service

logger = logging.getLogger(__name__)


@dataclass
class TrialCriterion:
    """Represents a single trial criterion"""
    id: int
    trial_id: int
    criterion_type: str  # inclusion/exclusion
    criterion_text: str
    category: str
    parsed_json: Dict[str, Any]
    is_required: bool


@dataclass
class PrescreeningQuestion:
    """Represents a generated prescreening question"""
    criterion_id: int
    question_text: str
    criterion_type: str
    category: str
    expected_answer_type: str  # "yes_no", "number", "text", "date"
    evaluation_hint: str


@dataclass
class PrescreeningAnswer:
    """Represents a user's answer to a prescreening question"""
    criterion_id: int
    question_text: str
    user_response: str
    parsed_value: Any
    interpretation: str  # "yes", "no", "unclear", "number", etc.
    confidence: float


@dataclass
class EligibilityResult:
    """Represents the final eligibility assessment"""
    trial_id: int
    trial_name: str
    overall_status: str  # "likely_eligible", "likely_ineligible", "needs_review"
    inclusion_met: int
    inclusion_total: int
    exclusion_met: int
    exclusion_total: int
    detailed_results: List[Dict[str, Any]]
    summary_text: str


class GeminiPrescreeningManager:
    """
    Gemini-powered prescreening manager for clinical trials.
    
    Uses Gemini to:
    1. Generate natural questions from database criteria
    2. Parse user responses flexibly
    3. Evaluate eligibility intelligently
    """
    
    def __init__(self, api_key: str = None):
        self.gemini = gemini_service
        
    def start_prescreening(self, trial_id: int, session_id: str = None, user_id: str = None, condition: str = None, location: str = None) -> Tuple[List[PrescreeningQuestion], str]:
        """
        Start prescreening for a specific trial and create database session.
        
        Args:
            trial_id: ID of the trial to screen for
            session_id: Conversation session ID  
            user_id: User identifier
            condition: Medical condition
            location: User location
            
        Returns:
            Tuple of (questions_list, trial_name)
        """
        try:
            # Get trial info
            trial_info = self._get_trial_info(trial_id)
            if not trial_info:
                raise ValueError(f"Trial {trial_id} not found")
            
            # Get required criteria
            criteria = self._get_trial_criteria(trial_id)
            if not criteria:
                raise ValueError(f"No criteria found for trial {trial_id}")
            
            # Generate questions using OpenAI
            questions = self._generate_questions(criteria, trial_info)
            
            # 🔥 CREATE PRESCREENING SESSION IN DATABASE (with duplicate prevention)
            if session_id and user_id:
                try:
                    # First check if session already exists to prevent duplicates
                    existing_check = db.execute_query("""
                        SELECT id FROM prescreening_sessions 
                        WHERE session_id = %s AND trial_id = %s
                        ORDER BY started_at DESC LIMIT 1
                    """, (session_id, trial_id))
                    
                    if existing_check:
                        existing_id = existing_check[0]['id']
                        logger.info(f"✅ Using existing prescreening session {existing_id} for session {session_id}")
                        prescreening_session_id = existing_id
                    else:
                        # Create new session only if none exists
                        logger.info(f"Creating prescreening session for trial_id={trial_id}, questions={len(questions)}")
                        try:
                            result = db.execute_query("""
                                INSERT INTO prescreening_sessions 
                                (user_id, session_id, trial_id, condition, location, status, total_questions)
                                VALUES (%s, %s, %s, %s, %s, 'in_progress', %s)
                                RETURNING id
                            """, (user_id, session_id, trial_id, condition, location, len(questions)))
                            logger.info(f"Prescreening session insert result: {result}")
                            prescreening_session_id = result[0]['id']
                            logger.info(f"✅ Created new prescreening session {prescreening_session_id} for session {session_id}")
                        except Exception as db_error:
                            logger.error(f"Error creating prescreening session: {str(db_error)}")
                            logger.error(f"Parameters: trial_id={trial_id}, questions={len(questions)}")
                            raise
                    
                except Exception as e:
                    logger.error(f"❌ Failed to create/check prescreening session: {str(e)}")
            else:
                logger.warning("⚠️ No session_id or user_id provided - prescreening session not created")
            
            logger.info(f"Generated {len(questions)} prescreening questions for trial {trial_id}")
            # Return actual trial name for internal storage and tracking
            actual_trial_name = trial_info.get('trial_name', f"Trial {trial_id}")
            return questions, actual_trial_name
            
        except Exception as e:
            logger.error(f"Error starting prescreening for trial {trial_id}: {str(e)}")
            raise
    
    def _get_trial_info(self, trial_id: int) -> Optional[Dict[str, Any]]:
        """Get basic trial information"""
        try:
            results = db.execute_query("""
                SELECT id, trial_name, conditions, description
                FROM clinical_trials
                WHERE id = %s
            """, (trial_id,))
            
            return results[0] if results else None
            
        except Exception as e:
            logger.error(f"Error fetching trial info: {str(e)}")
            return None
    
    def _get_trial_criteria(self, trial_id: int) -> List[TrialCriterion]:
        """Get required criteria for a trial with smart ordering"""
        try:
            logger.info(f"[CRITERIA_FETCH] Fetching required criteria for trial_id={trial_id}")

            results = db.execute_query("""
                SELECT id, trial_id, criterion_type, criterion_text, category, parsed_json, is_required, sort_order
                FROM trial_criteria
                WHERE trial_id = %s AND is_required = true
                ORDER BY 
                    CASE WHEN sort_order > 0 THEN sort_order ELSE 9999 END,
                    CASE 
                        WHEN criterion_type = 'exclusion' THEN 1 
                        WHEN criterion_type = 'inclusion' THEN 2 
                        ELSE 3 
                    END,
                    CASE 
                        WHEN category = 'demographics' THEN 1
                        WHEN category = 'demographic' THEN 1
                        WHEN category = 'safety' THEN 2
                        WHEN category = 'medical_history' THEN 3
                        WHEN category = 'disease_specific' THEN 4
                        WHEN category = 'laboratory' THEN 5
                        WHEN category = 'medications' THEN 6
                        WHEN category = 'reproductive' THEN 7
                        WHEN category = 'study_procedures' THEN 8
                        WHEN category = 'general' THEN 9
                        ELSE 10
                    END,
                    id
            """, (trial_id,))

            logger.info(f"[CRITERIA_FETCH] Found {len(results)} required criteria for trial_id={trial_id}")

            criteria = []
            for row in results:
                parsed_json = row['parsed_json']
                if parsed_json is None:
                    parsed_json = {}
                elif isinstance(parsed_json, str):
                    try:
                        parsed_json = json.loads(parsed_json)
                    except:
                        parsed_json = {}
                
                criteria.append(TrialCriterion(
                    id=row['id'],
                    trial_id=row['trial_id'],
                    criterion_type=row['criterion_type'],
                    criterion_text=row['criterion_text'],
                    category=row['category'],
                    parsed_json=parsed_json,
                    is_required=row['is_required']
                ))
            
            return criteria
            
        except Exception as e:
            logger.error(f"Error fetching trial criteria: {str(e)}")
            return []
    
    def _get_criterion_by_id(self, criterion_id: int) -> Optional[TrialCriterion]:
        """Get a single criterion by ID"""
        try:
            results = db.execute_query("""
                SELECT id, trial_id, criterion_type, criterion_text, category, parsed_json, is_required
                FROM trial_criteria
                WHERE id = %s
            """, (criterion_id,))
            
            if not results:
                return None
                
            row = results[0]
            try:
                parsed_json = json.loads(row['parsed_json']) if row['parsed_json'] else {}
            except:
                parsed_json = {}
            
            return TrialCriterion(
                id=row['id'],
                trial_id=row['trial_id'],
                criterion_type=row['criterion_type'],
                criterion_text=row['criterion_text'],
                category=row['category'],
                parsed_json=parsed_json,
                is_required=row['is_required']
            )
        except Exception as e:
            logger.error(f"Error fetching criterion by ID {criterion_id}: {str(e)}")
            return None
    
    def _generate_questions(self, criteria: List[TrialCriterion], trial_info: Dict[str, Any]) -> List[PrescreeningQuestion]:
        """Generate natural questions from criteria using simple generation (reliable)"""
        try:
            # Use simple question generation for now (OpenAI function calling has timeout issues)
            # TODO: Re-enable OpenAI question generation after fixing timeout issues
            logger.info("Using simple question generation for reliability")
            return self._generate_simple_questions(criteria)
            
        except Exception as e:
            logger.error(f"Error generating questions: {str(e)}")
            return self._generate_simple_questions(criteria)
    
    def _get_question_generation_prompt(self, trial_info: Dict[str, Any]) -> str:
        """Get system prompt for question generation"""
        return f"""You are an expert clinical trial coordinator generating prescreening questions.

Trial: {trial_info['trial_name']}
Condition: {trial_info['conditions']}

Guidelines:
1. Generate simple, direct questions (not lengthy explanations)
2. Use natural, conversational language
3. For age criteria, ask "What is your age?" or "Are you between X-Y years old?"
4. For yes/no criteria, ask clear yes/no questions
5. For complex criteria, break into simple questions
6. Order questions logically (demographics first, then medical history)
7. Make questions patient-friendly, not medical jargon

For each criterion, determine:
- The most natural question to ask
- Expected answer type (yes_no, number, text, date)
- How to evaluate the answer for eligibility
"""
    
    def _get_question_generation_function(self) -> Dict[str, Any]:
        """Define OpenAI function for question generation"""
        return {
            "name": "generate_prescreening_questions",
            "description": "Generate natural prescreening questions from clinical trial criteria",
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "criterion_id": {"type": "integer"},
                                "question_text": {"type": "string"},
                                "criterion_type": {"type": "string"},
                                "category": {"type": "string"},
                                "expected_answer_type": {
                                    "type": "string",
                                    "enum": ["yes_no", "number", "text", "date"]
                                },
                                "evaluation_hint": {"type": "string"}
                            },
                            "required": ["criterion_id", "question_text", "criterion_type", "category", "expected_answer_type", "evaluation_hint"]
                        }
                    }
                },
                "required": ["questions"]
            }
        }
    
    def _parse_generated_questions(self, result: Dict[str, Any], criteria: List[TrialCriterion]) -> List[PrescreeningQuestion]:
        """Parse OpenAI-generated questions into PrescreeningQuestion objects"""
        questions = []
        
        for q_data in result.get("questions", []):
            try:
                question = PrescreeningQuestion(
                    criterion_id=q_data["criterion_id"],
                    question_text=q_data["question_text"],
                    criterion_type=q_data["criterion_type"],
                    category=q_data["category"],
                    expected_answer_type=q_data["expected_answer_type"],
                    evaluation_hint=q_data["evaluation_hint"]
                )
                questions.append(question)
            except Exception as e:
                logger.error(f"Error parsing question: {str(e)}")
                continue
        
        return questions
    
    def _generate_simple_questions(self, criteria: List[TrialCriterion]) -> List[PrescreeningQuestion]:
        """Fallback simple question generation with validation and deduplication"""
        questions = []
        seen_questions = {}  # Track question_text -> criterion_ids mapping

        for criterion in criteria:
            question_text = self._simple_question_from_criterion(criterion)
            expected_type = self._determine_answer_type(criterion)

            # Validate question matches expected answer type
            type_mismatch = self._check_question_type_mismatch(question_text, expected_type)
            if type_mismatch:
                logger.error(f"⚠️  QUESTION TYPE MISMATCH - Criterion {criterion.id}:")
                logger.error(f"   Question: '{question_text}'")
                logger.error(f"   Expected type: {expected_type}")
                logger.error(f"   Mismatch: {type_mismatch}")
                # Fix: Adjust expected_type based on question_text
                if "how many" in question_text.lower() or "how much" in question_text.lower():
                    expected_type = "number"
                    logger.error(f"   → Corrected to: {expected_type}")

            # Validate the generated question
            if not self._validate_generated_question(question_text):
                logger.warning(f"Question validation failed for criterion {criterion.id}: '{question_text}'")
                # Try to create a fallback question
                question_text = self._create_fallback_question(criterion)
                logger.info(f"Using fallback question for criterion {criterion.id}: '{question_text}'")

            # DEDUPLICATION: Check if this exact question was already generated
            if question_text in seen_questions:
                logger.warning(f"🔄 DUPLICATE QUESTION DETECTED:")
                logger.warning(f"   Question: '{question_text}'")
                logger.warning(f"   Original criterion: {seen_questions[question_text]}")
                logger.warning(f"   Duplicate criterion: {criterion.id}")
                logger.warning(f"   Skipping duplicate to avoid asking same question twice")
                continue  # Skip this duplicate question

            # Track this question text
            seen_questions[question_text] = criterion.id

            question = PrescreeningQuestion(
                criterion_id=criterion.id,
                question_text=question_text,
                criterion_type=criterion.criterion_type,
                category=criterion.category,
                expected_answer_type=expected_type,
                evaluation_hint=f"Check {criterion.criterion_type} criterion"
            )
            questions.append(question)

        logger.info(f"Generated {len(questions)} unique questions from {len(criteria)} criteria ({len(criteria) - len(questions)} duplicates removed)")
        return questions
    
    def _create_fallback_question(self, criterion: TrialCriterion) -> str:
        """Create a safe fallback question when validation fails"""
        if criterion.criterion_type == "inclusion":
            return f"Do you meet this requirement: {criterion.criterion_text}?"
        else:  # exclusion
            return f"Do you have any of the following: {criterion.criterion_text}?"
    
    def _validate_session_state(self, session_id: str, expected_trial_id: int) -> bool:
        """Validate that the session state is consistent"""
        try:
            # Check if session exists and matches expected trial
            session_info = db.execute_query("""
                SELECT trial_id, status, started_at
                FROM prescreening_sessions 
                WHERE session_id = %s
                ORDER BY started_at DESC LIMIT 1
            """, (session_id,))
            
            if not session_info:
                logger.warning(f"No prescreening session found for session_id {session_id}")
                return False
            
            session_trial_id = session_info[0]['trial_id']
            session_status = session_info[0]['status']
            
            if session_trial_id != expected_trial_id:
                logger.warning(f"Trial ID mismatch: session has {session_trial_id}, expected {expected_trial_id}")
                return False
            
            if session_status not in ['in_progress', 'started']:
                logger.warning(f"Invalid session status: {session_status}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating session state: {e}")
            return False
    
    def save_prescreening_answer(self, session_id: str, user_id: str, question: PrescreeningQuestion, user_answer: str, parsed_answer: PrescreeningAnswer) -> None:
        """
        Save a prescreening answer to the database.
        
        Args:
            session_id: Conversation session ID
            user_id: User identifier  
            question: The prescreening question that was asked
            user_answer: Raw user response
            parsed_answer: Parsed answer with interpretation
        """
        try:
            # Get trial and condition info from prescreening session
            session_info = db.execute_query("""
                SELECT id, trial_id, condition, location FROM prescreening_sessions
                WHERE session_id = %s AND user_id = %s
                ORDER BY started_at DESC LIMIT 1
            """, (session_id, user_id))

            if not session_info:
                logger.error(f"❌ No prescreening session found for session {session_id}")
                return

            prescreening_session_id = session_info[0]['id']
            trial_id = session_info[0]['trial_id']
            condition = session_info[0]['condition']
            location = session_info[0]['location']

            # Save the answer WITH criterion_id and prescreening_session_id
            db.execute_update("""
                INSERT INTO prescreening_answers
                (session_id, prescreening_session_id, criterion_id, trial_id,
                 question_id, question_text, user_answer, parsed_value,
                 condition, location)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                session_id,
                prescreening_session_id,
                parsed_answer.criterion_id,
                trial_id,
                str(parsed_answer.criterion_id),
                parsed_answer.question_text,
                parsed_answer.user_response,
                str(parsed_answer.parsed_value),
                condition,
                location
            ))
            
            # Update questions answered count
            db.execute_update("""
                UPDATE prescreening_sessions 
                SET answered_questions = answered_questions + 1
                WHERE session_id = %s
            """, (session_id,))
            
            logger.info(f"✅ Saved prescreening answer for session {session_id}: {question.question_text[:50]}...")
            
        except Exception as e:
            logger.error(f"❌ Failed to save prescreening answer: {str(e)}")
    
    def complete_prescreening_session(self, session_id: str, user_id: str, eligibility_result: str = None) -> None:
        """
        Mark prescreening session as completed.
        
        Args:
            session_id: Conversation session ID
            user_id: User identifier
            eligibility_result: Optional eligibility determination
        """
        try:
            # Determine boolean eligible value from eligibility_result
            eligible_boolean = None
            if eligibility_result:
                eligible_boolean = eligibility_result in ['likely_eligible', 'potentially_eligible', 'eligible']
            
            db.execute_update("""
                UPDATE prescreening_sessions 
                SET status = 'completed', completed_at = NOW(),
                    eligibility_result = COALESCE(%s, 'evaluated'),
                    eligible = %s
                WHERE session_id = %s AND status = 'in_progress'
            """, (eligibility_result, eligible_boolean, session_id))
            
            logger.info(f"✅ Marked prescreening session {session_id} as completed")
            
        except Exception as e:
            logger.error(f"❌ Failed to complete prescreening session: {str(e)}")
    
    def _preprocess_criterion_text(self, criterion_text: str) -> str:
        """Clean up and preprocess criterion text for question generation"""
        
        text = criterion_text
        
        # Fix common grammar issues that cause malformed questions
        text = re.sub(r'\bpatient has\b', 'you have', text, flags=re.IGNORECASE)
        text = re.sub(r'\bpatient meets\b', 'you meet', text, flags=re.IGNORECASE)
        text = re.sub(r'\bpatient\b', 'you', text, flags=re.IGNORECASE)
        text = re.sub(r'\bhis/her\b', 'your', text, flags=re.IGNORECASE)
        text = re.sub(r'\bhe/she\b', 'you', text, flags=re.IGNORECASE)
        
        # Fix the specific "has more than" issue seen in transcript
        text = re.sub(r'\bhas more than\b', 'have more than', text, flags=re.IGNORECASE)
        text = re.sub(r'\bhas taken\b', 'have taken', text, flags=re.IGNORECASE)
        text = re.sub(r'\bhas greater than\b', 'have greater than', text, flags=re.IGNORECASE)
        
        # Remove duplicate words (like "has has" or "have have")
        text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text, flags=re.IGNORECASE)
        
        # Clean up extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
    def _validate_generated_question(self, question_text: str) -> bool:
        """Validate question grammar and clarity"""
        if not question_text or not question_text.strip():
            return False
            
        # Check for proper question structure
        if not question_text.strip().endswith('?'):
            return False
        
        # Check for duplicate consecutive words (but allow some intentional cases)
        words = question_text.lower().split()
        for i in range(len(words) - 1):
            if words[i] == words[i + 1] and len(words[i]) > 2:  # Ignore short words like "a a"
                # Allow intentional duplicates in medical contexts
                allowed_duplicates = ['gout', 'trial', 'study', 'test', 'drug', 'medication']
                if words[i] not in allowed_duplicates:
                    logger.warning(f"Duplicate words found in question: '{words[i]}' in '{question_text}'")
                    return False
        
        # Check for common grammar issues
        problematic_patterns = [
            r'\bhave has\b',
            r'\bhas have\b',
            r'\bis are\b',
            r'\bare is\b',
            r'\byou patient\b',
            r'\bpatient you\b'
        ]
        
        for pattern in problematic_patterns:
            if re.search(pattern, question_text, re.IGNORECASE):
                logger.warning(f"Grammar issue found in question: '{question_text}' matches pattern '{pattern}'")
                return False
        
        return True
    
    def _simple_question_from_criterion(self, criterion: TrialCriterion) -> str:
        """Generate a simple question from a criterion using dynamic patterns"""
        text = criterion.criterion_text.lower()
        original_text = self._preprocess_criterion_text(criterion.criterion_text)
        
        # 🐛 DEBUG: Log the question generation process
        logger.info(f"🔍 DEBUG QUESTION GENERATION:")
        logger.info(f"   Criterion ID: {criterion.id}")
        logger.info(f"   Original text: '{criterion.criterion_text}'")
        logger.info(f"   Lowercase text: '{text}'")
        logger.info(f"   Criterion type: {criterion.criterion_type}")
        
        # Age questions (universal pattern) - but distinguish between current age vs age of onset
        if "age" in text and "years" in text:
            logger.info(f"   🎯 AGE PATTERN MATCHED - checking for onset keywords...")
            logger.info(f"   Contains 'first': {'first' in text}")
            logger.info(f"   Contains 'onset': {'onset' in text}")
            logger.info(f"   Contains 'started': {'started' in text}")
            logger.info(f"   Contains 'migraine': {'migraine' in text}")
            
            if "first" in text or "onset" in text or "started" in text:
                logger.info(f"   ✅ ONSET PATTERN DETECTED - generating onset question")
                if "migraine" in text:
                    generated_question = "How old were you when you first started having migraines?"
                    logger.info(f"   📝 Generated: '{generated_question}'")
                    return generated_question
                else:
                    generated_question = "How old were you when this condition first started?"
                    logger.info(f"   📝 Generated: '{generated_question}'")
                    return generated_question
            else:
                logger.info(f"   ⚠️  CURRENT AGE PATTERN - generating current age question")
                generated_question = "What is your age?"
                logger.info(f"   📝 Generated: '{generated_question}'")
                return generated_question
        
        # BMI/weight questions - ALWAYS ask for height and weight (patients don't know BMI)
        if "bmi" in text or "body mass index" in text:
            return "What is your height and weight?"
        
        # Weight change questions (separate from BMI)
        if "body weight change" in text or "weight change" in text:
            if "5%" in text or "five percent" in text:
                return "Has your weight changed by 5% or more in the past 3 months?"
            else:
                return "Has your weight changed significantly in the past few months?"
        
        # Vaccination questions (universal pattern)
        if "vaccination" in text or "vaccinated" in text:
            return "Have you received any vaccinations within the past 14 days?"
        
        # Dynamic diagnosis questions
        if "diagnosis" in text or "documented medical records" in text:
            condition = self._extract_condition_from_text(text)
            if condition:
                return f"Have you been diagnosed with {condition} by a doctor?"
        
        # Migraine-specific questions
        if "migraine" in text:
            if "distinguish" in text and "tension" in text:
                return "Can you tell the difference between your migraine attacks and regular tension headaches?"
            elif "mild" in text and "resolve" in text:
                return "Do your migraines usually go away on their own within 2 hours without treatment?"
            elif "headache-days per month" in text or "headache on more than" in text or "headache-days" in text:
                return "How many days per month do you have headaches or take headache medication?"
            elif "prophylactic" in text or "prevention" in text:
                return "Are you currently taking any migraine prevention medications?"
            elif "basilar" in text or "hemiplegic" in text or "retinal" in text:
                return "Have you been diagnosed with basilar-type, hemiplegic, or retinal migraines?"
        
        # Dynamic symptom/episode frequency questions
        if any(freq_word in text for freq_word in ["flare", "episode", "attack", "occurrence"]):
            condition = self._extract_condition_from_text(text)
            symptom = self._extract_symptom_from_text(text)
            timeframe = self._extract_timeframe_from_text(text)
            
            if symptom and timeframe:
                return f"How many {symptom}s have you had in the {timeframe}?"
            elif condition and timeframe:
                return f"How many episodes have you had in the {timeframe}?"
        
        # PRIORITY: Handle medication COUNT questions BEFORE general medication patterns
        # These should ask "How many" instead of yes/no
        medication_count_patterns = [
            "taking more than", "concurrent medications", "more than 3", "more than 2", 
            "headache-days per month", "twice daily", "three times daily", "times per day"
        ]
        
        if any(pattern in text for pattern in medication_count_patterns):
            if "headache-days per month" in text or "headache medication" in text:
                return "How many days per month do you have headaches or take headache medication?"
            elif "concurrent medications" in text or "taking more than" in text:
                # Extract the number if present
                number_match = re.search(r'more than (\d+)', text)
                if number_match:
                    threshold = number_match.group(1)
                    return f"How many medications are you currently taking? (The study requires fewer than {threshold})"
                else:
                    return "How many medications are you currently taking?"
            elif "twice daily" in text or "times per day" in text:
                return "How many times per day do you take this medication?"
        
        # Enhanced medication/therapy questions with specific ULT handling
        med_triggers = [
            r'\btherapy\b', r'\btreatment\b', r'\bmedication\b', r'\bwashout\b', 
            r'\bagents\b', r'\binhibitor\b', r'\bULT\b', r'\buric acid\b',
            r'\badderall\b', r'\bamphetamine\b', r'\bdextroamphetamine\b',
            r'\bult-na[iï]ve\b'  # Added ULT-naïve pattern
        ]
        if any(re.search(trigger, text, re.IGNORECASE) for trigger in med_triggers):
            # Handle specific common medications first
            if "adderall" in text or "amphetamine" in text or "dextroamphetamine" in text:
                return "Have you taken Adderall or other ADHD medications (like amphetamines) in the past 12 weeks?"
            
            # Handle ULT-naïve criteria specifically
            elif "ult-naïve" in text or ("ult" in text and ("washout" in text or "naïve" in text)):
                return "Are you currently taking any uric acid-lowering medications (such as allopurinol, febuxostat, or probenecid)?"
            # Enhanced: Dynamic multi-turn medication questions based on actual trial criteria
            specific_medications = self._extract_specific_medications_from_text(text)
            medication_class = self._extract_medication_type_from_text(text)
            washout_period = self._extract_washout_period_from_text(text)
            
            # Check if this appears to be a washout/medication criterion that could benefit from multi-turn
            is_washout_criterion = bool(washout_period or re.search(r'washout|naive|willing', text, re.IGNORECASE))
            
            if is_washout_criterion:
                # Multi-turn approach: First ask if they're taking the medications
                if specific_medications:
                    med_list = "\n".join([f"• {med}" for med in specific_medications])
                    return f"Are you currently taking any of these medications?\n{med_list}"
                elif medication_class:
                    # Get examples for the medication class
                    examples = self._get_medication_class_examples(medication_class)
                    if examples:
                        examples_text = "\n".join([f"• {ex}" for ex in examples])
                        return (f"Are you currently taking {medication_class}?\n\n"
                               f"Common examples include:\n{examples_text}")
                    else:
                        return f"Are you currently taking {medication_class}?"
            else:
                # Single-turn approach for non-washout medication questions
                if specific_medications and washout_period:
                    med_list = "\n".join([f"• {med}" for med in specific_medications])
                    return (f"Are you currently taking any of these medications?\n{med_list}\n\n"
                           f"If yes, would you be willing to stop them for at least {washout_period} before starting the trial?")
                elif specific_medications:
                    med_list = "\n".join([f"• {med}" for med in specific_medications])
                    return (f"Are you currently taking any of these medications?\n{med_list}\n\n"
                           "If yes, would you be willing to stop them for the required washout period before starting the trial?")
                elif medication_class and washout_period:
                    # Get examples for the medication class
                    examples = self._get_medication_class_examples(medication_class)
                    if examples:
                        examples_text = "\n".join([f"• {ex}" for ex in examples])
                        return (f"Are you currently taking {medication_class}?\n\n"
                               f"Common examples include:\n{examples_text}\n\n"
                               f"If yes, would you be willing to stop them for at least {washout_period} before starting the trial?")
                    else:
                        return (f"Are you currently taking {medication_class}?\n\n"
                               f"If yes, would you be willing to stop them for at least {washout_period} before starting the trial?")
                elif medication_class:
                    # Get examples for the medication class
                    examples = self._get_medication_class_examples(medication_class)
                    if examples:
                        examples_text = "\n".join([f"• {ex}" for ex in examples])
                        return (f"Are you currently taking {medication_class}?\n\n"
                               f"Common examples include:\n{examples_text}\n\n"
                               "If yes, would you be willing to stop them for the required washout period before starting the trial?")
                    else:
                        return (f"Are you currently taking {medication_class}?\n\n"
                               "If yes, would you be willing to stop them for the required washout period before starting the trial?")
            
            # Generic washout handling for any medication type
            washout_match = re.search(r'washout|wash-out', text, re.IGNORECASE)
            if washout_match:
                # Extract medication type and washout period
                medication_type = self._extract_medication_type_from_text(text)
                washout_period = self._extract_washout_period_from_text(text)
                
                if medication_type and washout_period:
                    return (f"Are you currently taking {medication_type}?\n\n"
                           f"If yes, would you be willing to stop these medications for at least {washout_period} before starting the trial treatment?")
                elif medication_type:
                    return (f"Are you currently taking {medication_type}?\n\n"
                           "If yes, would you be willing to stop these medications for the required washout period before starting the trial treatment?")
            
            condition = self._extract_condition_from_text(text)
            specific_med = self._extract_specific_medication_from_text(text)
            
            # Handle complex medication criteria with "stable dose" or lists
            if "stable dose" in text and ("agents" in text or "following" in text):
                return f"Are you currently taking any of the medications mentioned in this requirement?"
            elif specific_med:
                return f"Are you currently taking {specific_med}?"
            elif condition:
                return f"Are you currently taking any medications for {condition}?"
        
        # Clean up the criterion text for natural questions
        clean_text = self._clean_criterion_text(original_text)
        
        # Laboratory values pattern - handle before exclusion/inclusion split
        lab_pattern = r'\b(\w+)\s+(?:>|<|≥|≤|between)\s+[\d.]+\s*(mg/dL|mmol/mol|mL/min|%|times?\s+(?:the\s+)?(?:upper\s+)?(?:limit\s+)?(?:of\s+)?(?:normal|ULN))'
        if re.search(lab_pattern, text, re.IGNORECASE):
            return self._create_laboratory_question(text)
        
        # Time-based exclusions pattern - handle before exclusion questions
        time_pattern = r'within\s+(\d+)\s+(days?|weeks?|months?|years?)'
        if criterion.criterion_type == "exclusion" and re.search(time_pattern, text, re.IGNORECASE):
            return self._create_time_based_exclusion_question(text)
        
        # Specific exclusion question patterns (medical tests and conditions)
        if criterion.criterion_type == "exclusion":
            # Handle specific medical conditions more naturally
            if "hemoglobin" in text:
                return "What is your most recent hemoglobin level? (If you don't know, just say 'I don't know')"
            elif "kidney stone" in text or "nephrectomy" in text or "renal transplant" in text:
                return "Have you had kidney stones, kidney surgery, or a kidney transplant in the past 6 months?"
            elif "allergic reaction" in text or "anaphylaxis" in text:
                return "Do you have any history of severe allergic reactions (such as anaphylaxis) to foods, drugs, chemicals, or other substances?"
            elif "serum creatinine" in text or "creatinine clearance" in text:
                return "Do you have any kidney problems or have you been told you have abnormal kidney function?"
            elif "liver" in text or "hepatic" in text or "bilirubin" in text:
                return "Do you have any liver problems or abnormal liver function tests?"
            elif "malignancy" in text or "cancer" in text:
                return "Have you been diagnosed with cancer within the past 5 years (excluding successfully treated skin cancer)?"
            elif "heart block" in text or "qtc" in text or "cardiac" in text:
                return "Do you have any heart problems or abnormal heart rhythm?"
            elif "hypertension" in text and "uncontrolled" in text:
                return "Do you have high blood pressure that is not well controlled with medication?"
            elif len(clean_text) > 150:  # Very long exclusion criteria
                self._log_fallback_usage("LONG_EXCLUSION_GENERIC", criterion.id, text, "Very long exclusion criteria - using generic question")
                return "Do you have any significant medical conditions that might affect your participation in this study?"
            else:
                # Smart exclusion fallback - avoid direct copying
                return self._create_smart_exclusion_question(clean_text, criterion.id)
        
        # Inclusion questions - make them more user-friendly
        if criterion.criterion_type == "inclusion":
            # Try to extract meaningful information from complex criteria
            if "age" in text and ("years" in text or "≥" in text or "≤" in text):
                return "What is your age?"
            elif "gout" in text and "flare" in text:
                return "How many gout flares have you had in the past 12 months?"
            elif "weight" in text and "kg" in text:
                return "What is your current weight and height?"
            elif "medication" in text or "washout" in text or "ULT" in text:
                return self._create_medication_question(text)
            elif "informed consent" in text or "written consent" in text:
                return "Are you willing and able to provide informed consent for this study?"
            elif "contraception" in text or "birth control" in text or "childbearing" in text:
                if "male" in text:
                    return "Are you willing to use appropriate contraception during the study period?"
                else:
                    return "Are you willing to use appropriate birth control during the study period?"
            elif "pregnancy" in text and "intends" in text:
                return "Are you currently pregnant, planning to become pregnant, or breastfeeding during the study period?"
            elif "comply" in text or "adherent" in text or "follow" in text:
                return "Are you able to follow the study requirements and attend all scheduled visits?"
            elif "participation" in text and "investigational" in text:
                return "Are you currently participating in any other research studies?"
            elif "visit" in text and ("scheduled" in text or "attend" in text):
                return "Are you able to attend all required study visits?"
            elif "migraine" in text and "history" in text and "year" in text:
                return "Have you had migraines for at least one year?"
            elif len(clean_text) > 200:  # Very long, complex criteria
                self._log_fallback_usage("LONG_INCLUSION_GENERIC", criterion.id, text, "Very long inclusion criteria - delegating to medical team")
                return "This study has specific medical requirements. Would you like our medical team to review your eligibility in detail?"
            else:
                # Smart inclusion fallback - avoid direct copying
                return self._create_smart_inclusion_question(clean_text, criterion.id)
        
        # Default fallback - this should rarely be used
        self._log_fallback_usage("ULTIMATE_FALLBACK", criterion.id, text, "No criterion type matched - using ultimate fallback")
        return f"Can you tell me about {clean_text}?"
    
    def _create_smart_exclusion_question(self, clean_text: str, criterion_id: int) -> str:
        """Create smart exclusion questions that avoid direct copying"""
        text_lower = clean_text.lower()
        
        # Medical conditions and symptoms patterns
        if any(condition in text_lower for condition in ['malignancy', 'cancer', 'tumor']):
            self._log_fallback_usage("EXCLUSION_SMART_MEDICAL", criterion_id, clean_text, "Cancer/malignancy pattern")
            return "Have you been diagnosed with cancer within the past 5 years?"
        
        elif any(condition in text_lower for condition in ['infection', 'infectious', 'sepsis']):
            self._log_fallback_usage("EXCLUSION_SMART_MEDICAL", criterion_id, clean_text, "Infection pattern")
            return "Do you currently have any active infections?"
        
        elif any(condition in text_lower for condition in ['pregnant', 'pregnancy', 'nursing', 'breastfeeding']):
            self._log_fallback_usage("EXCLUSION_SMART_REPRODUCTIVE", criterion_id, clean_text, "Pregnancy pattern")
            return "Are you currently pregnant or breastfeeding?"
        
        elif any(condition in text_lower for condition in ['employee', 'investigator', 'sponsor', 'staff']):
            self._log_fallback_usage("EXCLUSION_SMART_ADMINISTRATIVE", criterion_id, clean_text, "Study personnel pattern")
            return "Are you employed by or related to anyone involved in conducting this study?"
        
        elif any(condition in text_lower for condition in ['surgery', 'surgical', 'procedure']):
            self._log_fallback_usage("EXCLUSION_SMART_MEDICAL", criterion_id, clean_text, "Surgery pattern")
            return "Have you had any major surgeries recently?"
        
        elif 'hypersensitivity' in text_lower or 'allergy' in text_lower or 'allergic' in text_lower:
            self._log_fallback_usage("EXCLUSION_SMART_MEDICAL", criterion_id, clean_text, "Allergy pattern")
            return "Do you have any known allergies to medications used in this study?"
        
        elif any(condition in text_lower for condition in ['participation', 'participating', 'investigational']):
            self._log_fallback_usage("EXCLUSION_SMART_STUDY", criterion_id, clean_text, "Other studies pattern")
            return "Are you currently participating in any other research studies?"
        
        else:
            # Generic but user-friendly fallback
            self._log_fallback_usage("EXCLUSION_SMART_GENERIC", criterion_id, clean_text, "Generic smart exclusion")
            return "Do you have any medical conditions that might prevent you from participating in this study?"

    def _create_smart_inclusion_question(self, clean_text: str, criterion_id: int) -> str:
        """Create smart inclusion questions that avoid direct copying"""
        text_lower = clean_text.lower()
        
        # Study procedures and consent patterns
        if 'informed consent' in text_lower or 'willing' in text_lower:
            self._log_fallback_usage("INCLUSION_SMART_CONSENT", criterion_id, clean_text, "Informed consent pattern")
            return "Are you willing and able to provide informed consent for this study?"
        
        elif 'comply' in text_lower or 'follow' in text_lower or 'adherent' in text_lower:
            self._log_fallback_usage("INCLUSION_SMART_COMPLIANCE", criterion_id, clean_text, "Study compliance pattern")
            return "Are you able to follow the study requirements and attend all scheduled visits?"
        
        elif any(condition in text_lower for condition in ['contraception', 'childbearing', 'birth control']):
            self._log_fallback_usage("INCLUSION_SMART_REPRODUCTIVE", criterion_id, clean_text, "Contraception pattern")
            if 'male' in text_lower:
                return "Are you willing to use effective contraception during the study period?"
            else:
                return "Are you willing to use appropriate birth control during the study period?"
        
        elif 'diagnosis' in text_lower or 'diagnosed' in text_lower:
            self._log_fallback_usage("INCLUSION_SMART_DIAGNOSIS", criterion_id, clean_text, "Diagnosis pattern")
            return "Have you been diagnosed with the condition being studied in this trial?"
        
        elif any(word in text_lower for word in ['stable', 'dose', 'medication', 'therapy']):
            self._log_fallback_usage("INCLUSION_SMART_MEDICATION", criterion_id, clean_text, "Stable medication pattern")
            return "Are you currently on stable medication therapy for your condition?"

        # Handle "underlying conditions" / "high risk" patterns
        elif any(phrase in text_lower for phrase in ['underlying conditions', 'high risk', 'immunocompromised', 'severe outcomes']):
            self._log_fallback_usage("INCLUSION_SMART_RISK_FACTORS", criterion_id, clean_text, "High risk/underlying conditions pattern")
            # Only use COVID-specific language if criterion mentions COVID
            if 'covid' in text_lower or 'coronavirus' in text_lower or 'sars-cov' in text_lower:
                return "Do you have any underlying health conditions that put you at high risk for severe COVID-19 complications (such as heart disease, diabetes, lung disease, or weakened immune system)?"
            else:
                return "Do you have any underlying health conditions that might affect your safety in this study?"

        # Handle general health status requirements
        elif any(phrase in text_lower for phrase in ['good health', 'general health', 'healthy', 'medically stable']):
            self._log_fallback_usage("INCLUSION_SMART_HEALTH_STATUS", criterion_id, clean_text, "General health status pattern")
            return "Would you describe yourself as being in generally good health?"

        else:
            # Generic but user-friendly fallback
            self._log_fallback_usage("INCLUSION_SMART_GENERIC", criterion_id, clean_text, "Generic smart inclusion")
            return "Do you meet the medical requirements needed to participate in this study?"

    def _create_laboratory_question(self, text: str) -> str:
        """Create user-friendly questions for laboratory values"""
        text_lower = text.lower()
        
        # Extract lab name and create user-friendly questions
        if 'hba1c' in text_lower:
            return "Do you know your most recent HbA1c level? (This measures average blood sugar over 2-3 months)"
        elif 'egfr' in text_lower or 'glomerular filtration' in text_lower:
            return "Do you have any kidney problems or have you been told about abnormal kidney function?"
        elif 'alt' in text_lower or 'ast' in text_lower or 'alanine' in text_lower or 'aspartate' in text_lower:
            return "Do you have any liver problems or abnormal liver function tests?"
        elif 'bilirubin' in text_lower:
            return "Have you been told you have elevated bilirubin or liver problems?"
        elif 'triglycerides' in text_lower:
            return "Do you know your recent triglyceride levels from blood work?"
        elif 'creatinine' in text_lower:
            return "Do you have any kidney problems based on recent blood work?"
        elif 'hemoglobin' in text_lower:
            return "Have you been told you have anemia or low blood count?"
        else:
            # Generic laboratory question
            return "Have you had recent blood work done? Do you have any abnormal lab results?"

    def _create_time_based_exclusion_question(self, text: str) -> str:
        """Create questions for time-based exclusions"""
        text_lower = text.lower()
        
        # Extract timeframe
        time_match = re.search(r'within\s+(\d+)\s+(days?|weeks?|months?|years?)', text_lower)
        if time_match:
            number = time_match.group(1)
            unit = time_match.group(2)
            timeframe = f"{number} {unit}"
        else:
            timeframe = "recently"
        
        # Extract condition/procedure
        if 'surgery' in text_lower or 'surgical' in text_lower:
            return f"Have you had any major surgeries in the past {timeframe}?"
        elif 'medication' in text_lower or 'drug' in text_lower:
            return f"Have you started any new medications in the past {timeframe}?"
        elif 'vaccine' in text_lower or 'vaccination' in text_lower:
            return f"Have you received any vaccines in the past {timeframe}?"
        elif 'infection' in text_lower:
            return f"Have you had any infections requiring treatment in the past {timeframe}?"
        elif 'hospitalization' in text_lower or 'hospital' in text_lower:
            return f"Have you been hospitalized in the past {timeframe}?"
        elif 'investigational' in text_lower or 'research' in text_lower:
            return f"Have you participated in any other research studies in the past {timeframe}?"
        else:
            # Generic time-based exclusion
            return f"Have you had any significant medical events in the past {timeframe}?"

    def _log_fallback_usage(self, fallback_type: str, criterion_id: int, original_text: str, reason: str):
        """Log when fallback patterns are used for investigation and improvement"""
        logger.warning(f"🔍 FALLBACK_PATTERN_USED: {fallback_type}")
        logger.warning(f"   Criterion ID: {criterion_id}")
        logger.warning(f"   Reason: {reason}")
        logger.warning(f"   Original Text: {original_text[:100]}...")
        logger.warning(f"   Full Text Length: {len(original_text)} chars")
        
        # Also log to a structured format for analytics
        fallback_data = {
            "type": fallback_type,
            "criterion_id": criterion_id,
            "reason": reason,
            "text_length": len(original_text),
            "text_preview": original_text[:200],
            "timestamp": datetime.utcnow().isoformat()
        }
        logger.info(f"FALLBACK_ANALYTICS: {fallback_data}")

    def _clean_criterion_text(self, text: str) -> str:
        """Clean up criterion text to make it conversational"""
        # Remove numbered lists (1., 2., 12., etc.)
        cleaned = re.sub(r'^\d+\.\s*', '', text)
        
        # Remove leading/trailing whitespace
        cleaned = cleaned.strip()
        
        # Fix common database text anomalies
        cleaned = re.sub(r'\bhas\s+evidence\s+or\b', 'evidence of', cleaned)
        cleaned = re.sub(r'\bhas\s+a\s*,?\s*or\s+current\b', 'has a history or current', cleaned)
        cleaned = re.sub(r'\bhas\s+clinically\s+significant\b', 'clinically significant', cleaned)
        cleaned = re.sub(r'\bhas\s+been\s+hospitalized\b', 'been hospitalized', cleaned)
        cleaned = re.sub(r'\bhas\s+a\s+known\b', 'a known', cleaned)
        cleaned = re.sub(r'\bhas\s+any\s+condition\b', 'any condition', cleaned)
        
        # Fix double punctuation (.?, ?., etc.)
        cleaned = re.sub(r'[.?]{2,}', '.', cleaned)
        cleaned = re.sub(r'[.?]$', '', cleaned)  # Remove trailing punctuation
        
        # Lowercase the first letter unless it's an acronym
        if len(cleaned) > 1 and not cleaned[:2].isupper():
            cleaned = cleaned[0].lower() + cleaned[1:]
        
        # Handle specific medical terms to make them more natural
        replacements = {
            'history of': '',  # Remove redundant "history of" since we'll say "Do you have any..."
            'History of': '',
            'presence of': '',
            'Presence of': '',
            'ULT-naïve': 'not currently taking uric acid-lowering medications',
            'ULT': 'uric acid-lowering medications',
            'sUA': 'serum uric acid',
            'CLcr': 'creatinine clearance',
            'QTcF interval': 'heart rhythm measurement',
            'ICF': 'informed consent form',
            'IP administration': 'study medication',
            'concomitant medications': 'other medications',
            'documented medical records': 'medical records showing',
            'clinically significant': 'significant',
            'symptomatic kidney stones': 'kidney stones with symptoms',
            'moderately impaired hepatic function': 'moderate liver problems',
            'Child-Pugh Class B': 'moderate liver problems',
            'anaphylaxis': 'severe allergic reaction',
            'substance use disorders': 'drug or alcohol problems',
            'uncontrolled hypertension': 'high blood pressure that is not well controlled'
        }
        
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)
        
        # Clean up extra spaces and double spaces
        cleaned = ' '.join(cleaned.split())
        
        # Final validation - if text starts with grammatically incorrect patterns, fix them
        if cleaned.startswith('evidence or'):
            cleaned = 'evidence of ' + cleaned[11:]
        if cleaned.startswith('a , or current'):
            cleaned = 'a history or current' + cleaned[14:]
            
        return cleaned
    
    def _create_medication_question(self, text: str) -> str:
        """Create user-friendly medication questions from complex criteria"""
        text_lower = text.lower()
        
        # Handle ULT-specific criteria
        if "ult-naïve" in text_lower or ("ult" in text_lower and "washout" in text_lower):
            return "Are you currently taking any uric acid-lowering medications (such as allopurinol, febuxostat, or probenecid)?"
        elif "ult" in text_lower and "washout" in text_lower:
            return "Are you currently taking uric acid-lowering medications? If yes, would you be willing to stop them for the required washout period before starting the trial?"
        elif "stable dose" in text_lower:
            return "Are you currently taking any medications regularly? If yes, have you been on the same dose for at least 4 weeks?"
        elif "concomitant" in text_lower or "other medications" in text_lower:
            return "What other medications are you currently taking?"
        else:
            self._log_fallback_usage("MEDICATION_GENERIC", None, text, "No specific medication pattern matched - using generic medication question")
            return "Are you currently taking any medications related to your condition? If yes, would you be able to adjust them as needed for the trial?"
    
    def _extract_condition_from_text(self, text: str) -> str:
        """Extract medical condition from criterion text"""

        # Common condition patterns - ORDER MATTERS (most specific first)
        condition_patterns = [
            # Match "diagnosis of X" specifically
            r'diagnosis of ([^.,;]+)',
            # Skip "current diagnosis" and look for actual conditions
            r'current diagnosis or history of ([^.,;]+)',
            # Other patterns
            r'participants with ([^.,;]+)',
            r'subjects with ([^.,;]+)',
            r'history of ([^.,;]+)',
            r'confirmed ([^.,;]+)',
            # Known condition names (fallback)
            r'(gout|diabetes|depression|cancer|asthma|obesity|psoriasis|migraine|arthritis|t2dm|type 2 diabetes|rheumatoid arthritis|psoriatic arthritis)'
        ]

        for pattern in condition_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                condition = match.group(1).strip()

                # Skip if we extracted just "current" or other non-conditions
                if condition.lower() in ['current', 'a current', 'an acute', 'chronic', 'active']:
                    continue

                # For complex exclusion criteria with multiple conditions, take the first one
                if ' or ' in condition:
                    # Split and take first condition
                    condition = condition.split(' or ')[0].strip()

                # Clean up common suffixes
                condition = re.sub(r'\s+(diagnosis|mellitus|disorder).*$', '', condition, flags=re.IGNORECASE)

                # Handle specific mappings
                if 't2dm' in condition.lower():
                    return 'type 2 diabetes'

                return condition.lower()

        return ""
    
    def _extract_symptom_from_text(self, text: str) -> str:
        """Extract symptom/episode type from criterion text"""
        
        # Prioritize more specific symptoms over generic ones
        specific_patterns = [
            r'(gout\s+flare|flare)',
            r'(migraine|headache)',
            r'(seizure)',
            r'(panic\s+attack|attack)',
            r'(exacerbation)'
        ]
        
        generic_patterns = [
            r'(occurrence|episode)'
        ]
        
        # Try specific patterns first
        for pattern in specific_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                symptom = match.group(1).lower()
                # Clean up compound terms
                if 'gout flare' in symptom:
                    return 'flare'
                elif 'panic attack' in symptom:
                    return 'attack'
                return symptom
        
        # Fall back to generic patterns
        for pattern in generic_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).lower()
        
        return "episode"
    
    def _extract_timeframe_from_text(self, text: str) -> str:
        """Extract timeframe from criterion text"""
        
        # Timeframe patterns
        timeframe_patterns = [
            r'(last|past|previous)\s+(\d+)\s+(month|year|week|day)s?',
            r'within\s+(\d+)\s+(month|year|week|day)s?',
            r'in\s+the\s+(last|past)\s+(\d+)\s+(month|year|week|day)s?'
        ]
        
        for pattern in timeframe_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                groups = match.groups()
                if len(groups) >= 3:
                    number = groups[1]
                    unit = groups[2]
                elif len(groups) >= 2:
                    number = groups[0] if groups[0].isdigit() else groups[1]
                    unit = groups[1] if groups[0].isdigit() else groups[0]
                else:
                    continue
                return f"past {number} {unit}s" if number != "1" else f"past {unit}"
        
        return "past 12 months"  # Default timeframe
    
    def _extract_specific_medication_from_text(self, text: str) -> str:
        """Extract specific medication or therapy type from criterion text"""
        
        # Specific medication patterns
        med_patterns = [
            r'(urate-lowering therapy)',
            r'(insulin)',
            r'(metformin)',
            r'(chemotherapy)',
            r'(antidepressant)',
            r'([a-z]+-\d+\s+inhibitor)',  # e.g., DPP-4 inhibitor
            r'(glucocorticoid|steroid)',
            r'(antibiotic)'
        ]
        
        for pattern in med_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).lower()
        
        return ""
    
    def _extract_medication_type_from_text(self, text: str) -> str:
        """Extract medication type from criterion text for generic washout handling"""
        
        # Enhanced medication type patterns - dynamic for any indication
        patterns = [
            r'(urate-lowering therapy|ULT)',
            r'(pain medications?)',
            r'(incretin medications?)',
            r'(antidepressant medications?)',
            r'(steroid medications?)',
            r'(glucocorticoid medications?)',
            r'(antibiotic medications?)',
            r'(insulin)',
            r'(metformin)',
            r'(chemotherapy)',
            r'([a-z]+-\d+\s+inhibitors?)',  # e.g., DPP-4 inhibitors
            r'(ACE inhibitors?)',
            r'(beta[- ]?blockers?)',
            r'(calcium[- ]?channel[- ]?blockers?)',
            r'(proton[- ]?pump[- ]?inhibitors?)',
            r'(statin medications?)',
            r'(anti[- ]?inflammatory medications?)',
            r'(\w+\s+therapy)',  # generic therapy types
            r'(\w+\s+medications?)',  # generic medication types
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).lower()
        
        return "medications"  # Generic fallback
    
    def _extract_specific_medications_from_text(self, text: str) -> List[str]:
        """Extract specific medication names from criterion text"""
        from typing import List
        medications = []
        
        # Common medication patterns - look for drug names
        # First look for explicit lists or bullet points
        list_patterns = [
            r'including\s+([^.]+)',
            r'such\s+as\s+([^.]+)',
            r'following\s+([^:]+):([^.]+)',
            r'agents?\s*:\s*([^.]+)',
        ]
        
        for pattern in list_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                # Safely get the last group that exists
                try:
                    if len(match.groups()) > 1:
                        med_text = match.group(-1)  # Get last group if multiple groups exist
                    else:
                        med_text = match.group(1)  # Get first group if only one exists
                except IndexError:
                    # Fallback to the entire match if no groups
                    med_text = match.group(0)
                
                # Split by common delimiters and clean up
                potential_meds = re.split(r'[,;]\s*|\s+and\s+|\s+or\s+', med_text)
                for med in potential_meds:
                    cleaned = med.strip().strip('(),')
                    if cleaned and len(cleaned) > 2:
                        medications.append(cleaned.title())
        
        # Look for specific drug name patterns (capitalize first letter, common endings)
        drug_patterns = [
            r'\b([A-Z][a-z]+(?:ol|in|ate|ide|ine|one|pril|tan|zine|mab))\b',  # Common drug endings
            r'\b([A-Z][a-z]{3,})\s+(?:mg|mcg|units?|tablets?)\b',  # Drug followed by dosage
        ]
        
        for pattern in drug_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if match not in medications and len(match) > 3:
                    medications.append(match)
        
        return medications[:5]  # Limit to prevent overly long lists
    
    def _get_medication_class_examples(self, medication_class: str) -> List[str]:
        """Get common examples for medication classes to help patients understand"""
        examples_map = {
            'ult': ['Allopurinol', 'Febuxostat', 'Probenecid'],
            'urate-lowering therapy': ['Allopurinol', 'Febuxostat', 'Probenecid'],
            'statin medications': ['Atorvastatin (Lipitor)', 'Simvastatin (Zocor)', 'Rosuvastatin (Crestor)'],
            'statins': ['Atorvastatin (Lipitor)', 'Simvastatin (Zocor)', 'Rosuvastatin (Crestor)'],
            'ace inhibitors': ['Lisinopril', 'Enalapril', 'Ramipril'],
            'beta blockers': ['Metoprolol', 'Propranolol', 'Atenolol'],
            'beta-blockers': ['Metoprolol', 'Propranolol', 'Atenolol'],
            'calcium channel blockers': ['Amlodipine', 'Nifedipine', 'Diltiazem'],
            'calcium-channel-blockers': ['Amlodipine', 'Nifedipine', 'Diltiazem'],
            'proton pump inhibitors': ['Omeprazole (Prilosec)', 'Lansoprazole (Prevacid)', 'Esomeprazole (Nexium)'],
            'proton-pump-inhibitors': ['Omeprazole (Prilosec)', 'Lansoprazole (Prevacid)', 'Esomeprazole (Nexium)'],
            'pain medications': ['Ibuprofen (Advil)', 'Acetaminophen (Tylenol)', 'Naproxen (Aleve)'],
            'anti-inflammatory medications': ['Ibuprofen (Advil)', 'Naproxen (Aleve)', 'Diclofenac'],
            'antidepressant medications': ['Sertraline (Zoloft)', 'Fluoxetine (Prozac)', 'Escitalopram (Lexapro)'],
            'steroid medications': ['Prednisone', 'Hydrocortisone', 'Methylprednisolone'],
            'glucocorticoid medications': ['Prednisone', 'Hydrocortisone', 'Methylprednisolone'],
            'incretin medications': ['Metformin', 'Semaglutide (Ozempic)', 'Liraglutide (Victoza)'],
        }
        
        # Normalize the medication class for lookup
        normalized_class = medication_class.lower().strip()
        
        # Try exact match first
        if normalized_class in examples_map:
            return examples_map[normalized_class]
        
        # Try with normalized spaces/hyphens
        normalized_with_hyphens = normalized_class.replace(' ', '-')
        if normalized_with_hyphens in examples_map:
            return examples_map[normalized_with_hyphens]
        
        # Try partial matches (both ways)
        for key, examples in examples_map.items():
            key_normalized = key.replace('-', ' ').replace('  ', ' ')
            class_normalized = normalized_class.replace('-', ' ').replace('  ', ' ')
            
            if (key_normalized in class_normalized or 
                class_normalized in key_normalized or
                key in normalized_class or 
                normalized_class in key):
                return examples[:3]  # Limit to 3 examples
        
        return []  # No examples found
    
    def _extract_washout_period_from_text(self, text: str) -> str:
        """Extract washout period from criterion text"""
        
        # Washout period patterns
        patterns = [
            r'(\d+)\s*days?',
            r'(\d+)\s*weeks?',
            r'(\d+)\s*months?',
            r'at least\s*(\d+)\s*days?',
            r'minimum\s*(\d+)\s*days?',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                # Return the full matched phrase for better context
                return match.group(0)
        
        return ""  # No specific period found
    
    def _parse_height_weight(self, text: str) -> Dict[str, float]:
        """Parse height and weight from user input and convert to metric"""

        logger.info(f"BMI_PARSING: Starting height/weight parsing for input: '{text}'")
        result = {"height_cm": None, "weight_kg": None}

        # Convert written numbers to digits (e.g., "six foot" → "6 foot")
        word_to_number = {
            'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
            'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
            'ten': '10', 'eleven': '11', 'twelve': '12'
        }

        text_normalized = text.lower()
        for word, digit in word_to_number.items():
            text_normalized = re.sub(r'\b' + word + r'\b', digit, text_normalized, flags=re.IGNORECASE)

        # Use normalized text for parsing
        text = text_normalized
        logger.debug(f"BMI_PARSING: After word-to-number conversion: '{text}'")

        # Height patterns (ordered from most specific to least specific)
        height_patterns = [
            r"(\d+)'(\d+)\"",  # 6'6"
            r"(\d+)'(\d+)",  # 6'2 (no closing quote - common format)
            r"(\d+)'(\d*)\"",  # 6'0" (handles missing or single digit inches)
            r"(\d+)'\s*,",  # 6', (apostrophe with comma separator)
            r"(\d+)\s*feet?\s*(\d+)\s*inch",  # 6 feet 6 inches
            r"(\d+)\s*foot\s*(\d+)",  # 6 foot 5
            r"(\d+)\s*foot",  # 6 foot (no inches)
            r"(\d+)\s*ft\s*(\d+)\s*in",  # 6 ft 6 in
            r"(\d+)\s*ft",  # 6 ft (no inches)
            r"(\d+)\s*cm",  # 180 cm
            r"(\d+\.?\d*)\s*m",  # 1.8 m
        ]
        
        for pattern in height_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                logger.debug(f"BMI_PARSING: Height pattern matched: {pattern} -> {match.groups()}")
                if "cm" in pattern:
                    result["height_cm"] = float(match.group(1))
                    logger.info(f"BMI_PARSING: Height parsed as {result['height_cm']} cm (direct)")
                elif "m" in pattern and "cm" not in pattern:
                    result["height_cm"] = float(match.group(1)) * 100
                    logger.info(f"BMI_PARSING: Height parsed as {result['height_cm']} cm (from meters)")
                else:  # feet and inches
                    feet = int(match.group(1))
                    # Handle cases where inches might be empty or missing
                    inches = 0
                    if len(match.groups()) > 1 and match.group(2):
                        inches = int(match.group(2))
                    result["height_cm"] = (feet * 12 + inches) * 2.54
                    logger.info(f"BMI_PARSING: Height parsed as {result['height_cm']} cm (from {feet}'{inches}\")")
                break
        
        if not result["height_cm"]:
            logger.warning(f"BMI_PARSING: No height pattern matched in: '{text}'")
        
        # Weight patterns (more specific to avoid matching height numbers)
        weight_patterns = [
            r"(\d+\.?\d*)\s*(?:lbs?|pounds?)",  # 230 lbs, 230 pounds
            r"(\d+\.?\d*)\s*kg",  # 104 kg
            # Handle comma-separated format "6 foot, 215 pounds"
            r",\s*(\d+\.?\d*)\s*(?:lbs?|pounds?)",  # ", 215 pounds"
            r",\s*(\d+\.?\d*)\s*kg",  # ", 104 kg"
        ]
        
        # Try explicit weight patterns first
        weight_found = False
        for pattern in weight_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                weight = float(match.group(1))
                logger.debug(f"BMI_PARSING: Weight pattern matched: {pattern} -> {match.groups()}")
                if "kg" in pattern:
                    result["weight_kg"] = weight
                    logger.info(f"BMI_PARSING: Weight parsed as {result['weight_kg']} kg (direct)")
                else:  # pounds
                    result["weight_kg"] = weight * 0.453592
                    logger.info(f"BMI_PARSING: Weight parsed as {result['weight_kg']} kg (from {weight} lbs)")
                weight_found = True
                break
        
        # If no explicit weight units found, look for standalone numbers > 50 (likely weight)
        if not weight_found:
            logger.debug(f"BMI_PARSING: No explicit weight units found, searching for standalone numbers")
            # Find all numbers in the text
            all_numbers = re.findall(r'\b(\d+\.?\d*)\b', text)
            logger.debug(f"BMI_PARSING: All numbers found: {all_numbers}")
            
            if all_numbers:
                # Filter out numbers that are likely height-related (under 10 or between 60-84 inches)
                potential_weights = []
                for num_str in all_numbers:
                    num = float(num_str)
                    # Skip numbers likely to be height (feet, inches in reasonable ranges)
                    if not (num < 10 or (60 <= num <= 84)):
                        potential_weights.append(num)
                        logger.debug(f"BMI_PARSING: Number {num} considered as potential weight")
                    else:
                        logger.debug(f"BMI_PARSING: Number {num} filtered out (likely height)")
                
                logger.debug(f"BMI_PARSING: Potential weights after filtering: {potential_weights}")
                
                # Use the largest remaining number as weight (assuming pounds)
                if potential_weights:
                    weight = max(potential_weights)
                    if weight >= 50:  # Reasonable minimum weight
                        result["weight_kg"] = weight * 0.453592
                        logger.info(f"BMI_PARSING: Weight parsed as {result['weight_kg']} kg (from standalone {weight}, assumed lbs)")
                    else:
                        logger.warning(f"BMI_PARSING: Largest potential weight {weight} too small (< 50)")
                else:
                    logger.warning(f"BMI_PARSING: No potential weights found after filtering")
        
        if not weight_found and not result["weight_kg"]:
            logger.warning(f"BMI_PARSING: No weight could be parsed from: '{text}'")
        
        logger.info(f"BMI_PARSING: Final result - Height: {result['height_cm']} cm, Weight: {result['weight_kg']} kg")
        
        return result
    
    def _validate_user_response(self, criterion: 'TrialCriterion', user_response: str) -> Dict[str, Any]:
        """Validate user response and return validation result with helpful feedback"""
        
        logger.debug(f"VALIDATION: Starting validation for response: '{user_response}' to criterion: {criterion.criterion_text[:50]}...")
        
        validation_result = {
            "is_valid": True,
            "needs_confirmation": False,
            "feedback_message": "",
            "parsed_data": None,
            "suggested_format": ""
        }
        
        text = criterion.criterion_text.lower()
        response = user_response.strip()
        
        # Determine expected answer type for this criterion
        expected_answer_type = self._determine_answer_type(criterion)
        
        # Use our improved answer type detection instead of hardcoded keywords
        logger.debug(f"VALIDATION: Expected answer type determined as: {expected_answer_type}")
        
        # BMI/Height/Weight validation
        if expected_answer_type == "text" and ("bmi" in text or "body mass index" in text or "body weight" in text):
            return self._validate_bmi_response(response, validation_result)
        
        # Medication validation for text responses
        elif expected_answer_type == "text" and any(word in text for word in ["medication", "taking", "medicines"]):
            return self._validate_medication_response(response, validation_result)
        
        # Numeric validation (age, counts, etc.)
        elif expected_answer_type == "number":
            return self._validate_numeric_response(response, text, validation_result)
        
        # Yes/No validation - this should handle inclusion/exclusion criteria properly
        elif expected_answer_type == "yes_no":
            return self._validate_yes_no_response(response, validation_result)
        
        # Date validation
        elif "date" in text or expected_answer_type == "date":
            validation_result = self._validate_date_response(response, validation_result)
        
        # Log validation result
        if not validation_result["is_valid"]:
            logger.warning(f"VALIDATION: Response invalid - {validation_result['feedback_message']}")
        elif validation_result["needs_confirmation"]:
            logger.info(f"VALIDATION: Response needs confirmation - {validation_result['feedback_message']}")
        else:
            logger.debug(f"VALIDATION: Response valid")
        
        return validation_result
    
    def _validate_bmi_response(self, response: str, validation_result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate BMI/height/weight responses with detailed feedback"""
        hw_data = self._parse_height_weight(response)
        
        if not hw_data["height_cm"] and not hw_data["weight_kg"]:
            validation_result.update({
                "is_valid": False,
                "feedback_message": "I couldn't find height and weight information in your response. Could you please provide both your height and weight together?",
                "suggested_format": "Examples:\n• '6 feet 2 inches, 180 pounds'\n• '6'0\", 215 lbs'\n• '6 foot 5, 200 pounds'"
            })
            return validation_result
        
        if not hw_data["height_cm"]:
            # We have weight but no height - show what we found
            weight_lbs = int(hw_data["weight_kg"] / 0.453592) if hw_data["weight_kg"] else "your weight"
            validation_result.update({
                "is_valid": False,
                "feedback_message": f"I found your weight ({weight_lbs} lbs) but couldn't determine your height. Could you specify your height?",
                "suggested_format": "Examples: '6 feet 2 inches', '5'8\"', '6 foot 5', or '72 inches'"
            })
            return validation_result
        
        if not hw_data["weight_kg"]:
            # We have height but no weight - show what we found
            height_ft = int(hw_data['height_cm'] / 2.54 / 12)
            height_in = int((hw_data['height_cm'] / 2.54) % 12)
            validation_result.update({
                "is_valid": False,
                "feedback_message": f"I found your height ({height_ft}'{height_in}\") but couldn't determine your weight. Could you specify your weight?",
                "suggested_format": "Examples: '180 pounds', '180 lbs', '215', or '82 kg'"
            })
            return validation_result
        
        # Calculate BMI and validate ranges
        bmi = self._calculate_bmi(hw_data["height_cm"], hw_data["weight_kg"])
        if bmi:
            height_ft = int(hw_data['height_cm'] / 2.54 / 12)
            height_in = int((hw_data['height_cm'] / 2.54) % 12)
            weight_lbs = int(hw_data['weight_kg'] / 0.453592)
            
            validation_result["parsed_data"] = {
                "height_cm": hw_data["height_cm"],
                "weight_kg": hw_data["weight_kg"],
                "bmi": bmi,
                "display_height": f"{height_ft}'{height_in}\"",
                "display_weight": f"{weight_lbs} lbs"
            }
            
            # Gentle confirmation for values that might be data entry errors
            if bmi < 15 or bmi > 60:
                validation_result.update({
                    "needs_confirmation": True,
                    "feedback_message": f"I calculated your BMI as {bmi:.1f} based on {height_ft}'{height_in}\\\" and {weight_lbs} lbs. Just to double-check - could you please confirm these measurements are correct?"
                })
            elif hw_data["height_cm"] < 120 or hw_data["height_cm"] > 220:  # 4' to 7'2"
                validation_result.update({
                    "needs_confirmation": True,
                    "feedback_message": f"I have your height as {height_ft}'{height_in}\\\". Could you please confirm this is correct?"
                })
            elif hw_data["weight_kg"] < 30 or hw_data["weight_kg"] > 300:  # 66 lbs to 660 lbs
                validation_result.update({
                    "needs_confirmation": True,
                    "feedback_message": f"I have your weight as {weight_lbs} pounds. Could you please confirm this is correct?"
                })
            else:
                # Quiet success for normal values - no need to repeat back their info
                pass
        
        return validation_result
    
    def _validate_numeric_response(self, response: str, criterion_text: str, validation_result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate numeric responses with range checking"""
        numeric_value = self._extract_numeric_value(response)
        
        if numeric_value is None:
            # Check for common non-numeric responses
            response_lower = response.lower().strip()
            if any(word in response_lower for word in ["none", "zero", "no", "never"]):
                validation_result["parsed_data"] = {"numeric_value": 0}
                return validation_result
            elif any(word in response_lower for word in ["many", "lots", "several", "multiple"]):
                validation_result.update({
                    "is_valid": False,
                    "feedback_message": "Could you provide a specific number instead of a general term?",
                    "suggested_format": "Please provide an exact number (e.g., '5', '12', '0')"
                })
                return validation_result
            else:
                validation_result.update({
                    "is_valid": False,
                    "feedback_message": "I couldn't find a number in your response. Could you please provide a numeric value?",
                    "suggested_format": "Examples: '3', 'zero', '0', '15'"
                })
                return validation_result
        
        validation_result["parsed_data"] = {"numeric_value": numeric_value}
        
        # Age validation
        if "age" in criterion_text:
            if numeric_value < 1 or numeric_value > 120:
                validation_result.update({
                    "needs_confirmation": True,
                    "feedback_message": f"You indicated your age is {int(numeric_value)}. Could you please confirm this is correct?"
                })
        
        # Flare/episode validation
        elif any(word in criterion_text for word in ["flare", "episode", "occurrence"]):
            if numeric_value < 0:
                validation_result.update({
                    "is_valid": False,
                    "feedback_message": "The number of episodes cannot be negative. Could you provide a number of 0 or greater?"
                })
            elif numeric_value > 100:
                validation_result.update({
                    "needs_confirmation": True,
                    "feedback_message": f"You indicated {int(numeric_value)} episodes. This seems quite high - could you please confirm this number is correct?"
                })
        
        return validation_result
    
    def _validate_medication_response(self, response: str, validation_result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate medication responses - accept both yes/no and specific medication names"""
        response_lower = response.lower().strip()
        
        # Check for no/none responses
        no_patterns = ["no", "n", "none", "nothing", "not taking", "don't take", "no medications"]
        if any(pattern in response_lower for pattern in no_patterns):
            validation_result["parsed_data"] = {"medication": "none", "taking_medication": False}
            return validation_result
        
        # Check for yes responses (without specific medications)
        yes_patterns = ["yes", "y", "yeah", "yep"]
        if any(pattern in response_lower for pattern in yes_patterns) and len(response_lower.split()) <= 2:
            validation_result["parsed_data"] = {"medication": "unspecified", "taking_medication": True}
            return validation_result
        
        # Check for specific medication names (common medications)
        common_medications = [
            "advil", "ibuprofen", "tylenol", "acetaminophen", "aspirin", "aleve", "naproxen",
            "allopurinol", "febuxostat", "probenecid", "colchicine", "prednisone", "metformin",
            "lisinopril", "amlodipine", "atorvastatin", "omeprazole", "gabapentin", "tramadol"
        ]
        
        medications_found = [med for med in common_medications if med in response_lower]
        if medications_found:
            validation_result["parsed_data"] = {
                "medication": ", ".join(medications_found), 
                "taking_medication": True,
                "specific_medications": medications_found
            }
            return validation_result
        
        # If response contains text but no recognized patterns, accept as medication name
        if len(response.strip()) > 0:
            validation_result["parsed_data"] = {
                "medication": response.strip(), 
                "taking_medication": True,
                "user_provided": True
            }
            return validation_result
        
        # Empty or unclear response
        validation_result.update({
            "is_valid": False,
            "feedback_message": "Could you please tell me what medications you're taking, or respond with 'none' if you're not taking any?",
            "suggested_format": "Examples: 'advil', 'none', 'metformin and lisinopril'"
        })
        
        return validation_result
    
    def _validate_yes_no_response(self, response: str, validation_result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate yes/no responses"""
        response_lower = response.lower().strip()
        
        yes_patterns = ["yes", "y", "yeah", "yep", "true", "correct", "right"]
        no_patterns = ["no", "n", "nope", "false", "incorrect", "wrong"]
        
        if any(pattern in response_lower for pattern in yes_patterns):
            validation_result["parsed_data"] = {"answer": "yes"}
        elif any(pattern in response_lower for pattern in no_patterns):
            validation_result["parsed_data"] = {"answer": "no"}
        else:
            validation_result.update({
                "is_valid": False,
                "feedback_message": "I couldn't determine if your answer is yes or no. Could you please respond with 'yes' or 'no'?",
                "suggested_format": "Please answer: 'yes' or 'no'"
            })
        
        return validation_result
    
    def _validate_date_response(self, response: str, validation_result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate date responses"""
        from datetime import datetime
        
        # Basic date patterns
        date_patterns = [
            r'\d{1,2}/\d{1,2}/\d{4}',  # MM/DD/YYYY
            r'\d{4}-\d{1,2}-\d{1,2}',  # YYYY-MM-DD
            r'\b\w+\s+\d{1,2},?\s+\d{4}',  # Month DD, YYYY
        ]
        
        has_date_pattern = any(re.search(pattern, response) for pattern in date_patterns)
        
        if not has_date_pattern:
            validation_result.update({
                "is_valid": False,
                "feedback_message": "I couldn't find a date in your response. Could you provide a date?",
                "suggested_format": "Example: 'January 15, 2023' or '1/15/2023'"
            })
        
        return validation_result
    
    def _calculate_bmi(self, height_cm: float, weight_kg: float) -> float:
        """Calculate BMI from height in cm and weight in kg"""
        
        logger.debug(f"BMI_CALCULATION: Input - Height: {height_cm} cm, Weight: {weight_kg} kg")
        
        if height_cm and weight_kg:
            height_m = height_cm / 100
            bmi = weight_kg / (height_m ** 2)
            
            # Log BMI with context
            height_ft = int(height_cm / 2.54 / 12)
            height_in = int((height_cm / 2.54) % 12)
            weight_lbs = int(weight_kg / 0.453592)
            
            logger.info(f"BMI_CALCULATION: BMI {bmi:.1f} calculated from {height_ft}'{height_in}\" ({height_cm:.1f}cm), {weight_lbs}lbs ({weight_kg:.1f}kg)")
            
            # Flag unusual BMI calculations
            if bmi < 15 or bmi > 60:
                logger.warning(f"BMI_CALCULATION: Unusual BMI value {bmi:.1f} - may indicate parsing error")
            
            return bmi
        else:
            logger.warning(f"BMI_CALCULATION: Cannot calculate BMI - missing height ({height_cm}) or weight ({weight_kg})")
            return None
    
    def _extract_numeric_value(self, text: str) -> float:
        """Extract numeric value from text"""
        
        text_lower = text.lower().strip()
        
        # Handle text numbers first
        text_numbers = {
            'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
            'once': 1, 'twice': 2, 'thrice': 3
        }
        
        for word, num in text_numbers.items():
            if word in text_lower:
                return float(num)
        
        # Handle frequency descriptions that should convert to numbers
        frequency_conversions = {
            'daily': 1, 'every day': 1, 'once daily': 1, 'once a day': 1,
            'twice daily': 2, 'twice a day': 2, 'two times daily': 2,
            'three times daily': 3, 'three times a day': 3, 'with meals': 3,
            'four times daily': 4, 'four times a day': 4,
            'every 8 hours': 3, 'every 6 hours': 4, 'every 4 hours': 6,
            'morning and evening': 2, 'morning and night': 2
        }
        
        for phrase, num in frequency_conversions.items():
            if phrase in text_lower:
                return float(num)
        
        # Handle decimal numbers
        numbers = re.findall(r'\d+\.?\d*', text)
        if numbers:
            return float(numbers[0])
        
        return None
    
    def _count_medications_in_text(self, text: str) -> int:
        """Count medications mentioned in text"""
        text_lower = text.lower().strip()
        
        # Common medications to recognize
        common_medications = [
            'advil', 'ibuprofen', 'tylenol', 'acetaminophen', 'aspirin', 'aleve', 'naproxen',
            'allopurinol', 'febuxostat', 'probenecid', 'colchicine', 'prednisone', 'metformin',
            'lisinopril', 'amlodipine', 'atorvastatin', 'omeprazole', 'gabapentin', 'tramadol',
            'insulin', 'synthroid', 'levothyroxine', 'hydrochlorothiazide', 'simvastatin',
            'warfarin', 'clopidogrel', 'losartan', 'furosemide', 'pantoprazole'
        ]
        
        # Count recognized medications
        count = 0
        for med in common_medications:
            if med in text_lower:
                count += 1
        
        # If no specific medications found, try to count using conjunctions
        if count == 0:
            # Look for patterns like \"X and Y\" or \"X, Y, Z\"
            conjunctions = [' and ', ', ', ' & ', ' plus ']
            for conj in conjunctions:
                if conj in text_lower:
                    # Simple heuristic: count parts separated by conjunctions
                    parts = len([part.strip() for part in text_lower.split(conj) if part.strip()])
                    if parts > 1:
                        count = parts
                        break
        
        # If still no count but text contains medication-like words, assume 1
        if count == 0:
            medication_indicators = ['pill', 'tablet', 'capsule', 'medication', 'medicine', 'drug']
            if any(indicator in text_lower for indicator in medication_indicators) and not any(negative in text_lower for negative in ['no', 'none', 'never']):
                count = 1
        
        return count
    
    def _evaluate_numeric_criterion(self, criterion_text: str, user_value: float) -> Dict[str, Any]:
        """Evaluate numeric criteria like ranges, minimums, maximums"""
        
        text = criterion_text.lower()
        
        # Range patterns: "between X and Y", "X to Y", "X-Y"
        range_patterns = [
            r'between\s+(\d+\.?\d*)\s+(?:and|to)\s+(\d+\.?\d*)',
            r'(\d+\.?\d*)\s*(?:to|-)\s*(\d+\.?\d*)',
            r'≥\s*(\d+\.?\d*)\s*(?:and|to)\s*≤\s*(\d+\.?\d*)',
        ]
        
        for pattern in range_patterns:
            match = re.search(pattern, text)
            if match:
                min_val = float(match.group(1))
                max_val = float(match.group(2))
                
                if min_val <= user_value <= max_val:
                    return {
                        "eligible": True,
                        "explanation": f"Value {user_value} is within required range {min_val}-{max_val}"
                    }
                else:
                    return {
                        "eligible": False,
                        "explanation": f"Value {user_value} is outside required range {min_val}-{max_val}"
                    }
        
        # Minimum patterns: "≥ X", "at least X", "minimum X"
        min_patterns = [
            r'≥\s*(\d+\.?\d*)',
            r'at least\s+(\d+\.?\d*)',
            r'minimum\s+(\d+\.?\d*)',
            r'>\s*(\d+\.?\d*)',
        ]
        
        for pattern in min_patterns:
            match = re.search(pattern, text)
            if match:
                min_val = float(match.group(1))
                operator = "≥" if "≥" in pattern or "at least" in pattern or "minimum" in pattern else ">"
                
                if (operator == "≥" and user_value >= min_val) or (operator == ">" and user_value > min_val):
                    return {
                        "eligible": True,
                        "explanation": f"{user_value} meets minimum requirement of {operator}{min_val}"
                    }
                else:
                    return {
                        "eligible": False,
                        "explanation": f"{user_value} does not meet minimum requirement of {operator}{min_val}"
                    }
        
        # Maximum patterns: "≤ X", "no more than X", "maximum X"
        max_patterns = [
            r'≤\s*(\d+\.?\d*)',
            r'no more than\s+(\d+\.?\d*)',
            r'maximum\s+(\d+\.?\d*)',
            r'<\s*(\d+\.?\d*)',
        ]
        
        for pattern in max_patterns:
            match = re.search(pattern, text)
            if match:
                max_val = float(match.group(1))
                operator = "≤" if "≤" in pattern or "no more than" in pattern or "maximum" in pattern else "<"
                
                if (operator == "≤" and user_value <= max_val) or (operator == "<" and user_value < max_val):
                    return {
                        "eligible": True,
                        "explanation": f"{user_value} meets maximum requirement of {operator}{max_val}"
                    }
                else:
                    return {
                        "eligible": False,
                        "explanation": f"{user_value} exceeds maximum requirement of {operator}{max_val}"
                    }
        
        return None
    
    def _determine_answer_type(self, criterion: TrialCriterion) -> str:
        """Enhanced answer type detection with better logic"""
        parsed = criterion.parsed_json or {}
        text = criterion.criterion_text.lower()
        
        # Age questions - always numeric
        if parsed.get("field") == "age" or "age" in text or "years old" in text:
            return "number"
        
        # Count questions - always numeric  
        if any(phrase in text for phrase in ["how many", "number of", "count of", "per month", "per day", "headache-days", "more than"]):
            return "number"
        
        # Specific medical measurements and frequency questions - MUST come before yes/no detection
        # Check for frequency/count keywords that indicate numeric answer
        if any(keyword in text for keyword in ["flare", "attack", "episode", "occurrence", "times per", "per month", "per day"]):
            return "number"
        elif "hemoglobin" in text:
            return "text"  # Can be a number or "I don't know"
        # CRITICAL FIX: Check for "body weight change" BEFORE "body weight" to avoid false match
        elif "body weight change" in text or "weight change" in text:
            return "yes_no"  # Weight change questions are yes/no, not height/weight entry
        elif "bmi" in text or "body mass index" in text or "body weight" in text or "height" in text:
            return "text"  # Weight and height parsing needed
        elif "washout" in text or "wash-out" in text:
            return "text"  # More complex response needed
            
        # Yes/No questions - explicit detection for inclusion/exclusion criteria
        if criterion.criterion_type in ["inclusion", "exclusion"]:
            # Check if it's asking about having/meeting a condition
            if any(phrase in text for phrase in [
                "do you have", "have you", "are you", "do you meet", "does the patient",
                "patient has", "patient meets", "diagnosed with", "history of",
                "able to", "willing to", "meets this requirement", "meet this requirement"
            ]):
                return "yes_no"
            
            # Medication COUNT questions should be numeric (overrides general medication text rule)
            if any(phrase in text for phrase in [
                "more than", "taking more than", "concurrent medications", "headache-days per month", "twice daily", "three times daily"
            ]):
                return "number"
                
            # Medication questions should allow text responses (medication names)
            elif any(phrase in text for phrase in [
                "currently taking", "taking any", "medication", "medicines"
            ]):
                return "text"
                
            # Check for criterion text patterns that indicate yes/no
            if any(phrase in text for phrase in [
                "greater than", "less than", "between", "within", "defined by",
                "criteria", "without aura", "with aura", "last between"
            ]):
                return "yes_no"
        
        # Diagnosis field should be yes/no
        if parsed.get("field") == "diagnosis":
            return "yes_no"
        
        # Default fallback with better logic
        # If it's an exclusion criterion, it's usually yes/no
        if criterion.criterion_type == "exclusion":
            return "yes_no"
            
        # If it's an inclusion criterion, check content
        if criterion.criterion_type == "inclusion":
            # Inclusion criteria are often about meeting requirements
            return "yes_no"
        
        # Final fallback
        return "text"

    def _check_question_type_mismatch(self, question_text: str, expected_answer_type: str) -> str:
        """
        Check if question text matches expected answer type.
        Returns description of mismatch, or empty string if valid.
        """
        text_lower = question_text.lower()

        # Check for count questions (should be "number" type)
        if any(phrase in text_lower for phrase in ["how many", "how much", "how often", "count"]):
            if expected_answer_type != "number":
                return f"Question asks for count but expects {expected_answer_type}"

        # Check for yes/no questions (shouldn't ask for counts)
        if expected_answer_type == "yes_no":
            if any(phrase in text_lower for phrase in ["how many", "how much", "count", "number of"]):
                return f"Question asks for count but type is yes_no"

        # Check for numeric type (should ask for numbers)
        if expected_answer_type == "number":
            if not any(phrase in text_lower for phrase in ["age", "weight", "height", "bmi", "how many", "how much", "count", "number"]):
                return f"Expected number but question doesn't ask for one"

        return ""  # No mismatch

    async def parse_answer(self, question: PrescreeningQuestion, user_response: str) -> PrescreeningAnswer:
        """Parse user response using Gemini"""
        try:
            # Build prompt for Gemini
            prompt = f"""{self._get_answer_parsing_prompt(question)}

User's answer: '{user_response}'

Return a JSON object with:
- parsed_value: The extracted value
- interpretation: "yes", "no", "unclear", "number", or "text"
- confidence: Number between 0 and 1
"""
            
            # Use Gemini to parse the response
            result = await self.gemini.extract_json(prompt, "")
            
            if result and "interpretation" in result:
                return PrescreeningAnswer(
                    criterion_id=question.criterion_id,
                    question_text=question.question_text,
                    user_response=user_response,
                    parsed_value=result.get("parsed_value"),
                    interpretation=result.get("interpretation"),
                    confidence=result.get("confidence", 0.8)
                )
            
            # Fallback parsing
            return self._parse_answer_simple(question, user_response)
            
        except Exception as e:
            logger.error(f"Error parsing answer with Gemini: {str(e)}")
            return self._parse_answer_simple(question, user_response)
    
    def _get_answer_parsing_prompt(self, question: PrescreeningQuestion) -> str:
        """Get system prompt for answer parsing"""
        return f"""You are parsing user responses to clinical trial prescreening questions.

Question: {question.question_text}
Expected answer type: {question.expected_answer_type}
Criterion type: {question.criterion_type}

Your job is to interpret the user's natural language response and extract the key information.

For yes/no questions, accept variations like:
- "yes", "yeah", "yep", "y", "sure", "definitely"
- "no", "nope", "not really", "never", "negative"
- "I have been diagnosed" = yes
- "My doctor said I have X" = yes

For numbers, extract the numeric value even from text like "thirty-five" or "I'm 45 years old"

Be flexible with natural language but confident in your interpretation.
"""
    
    
    def _parse_answer_simple(self, question: PrescreeningQuestion, user_response: str) -> PrescreeningAnswer:
        """Simple fallback answer parsing"""
        response_lower = user_response.lower().strip()
        
        # Yes/no parsing
        if question.expected_answer_type == "yes_no":
            yes_words = ["yes", "yeah", "yep", "y", "sure", "definitely", "i have", "diagnosed", "correct"]
            no_words = ["no", "nope", "not", "never", "negative", "don't", "haven't"]
            
            if any(word in response_lower for word in yes_words):
                return PrescreeningAnswer(
                    criterion_id=question.criterion_id,
                    question_text=question.question_text,
                    user_response=user_response,
                    parsed_value=True,
                    interpretation="yes",
                    confidence=0.8
                )
            elif any(word in response_lower for word in no_words):
                return PrescreeningAnswer(
                    criterion_id=question.criterion_id,
                    question_text=question.question_text,
                    user_response=user_response,
                    parsed_value=False,
                    interpretation="no",
                    confidence=0.8
                )
        
        # Number parsing
        if question.expected_answer_type == "number":
            numbers = re.findall(r'\d+', user_response)
            if numbers:
                return PrescreeningAnswer(
                    criterion_id=question.criterion_id,
                    question_text=question.question_text,
                    user_response=user_response,
                    parsed_value=int(numbers[0]),
                    interpretation="number",
                    confidence=0.7
                )
        
        # Default unclear
        return PrescreeningAnswer(
            criterion_id=question.criterion_id,
            question_text=question.question_text,
            user_response=user_response,
            parsed_value=user_response,
            interpretation="unclear",
            confidence=0.3
        )
    
    async def evaluate_eligibility(self, trial_id: int, answers: List[PrescreeningAnswer]) -> EligibilityResult:
        """Evaluate eligibility based on answers"""
        
        logger.info(f"ELIGIBILITY_EVAL: Starting evaluation for trial_id={trial_id} with {len(answers)} answers")
        
        try:
            # Get trial info and criteria
            trial_info = self._get_trial_info(trial_id)
            criteria = self._get_trial_criteria(trial_id)
            
            logger.info(f"ELIGIBILITY_EVAL: Trial info - Name: {trial_info.get('trial_name', 'Unknown')}, Total criteria: {len(criteria)}")
            
            # Create criteria lookup
            criteria_lookup = {c.id: c for c in criteria}
            
            # Evaluate each answer
            detailed_results = []
            inclusion_met = 0
            inclusion_total = 0
            exclusion_met = 0
            exclusion_total = 0
            
            for answer in answers:
                criterion = criteria_lookup.get(answer.criterion_id)
                if not criterion:
                    logger.warning(f"ELIGIBILITY_EVAL: Criterion {answer.criterion_id} not found for answer: {answer.user_response}")
                    continue
                
                logger.debug(f"ELIGIBILITY_EVAL: Evaluating {criterion.criterion_type} criterion {criterion.id}: {criterion.criterion_text[:100]}...")
                
                # Evaluate this specific answer
                result = await self._evaluate_single_answer(criterion, answer)
                detailed_results.append(result)
                
                logger.info(f"ELIGIBILITY_EVAL: Criterion {criterion.id} result - Eligible: {result['eligible']}, Status: {result['status']}")
                
                # Count totals
                if criterion.criterion_type == "inclusion":
                    inclusion_total += 1
                    if result["eligible"]:
                        inclusion_met += 1
                        logger.debug(f"ELIGIBILITY_EVAL: Inclusion criterion {criterion.id} MET ({inclusion_met}/{inclusion_total})")
                    else:
                        logger.debug(f"ELIGIBILITY_EVAL: Inclusion criterion {criterion.id} NOT MET ({inclusion_met}/{inclusion_total})")
                else:  # exclusion
                    exclusion_total += 1
                    if result["eligible"]:
                        exclusion_met += 1
                        logger.debug(f"ELIGIBILITY_EVAL: Exclusion criterion {criterion.id} PASSED ({exclusion_met}/{exclusion_total})")
                    else:
                        logger.debug(f"ELIGIBILITY_EVAL: Exclusion criterion {criterion.id} FAILED ({exclusion_met}/{exclusion_total})")
            
            # Determine overall status
            overall_status = self._determine_overall_status(
                inclusion_met, inclusion_total, exclusion_met, exclusion_total
            )
            
            # Log final eligibility decision
            logger.info(f"ELIGIBILITY_EVAL: Final status - {overall_status}")
            logger.info(f"ELIGIBILITY_EVAL: Inclusion criteria: {inclusion_met}/{inclusion_total} met")
            logger.info(f"ELIGIBILITY_EVAL: Exclusion criteria: {exclusion_met}/{exclusion_total} passed")
            
            # Generate summary
            summary_text = self._generate_summary_text(
                trial_info, overall_status, inclusion_met, inclusion_total, 
                exclusion_met, exclusion_total, detailed_results
            )
            
            # Get actual trial name for proper tracking and database storage  
            actual_trial_name = trial_info.get('trial_name', f"Trial {trial_id}") if trial_info else f"Trial {trial_id}"
            
            return EligibilityResult(
                trial_id=trial_id,
                trial_name=actual_trial_name,
                overall_status=overall_status,
                inclusion_met=inclusion_met,
                inclusion_total=inclusion_total,
                exclusion_met=exclusion_met,
                exclusion_total=exclusion_total,
                detailed_results=detailed_results,
                summary_text=summary_text
            )
            
        except Exception as e:
            logger.error(f"Error evaluating eligibility: {str(e)}")
            raise
    
    async def _evaluate_single_answer(self, criterion: TrialCriterion, answer: PrescreeningAnswer) -> Dict[str, Any]:
        """Evaluate a single answer against a criterion with enhanced auto-evaluation"""
        try:
            # Try auto-evaluation first for all criteria types
            auto_result = self._try_auto_evaluation(criterion, answer)
            if auto_result:
                return auto_result
            
            # Use Gemini for complex evaluation if auto-evaluation fails
            if criterion.parsed_json.get("field") == "unparsed":
                return await self._evaluate_with_gemini(criterion, answer)
            
            # Simple evaluation for structured criteria
            return self._evaluate_simple(criterion, answer)
            
        except Exception as e:
            logger.error(f"Error evaluating answer: {str(e)}")
            return {
                "criterion_id": criterion.id,
                "criterion_text": criterion.criterion_text,
                "user_answer": answer.user_response,
                "eligible": None,
                "status": "error",
                "explanation": f"Error evaluating: {str(e)}"
            }
    
    def _try_auto_evaluation(self, criterion: TrialCriterion, answer: PrescreeningAnswer) -> Dict[str, Any]:
        """Try to auto-evaluate before falling back to complex logic"""
        text = criterion.criterion_text.lower()
        
        # BMI/Weight auto-evaluation
        if "bmi" in text or "body mass index" in text or "body weight" in text:
            hw_data = self._parse_height_weight(answer.user_response)
            
            if hw_data["height_cm"] and hw_data["weight_kg"]:
                bmi = self._calculate_bmi(hw_data["height_cm"], hw_data["weight_kg"])
                
                if bmi:
                    bmi_eval = self._evaluate_numeric_criterion(criterion.criterion_text, bmi)
                    
                    if bmi_eval:
                        return {
                            "criterion_id": criterion.id,
                            "criterion_text": criterion.criterion_text,
                            "user_answer": answer.user_response,
                            "eligible": bmi_eval["eligible"],
                            "status": "confirmed" if bmi_eval["eligible"] else "likely_ineligible",
                            "explanation": f"BMI {bmi:.1f} (calculated from {hw_data['height_cm']:.0f}cm, {hw_data['weight_kg']:.1f}kg): {bmi_eval['explanation']}"
                        }
        
        # Numeric auto-evaluation for counts, ranges, ages, etc.
        if any(keyword in text for keyword in ["flare", "occurrence", "episode", "≥", "≤", "between", "minimum", "maximum", "age", "years"]):
            user_value = self._extract_numeric_value(answer.user_response)
            
            if user_value is not None:
                numeric_eval = self._evaluate_numeric_criterion(criterion.criterion_text, user_value)
                
                if numeric_eval:
                    return {
                        "criterion_id": criterion.id,
                        "criterion_text": criterion.criterion_text,
                        "user_answer": answer.user_response,
                        "eligible": numeric_eval["eligible"],
                        "status": "confirmed" if numeric_eval["eligible"] else "likely_ineligible",
                        "explanation": numeric_eval["explanation"]
                    }
        
        return None  # No auto-evaluation possible
    
    def _evaluate_simple(self, criterion: TrialCriterion, answer: PrescreeningAnswer) -> Dict[str, Any]:
        """Enhanced evaluation for structured criteria with auto-evaluation"""
        parsed = criterion.parsed_json
        text = criterion.criterion_text.lower()
        
        # Enhanced BMI/Weight evaluation
        if "bmi" in text or "body mass index" in text or "body weight" in text:
            # Try to parse height and weight from user response
            hw_data = self._parse_height_weight(answer.user_response)
            
            if hw_data["height_cm"] and hw_data["weight_kg"]:
                bmi = self._calculate_bmi(hw_data["height_cm"], hw_data["weight_kg"])
                
                if bmi:
                    # Evaluate BMI against criterion
                    bmi_eval = self._evaluate_numeric_criterion(criterion.criterion_text, bmi)
                    
                    if bmi_eval:
                        # Add validation confirmation for edge cases
                        height_ft = int(hw_data['height_cm'] / 2.54 / 12)
                        height_in = int((hw_data['height_cm'] / 2.54) % 12)
                        weight_lbs = int(hw_data['weight_kg'] / 0.453592)
                        
                        explanation = f"BMI {bmi:.1f} (calculated from {height_ft}'{height_in}\", {weight_lbs} lbs): {bmi_eval['explanation']}"
                        
                        # Flag unusual BMI values for confirmation
                        if bmi < 15 or bmi > 60:
                            explanation += f" - This BMI seems unusual. Please confirm your height and weight are correct."
                        
                        return {
                            "criterion_id": criterion.id,
                            "criterion_text": criterion.criterion_text,
                            "user_answer": answer.user_response,
                            "eligible": bmi_eval["eligible"],
                            "status": "confirmed" if bmi_eval["eligible"] else "likely_ineligible",
                            "explanation": explanation
                        }
        
        # Enhanced dynamic medication evaluation based on actual trial criteria
        if any(keyword in text.lower() for keyword in ["medication", "therapy", "washout", "naive", "agents"]):
            user_response = answer.user_response.lower().strip()
            
            # Extract the medications mentioned in the trial criteria
            trial_medications = self._extract_specific_medications_from_text(text)
            medication_class = self._extract_medication_type_from_text(text)
            
            # Check if user mentioned any of the trial-specific medications
            mentioned_trial_meds = []
            for med in trial_medications:
                if med.lower() in user_response:
                    mentioned_trial_meds.append(med)
            
            # Also check if user mentioned medications from the examples we showed them
            medication_examples = self._get_medication_class_examples(medication_class)
            for example in medication_examples:
                # Check both the full name and just the drug name (before parentheses)
                drug_name = example.split('(')[0].strip()
                if (drug_name.lower() in user_response or 
                    example.lower() in user_response):
                    mentioned_trial_meds.append(drug_name)
            
            # Check if user mentioned common non-relevant medications
            common_non_meds = ['tylenol', 'acetaminophen', 'ibuprofen', 'aspirin', 'advil', 'motrin']
            mentioned_non_relevant = [med for med in common_non_meds if med in user_response]
            
            # Enhanced willingness detection
            willing_phrases = ["willing to stop", "can stop", "would stop", "will stop", "ok to stop", "fine to stop"]
            not_willing_phrases = ["not willing", "won't stop", "cannot stop", "can't stop", "will not stop", "not able to stop"]
            
            is_willing = any(phrase in user_response for phrase in willing_phrases)
            is_not_willing = any(phrase in user_response for phrase in not_willing_phrases)
            
            if mentioned_trial_meds and is_willing and not is_not_willing:
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": True,
                    "status": "confirmed",
                    "explanation": f"User takes {', '.join(mentioned_trial_meds)} but is willing to stop for trial"
                }
            elif mentioned_trial_meds and is_not_willing:
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": False,
                    "status": "confirmed",
                    "explanation": f"User takes {', '.join(mentioned_trial_meds)} but is not willing to stop"
                }
            elif mentioned_trial_meds and not is_willing and not is_not_willing:
                # Check if this is a multi-turn medication question scenario
                washout_period = self._extract_washout_period_from_text(text)
                is_washout_criterion = bool(washout_period or re.search(r'washout|naive|willing', text, re.IGNORECASE))
                
                if is_washout_criterion and washout_period:
                    # Generate follow-up question for willingness to stop
                    follow_up_question = f"Would you be willing to stop taking {', '.join(mentioned_trial_meds)} for at least {washout_period} before starting the trial?"
                    return {
                        "criterion_id": criterion.id,
                        "criterion_text": criterion.criterion_text,
                        "user_answer": answer.user_response,
                        "eligible": None,
                        "status": "needs_follow_up",
                        "explanation": f"User takes {', '.join(mentioned_trial_meds)} - need to confirm willingness to stop",
                        "follow_up_question": follow_up_question,
                        "follow_up_context": {
                            "medication_names": mentioned_trial_meds,
                            "washout_period": washout_period,
                            "original_criterion": criterion.id
                        }
                    }
                elif is_washout_criterion:
                    # Generate follow-up question without specific washout period
                    follow_up_question = f"Would you be willing to stop taking {', '.join(mentioned_trial_meds)} for the required washout period before starting the trial?"
                    return {
                        "criterion_id": criterion.id,
                        "criterion_text": criterion.criterion_text,
                        "user_answer": answer.user_response,
                        "eligible": None,
                        "status": "needs_follow_up",
                        "explanation": f"User takes {', '.join(mentioned_trial_meds)} - need to confirm willingness to stop",
                        "follow_up_question": follow_up_question,
                        "follow_up_context": {
                            "medication_names": mentioned_trial_meds,
                            "original_criterion": criterion.id
                        }
                    }
                else:
                    return {
                        "criterion_id": criterion.id,
                        "criterion_text": criterion.criterion_text,
                        "user_answer": answer.user_response,
                        "eligible": None,
                        "status": "needs_clarification",
                        "explanation": f"User takes {', '.join(mentioned_trial_meds)} but didn't specify if willing to stop for washout period"
                    }
            elif mentioned_non_relevant and not mentioned_trial_meds:
                med_class_desc = medication_class if medication_class != "medications" else "the specified medications"
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": True,
                    "status": "confirmed",
                    "explanation": f"User takes only {', '.join(mentioned_non_relevant)}, which are not {med_class_desc}"
                }
            elif any(phrase in user_response for phrase in ["no", "none", "not taking", "don't take"]):
                # Check if this is a medication naive criterion (good outcome)
                is_washout_criterion = bool(re.search(r'washout|naive|willing', text, re.IGNORECASE))
                if is_washout_criterion:
                    if 'naive' in text.lower():
                        explanation = f"User is not taking {medication_class} (medication-naive)"
                    else:
                        explanation = f"User is not taking {medication_class} - no washout required"
                else:
                    med_class_desc = medication_class if medication_class != "medications" else "the specified medications"
                    explanation = f"User is not taking {med_class_desc}"
                
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": True,
                    "status": "confirmed",
                    "explanation": explanation
                }
        
        # Enhanced washout evaluation for any medication type
        if "washout" in text or "wash-out" in text:
            user_response = answer.user_response.lower().strip()
            medication_type = self._extract_medication_type_from_text(text)
            
            # More nuanced evaluation for washout questions
            if any(phrase in user_response for phrase in ["yes, willing", "yes willing", "will stop", "can stop", "yes, i would", "yes i would"]):
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": True,
                    "status": "confirmed",
                    "explanation": f"User is willing to undergo washout period for {medication_type}"
                }
            elif any(phrase in user_response for phrase in ["no", "won't stop", "will not stop", "cannot stop", "can't stop", "not willing"]):
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": False,
                    "status": "confirmed",
                    "explanation": f"User is not willing to undergo required washout period for {medication_type}"
                }
            elif user_response == "yes":
                washout_period = self._extract_washout_period_from_text(text)
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": None,
                    "status": "needs_clarification",
                    "explanation": f"The user's response is unclear. They simply answered 'Yes' without specifying whether they are currently taking {medication_type} or willing to undergo the required {washout_period} washout period."
                }
        
        # Enhanced numeric evaluation for flares, counts, ranges
        if any(keyword in text for keyword in ["flare", "occurrence", "episode", "≥", "≤", "between", "minimum", "maximum"]):
            user_value = self._extract_numeric_value(answer.user_response)
            
            if user_value is not None:
                numeric_eval = self._evaluate_numeric_criterion(criterion.criterion_text, user_value)
                
                if numeric_eval:
                    return {
                        "criterion_id": criterion.id,
                        "criterion_text": criterion.criterion_text,
                        "user_answer": answer.user_response,
                        "eligible": numeric_eval["eligible"],
                        "status": "confirmed" if numeric_eval["eligible"] else "likely_ineligible",
                        "explanation": numeric_eval["explanation"]
                    }
        
        # Age evaluation (existing logic)
        if parsed.get("field") == "age" and answer.interpretation == "number":
            age_range = parsed.get("value", [18, 85])
            user_age = answer.parsed_value
            
            if isinstance(user_age, int) and age_range[0] <= user_age <= age_range[1]:
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": True,
                    "status": "confirmed",
                    "explanation": f"Age {user_age} is within required range {age_range[0]}-{age_range[1]}"
                }
            else:
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": False,
                    "status": "likely_ineligible",
                    "explanation": f"Age {user_age} is outside required range {age_range[0]}-{age_range[1]}"
                }
        
        # Diagnosis evaluation
        if parsed.get("field") == "diagnosis" and criterion.criterion_type == "inclusion":
            if answer.interpretation == "yes":
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": True,
                    "status": "confirmed",
                    "explanation": "Confirmed diagnosis by doctor"
                }
            elif answer.interpretation == "no":
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": False,
                    "status": "likely_ineligible",
                    "explanation": "No confirmed diagnosis"
                }
        
        # Exclusion criteria evaluation
        if criterion.criterion_type == "exclusion":
            if answer.interpretation == "yes":
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": False,
                    "status": "likely_ineligible",
                    "explanation": "Has excluded condition (disqualifying)"
                }
            elif answer.interpretation == "no":
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": True,
                    "status": "confirmed",
                    "explanation": "Does not have excluded condition (passes)"
                }
        
        # Default to needs review with more descriptive explanation
        return {
            "criterion_id": criterion.id,
            "criterion_text": criterion.criterion_text,
            "user_answer": answer.user_response,
            "eligible": None,
            "status": "needs_review",
            "explanation": f"Answer '{answer.user_response}' for criterion '{criterion.criterion_text}' requires manual review by study staff"
        }
    
    async def _evaluate_with_gemini(self, criterion: TrialCriterion, answer: PrescreeningAnswer) -> Dict[str, Any]:
        """Use Gemini to evaluate complex criteria"""
        try:
            prompt = f"""You are evaluating clinical trial eligibility.

For inclusion criteria: User must meet the requirement to be eligible
For exclusion criteria: User must NOT meet the requirement to be eligible

Determine if the user's answer makes them eligible or ineligible for this specific criterion.

Criterion Type: {criterion.criterion_type}
Criterion Text: {criterion.criterion_text}
User's Answer: {answer.user_response}

Based on the user's answer, are they eligible for this criterion?

Return a JSON object with:
- eligible: true, false, or null (if unclear)
- confidence: number between 0 and 1
- explanation: brief explanation of the decision
"""
            
            result = await self.gemini.extract_json(prompt, "")
            
            if result and "eligible" in result:
                # Determine status
                if result["eligible"] is True:
                    status = "likely_eligible" if result.get("confidence", 0.5) < 0.9 else "confirmed"
                elif result["eligible"] is False:
                    status = "likely_ineligible" if result.get("confidence", 0.5) < 0.9 else "confirmed"
                else:
                    status = "needs_review"
                
                return {
                    "criterion_id": criterion.id,
                    "criterion_text": criterion.criterion_text,
                    "user_answer": answer.user_response,
                    "eligible": result["eligible"],
                    "status": status,
                    "explanation": result.get("explanation", "Evaluated using Gemini AI")
                }
            
        except Exception as e:
            logger.error(f"Error in Gemini evaluation: {str(e)}")
        
        # Fallback with more descriptive explanation
        return {
            "criterion_id": criterion.id,
            "criterion_text": criterion.criterion_text,
            "user_answer": answer.user_response,
            "eligible": None,
            "status": "needs_review",
            "explanation": f"Answer '{answer.user_response}' for criterion '{criterion.criterion_text}' requires manual review by study staff"
        }
    
    def _determine_overall_status(self, inclusion_met: int, inclusion_total: int, 
                                 exclusion_met: int, exclusion_total: int) -> str:
        """Determine overall eligibility status with optimistic approach"""
        # Must meet all inclusions and no exclusions for full eligibility
        if inclusion_met == inclusion_total and exclusion_met == exclusion_total:
            return "likely_eligible"
        
        # Calculate percentage of criteria met for optimistic messaging
        total_criteria = inclusion_total + exclusion_total
        criteria_met = inclusion_met + exclusion_met
        success_rate = (criteria_met / total_criteria) if total_criteria > 0 else 0
        
        # More optimistic approach: if meeting ≥80% of criteria, suggest potential eligibility
        if success_rate >= 0.8:
            return "potentially_eligible"
        elif inclusion_met < inclusion_total or exclusion_met < exclusion_total:
            return "likely_ineligible"
        else:
            return "needs_review"
    
    def _generate_summary_text(self, trial_info: Dict[str, Any], overall_status: str,
                             inclusion_met: int, inclusion_total: int,
                             exclusion_met: int, exclusion_total: int,
                             detailed_results: List[Dict[str, Any]]) -> str:
        """Generate simplified eligibility summary with immediate contact offer"""
        # Use condition-based name for summary
        # Defensive: handle case where conditions might be a list instead of string
        conditions_raw = trial_info.get('conditions', 'clinical trial')
        if isinstance(conditions_raw, list):
            condition = ', '.join(conditions_raw).lower()
        elif isinstance(conditions_raw, str):
            condition = conditions_raw.lower()
        else:
            condition = 'clinical trial'
        condition_name = f"the {condition} trial" if condition else "the clinical trial"
        # Simplified, concise eligibility summary
        if overall_status == "likely_eligible":
            summary = "✓ You appear to be eligible for this trial!\n"
        elif overall_status == "potentially_eligible":
            summary = "🎯 You may be a good candidate for this trial!\n"
        elif overall_status == "likely_ineligible":
            summary = "✗ You may not be eligible for this trial.\n"
        else:
            summary = "? Your eligibility needs further review.\n"

        summary += f"\n• Inclusion criteria: {inclusion_met}/{inclusion_total}\n"
        summary += f"• Exclusion criteria: {exclusion_met}/{exclusion_total}\n"
        
        # Note: Contact collection prompt is handled separately by ContactCollectionService
        # to avoid duplication and ensure proper flow control
        
        return summary

    def _create_clear_eligibility_description(self, criterion_text: str, user_answer: str, 
                                            explanation: str, eligible: bool) -> str:
        """Create clear, user-friendly eligibility descriptions"""
        criterion_lower = criterion_text.lower()
        
        # Age criteria
        if "age" in criterion_lower and "years" in criterion_lower:
            if eligible:
                return f"Age {user_answer} (meets requirement)"
            else:
                return f"Age {user_answer} (outside required range)"
        
        # BMI criteria  
        if "bmi" in criterion_lower or "body mass index" in criterion_lower:
            # Extract BMI from explanation if available
            bmi_match = re.search(r'BMI (\d+\.?\d*)', explanation)
            if bmi_match:
                bmi_value = bmi_match.group(1)
                if eligible:
                    return f"BMI {bmi_value} (meets requirement)"
                else:
                    return f"BMI {bmi_value} (below/above required range)"
            else:
                return f"Height/weight provided"
        
        # Weight change criteria
        if "body weight change" in criterion_lower or "weight change" in criterion_lower:
            if eligible is None:
                return f"Weight change history needs review"
            elif eligible:
                return "No significant weight change (good)"
            else:
                return "Recent significant weight change"
        
        # Medication criteria
        if any(med_word in criterion_lower for med_word in ["medication", "adderall", "amphetamine", "therapy"]):
            med_name = "medications"
            if "adderall" in criterion_lower or "amphetamine" in criterion_lower:
                med_name = "ADHD medications"
            elif "statin" in criterion_lower:
                med_name = "statin medications"
            
            if eligible:
                return f"No problematic {med_name}"
            else:
                return f"Taking {med_name} (exclusion)"
        
        # Allergic reaction criteria
        if "allergic" in criterion_lower or "anaphylaxis" in criterion_lower:
            if eligible:
                return "No severe allergic reactions"
            else:
                return "History of severe allergic reactions"
        
        # Medical history criteria
        if any(word in criterion_lower for word in ["history", "diagnosed", "condition"]):
            if eligible:
                return "No concerning medical history"
            else:
                return "Has relevant medical condition"
        
        # Flare/episode criteria  
        if "flare" in criterion_lower or "episode" in criterion_lower:
            if user_answer.isdigit():
                count = int(user_answer)
                condition = "episodes"
                if "gout" in criterion_lower:
                    condition = "gout flares"
                elif "migraine" in criterion_lower:
                    condition = "migraines"
                
                if eligible:
                    return f"{count} {condition} (meets requirement)"
                else:
                    return f"{count} {condition} (too few/many)"
        
        # Fallback: use original explanation or extract key info from criterion
        if explanation and len(explanation) < 150:
            # Clean up technical explanations
            clean_explanation = explanation.replace("The user answered", "Answered").replace("Value ", "")
            return clean_explanation.split('.')[0]
        elif criterion_text:
            # Extract first meaningful phrase from criterion (up to 60 chars)
            # Remove numbering and formatting
            clean_criterion = re.sub(r'^\d+\.\s*', '', criterion_text)
            clean_criterion = clean_criterion.split('.')[0].strip()[:60]
            status = "meets" if eligible else "does not meet" if eligible is False else "unclear for"
            return f"{status} requirement: {clean_criterion}..."
        else:
            return f"Requirement {'met' if eligible else 'not met' if eligible is False else 'needs review'}"


