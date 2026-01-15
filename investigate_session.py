#!/usr/bin/env python3
"""Investigate circular conversation in session_zjvj4hlnb"""

from core.database import db

def investigate_session():
    session_id = 'session_zjvj4hlnb'

    print(f"\n{'='*80}")
    print(f"INVESTIGATING SESSION: {session_id}")
    print(f"{'='*80}\n")

    # Get chat logs
    print("📝 CONVERSATION HISTORY:")
    print("-" * 80)
    chat_logs = db.execute_query("""
        SELECT timestamp, user_message, LEFT(bot_response, 200) as bot_preview
        FROM chat_logs
        WHERE session_id = %s
        ORDER BY timestamp ASC
    """, (session_id,))

    if chat_logs:
        for i, log in enumerate(chat_logs, 1):
            print(f"\n[{i}] {log['timestamp']}")
            print(f"👤 USER: {log['user_message']}")
            print(f"🤖 BOT: {log['bot_preview']}...")
    else:
        print("No chat logs found")

    # Get conversation context
    print(f"\n\n{'='*80}")
    print("🧠 CONVERSATION CONTEXT:")
    print("-" * 80)
    context = db.execute_query("""
        SELECT conversation_state, focus_condition, focus_location,
               last_shown_trials, current_trial_id
        FROM conversation_contexts
        WHERE session_id = %s
    """, (session_id,))

    if context:
        ctx = context[0]
        print(f"State: {ctx['conversation_state']}")
        print(f"Condition: {ctx['focus_condition']}")
        print(f"Location: {ctx['focus_location']}")
        print(f"Last Shown Trials: {ctx['last_shown_trials']}")
        print(f"Current Trial ID: {ctx['current_trial_id']}")
    else:
        print("No context found")

    # Get prescreening session
    print(f"\n\n{'='*80}")
    print("✅ PRESCREENING SESSION:")
    print("-" * 80)
    prescreen = db.execute_query("""
        SELECT id, trial_id, status, created_at
        FROM prescreening_sessions
        WHERE session_id = %s
    """, (session_id,))

    if prescreen:
        for ps in prescreen:
            print(f"ID: {ps['id']}, Trial: {ps['trial_id']}, Status: {ps['status']}, Created: {ps['created_at']}")
    else:
        print("No prescreening session found")

    # Get prescreening answers
    print(f"\n\n{'='*80}")
    print("❓ PRESCREENING ANSWERS:")
    print("-" * 80)
    answers = db.execute_query("""
        SELECT question_text, user_answer, parsed_value
        FROM prescreening_answers
        WHERE session_id = %s
        ORDER BY created_at ASC
    """, (session_id,))

    if answers:
        for i, ans in enumerate(answers, 1):
            print(f"\n[{i}] Q: {ans['question_text']}")
            print(f"    A: {ans['user_answer']}")
            print(f"    Parsed: {ans['parsed_value']}")
    else:
        print("No answers found")

    print(f"\n{'='*80}\n")

if __name__ == "__main__":
    investigate_session()
