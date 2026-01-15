#!/usr/bin/env python3
"""
Database Debugging Script

Uses the existing database infrastructure to investigate issues
"""

import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from core.database import db


def check_auto_test_sessions():
    """Check AUTO_TEST sessions in the database"""
    print("=" * 80)
    print("🔍 CHECKING AUTO_TEST SESSIONS IN DATABASE")
    print("=" * 80)
    print()

    # Get all AUTO_TEST sessions
    results = db.execute_query("""
        SELECT
            session_id,
            user_id,
            focus_condition,
            focus_location,
            context_data,
            created_at,
            updated_at
        FROM conversation_context
        WHERE session_id LIKE 'AUTO_TEST%'
        ORDER BY created_at DESC
        LIMIT 20
    """)

    print(f"Found {len(results)} AUTO_TEST sessions\n")

    for i, row in enumerate(results, 1):
        print(f"[{i}] Session: {row['session_id']}")
        print(f"    User: {row['user_id']}")
        print(f"    Condition: {row['focus_condition']}")
        print(f"    Location: {row['focus_location']}")
        print(f"    Created: {row['created_at']}")

        # Parse context_data
        context_data = row['context_data']
        if isinstance(context_data, str):
            context_data = json.loads(context_data)

        # Check prescreening_data
        prescreening_data = context_data.get('prescreening_data', {})
        conversation_state = context_data.get('conversation_state', 'unknown')

        print(f"    State: {conversation_state}")

        if prescreening_data:
            print(f"    ✅ Prescreening Data: {len(prescreening_data)} keys")
            if 'questions' in prescreening_data:
                print(f"       Questions: {len(prescreening_data.get('questions', []))}")
            if 'current_question_index' in prescreening_data:
                print(f"       Current Index: {prescreening_data.get('current_question_index')}")
            if 'trial_id' in prescreening_data:
                print(f"       Trial ID: {prescreening_data.get('trial_id')}")
        else:
            print(f"    ❌ Prescreening Data: EMPTY or MISSING")

        print()

    return results


def check_prescreening_sessions():
    """Check prescreening_sessions table"""
    print("=" * 80)
    print("🔍 CHECKING PRESCREENING_SESSIONS TABLE")
    print("=" * 80)
    print()

    results = db.execute_query("""
        SELECT
            session_id,
            trial_id,
            status,
            total_questions,
            answered_questions,
            eligible,
            started_at,
            completed_at
        FROM prescreening_sessions
        WHERE session_id LIKE 'AUTO_TEST%'
        ORDER BY started_at DESC
        LIMIT 20
    """)

    print(f"Found {len(results)} prescreening sessions\n")

    for i, row in enumerate(results, 1):
        print(f"[{i}] Session: {row['session_id']}")
        print(f"    Trial: {row['trial_id']}")
        print(f"    Status: {row['status']}")
        print(f"    Questions: {row['answered_questions']}/{row['total_questions']}")
        print(f"    Eligible: {row['eligible']}")
        print(f"    Started: {row['started_at']}")
        print(f"    Completed: {row['completed_at']}")
        print()

    return results


def check_chat_logs():
    """Check chat logs for AUTO_TEST sessions"""
    print("=" * 80)
    print("🔍 CHECKING CHAT_LOGS")
    print("=" * 80)
    print()

    results = db.execute_query("""
        SELECT
            session_id,
            user_message,
            bot_response,
            timestamp
        FROM chat_logs
        WHERE session_id LIKE 'AUTO_TEST%'
        ORDER BY timestamp DESC
        LIMIT 30
    """)

    print(f"Found {len(results)} chat log entries\n")

    # Group by session
    sessions = {}
    for row in results:
        session_id = row['session_id']
        if session_id not in sessions:
            sessions[session_id] = []
        sessions[session_id].append(row)

    for session_id, logs in list(sessions.items())[:5]:  # Show first 5 sessions
        print(f"Session: {session_id}")
        print(f"Total turns: {len(logs)}")

        # Show first 3 turns
        for i, log in enumerate(logs[-3:], 1):  # Last 3 (newest)
            print(f"\n  [Turn {i}]")
            print(f"  User: {log['user_message'][:80]}")
            print(f"  Bot: {log['bot_response'][:150]}...")

        print("\n" + "-" * 80 + "\n")

    return results


