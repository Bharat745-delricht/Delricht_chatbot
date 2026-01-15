"""
Conversation Simulator for Automated Testing

Simulates patient interactions with the chatbot API, maintaining
session state and generating appropriate responses based on patient profiles.
"""

import aiohttp
import asyncio
import re
import time
import os
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import json
import random


class ConversationTurn:
    """Represents a single turn in the conversation"""

    def __init__(self, user_message: str, bot_response: str, metadata: Dict, response_time: float):
        self.user_message = user_message
        self.bot_response = bot_response
        self.metadata = metadata
        self.response_time = response_time
        self.timestamp = datetime.now().isoformat()

    def to_dict(self):
        return {
            "user_message": self.user_message,
            "bot_response": self.bot_response,
            "metadata": self.metadata,
            "response_time": self.response_time,
            "timestamp": self.timestamp,
        }


class IntelligentAnswerGenerator:
    """Uses Gemini API to intelligently understand questions and generate answers"""

    def __init__(self):
        self.api_key = os.getenv('GEMINI_API_KEY')
        self.api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent"

    async def generate_intelligent_answer(self, question: str, patient_profile: Dict) -> Optional[str]:
        """
        Use Gemini to understand the question and generate appropriate answer from patient profile

        Returns None if fails (triggers fallback)
        """
        if not self.api_key:
            return None

        try:
            # Create prompt with patient profile
            prompt = f"""You are a patient being asked questions for a clinical trial prescreening. Answer the question naturally and accurately based on your profile.

YOUR PATIENT PROFILE:
- Age: {patient_profile['demographics']['age']}
- Gender: {patient_profile['demographics']['gender']}
- Location: {patient_profile['demographics']['location']}
- Primary Condition: {patient_profile['medical_history']['primary_condition']}
- All Conditions: {', '.join(patient_profile['medical_history'].get('conditions', []))}
- Medications: {', '.join(patient_profile['medical_history'].get('medications', [])) if patient_profile['medical_history'].get('medications') else 'None'}
- Duration: {patient_profile['medical_history'].get('duration_years', 'N/A')} years

QUESTION: {question}

INSTRUCTIONS:
- Answer as this patient would
- Be direct and concise (1-2 sentences max)
- If asked yes/no, respond with "Yes" or "No" (can add brief explanation)
- If asked for medications, list them from your profile
- If asked about diagnoses, confirm based on your conditions
- If you don't have that information in your profile, say "I'm not sure" or "I don't have that information"
- DO NOT make up information not in your profile
- Answer naturally like a real patient would

YOUR ANSWER:"""

            payload = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 100,
                }
            }

            headers = {
                "Content-Type": "application/json",
            }

            url_with_key = f"{self.api_url}?key={self.api_key}"

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url_with_key,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status != 200:
                        return None

                    data = await response.json()

                    # Extract answer from Gemini response
                    if 'candidates' in data and len(data['candidates']) > 0:
                        candidate = data['candidates'][0]
                        if 'content' in candidate and 'parts' in candidate['content']:
                            answer = candidate['content']['parts'][0].get('text', '').strip()
                            return answer if answer else None

                    return None

        except Exception as e:
            # Fail silently, let fallback handle it
            return None


