#!/usr/bin/env python3
"""Deep investigation of session_zjvj4hlnb circular conversation"""

import json
from core.database import db

def investigate_session_detailed():
    session_id = 'session_zjvj4hlnb'

    print(f"\n{'='*100}")
    print(f"DEEP INVESTIGATION: {session_id}")
    print(f"{'='*100}\n")

    # =========================================================================
    # PART 1: Complete Conversation Flow
    # =========================================================================
    print("📝 COMPLETE CONVERSATION HISTORY:")
    print("-" * 100)

    chat_logs = db.execute_query("""
        SELECT id, timestamp, user_message, bot_response
        FROM chat_logs
        WHERE session_id = %s
        ORDER BY timestamp ASC
    """, (session_id,))

    if chat_logs:
        for i, log in enumerate(chat_logs, 1):
            print(f"\n{'='*100}")
            print(f"[{i}] Timestamp: {log['timestamp']}")
            print(f"{'='*100}")
            print(f"👤 USER: {log['user_message']}")
            print(f"\n🤖 BOT: {log['bot_response']}")
    else:
        print("❌ No chat logs found")

    # =========================================================================
    # PART 2: Prescreening Sessions
    # =========================================================================
    print(f"\n\n{'='*100}")
    print("✅ PRESCREENING SESSIONS:")
    print("-" * 100)

    prescreen_sessions = db.execute_query("""
        SELECT id, trial_id, status, created_at, completed_at
        FROM prescreening_sessions
        WHERE session_id = %s
        ORDER BY created_at ASC
    """, (session_id,))

    if prescreen_sessions:
        for i, ps in enumerate(prescreen_sessions, 1):
            print(f"\n[{i}] Prescreening Session ID: {ps['id']}")
            print(f"    Trial ID: {ps['trial_id']}")
            print(f"    Status: {ps['status']}")
            print(f"    Created: {ps['created_at']}")
            print(f"    Completed: {ps['completed_at']}")

            # Get answers for this prescreening session
            answers = db.execute_query("""
                SELECT question_text, user_answer, parsed_value,
                       question_number, created_at
                FROM prescreening_answers
                WHERE prescreening_session_id = %s
                ORDER BY created_at ASC
            """, (ps['id'],))

            if answers:
                print(f"    Answers ({len(answers)}):")
                for j, ans in enumerate(answers, 1):
                    print(f"      Q{ans.get('question_number', j)}: {ans['question_text']}")
                    print(f"      A: {ans['user_answer']}")
                    print(f"      Parsed: {ans['parsed_value']}")
    else:
        print("❌ No prescreening sessions found")

    # =========================================================================
    # PART 3: Contact Collection
    # =========================================================================
    print(f"\n\n{'='*100}")
    print("📞 CONTACT INFORMATION:")
    print("-" * 100)

    contacts = db.execute_query("""
        SELECT first_name, last_name, phone, email, created_at
        FROM patient_contact_info
        WHERE session_id = %s
    """, (session_id,))

    if contacts:
        for contact in contacts:
            print(f"Name: {contact['first_name']} {contact['last_name']}")
            print(f"Phone: {contact['phone']}")
            print(f"Email: {contact['email']}")
            print(f"Created: {contact['created_at']}")
    else:
        print("❌ No contact info found")

    # =========================================================================
    # PART 4: Trial Information
    # =========================================================================
    print(f"\n\n{'='*100}")
    print("🔬 TRIAL DETAILS:")
    print("-" * 100)

    # Get unique trial IDs from prescreening sessions
    if prescreen_sessions:
        trial_ids = set(ps['trial_id'] for ps in prescreen_sessions if ps['trial_id'])
        for trial_id in trial_ids:
            trial = db.execute_query("""
                SELECT t.id, t.title, t.condition, t.phase, t.status,
                       ti.site_id, sc.site_name
                FROM trials t
                LEFT JOIN trial_investigators ti ON t.id = ti.trial_id
                LEFT JOIN site_coordinators sc ON ti.site_id = sc.site_id
                WHERE t.id = %s
                LIMIT 1
            """, (trial_id,))

            if trial:
                tr = trial[0]
                print(f"\nTrial ID: {tr['id']}")
                print(f"Title: {tr['title']}")
                print(f"Condition: {tr['condition']}")
                print(f"Phase: {tr['phase']}")
                print(f"Status: {tr['status']}")
                print(f"Site: {tr['site_name']} ({tr['site_id']})")

    # =========================================================================
    # PART 5: Analysis - Find the Problem
    # =========================================================================
    print(f"\n\n{'='*100}")
    print("🔍 ANALYSIS - WHAT WENT WRONG:")
    print("-" * 100)

    if chat_logs and len(chat_logs) >= 19:
        # Message 18 - Bot shows trials again
        msg_18 = chat_logs[17]  # 0-indexed
        # Message 19 - User says "1"
        msg_19 = chat_logs[18]

        print("\n❌ PROBLEM IDENTIFIED:")
        print(f"Message 18 [{msg_18['timestamp']}]:")
        print(f"  User: \"{msg_18['user_message']}\"")
        print(f"  Bot response starts: \"{msg_18['bot_response'][:150]}...\"")

        print(f"\nMessage 19 [{msg_19['timestamp']}]:")
        print(f"  User: \"{msg_19['user_message']}\"")
        print(f"  Bot response starts: \"{msg_19['bot_response'][:150]}...\"")

        print("\n🔍 ROOT CAUSE:")
        print("  1. User asks follow-up question about travel compensation (msg 17)")
        print("  2. Bot responds about travel reimbursement BUT ALSO shows trials again (msg 18)")
        print("  3. User selects '1' thinking it's new info, but bot restarts prescreening (msg 19)")
        print("\n💡 WHY THIS HAPPENED:")
        print("  - Bot detected 'trials' keyword in msg 17 and triggered trial search")
        print("  - Showed same trials user already prescreened for")
        print("  - Lost context that prescreening was already completed")
        print("  - Started fresh prescreening when user selected option 1")

    # =========================================================================
    # PART 6: State Machine Issue
    # =========================================================================
    print(f"\n\n{'='*100}")
    print("⚙️  STATE MACHINE ANALYSIS:")
    print("-" * 100)

    # Analyze conversation state transitions
    critical_messages = [
        ("Message 16", "Contact confirmed", "Should be 'completed'"),
        ("Message 17", "User asks about travel", "Should stay 'completed' or 'follow_up'"),
        ("Message 18", "Bot shows trials AGAIN", "⚠️ WRONG - Reset to 'trials_shown'"),
        ("Message 19", "User selects 1", "⚠️ WRONG - Started prescreening again"),
    ]

    for msg_label, action, expected_state in critical_messages:
        print(f"\n{msg_label}:")
        print(f"  Action: {action}")
        print(f"  Expected State: {expected_state}")

    print(f"\n\n{'='*100}")
    print("📊 SUMMARY:")
    print("-" * 100)
    print(f"Total Messages: {len(chat_logs) if chat_logs else 0}")
    print(f"Prescreening Sessions: {len(prescreen_sessions) if prescreen_sessions else 0}")
    print(f"Contact Info Collected: {'Yes' if contacts else 'No'}")

    if prescreen_sessions and len(prescreen_sessions) >= 2:
        print(f"\n⚠️  CIRCULAR CONVERSATION CONFIRMED:")
        print(f"   - First prescreening: {prescreen_sessions[0]['created_at']}")
        print(f"   - Second prescreening: {prescreen_sessions[1]['created_at']}")
        print(f"   - User answered same questions twice for same trial")

    print(f"\n{'='*100}\n")

if __name__ == "__main__":
    investigate_session_detailed()
