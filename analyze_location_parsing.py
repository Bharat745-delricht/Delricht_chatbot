#!/usr/bin/env python3
"""Analyze recent sessions for location parsing issues"""

from core.database import db
import re

def analyze_location_issues():
    """Focus on location identification problems"""

    print("\n" + "="*100)
    print("LOCATION PARSING ANALYSIS - LAST 10 SESSIONS")
    print("="*100)

    # Get last 10 sessions
    recent_sessions = db.execute_query("""
        SELECT DISTINCT session_id,
               MIN(timestamp) as first_message,
               MAX(timestamp) as last_message,
               COUNT(*) as message_count
        FROM chat_logs
        WHERE timestamp > NOW() - INTERVAL '3 days'
        AND session_id NOT IN ('session_zjvj4hlnb', 'session_wd47ilgkd')
        GROUP BY session_id
        ORDER BY MAX(timestamp) DESC
        LIMIT 10
    """)

    issues = {
        'location_parsing_failed': [],
        'zip_code_only': [],
        'city_state_zip_combo': [],
        'no_results_found': [],
        'repeated_clarification': []
    }

    for session_info in recent_sessions:
        session_id = session_info['session_id']

        # Get conversation
        conversation = db.execute_query("""
            SELECT timestamp, user_message, bot_response
            FROM chat_logs
            WHERE session_id = %s
            ORDER BY timestamp ASC
        """, (session_id,))

        print(f"\n{'='*100}")
        print(f"SESSION: {session_id} ({len(conversation)} messages)")
        print("="*100)

        for i, turn in enumerate(conversation):
            user_msg = turn['user_message']
            bot_msg = turn['bot_response']

            # Check for location patterns in user message
            zip_pattern = r'\b\d{5}\b'
            city_state_pattern = r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),?\s+([A-Z]{2})\b'
            city_state_zip_pattern = r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+([A-Z][a-z]+)\s+(\d{5})'

            zip_match = re.search(zip_pattern, user_msg)
            city_state_match = re.search(city_state_pattern, user_msg)
            city_state_zip_match = re.search(city_state_zip_pattern, user_msg)

            # Check bot's response
            couldnt_find = "couldn't find" in bot_msg.lower() or "no trials" in bot_msg.lower()
            asked_for_location = "location" in bot_msg.lower() and ("what" in bot_msg.lower() or "where" in bot_msg.lower())

            # Display
            print(f"\n[{i+1}] User: {user_msg}")
            print(f"    Bot: {bot_msg[:200]}...")

            # Analyze location issues
            if zip_match and not city_state_match:
                print(f"    🔍 ZIP CODE ONLY: {zip_match.group()}")
                if couldnt_find:
                    issues['zip_code_only'].append({
                        'session_id': session_id,
                        'zip': zip_match.group(),
                        'user_msg': user_msg,
                        'turn': i+1
                    })
                    print(f"    ❌ NO RESULTS for zip code")

            if city_state_zip_match:
                city, state, zipcode = city_state_zip_match.groups()
                print(f"    🔍 CITY+STATE+ZIP: {city} {state} {zipcode}")
                if couldnt_find:
                    issues['city_state_zip_combo'].append({
                        'session_id': session_id,
                        'location': f"{city} {state} {zipcode}",
                        'parsed_as': user_msg,
                        'turn': i+1
                    })
                    print(f"    ❌ NO RESULTS for complex location")

            if couldnt_find:
                # Extract what the bot tried to search
                search_pattern = r"couldn't find.*for (.*?) in (.*?)(?:\.|at)"
                search_match = re.search(search_pattern, bot_msg, re.IGNORECASE)
                if search_match:
                    condition, location = search_match.groups()
                    print(f"    🔍 BOT SEARCHED: condition='{condition}', location='{location}'")

                    issues['no_results_found'].append({
                        'session_id': session_id,
                        'user_input': user_msg,
                        'bot_searched_location': location.strip(),
                        'turn': i+1
                    })

            if asked_for_location and i > 0:
                print(f"    ⚠️  BOT ASKING FOR LOCATION (turn {i+1})")
                issues['repeated_clarification'].append({
                    'session_id': session_id,
                    'turn': i+1
                })

    # Summary
    print(f"\n\n{'='*100}")
    print("LOCATION ISSUE SUMMARY")
    print("="*100)

    print(f"\n📍 ZIP CODE ONLY (no city/state): {len(issues['zip_code_only'])} cases")
    for case in issues['zip_code_only'][:5]:
        print(f"   - {case['session_id']} turn {case['turn']}: ZIP {case['zip']}")
        print(f"     User said: '{case['user_msg']}'")

    print(f"\n📍 CITY+STATE+ZIP COMBO: {len(issues['city_state_zip_combo'])} cases")
    for case in issues['city_state_zip_combo'][:5]:
        print(f"   - {case['session_id']} turn {case['turn']}: {case['location']}")
        print(f"     User said: '{case['parsed_as']}'")

    print(f"\n❌ NO RESULTS FOUND: {len(issues['no_results_found'])} cases")
    location_searches = {}
    for case in issues['no_results_found']:
        loc = case['bot_searched_location']
        location_searches[loc] = location_searches.get(loc, 0) + 1

    print(f"   Most common failed searches:")
    for loc, count in sorted(location_searches.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"   - '{loc}': {count}x")

    print(f"\n🔄 REPEATED CLARIFICATION: {len(issues['repeated_clarification'])} cases")

    return issues

if __name__ == "__main__":
    issues = analyze_location_issues()
