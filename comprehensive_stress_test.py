#!/usr/bin/env python3
"""
Comprehensive Stress Test - Organic Patient Conversations
Tests complete flows from search to booking with realistic personas
"""

import requests
import time
import json
from datetime import datetime
import os
import sys

# Add project to path
sys.path.insert(0, '/Users/marshallmorris/gemini-chatbot')
from core.database import db

API_URL = "https://gemini-chatbot-480267397633.us-central1.run.app/api/gemini/chat"

# Diverse patient personas
PERSONAS = [
    # Persona 1: Young adult, Gout, Tulsa
    {
        "name": "Sarah Martinez", "age": 34, "location": "Tulsa", "condition": "Gout",
        "height": "5 foot 6", "weight": "165 lbs", "flares": 4, "style": "concise"
    },
    # Persona 2: Middle-aged, COVID, Dallas, verbose
    {
        "name": "James Chen", "age": 58, "location": "Dallas", "condition": "COVID-19",
        "height": "six foot one", "weight": "210lbs", "style": "verbose"
    },
    # Persona 3: Adult, Migraine, New Orleans
    {
        "name": "Maria Rodriguez", "age": 45, "location": "New Orleans", "condition": "Migraine",
        "height": "5'4\"", "weight": "145 pounds", "migraines": 8, "style": "natural"
    },
    # Persona 4: Older adult, Diabetes, Atlanta
    {
        "name": "Robert Thompson", "age": 52, "location": "Atlanta", "condition": "Type 2 Diabetes",
        "height": "6 feet 2 inches", "weight": "240lbs", "style": "concise"
    },
    # Persona 5: Adult, Hidradenitis, Baton Rouge
    {
        "name": "Jennifer Williams", "age": 39, "location": "Baton Rouge", "condition": "Hidradenitis Suppurativa",
        "height": "five seven", "weight": "180 pounds", "style": "verbose"
    },
]

def chat(session_id, message):
    """Send message to chatbot"""
    response = requests.post(API_URL, json={"message": message, "session_id": session_id})
    return response.json() if response.status_code == 200 else {"error": True}

def smart_answer(persona, question, question_num):
    """Generate realistic answer based on question"""
    q = question.lower()

    # Age
    if "age" in q and question_num <= 2:
        return str(persona["age"])

    # Height/weight - use persona's style
    if ("weight" in q or "height" in q):
        if persona["style"] == "verbose":
            return f"I'm {persona['height']} tall and I weigh {persona['weight']}"
        elif persona["style"] == "natural":
            return f"{persona['height']}, {persona['weight']}"
        else:
            return f"{persona['height']}, {persona['weight']}"

    # Flares/episodes/attacks
    if any(word in q for word in ["flare", "episode", "attack", "how many"]):
        return str(persona.get("flares", persona.get("migraines", 5)))

    # Medications
    if "medication" in q or "taking" in q:
        if persona["style"] == "verbose":
            return "No, I'm not currently taking any medications for this condition"
        return "No"

    # Allergies
    if "allergic" in q or "allergy" in q:
        return "No"

    # General yes/no questions
    if any(word in q for word in ["do you", "have you", "are you", "willing"]):
        return "Yes" if persona["style"] != "verbose" else "Yes, I am"

    # Medical history
    if "medical" in q and ("condition" in q or "history" in q):
        return "No significant conditions"

    # Default yes for unclear questions
    return "Yes"