def analyze_specific_session(session_id: str):
    """Deep dive into a specific session"""
    print("=" * 80)
    print(f"🔍 DEEP DIVE: {session_id}")
    print("=" * 80)
    print()

    # Get conversation context
    context_results = db.execute_query("""
        SELECT * FROM conversation_context
        WHERE session_id = %s
    """, (session_id,))

    if not context_results:
        print(f"❌ No conversation_context found for {session_id}")
        return

    context_row = context_results[0]
    context_data = context_row['context_data']
    if isinstance(context_data, str):
        context_data = json.loads(context_data)

    print("📋 CONVERSATION CONTEXT:")
    print(f"  State: {context_data.get('conversation_state')}")
    print(f"  Condition: {context_row['focus_condition']}")
    print(f"  Location: {context_row['focus_location']}")
    print(f"  Created: {context_row['created_at']}")
    print(f"  Updated: {context_row['updated_at']}")

    # Check prescreening_data
    prescreening_data = context_data.get('prescreening_data', {})
    print(f"\n📊 PRESCREENING DATA:")
    if prescreening_data:
        print(f"  Keys: {list(prescreening_data.keys())}")
        print(f"  Trial ID: {prescreening_data.get('trial_id')}")
        print(f"  Trial Name: {prescreening_data.get('trial_name')}")
        print(f"  Questions: {len(prescreening_data.get('questions', []))}")
        print(f"  Current Index: {prescreening_data.get('current_question_index')}")
        print(f"  Answers: {len(prescreening_data.get('answers', []))}")
    else:
        print(f"  ❌ EMPTY or MISSING")

    # Get prescreening session
    prescreening_results = db.execute_query("""
        SELECT * FROM prescreening_sessions
        WHERE session_id = %s
    """, (session_id,))

    if prescreening_results:
        ps = prescreening_results[0]
        print(f"\n📋 PRESCREENING SESSION:")
        print(f"  Trial ID: {ps['trial_id']}")
        print(f"  Status: {ps['status']}")
        print(f"  Questions: {ps['answered_questions']}/{ps['total_questions']}")
        print(f"  Eligible: {ps['eligible']}")
        print(f"  Result: {ps['eligibility_result']}")
    else:
        print(f"\n❌ No prescreening_sessions record")

    # Get chat logs
    chat_results = db.execute_query("""
        SELECT user_message, bot_response, timestamp
        FROM chat_logs
        WHERE session_id = %s
        ORDER BY timestamp ASC
    """, (session_id,))

    print(f"\n💬 CHAT HISTORY ({len(chat_results)} turns):")
    for i, log in enumerate(chat_results, 1):
        print(f"\n  [Turn {i}] {log['timestamp']}")
        print(f"  User: {log['user_message']}")
        print(f"  Bot: {log['bot_response'][:200]}")
        if len(log['bot_response']) > 200:
            print(f"       ... (truncated)")

    print()


def compare_working_vs_broken():
    """Compare a working session vs a broken session"""
    print("=" * 80)
    print("🔍 COMPARING WORKING VS BROKEN SESSIONS")
    print("=" * 80)
    print()

    # Find a working session (has prescreening_data)
    working = db.execute_query("""
        SELECT session_id, context_data
        FROM conversation_context
        WHERE session_id LIKE 'AUTO_TEST%'
        AND context_data::text LIKE '%prescreening_data%'
        AND context_data->'prescreening_data' != '{}'
        ORDER BY created_at DESC
        LIMIT 1
    """)

    # Find a broken session (no prescreening_data or empty)
    broken = db.execute_query("""
        SELECT session_id, context_data
        FROM conversation_context
        WHERE session_id LIKE 'AUTO_TEST%'
        AND (
            context_data::text NOT LIKE '%prescreening_data%'
            OR context_data->'prescreening_data' = '{}'
        )
        ORDER BY created_at DESC
        LIMIT 1
    """)

    if working:
        print("✅ WORKING SESSION:")
        analyze_specific_session(working[0]['session_id'])
        print("\n" + "=" * 80 + "\n")

    if broken:
        print("❌ BROKEN SESSION:")
        analyze_specific_session(broken[0]['session_id'])


def main():
    """Main debugging entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Database debugging for AUTO_TEST sessions")
    parser.add_argument("--sessions", action="store_true", help="Check AUTO_TEST sessions")
    parser.add_argument("--prescreening", action="store_true", help="Check prescreening_sessions table")
    parser.add_argument("--chat", action="store_true", help="Check chat_logs")
    parser.add_argument("--session", type=str, help="Deep dive into specific session")
    parser.add_argument("--compare", action="store_true", help="Compare working vs broken sessions")
    parser.add_argument("--all", action="store_true", help="Run all checks")

    args = parser.parse_args()

    if args.all or (not any([args.sessions, args.prescreening, args.chat, args.session, args.compare])):
        # Run all checks by default
        check_auto_test_sessions()
        print("\n")
        check_prescreening_sessions()
        print("\n")
        check_chat_logs()
        print("\n")
        compare_working_vs_broken()
    else:
        if args.sessions:
            check_auto_test_sessions()
        if args.prescreening:
            check_prescreening_sessions()
        if args.chat:
            check_chat_logs()
        if args.session:
            analyze_specific_session(args.session)
        if args.compare:
            compare_working_vs_broken()


if __name__ == "__main__":
    main()