class ConversationSimulator:
    """
    Simulates patient conversations with the chatbot API

    Handles:
    - Session management
    - Answer generation based on patient profile
    - Conversation state tracking
    - Edge case responses
    """

    def __init__(self, api_base_url: str, patient_profile: Dict):
        self.api_url = f"{api_base_url}/api/gemini/chat"
        self.patient = patient_profile
        self.session_id = patient_profile["patient_id"]  # Use patient_id as session_id (with AUTO_TEST_ prefix)
        self.conversation_log: List[ConversationTurn] = []
        self.conversation_state = "initial"
        self.current_question_number = 0
        self.total_questions = 0
        self.errors: List[Dict] = []
        self.intelligent_generator = IntelligentAnswerGenerator()
        self.answer_stats = {"ai_answers": 0, "pattern_answers": 0, "fallback_answers": 0}

    async def run_full_conversation(self) -> Dict[str, Any]:
        """
        Run a complete conversation from search to completion

        Returns:
            Dict containing conversation log and metadata
        """
        try:
            # Step 1: Initial search for trials
            await self._search_for_trials()

            # Step 2: Start prescreening
            if self.conversation_state == "trials_shown":
                await self._start_prescreening()

            # Step 3: Answer all prescreening questions
            if self.conversation_state == "prescreening_active":
                await self._complete_prescreening()

            # Step 4: Contact collection (if eligible)
            if self.conversation_state == "prescreening_complete":
                await self._provide_contact_info()

            # Mark as completed
            self.conversation_state = "completed"

        except Exception as e:
            self.errors.append({
                "type": "conversation_failure",
                "error": str(e),
                "state": self.conversation_state,
                "timestamp": datetime.now().isoformat(),
            })
            self.conversation_state = "failed"

        return {
            "session_id": self.session_id,
            "patient_id": self.patient["patient_id"],
            "conversation_log": [turn.to_dict() for turn in self.conversation_log],
            "final_state": self.conversation_state,
            "total_turns": len(self.conversation_log),
            "total_questions_asked": self.total_questions,
            "errors": self.errors,
        }

    async def _send_message(self, message: str) -> Tuple[str, Dict, float]:
        """
        Send message to chatbot API and get response

        Returns:
            Tuple of (response_text, metadata, response_time)
        """
        start_time = time.time()

        payload = {
            "message": message,
            "session_id": self.session_id,
            "user_id": "automated_test"
        }

        try:
            # Create SSL context that doesn't verify certificates (for testing)
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            connector = aiohttp.TCPConnector(ssl=ssl_context)

            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    self.api_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)  # Increased from 30s to 60s for slow Gemini API
                ) as response:
                    response_time = time.time() - start_time

                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"HTTP {response.status}: {error_text}")

                    data = await response.json()

                    # Log the turn
                    turn = ConversationTurn(
                        user_message=message,
                        bot_response=data.get("response", ""),
                        metadata=data.get("metadata", {}),
                        response_time=response_time
                    )
                    self.conversation_log.append(turn)

                    return data.get("response", ""), data.get("metadata", {}), response_time

        except asyncio.TimeoutError:
            response_time = time.time() - start_time
            self.errors.append({
                "type": "timeout",
                "message": message,
                "response_time": response_time,
            })
            raise

        except Exception as e:
            response_time = time.time() - start_time
            self.errors.append({
                "type": "api_error",
                "message": message,
                "error": str(e),
                "response_time": response_time,
            })
            raise

    async def _search_for_trials(self):
        """Step 1: Search for trials"""
        primary_condition = self.patient["medical_history"]["primary_condition"]
        location = self.patient["demographics"]["location"]

        search_query = f"{primary_condition} trials in {location}"

        response, metadata, _ = await self._send_message(search_query)

        # Check if trials were shown
        if "trial" in response.lower() or "study" in response.lower():
            self.conversation_state = "trials_shown"
        else:
            self.conversation_state = "no_trials_found"
            self.errors.append({
                "type": "no_trials_found",
                "search_query": search_query,
            })

    async def _start_prescreening(self):
        """Step 2: Express interest in prescreening"""
        response, metadata, _ = await self._send_message("Yes, check my eligibility")

        # Check if prescreening started
        if self._is_prescreening_question(response):
            self.conversation_state = "prescreening_active"
            self.total_questions = self._extract_total_questions(response)
        else:
            self.errors.append({
                "type": "prescreening_not_started",
                "bot_response": response,
            })

    async def _complete_prescreening(self):
        """Step 3: Answer all prescreening questions"""

        max_turns = 30  # Safety limit (includes questions + reviews + confirmations)
        turns_processed = 0

        while turns_processed < max_turns:
            # Get last response
            if not self.conversation_log:
                break

            last_response = self.conversation_log[-1].bot_response

            # Check if prescreening is complete (eligibility shown)
            if self._is_prescreening_complete(last_response):
                self.conversation_state = "prescreening_complete"
                # After completion, check if bot is asking about contact info
                if "contact" in last_response.lower() or "connect" in last_response.lower():
                    await self._provide_contact_info()
                break

            # Handle review/clarification phase
            if "clarification" in last_response.lower() or "need clarification" in last_response.lower():
                # Bot asking for clarifications after all questions
                await self._send_message("Yes, everything is correct. Please continue with the eligibility evaluation.")
                turns_processed += 1
                # Don't break - wait for eligibility result
                continue

            # Handle review confirmation
            if "thanks for completing" in last_response.lower():
                # Bot finished questions, showing review
                await self._send_message("Yes, that all looks correct")
                turns_processed += 1
                continue

            # Handle general confirmations
            if ("confirm" in last_response.lower() or "correct" in last_response.lower()) and "?" in last_response:
                await self._send_message("Yes, that's correct")
                turns_processed += 1
                continue

            # Check if still asking prescreening questions
            if self._is_prescreening_question(last_response):
                # Generate and send answer (now async with AI)
                answer = await self._generate_answer(last_response)
                response, metadata, _ = await self._send_message(answer)
                turns_processed += 1
                self.current_question_number += 1
                continue

            # If we get here, it's likely an intermediate state - send affirmative response and continue
            if "?" in last_response:
                # Bot is asking something - give affirmative response
                await self._send_message("Yes")
                turns_processed += 1
                continue
            else:
                # Bot made a statement - acknowledge and wait for next message
                # Check if this is the final eligibility result
                if any(marker in last_response.lower() for marker in ["eligible", "unfortunately", "congratulations"]):
                    # This looks like the final result - we're done
                    self.conversation_state = "prescreening_complete"
                    break
                else:
                    # Unknown state - log but don't break immediately, try one more turn
                    self.errors.append({
                        "type": "unexpected_state",
                        "bot_response": last_response[:100],
                    })
                    # Try sending affirmative response
                    await self._send_message("Yes, please continue")
                    turns_processed += 1

                    # If still stuck after 2 unknown states in a row, break
                    if len([e for e in self.errors if e.get("type") == "unexpected_state"]) >= 2:
                        break
                    continue

        # If we hit max turns, something went wrong
        if turns_processed >= max_turns:
            self.errors.append({
                "type": "too_many_turns",
                "turns_processed": turns_processed,
            })

    async def _provide_contact_info(self):
        """Step 4: Provide contact information if eligible"""

        last_response = self.conversation_log[-1].bot_response

        # Check if asking for contact info
        if "contact" in last_response.lower() or "connect" in last_response.lower():
            # Say yes
            await self._send_message("Yes, please connect me")

            # Provide name
            first_name = self.patient["demographics"]["first_name"]
            await self._send_message(first_name)

            last_name = self.patient["demographics"]["last_name"]
            await self._send_message(last_name)

            # Provide phone
            fake_phone = f"555-{self.patient['patient_id'][-7:]}"
            await self._send_message(fake_phone)

            # Provide email
            fake_email = f"{self.patient['patient_id']}@test.com"
            await self._send_message(fake_email)

            self.conversation_state = "contact_collected"

    def _is_prescreening_question(self, response: str) -> bool:
        """Check if response is a prescreening question"""
        # Look for question markers
        question_markers = [
            r"\*\*Question \d+ of \d+:",
            r"What is your",
            r"Do you have",
            r"Have you",
            r"Are you",
            r"How many",
            r"How long",
            r"\?$",  # Ends with question mark
        ]

        for marker in question_markers:
            if re.search(marker, response, re.IGNORECASE):
                return True

        return False

    def _is_prescreening_complete(self, response: str) -> bool:
        """Check if prescreening is complete"""
        completion_markers = [
            "eligibility summary",
            "likely eligible",
            "likely ineligible",
            "potentially eligible",
            "based on your responses",
            "here's your summary",
        ]

        for marker in completion_markers:
            if marker.lower() in response.lower():
                return True

        return False

    def _extract_total_questions(self, response: str) -> int:
        """Extract total number of questions from response"""
        match = re.search(r"Question \d+ of (\d+)", response)
        if match:
            return int(match.group(1))
        return 0

    async def _generate_answer(self, question: str) -> str:
        """
        Generate appropriate answer based on question and patient profile

        Three-tier approach:
        1. Fast pattern matching for simple questions (age, gender)
        2. AI-powered intelligent answers for complex questions
        3. Fallback heuristics if AI fails
        """
        question_lower = question.lower()
        patient = self.patient
        demographics = patient["demographics"]
        medical = patient["medical_history"]

        # Handle edge case patients with unclear responses
        if medical.get("response_style") == "unclear":
            self.answer_stats["fallback_answers"] += 1
            return self._generate_unclear_answer(question)

        # Handle contradictory patients
        if "contradictions" in medical:
            self.answer_stats["fallback_answers"] += 1
            return self._generate_contradictory_answer(question, medical["contradictions"])

        # TIER 1: Fast pattern matching for unambiguous questions
        simple_answer = self._try_simple_pattern_match(question_lower, demographics, medical)
        if simple_answer:
            self.answer_stats["pattern_answers"] += 1
            return simple_answer

        # TIER 2: AI-powered intelligent answer for complex questions
        try:
            ai_answer = await self.intelligent_generator.generate_intelligent_answer(question, patient)
            if ai_answer:
                self.answer_stats["ai_answers"] += 1
                return ai_answer
        except Exception:
            pass  # Fall through to Tier 3

        # TIER 3: Fallback heuristics
        self.answer_stats["fallback_answers"] += 1
        return self._generate_fallback_answer(question_lower, demographics, medical)

    def _try_simple_pattern_match(self, question_lower: str, demographics: Dict, medical: Dict) -> Optional[str]:
        """Fast pattern matching for simple, unambiguous questions"""

        # AGE - unambiguous
        if ("what is your age" in question_lower or "how old are you" in question_lower) and "when" not in question_lower:
            return str(demographics["age"])

        # GENDER - unambiguous
        if "gender" in question_lower or ("what is your sex" in question_lower):
            return demographics["gender"]

        # LOCATION - unambiguous
        if "where do you live" in question_lower or "what city" in question_lower:
            return demographics["location"]

        # For everything else, let AI handle it
        return None

    def _generate_fallback_answer(self, question_lower: str, demographics: Dict, medical: Dict) -> str:
        """Fallback heuristics when AI fails"""

        # AGE (if not caught by simple match)
        if "age" in question_lower or "how old" in question_lower:
            return str(demographics["age"])

        # HEIGHT/WEIGHT/BMI - handle combined questions
        if "height" in question_lower and "weight" in question_lower:
            # Asking for BOTH
            height = self._generate_height()
            weight = self._generate_weight()
            return f"{height}, {weight}"
        elif "height" in question_lower:
            return self._generate_height()
        elif "weight" in question_lower:
            return self._generate_weight()
        elif "bmi" in question_lower:
            # Check if also asking for height/weight as alternative
            if "don't know" in question_lower or "provide" in question_lower:
                height = self._generate_height()
                weight = self._generate_weight()
                return f"{height}, {weight}"
            else:
                return self._generate_bmi()

        # GENDER
        if "gender" in question_lower or "sex" in question_lower:
            return demographics["gender"]

        # LOCATION
        if "location" in question_lower or "where" in question_lower:
            return demographics["location"]

        # DIAGNOSIS / CONDITIONS
        if "diagnos" in question_lower or "condition" in question_lower:
            if "how long" in question_lower or "when" in question_lower:
                return f"{medical.get('duration_years', 5)} years"
            else:
                return f"Yes, I have {medical['primary_condition']}"

        # MEDICATIONS - More specific handling
        if "medication" in question_lower or "taking" in question_lower or "drug" in question_lower:
            medications = medical.get("medications", [])

            # Check what specifically is being asked
            if "adjust" in question_lower or "change" in question_lower:
                # "Can you adjust medications?"
                return "Yes, I can work with my doctor to adjust my medications as needed"
            elif "related to" in question_lower or "for" in question_lower:
                # "Are you taking medications for this condition?"
                if not medications:
                    return "No, I'm not currently taking any medications for this condition"
                elif len(medications) == 1:
                    return f"Yes, I take {medications[0]}"
                else:
                    return f"Yes, I'm taking {', '.join(medications[:-1])}, and {medications[-1]}"
            else:
                # General medication question
                if not medications:
                    return "No"
                else:
                    return f"Yes - {', '.join(medications)}"

        # MEDICAL REQUIREMENTS / GENERAL ELIGIBILITY
        if "meet" in question_lower and "requirement" in question_lower:
            # "Do you meet the medical requirements?"
            return "Yes, I believe I do"

        if "able to" in question_lower or "willing to" in question_lower:
            # "Are you able to attend appointments?"
            return "Yes"

        # YES/NO QUESTIONS - Better heuristics
        if question_lower.strip().startswith(("do you", "have you", "are you", "is", "can you", "would you")):
            # Look for negative keywords
            negative_keywords = ["not", "never", "exclude", "without"]
            has_negative = any(neg in question_lower for neg in negative_keywords)

            # Look for condition-related keywords
            condition_keywords = [cond.lower() for cond in medical.get("conditions", [])]
            medication_keywords = [med.lower() for med in medical.get("medications", [])]

            # If question mentions patient's condition/medication
            for keyword in condition_keywords + medication_keywords:
                if keyword in question_lower:
                    return "No" if has_negative else "Yes"

            # Default: healthy patient, answer favorably for inclusion criteria
            return "No" if has_negative else "Yes"

        # NUMERIC QUESTIONS (flares, episodes, etc.)
        if "how many" in question_lower:
            if "flare" in question_lower or "episode" in question_lower:
                return str(medical.get("flares_per_year", 3))
            else:
                return str(random.randint(1, 5))

        # DURATION QUESTIONS
        if "how long" in question_lower:
            return f"{medical.get('duration_years', 5)} years"

        # SEVERITY QUESTIONS
        if "severe" in question_lower or "scale" in question_lower:
            return "Moderate"

        # DEFAULT: Provide a safe generic answer
        return "I'm not sure, can you clarify?"

    def _generate_unclear_answer(self, question: str) -> str:
        """Generate unclear/ambiguous answer for edge case testing"""
        unclear_responses = [
            "I'm not really sure",
            "Maybe? I think so",
            "I don't know exactly",
            "Sometimes, but not always",
            "It varies",
            "I can't remember",
            "Probably around that, yeah",
        ]
        return random.choice(unclear_responses)

    def _generate_contradictory_answer(self, question: str, contradictions: Dict) -> str:
        """Generate contradictory answer based on contradiction flags"""
        question_lower = question.lower()

        if "diabetes" in question_lower and contradictions.get("says_no_diabetes"):
            if "diagnos" in question_lower:
                return "No, I don't have diabetes"
            elif "medication" in question_lower:
                return "Yes, I take Metformin and Insulin"

        # Default
        return self._generate_answer(question)

    def _generate_height(self) -> str:
        """Generate realistic height"""
        feet = random.randint(5, 6)
        inches = random.randint(0, 11)
        return f"{feet}'{inches}\""

    def _generate_weight(self) -> str:
        """Generate realistic weight"""
        weight = random.randint(140, 240)
        return f"{weight} lbs"

    def _generate_bmi(self) -> str:
        """Generate realistic BMI"""
        bmi = random.uniform(22.0, 32.0)
        return f"{bmi:.1f}"


