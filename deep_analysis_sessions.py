#!/usr/bin/env python3
"""Deep analysis of recent sessions focusing on conversation quality"""

from core.database import db
from collections import defaultdict, Counter
import re

def deep_analysis():
    """Perform deep analysis on recent sessions"""

    print("\n" + "="*100)
    print("DEEP ANALYSIS: CONVERSATION QUALITY & PATTERNS")
    print("="*100)

    # Get last 20 sessions for better sample size
    recent_sessions = db.execute_query("""
        SELECT DISTINCT session_id,
               MIN(timestamp) as first_message,
               MAX(timestamp) as last_message,
               COUNT(*) as message_count
        FROM chat_logs
        WHERE timestamp > NOW() - INTERVAL '7 days'
        GROUP BY session_id
        ORDER BY MAX(timestamp) DESC
        LIMIT 20
    """)

    issues = {
        'context_loss': [],
        'repetitive_questions': [],
        'unclear_responses': [],
        'failed_searches': [],
        'incomplete_workflows': [],
        'location_confusion': [],
        'condition_confusion': []
    }

    for session_info in recent_sessions:
        session_id = session_info['session_id']

        # Get full conversation
        conversation = db.execute_query("""
            SELECT timestamp, user_message, bot_response
            FROM chat_logs
            WHERE session_id = %s
            ORDER BY timestamp ASC
        """, (session_id,))

        if len(conversation) < 3:
            continue  # Skip very short sessions

        print(f"\n{'='*100}")
        print(f"SESSION: {session_id} ({len(conversation)} messages)")
        print("="*100)

        # Analyze conversation flow
        locations_mentioned = []
        conditions_mentioned = []
        bot_asked_location = 0
        bot_asked_condition = 0
        user_provided_both = False

        for i, turn in enumerate(conversation):
            user_msg = turn['user_message']
            bot_msg = turn['bot_response']

            # Extract locations
            location_pattern = r'\b(Atlanta|Tulsa|New Orleans|Baton Rouge|Dallas|Houston|Memphis|Nashville)\b'
            user_locations = re.findall(location_pattern, user_msg, re.IGNORECASE)
            bot_locations = re.findall(location_pattern, bot_msg, re.IGNORECASE)

            if user_locations:
                locations_mentioned.extend([loc.title() for loc in user_locations])

            # Extract conditions
            condition_keywords = ['covid', 'diabetes', 'psoriasis', 'asthma', 'arthritis', 'gout', 'lupus']
            for cond in condition_keywords:
                if cond in user_msg.lower():
                    conditions_mentioned.append(cond)

            # Check if bot asks for info
            if "what.*location" in bot_msg.lower() or "where.*located" in bot_msg.lower():
                bot_asked_location += 1
            if "what.*condition" in bot_msg.lower() or "which condition" in bot_msg.lower():
                bot_asked_condition += 1

            # Check if user provided both in first message
            if i == 0 and conditions_mentioned and locations_mentioned:
                user_provided_both = True

            # Display conversation flow
            if i < 5 or "trial" in bot_msg.lower() or "eligibility" in bot_msg.lower():
                print(f"\n[{i+1}] User: {user_msg[:100]}...")
                print(f"    Bot: {bot_msg[:150]}...")

        # Identify issues
        print(f"\n📊 PATTERNS:")
        print(f"   Locations mentioned: {set(locations_mentioned)}")
        print(f"   Conditions mentioned: {set(conditions_mentioned)}")
        print(f"   Bot asked for location: {bot_asked_location}x")
        print(f"   Bot asked for condition: {bot_asked_condition}x")
        print(f"   User provided both upfront: {user_provided_both}")

        # Issue: Bot asks for info user already provided
        if user_provided_both and (bot_asked_location > 0 or bot_asked_condition > 0):
            issues['context_loss'].append(session_id)
            print(f"   🔴 CONTEXT LOSS: User provided both, but bot asked again")

        # Issue: Location confusion
        if len(set(locations_mentioned)) > 1:
            issues['location_confusion'].append({
                'session_id': session_id,
                'locations': set(locations_mentioned)
            })
            print(f"   ⚠️  LOCATION CONFUSION: Multiple locations mentioned")

        # Issue: Condition confusion
        if len(set(conditions_mentioned)) > 1:
            issues['condition_confusion'].append({
                'session_id': session_id,
                'conditions': set(conditions_mentioned)
            })
            print(f"   ⚠️  CONDITION CONFUSION: Multiple conditions mentioned")

        # Issue: Repetitive questioning
        if bot_asked_location > 2 or bot_asked_condition > 2:
            issues['repetitive_questions'].append({
                'session_id': session_id,
                'location_asks': bot_asked_location,
                'condition_asks': bot_asked_condition
            })
            print(f"   🔴 REPETITIVE QUESTIONS: Asked multiple times")

    # Summary
    print(f"\n\n{'='*100}")
    print("ISSUE SUMMARY")
    print("="*100)

    for issue_type, affected_sessions in issues.items():
        if affected_sessions:
            print(f"\n🔴 {issue_type.upper().replace('_', ' ')}: {len(affected_sessions)} sessions")
            if isinstance(affected_sessions[0], dict):
                for example in affected_sessions[:3]:
                    print(f"   - {example['session_id']}: {example}")
            else:
                print(f"   Sessions: {affected_sessions[:5]}")

    # Sort issues by frequency
    sorted_issues = sorted(
        [(k, len(v)) for k, v in issues.items() if v],
        key=lambda x: x[1],
        reverse=True
    )

    print(f"\n\n🎯 TOP ISSUES BY FREQUENCY:")
    for i, (issue, count) in enumerate(sorted_issues[:3], 1):
        print(f"{i}. {issue.replace('_', ' ').title()}: {count} sessions")

    return issues, sorted_issues

if __name__ == "__main__":
    issues, sorted_issues = deep_analysis()
