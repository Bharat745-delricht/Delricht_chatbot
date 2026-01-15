#!/usr/bin/env python3
"""
Comprehensive Conversation Stress Test
Simulates diverse patient personas exploring clinical trials
"""

import requests
import time
import random
from datetime import datetime
from core.database import db
import json

API_URL = "https://gemini-chatbot-480267397633.us-central1.run.app/api/gemini/chat"

# Patient personas with realistic profiles
PATIENT_PERSONAS = [
    {
        "name": "Sarah Martinez",
        "age": 34,
        "location": "Tulsa",
        "condition": "Gout",
        "height": "5 foot 6 inches",
        "weight": "165 pounds",
        "symptoms": "4 flares in past year",
        "medications": "No",
        "personality": "direct",  # Gives concise answers
    },
    {
        "name": "James Chen",
        "age": 58,
        "location": "Dallas",
        "condition": "COVID-19",
        "height": "six foot one",
        "weight": "210lbs",
        "symptoms": "wants protection",
        "medications": "blood pressure meds",
        "personality": "verbose",  # Gives extra context
    },
    {
        "name": "Maria Rodriguez",
        "age": 45,
        "location": "New Orleans",
        "condition": "Migraine",
        "height": "5'4\"",
        "weight": "145 lbs",
        "symptoms": "8 migraines per month",
        "medications": "No",
        "personality": "conversational",  # Natural language
    },
    {
        "name": "Robert Thompson",
        "age": 52,
        "location": "Atlanta",
        "condition": "Type 2 Diabetes",
        "height": "6 feet 2 inches",
        "weight": "240 pounds",
        "symptoms": "diagnosed 5 years ago",
        "medications": "metformin",
        "personality": "direct",
    },
    {
        "name": "Jennifer Williams",
        "age": 39,
        "location": "Baton Rouge",
        "condition": "Hidradenitis Suppurativa",
        "height": "five foot seven",
        "weight": "180 pounds",
        "symptoms": "moderate severity",
        "medications": "antibiotics",
        "personality": "verbose",
    },
    {
        "name": "Michael Brown",
        "age": 47,
        "location": "Tulsa",
        "condition": "Fibromyalgia",
        "height": "6'0\"",
        "weight": "200lbs",
        "symptoms": "chronic pain",
        "medications": "No",
        "personality": "direct",
    },
    {
        "name": "Lisa Anderson",
        "age": 29,
        "location": "New Orleans",
        "condition": "Acne Vulgaris",
        "height": "5 foot 5 inches",
        "weight": "135 pounds",
        "symptoms": "moderate acne",
        "medications": "topical treatments",
        "personality": "conversational",
    },
    {
        "name": "David Garcia",
        "age": 61,
        "location": "Atlanta",
        "condition": "Obesity",
        "height": "5'10\"",
        "weight": "280 pounds",
        "symptoms": "want to lose weight",
        "medications": "No",
        "personality": "verbose",
    },
]