def run_organic_test(persona, max_turns=20):
    """Run organic conversation for persona"""
    session_id = f"stress_{persona['name'].replace(' ', '_').lower()}_{int(time.time())}"

    print(f"\n{'='*70}")
    print(f"PERSONA: {persona['name']} | {persona['age']}y/o | {persona['location']}")
    print(f"Seeking: {persona['condition']} trials")
    print('='*70)

    # Track conversation
    messages = []
    errors = []

    # Step 1: Initial search
    if persona["style"] == "verbose":
        query = f"Hi there! I'm looking for {persona['condition']} clinical trials in {persona['location']}"
    elif persona["style"] == "natural":
        query = f"Hey, are there any {persona['condition']} trials in {persona['location']}?"
    else:
        query = f"{persona['condition']} trials in {persona['location']}"

    print(f"\n[1] USER: {query}")
    result = chat(session_id, query)
    bot = result.get("response", "")
    print(f"BOT: {bot[:120]}...")
    messages.append((query, bot))

    time.sleep(0.5)

    # If no trials found, check for alternatives
    if "couldn't find" in bot.lower():
        if "however" in bot.lower():
            print("   ✅ Alternative trials suggested")
        else:
            print("   ❌ No alternatives suggested")
            return {"persona": persona["name"], "session_id": session_id, "success": False, "reason": "no_trials"}

    # Step 2: Start eligibility
    confirm = "Yes, let's check eligibility" if persona["style"] != "concise" else "1"
    print(f"\n[2] USER: {confirm}")
    result = chat(session_id, confirm)
    bot = result.get("response", "")
    print(f"BOT: {bot[:120]}...")
    messages.append((confirm, bot))

    time.sleep(0.5)

    # Steps 3-N: Answer questions until eligibility summary
    turn = 3
    while turn <= max_turns:
        # Check if we've reached eligibility summary or booking
        if "Eligibility Summary" in bot:
            print(f"\n✅ Eligibility shown at turn {turn}")
            break
        if "Your booking has been submitted" in bot:
            print(f"\n✅ Booking completed at turn {turn}!")
            break
        if "couldn't determine" in bot or ("apologize" in bot and "trouble" in bot):
            errors.append(f"Turn {turn}: {bot[:80]}")
            print(f"\n❌ ERROR at turn {turn}: {bot[:80]}...")

        # Generate answer
        answer = smart_answer(persona, bot, turn - 2)
        print(f"\n[{turn}] USER: {answer}")

        result = chat(session_id, answer)
        if result.get("error"):
            print(f"❌ API Error")
            break

        bot = result.get("response", "")
        print(f"BOT: {bot[:120]}...")
        messages.append((answer, bot))

        turn += 1
        time.sleep(0.5)

    # If eligibility shown and availability offered, try booking
    if "I can see availability" in bot:
        print(f"\n[{turn}] ✅ Availability shown - attempting booking...")

        # Confirm booking
        result = chat(session_id, "Yes")
        bot = result.get("response", "")
        messages.append(("Yes", bot))
        turn += 1
        time.sleep(0.5)

        # Provide contact details
        contact_flow = [
            persona["name"].split()[0],  # First name
            "918-555-0001",  # Phone
            f"{persona['name'].split()[0].lower()}@test.com",  # Email
            "01/15/1990",  # DOB
        ]

        for info in contact_flow:
            print(f"\n[{turn}] USER: {info}")
            result = chat(session_id, info)
            bot = result.get("response", "")
            print(f"BOT: {bot[:100]}...")
            messages.append((info, bot))

            if "booking has been submitted" in bot.lower():
                print(f"\n✅ BOOKING COMPLETE!")
                break

            turn += 1
            time.sleep(0.5)

    # Verify in database
    contact = db.execute_query("SELECT id FROM patient_contact_info WHERE session_id = %s", (session_id,))
    appt = db.execute_query("SELECT id FROM appointments WHERE session_id = %s", (session_id,))
    ps = db.execute_query("SELECT status, eligible FROM prescreening_sessions WHERE session_id = %s", (session_id,))

    print(f"\n📊 VERIFICATION:")
    print(f"   Messages: {len(messages)}")
    print(f"   Contact: {'✅' if contact else '❌'}")
    print(f"   Appointment: {'✅' if appt else '❌'}")
    print(f"   Prescreening: {ps[0]['status'] if ps else '❌'}")
    print(f"   Errors: {len(errors)}")

    return {
        "persona": persona["name"],
        "session_id": session_id,
        "messages": len(messages),
        "has_contact": bool(contact),
        "has_booking": bool(appt),
        "prescreening": ps[0]["status"] if ps else None,
        "eligible": ps[0]["eligible"] if ps else None,
        "errors": errors,
        "success": bool(contact or appt)
    }

if __name__ == "__main__":
    num_personas = int(sys.argv[1]) if len(sys.argv) > 1 else len(PERSONAS)

    print("\n" + "="*70)
    print("COMPREHENSIVE STRESS TEST")
    print("="*70)
    print(f"Testing {num_personas} personas")
    print(f"Started: {datetime.now()}")

    results = []
    for i, persona in enumerate(PERSONAS[:num_personas], 1):
        print(f"\n\n{'#'*70}")
        print(f"TEST {i}/{num_personas}")
        print(f"{'#'*70}")

        result = run_organic_test(persona)
        results.append(result)

        time.sleep(2)

    # Summary
    print("\n\n" + "="*70)
    print("FINAL SUMMARY")
    print("="*70)

    successful = [r for r in results if r["success"]]
    bookings = [r for r in results if r["has_booking"]]
    contacts = [r for r in results if r["has_contact"]]
    with_errors = [r for r in results if r["errors"]]

    print(f"\nSuccess Rate: {len(successful)}/{len(results)} ({len(successful)/len(results)*100:.1f}%)")
    print(f"Bookings: {len(bookings)}")
    print(f"Contacts: {len(contacts)}")
    print(f"Errors: {len(with_errors)}")

    print("\n" + "="*70)
    for r in results:
        status = "✅" if r["success"] else "❌"
        print(f"{status} {r['persona']}: {r['messages']} msgs | {r['session_id']}")
        if r["errors"]:
            for e in r["errors"]:
                print(f"   ⚠️  {e}")

    # Save results
    with open('/tmp/stress_test_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print("\nResults saved: /tmp/stress_test_results.json")