# Import random for some functions
import random


if __name__ == "__main__":
    # Test the simulator
    import sys
    sys.path.append("/Users/marshallmorris/gemini-chatbot")
    from automated_testing.generators.patient_generator import PatientGenerator

    async def test_simulator():
        # Generate a test patient
        generator = PatientGenerator()
        patients = generator.generate_batch(1)
        patient = patients[0].to_dict()

        print(f"🤖 Testing Conversation Simulator\n")
        print(f"Patient: {patient['demographics']['name']}")
        print(f"Condition: {patient['medical_history']['primary_condition']}")
        print(f"Location: {patient['demographics']['location']}\n")

        # Create simulator
        api_url = "https://gemini-chatbot-480267397633.us-central1.run.app"
        simulator = ConversationSimulator(api_url, patient)

        # Run conversation
        print("Starting conversation...\n")
        result = await simulator.run_full_conversation()

        print(f"\nConversation completed!")
        print(f"State: {result['final_state']}")
        print(f"Total turns: {result['total_turns']}")
        print(f"Questions asked: {result['total_questions_asked']}")

        if result['errors']:
            print(f"\nErrors encountered: {len(result['errors'])}")
            for error in result['errors']:
                print(f"  - {error['type']}: {error}")

        print("\nConversation log:")
        for i, turn in enumerate(result['conversation_log'], 1):
            print(f"\n[Turn {i}] User: {turn['user_message']}")
            print(f"[Turn {i}] Bot: {turn['bot_response'][:100]}...")
            print(f"[Turn {i}] Response time: {turn['response_time']:.2f}s")

    asyncio.run(test_simulator())
