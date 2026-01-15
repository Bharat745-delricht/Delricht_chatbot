#!/usr/bin/env python3
"""Analyze last 10 chat sessions for consistent issues"""

from core.database import db
from collections import defaultdict
import json

def analyze_recent_sessions():
    """Analyze the last 10 unique sessions"""

    print("\n" + "="*100)
    print("ANALYZING LAST 10 SESSIONS")
    print("="*100)

    # Get last 10 unique sessions
    recent_sessions = db.execute_query("""
        SELECT DISTINCT session_id,
               MIN(timestamp) as first_message,
               MAX(timestamp) as last_message,
               COUNT(*) as message_count
        FROM chat_logs
        WHERE timestamp > NOW() - INTERVAL '7 days'
        GROUP BY session_id
        ORDER BY MAX(timestamp) DESC
        LIMIT 10
    """)

    if not recent_sessions:
        print("No recent sessions found")
        return

    print(f"\nFound {len(recent_sessions)} recent sessions\n")

    issues_found = defaultdict(list)

    for idx, session_info in enumerate(recent_sessions, 1):
        session_id = session_info['session_id']
        print(f"\n{'='*100}")
        print(f"SESSION {idx}: {session_id}")
        print(f"Messages: {session_info['message_count']}")
        print(f"Duration: {session_info['first_message']} to {session_info['last_message']}")
        print("="*100)

        # Get conversation for this session
        conversation = db.execute_query("""
            SELECT timestamp, user_message, LEFT(bot_response, 200) as bot_preview
            FROM chat_logs
            WHERE session_id = %s
            ORDER BY timestamp ASC
        """, (session_id,))

        # Analyze conversation flow
        prev_bot_response = None
        repeated_responses = []
        search_count = 0
        prescreening_starts = 0
        error_messages = []
        clarification_requests = []

        for i, turn in enumerate(conversation, 1):
            user_msg = turn['user_message'].lower()
            bot_msg = turn['bot_preview'].lower()

            # Check for repeated bot responses
            if prev_bot_response and bot_msg == prev_bot_response:
                repeated_responses.append(f"Turn {i}: Repeated response")

            # Check for multiple trial searches
            if "found" in bot_msg and "trial" in bot_msg and "available" in bot_msg:
                search_count += 1

            # Check for prescreening starts
            if "check your eligibility" in bot_msg or "question 1 of" in bot_msg:
                prescreening_starts += 1

            # Check for error messages
            if "error" in bot_msg or "trouble" in bot_msg or "apologize" in bot_msg:
                error_messages.append(f"Turn {i}: {bot_msg[:100]}")

            # Check for clarification requests
            if "need" in bot_msg and ("condition" in bot_msg or "location" in bot_msg):
                clarification_requests.append(f"Turn {i}: Missing {bot_msg[50:150]}")

            prev_bot_response = bot_msg

        # Report issues
        print(f"\n📊 ANALYSIS:")
        print(f"   Search count: {search_count}")
        print(f"   Prescreening starts: {prescreening_starts}")
        print(f"   Repeated responses: {len(repeated_responses)}")
        print(f"   Error messages: {len(error_messages)}")
        print(f"   Clarification requests: {len(clarification_requests)}")

        # Identify specific issues
        if search_count > 1:
            issues_found['multiple_searches'].append({
                'session_id': session_id,
                'count': search_count,
                'messages': session_info['message_count']
            })
            print(f"   🔴 ISSUE: Multiple searches ({search_count}x)")

        if prescreening_starts > 1:
            issues_found['duplicate_prescreening'].append({
                'session_id': session_id,
                'count': prescreening_starts,
                'messages': session_info['message_count']
            })
            print(f"   🔴 ISSUE: Multiple prescreening starts ({prescreening_starts}x)")

        if repeated_responses:
            issues_found['repeated_responses'].append({
                'session_id': session_id,
                'examples': repeated_responses[:3]
            })
            print(f"   🔴 ISSUE: Repeated responses")

        if error_messages:
            issues_found['errors'].append({
                'session_id': session_id,
                'examples': error_messages[:2]
            })
            print(f"   🔴 ISSUE: Errors encountered")
            for err in error_messages[:2]:
                print(f"      {err}")

        if len(clarification_requests) > 2:
            issues_found['excessive_clarification'].append({
                'session_id': session_id,
                'count': len(clarification_requests)
            })
            print(f"   🔴 ISSUE: Excessive clarification requests ({len(clarification_requests)}x)")

    # Summary
    print(f"\n\n{'='*100}")
    print("SUMMARY OF ISSUES ACROSS ALL SESSIONS")
    print("="*100)

    issue_counts = {
        issue_type: len(sessions)
        for issue_type, sessions in issues_found.items()
    }

    # Sort by frequency
    sorted_issues = sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)

    for issue_type, count in sorted_issues:
        print(f"\n🔴 {issue_type.upper()}: {count} sessions affected")
        print(f"   Sessions: {[s['session_id'] for s in issues_found[issue_type][:5]]}")

        # Show examples
        if issues_found[issue_type]:
            example = issues_found[issue_type][0]
            if 'count' in example:
                print(f"   Example: Session had {example['count']} occurrences")
            if 'examples' in example:
                print(f"   Examples: {example['examples'][:2]}")

    print(f"\n{'='*100}\n")

    return issues_found, sorted_issues

if __name__ == "__main__":
    issues_found, sorted_issues = analyze_recent_sessions()

    # Return top 2 issues
    if len(sorted_issues) >= 2:
        print(f"\n🎯 TOP 2 ISSUES TO ADDRESS:")
        print(f"1. {sorted_issues[0][0]} ({sorted_issues[0][1]} sessions)")
        print(f"2. {sorted_issues[1][0]} ({sorted_issues[1][1]} sessions)")