class OrganicConversationTester:
    """Simulates organic patient conversations"""

    def __init__(self, api_url=API_URL):
        self.api_url = api_url
        self.session_results = []

    def chat(self, session_id, message):
        """Send message and get response"""
        response = requests.post(
            self.api_url,
            json={"message": message, "session_id": session_id},
            headers={"Content-Type": "application/json"}
        )

        if response.status_code != 200:
            return {
                "error": True,
                "status_code": response.status_code,
                "message": response.text
            }

        return response.json()

    def generate_initial_query(self, persona):
        """Generate initial search query based on personality"""
        if persona["personality"] == "direct":
            return f"{persona['condition']} trials in {persona['location']}"
        elif persona["personality"] == "verbose":
            return f"Hi, I'm interested in clinical trials for {persona['condition']} in the {persona['location']} area"
        else:  # conversational
            return f"Hey there, are there any {persona['condition']} trials in {persona['location']}?"

    def generate_response(self, persona, question_text, question_number):
        """Generate realistic response based on question and personality"""
        q_lower = question_text.lower()

        # Age question
        if "age" in q_lower and question_number == 1:
            return str(persona["age"])

        # Height/weight question
        if "weight" in q_lower and "height" in q_lower:
            if persona["personality"] == "verbose":
                return f"{persona['weight']} and {persona['height']}"
            else:
                return f"{persona['height']}, {persona['weight']}"

        # Medication questions
        if "medication" in q_lower or "taking" in q_lower:
            if persona["medications"] == "No":
                if persona["personality"] == "verbose":
                    return "No, I'm not currently taking any medications"
                else:
                    return "No"
            else:
                return f"Yes, {persona['medications']}"

        # Frequency questions (flares, episodes, attacks)
        if any(word in q_lower for word in ["how many", "many times", "frequency"]):
            # Extract number from symptoms if present
            import re
            match = re.search(r'(\d+)', persona["symptoms"])
            if match:
                return match.group(1)
            else:
                # Default reasonable answer
                return str(random.randint(2, 8))

        # Yes/No questions about medical history
        if any(phrase in q_lower for phrase in ["do you have", "have you", "history of"]):
            # Usually no for prescreening
            return "No" if persona["personality"] == "direct" else "Nope"

        # Allergy questions
        if "allergic" in q_lower or "allergy" in q_lower:
            return "No"

        # Default to yes for general inclusion questions
        if "meet" in q_lower or "willing" in q_lower:
            return "Yes"

        # Fallback
        return "Yes"

    def run_conversation(self, persona):
        """Run complete organic conversation for a persona"""
        session_id = f"stress_test_{persona['name'].replace(' ', '_').lower()}_{int(time.time())}"

        print("\n" + "=" * 70)
        print(f"TESTING: {persona['name']} ({persona['age']}y/o in {persona['location']})")
        print(f"Condition: {persona['condition']}")
        print(f"Personality: {persona['personality']}")
        print("=" * 70)

        conversation_log = []
        errors = []
        question_num = 0

        # Step 1: Initial search
        initial_query = self.generate_initial_query(persona)
        print(f"\n[1] USER: {initial_query}")

        result = self.chat(session_id, initial_query)
        if result.get("error"):
            print(f"❌ ERROR: {result}")
            return {"persona": persona["name"], "success": False, "error": "API error"}

        bot_response = result.get("response", "")
        print(f"BOT: {bot_response[:150]}...")
        conversation_log.append(("USER", initial_query))
        conversation_log.append(("BOT", bot_response))

        time.sleep(0.5)

        # Step 2: Check eligibility (if trials found)
        if "found" in bot_response.lower() and "trial" in bot_response.lower():
            confirm_msg = "Yes, let's check eligibility" if persona["personality"] != "direct" else "1"
            print(f"\n[2] USER: {confirm_msg}")

            result = self.chat(session_id, confirm_msg)
            bot_response = result.get("response", "")
            print(f"BOT: {bot_response[:150]}...")
            conversation_log.append(("USER", confirm_msg))
            conversation_log.append(("BOT", bot_response))

            time.sleep(0.5)
        else:
            # No trials found
            print(f"\n⚠️  No trials found for {persona['condition']} in {persona['location']}")

            # Check if alternative trials were suggested
            if "however" in bot_response.lower() and "we do have" in bot_response.lower():
                print("✅ Alternative trials suggested!")
                errors.append("No direct match but alternatives shown (expected)")
            else:
                errors.append("No trials found and no alternatives suggested")

            return {
                "persona": persona["name"],
                "session_id": session_id,
                "success": False,
                "reason": "no_trials_found",
                "errors": errors,
                "conversation": conversation_log
            }

        # Steps 3-N: Answer prescreening questions
        max_questions = 10
        while question_num < max_questions:
            question_num += 1

            # Check if we're still in prescreening
            if "Question" not in bot_response and "question" not in bot_response.lower():
                # Check if we've reached eligibility summary
                if "Eligibility Summary" in bot_response:
                    print(f"\n✅ Prescreening completed after {question_num-1} questions")
                    break
                # Check if there's an error
                if "couldn't determine" in bot_response or "apologize" in bot_response:
                    errors.append(f"Q{question_num}: {bot_response[:100]}")
                    print(f"\n❌ Error in prescreening: {bot_response[:100]}...")
                    break
                # Some other state
                break

            # Generate answer based on question
            answer = self.generate_response(persona, bot_response, question_num)
            print(f"\n[{question_num + 2}] USER: {answer}")

            result = self.chat(session_id, answer)
            bot_response = result.get("response", "")
            print(f"BOT: {bot_response[:150]}...")

            conversation_log.append(("USER", answer))
            conversation_log.append(("BOT", bot_response))

            time.sleep(0.5)

        # Step: Check if booking flow triggered
        if "I can see availability" in bot_response or "schedule" in bot_response.lower():
            print("\n✅ Availability shown - booking flow triggered!")

            # Confirm booking
            confirm = "Yes, let's book it"
            print(f"\nUSER: {confirm}")

            result = self.chat(session_id, confirm)
            bot_response = result.get("response", "")
            print(f"BOT: {bot_response[:150]}...")

            conversation_log.append(("USER", confirm))
            conversation_log.append(("BOT", bot_response))

            # Provide contact details
            contact_info = [
                (persona["name"].split()[0], "first name"),  # First name
                ("555-1234", "phone"),
                (f"{persona['name'].split()[0].lower()}@test.com", "email"),
                ("01/15/1985", "DOB"),
            ]

            for info, desc in contact_info:
                time.sleep(0.5)
                print(f"\nUSER: {info} ({desc})")
                result = self.chat(session_id, info)
                bot_response = result.get("response", "")
                print(f"BOT: {bot_response[:100]}...")

                conversation_log.append(("USER", info))
                conversation_log.append(("BOT", bot_response))

                # Check for booking confirmation
                if "booking has been submitted" in bot_response.lower():
                    print("\n✅ BOOKING COMPLETED!")
                    break

        elif "Would you like our research team to contact you" in bot_response:
            print("\n✅ Contact collection offered (eligible or ineligible)")

        # Verify in database
        verification = self.verify_in_database(session_id)

        return {
            "persona": persona["name"],
            "session_id": session_id,
            "success": verification["has_booking"] or verification["has_contact"],
            "booking_created": verification["has_booking"],
            "contact_collected": verification["has_contact"],
            "prescreening_completed": verification["prescreening_completed"],
            "errors": errors,
            "messages": len(conversation_log) // 2,
            "conversation": conversation_log
        }

    def verify_in_database(self, session_id):
        """Verify session results in database"""
        try:
            # Check contact info
            contact = db.execute_query("""
                SELECT first_name, phone_number FROM patient_contact_info
                WHERE session_id = %s
            """, (session_id,))

            # Check appointment
            appt = db.execute_query("""
                SELECT status FROM appointments WHERE session_id = %s
            """, (session_id,))

            # Check prescreening
            ps = db.execute_query("""
                SELECT status, eligible FROM prescreening_sessions
                WHERE session_id = %s
            """, (session_id,))

            return {
                "has_contact": bool(contact),
                "has_booking": bool(appt),
                "prescreening_completed": ps[0]["status"] == "completed" if ps else False,
                "eligible": ps[0]["eligible"] if ps else None
            }
        except Exception as e:
            print(f"⚠️  Database verification error: {e}")
            return {"has_contact": False, "has_booking": False, "prescreening_completed": False}

    def run_stress_test(self, num_personas=None):
        """Run stress test with multiple personas"""
        personas_to_test = PATIENT_PERSONAS if num_personas is None else PATIENT_PERSONAS[:num_personas]

        print("\n" + "=" * 70)
        print("COMPREHENSIVE CONVERSATION STRESS TEST")
        print("=" * 70)
        print(f"Testing {len(personas_to_test)} patient personas")
        print(f"API: {self.api_url}")
        print(f"Started: {datetime.now()}")
        print("=" * 70)

        results = []
        for i, persona in enumerate(personas_to_test, 1):
            print(f"\n\n{'#'*70}")
            print(f"PERSONA {i}/{len(personas_to_test)}")
            print(f"{'#'*70}")

            result = self.run_conversation(persona)
            results.append(result)

            # Brief pause between personas
            time.sleep(2)

        # Summary
        print("\n\n" + "=" * 70)
        print("STRESS TEST SUMMARY")
        print("=" * 70)

        successful = len([r for r in results if r["success"]])
        with_bookings = len([r for r in results if r.get("booking_created")])
        with_contact = len([r for r in results if r.get("contact_collected")])
        with_errors = len([r for r in results if r.get("errors")])

        print(f"\nTotal Personas Tested: {len(results)}")
        print(f"Successful Flows: {successful}/{len(results)} ({successful/len(results)*100:.1f}%)")
        print(f"Bookings Created: {with_bookings}")
        print(f"Contact Info Collected: {with_contact}")
        print(f"Sessions with Errors: {with_errors}")

        print("\n" + "=" * 70)
        print("DETAILED RESULTS:")
        print("=" * 70)

        for result in results:
            status = "✅" if result["success"] else "❌"
            print(f"\n{status} {result['persona']}")
            print(f"   Session: {result['session_id']}")
            print(f"   Messages: {result.get('messages', 0)}")

            if result.get("booking_created"):
                print(f"   ✅ Booking created")
            elif result.get("contact_collected"):
                print(f"   ✅ Contact collected")
            elif result.get("prescreening_completed"):
                print(f"   ✅ Prescreening completed")

            if result.get("errors"):
                print(f"   ⚠️  Errors: {len(result['errors'])}")
                for err in result["errors"][:2]:
                    print(f"      - {err[:80]}...")

        # Save detailed results
        with open('/tmp/stress_test_results.json', 'w') as f:
            json.dump(results, f, indent=2, default=str)

        print("\n" + "=" * 70)
        print("Detailed results saved to: /tmp/stress_test_results.json")
        print("=" * 70)

        return results


if __name__ == "__main__":
    import sys

    # Number of personas to test (default: all)
    num_personas = int(sys.argv[1]) if len(sys.argv) > 1 else None

    tester = OrganicConversationTester()
    results = tester.run_stress_test(num_personas)

    # Exit with success if majority passed
    success_rate = len([r for r in results if r["success"]]) / len(results)
    exit(0 if success_rate >= 0.5 else 1)
