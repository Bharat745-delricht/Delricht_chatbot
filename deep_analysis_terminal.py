#!/usr/bin/env python3
"""
Deep Conversation Analysis - Part 2
Terminal-based analysis with real examples to help prioritize fixes
"""

from core.database import db
from datetime import datetime
from typing import List, Dict, Any


class DeepAnalyzer:
    """Deep analysis with terminal examples"""

    def __init__(self, hours: int = 24):
        self.hours = hours
        self.total_sessions = 0
        self.issues_found = {
            'error_messages': [],
            'yes_no_confusion': [],
            'numeric_failures': [],
            'eligibility_calculation': [],
            'context_management': []
        }

    def get_total_sessions(self):
        """Get total number of sessions in time window"""
        query = """
            SELECT COUNT(DISTINCT session_id) as total
            FROM chat_logs
            WHERE timestamp > NOW() - INTERVAL '%s hours'
                AND session_id NOT LIKE 'AUTO_TEST_%%'
        """
        result = db.execute_query(query, (self.hours,))
        self.total_sessions = result[0]['total'] if result else 0
        return self.total_sessions

    def get_context_for_message(self, session_id: str, timestamp: datetime, context_count: int = 2):
        """Get previous messages for context"""
        query = """
            SELECT user_message, bot_response, timestamp
            FROM chat_logs
            WHERE session_id = %s
                AND timestamp < %s
            ORDER BY timestamp DESC
            LIMIT %s
        """
        return db.execute_query(query, (session_id, timestamp, context_count))

    def analyze_error_messages(self):
        """Category A: Find all error messages"""
        print("\n" + "="*80)
        print("CATEGORY A: ERROR MESSAGES")
        print("="*80)
        print("Goal: Distinguish DB connection errors from logic errors\n")

        query = """
            SELECT
                cl.session_id,
                cl.timestamp,
                cl.user_message,
                cl.bot_response
            FROM chat_logs cl
            WHERE cl.timestamp > NOW() - INTERVAL '%s hours'
                AND cl.session_id NOT LIKE 'AUTO_TEST_%%'
                AND (
                    cl.bot_response ILIKE '%%error%%'
                    OR cl.bot_response ILIKE '%%trouble%%'
                    OR cl.bot_response ILIKE '%%apologize%%'
                    OR cl.bot_response ILIKE '%%couldn''t%%'
                    OR cl.bot_response ILIKE '%%didn''t understand%%'
                )
            ORDER BY cl.timestamp DESC
            LIMIT 20
        """

        results = db.execute_query(query, (self.hours,))
        self.issues_found['error_messages'] = results

        if not results:
            print("✅ No error messages found in last {} hours!\n".format(self.hours))
            return

        print(f"Found {len(results)} error messages\n")

        # Categorize errors
        error_types = {
            'trouble_processing': [],
            'trouble_evaluating': [],
            'couldnt_understand': [],
            'didnt_understand': [],
            'couldnt_find_number': [],
            'other': []
        }

        for r in results:
            bot_lower = r['bot_response'].lower()
            if 'trouble processing your answer' in bot_lower:
                error_types['trouble_processing'].append(r)
            elif 'trouble evaluating your eligibility' in bot_lower:
                error_types['trouble_evaluating'].append(r)
            elif "couldn't understand" in bot_lower:
                error_types['couldnt_understand'].append(r)
            elif "didn't understand" in bot_lower:
                error_types['didnt_understand'].append(r)
            elif "couldn't find a number" in bot_lower:
                error_types['couldnt_find_number'].append(r)
            else:
                error_types['other'].append(r)

        # Show examples for each type
        for error_type, examples in error_types.items():
            if not examples:
                continue

            print(f"\n📍 Error Type: {error_type.replace('_', ' ').title()}")
            print(f"   Frequency: {len(examples)} occurrence(s)")

            # Show up to 2 examples
            for i, example in enumerate(examples[:2], 1):
                print(f"\n   Example {i}:")
                print(f"   Session: {example['session_id']}")
                print(f"   Time: {example['timestamp']}")

                # Get context
                context = self.get_context_for_message(
                    example['session_id'],
                    example['timestamp'],
                    context_count=1
                )

                if context:
                    prev = context[0]
                    print(f"   Previous Bot: {prev['bot_response'][:100]}...")
                    print(f"   Previous User: {prev['user_message'][:100] if prev['user_message'] else '(empty)'}...")

                print(f"   User: \"{example['user_message'][:150] if example['user_message'] else '(empty)'}\"")
                print(f"   Bot: \"{example['bot_response'][:200]}...\"")

        # Calculate frequency
        affected_sessions = len(set(r['session_id'] for r in results))
        frequency_pct = (affected_sessions / self.total_sessions * 100) if self.total_sessions > 0 else 0
        print(f"\n📊 Summary: {affected_sessions} of {self.total_sessions} sessions ({frequency_pct:.1f}%) had error messages")

    def analyze_yes_no_confusion(self):
        """Category B1: Yes/No interpretation issues"""
        print("\n" + "="*80)
        print("CATEGORY B1: YES/NO CONFUSION")
        print("="*80)
        print("Goal: Find where bot misunderstands yes/no answers\n")

        query = """
            SELECT
                cl.session_id,
                cl.timestamp,
                cl.user_message,
                cl.bot_response
            FROM chat_logs cl
            WHERE cl.timestamp > NOW() - INTERVAL '%s hours'
                AND cl.session_id NOT LIKE 'AUTO_TEST_%%'
                AND (cl.user_message ILIKE '%%yes%%' OR cl.user_message ILIKE '%%no%%')
                AND (
                    cl.bot_response ILIKE '%%didn''t understand%%'
                    OR cl.bot_response ILIKE '%%couldn''t understand%%'
                )
            ORDER BY cl.timestamp DESC
            LIMIT 10
        """

        results = db.execute_query(query, (self.hours,))
        self.issues_found['yes_no_confusion'] = results

        if not results:
            print("✅ No yes/no confusion found!\n")
            return

        print(f"Found {len(results)} cases of yes/no confusion\n")

        for i, r in enumerate(results[:3], 1):
            print(f"Example {i}:")
            print(f"Session: {r['session_id']}")
            print(f"Time: {r['timestamp']}")

            # Get context
            context = self.get_context_for_message(r['session_id'], r['timestamp'], 1)
            if context:
                prev = context[0]
                print(f"Previous Bot Question: \"{prev['bot_response'][:150]}...\"")

            print(f"User Answer: \"{r['user_message']}\"")
            print(f"Bot Response: \"{r['bot_response'][:150]}...\"")
            print()

        # Frequency
        affected_sessions = len(set(r['session_id'] for r in results))
        frequency_pct = (affected_sessions / self.total_sessions * 100) if self.total_sessions > 0 else 0
        print(f"📊 Summary: {affected_sessions} of {self.total_sessions} sessions ({frequency_pct:.1f}%) had yes/no confusion\n")

    def analyze_numeric_failures(self):
        """Category B2: Numeric interpretation failures"""
        print("\n" + "="*80)
        print("CATEGORY B2: NUMERIC INTERPRETATION FAILURES")
        print("="*80)
        print("Goal: Find where bot doesn't recognize valid numbers\n")

        query = """
            SELECT
                cl.session_id,
                cl.timestamp,
                cl.user_message,
                cl.bot_response
            FROM chat_logs cl
            WHERE cl.timestamp > NOW() - INTERVAL '%s hours'
                AND cl.session_id NOT LIKE 'AUTO_TEST_%%'
                AND cl.user_message ~ '[0-9]+'
                AND (
                    cl.bot_response ILIKE '%%couldn''t find a number%%'
                    OR cl.bot_response ILIKE '%%couldn''t find%%number%%'
                    OR cl.bot_response ILIKE '%%provide a numeric%%'
                )
            ORDER BY cl.timestamp DESC
            LIMIT 10
        """

        results = db.execute_query(query, (self.hours,))
        self.issues_found['numeric_failures'] = results

        if not results:
            print("✅ No numeric interpretation failures found!\n")
            return

        print(f"Found {len(results)} numeric interpretation failures\n")

        for i, r in enumerate(results[:3], 1):
            print(f"Example {i}:")
            print(f"Session: {r['session_id']}")
            print(f"Time: {r['timestamp']}")

            # Get context
            context = self.get_context_for_message(r['session_id'], r['timestamp'], 1)
            if context:
                prev = context[0]
                print(f"Previous Bot Question: \"{prev['bot_response'][:150]}...\"")

            print(f"User Answer: \"{r['user_message']}\"")
            print(f"Bot Response: \"{r['bot_response'][:150]}...\"")

            # Check if this is during prescreening
            prescreen_check = db.execute_query("""
                SELECT ps.trial_id, ct.trial_name
                FROM prescreening_sessions ps
                JOIN clinical_trials ct ON ps.trial_id = ct.id
                WHERE ps.session_id = %s
                LIMIT 1
            """, (r['session_id'],))

            if prescreen_check:
                print(f"Context: During prescreening for trial {prescreen_check[0]['trial_id']} ({prescreen_check[0]['trial_name'][:50]}...)")

            print()

        # Frequency
        affected_sessions = len(set(r['session_id'] for r in results))
        frequency_pct = (affected_sessions / self.total_sessions * 100) if self.total_sessions > 0 else 0
        print(f"📊 Summary: {affected_sessions} of {self.total_sessions} sessions ({frequency_pct:.1f}%) had numeric interpretation failures\n")

    def analyze_eligibility_calculation(self):
        """Category D: Verify eligibility calculations"""
        print("\n" + "="*80)
        print("CATEGORY D: ELIGIBILITY CALCULATION")
        print("="*80)
        print("Goal: Verify eligibility determinations are correct\n")

        query = """
            SELECT
                ps.session_id,
                ps.trial_id,
                ps.answered_questions,
                ps.total_questions,
                ps.eligible,
                ps.eligibility_result,
                ct.trial_name,
                ct.conditions,
                cl.bot_response
            FROM prescreening_sessions ps
            JOIN clinical_trials ct ON ps.trial_id = ct.id
            JOIN chat_logs cl ON ps.session_id = cl.session_id
            WHERE ps.completed_at > NOW() - INTERVAL '%s hours'
                AND ps.session_id NOT LIKE 'AUTO_TEST_%%'
                AND cl.bot_response ILIKE '%%inclusion criteria%%'
            ORDER BY ps.completed_at DESC
            LIMIT 10
        """

        results = db.execute_query(query, (self.hours,))
        self.issues_found['eligibility_calculation'] = results

        if not results:
            print("ℹ️  No completed prescreening sessions in last {} hours\n".format(self.hours))
            return

        print(f"Found {len(results)} completed prescreening sessions\n")

        for i, r in enumerate(results[:5], 1):
            print(f"Example {i}:")
            print(f"Session: {r['session_id']}")
            print(f"Trial: {r['trial_name'][:60]}...")
            print(f"Condition: {r['conditions']}")
            print(f"Questions: {r['answered_questions']}/{r['total_questions']}")
            print(f"Eligible: {r['eligible']}")
            print(f"Result: {r['eligibility_result']}")

            # Extract criteria from bot response
            bot_response = r['bot_response']
            if 'Inclusion criteria:' in bot_response:
                # Extract the criteria line
                lines = bot_response.split('\n')
                for line in lines:
                    if 'Inclusion criteria' in line or 'Exclusion criteria' in line:
                        print(f"  {line.strip()}")

            print()

        print(f"📊 Summary: {len(results)} completed prescreening sessions found\n")

    def analyze_context_management(self):
        """Category E: Context and state management issues"""
        print("\n" + "="*80)
        print("CATEGORY E: CONTEXT & STATE MANAGEMENT")
        print("="*80)
        print("Goal: Find where bot loses context or state\n")

        # Check for repeated questions
        query = """
            WITH consecutive_messages AS (
                SELECT
                    session_id,
                    bot_response,
                    timestamp,
                    LAG(bot_response) OVER (PARTITION BY session_id ORDER BY timestamp) as prev_response
                FROM chat_logs
                WHERE timestamp > NOW() - INTERVAL '%s hours'
                    AND session_id NOT LIKE 'AUTO_TEST_%%'
            )
            SELECT session_id, bot_response, timestamp
            FROM consecutive_messages
            WHERE bot_response = prev_response
                AND LENGTH(bot_response) > 50
            ORDER BY timestamp DESC
            LIMIT 10
        """

        results = db.execute_query(query, (self.hours,))

        if not results:
            print("✅ No repetitive messages found!\n")
        else:
            print(f"Found {len(results)} cases of repeated messages\n")

            for i, r in enumerate(results[:3], 1):
                print(f"Example {i}:")
                print(f"Session: {r['session_id']}")
                print(f"Time: {r['timestamp']}")
                print(f"Repeated Message: \"{r['bot_response'][:150]}...\"")
                print()

            affected_sessions = len(set(r['session_id'] for r in results))
            frequency_pct = (affected_sessions / self.total_sessions * 100) if self.total_sessions > 0 else 0
            print(f"📊 Summary: {affected_sessions} of {self.total_sessions} sessions ({frequency_pct:.1f}%) had repeated messages\n")

    def generate_prioritization_summary(self):
        """Generate prioritized list of issues"""
        print("\n" + "="*80)
        print("PRIORITIZATION SUMMARY")
        print("="*80)
        print()

        priorities = []

        # Calculate severity scores
        for issue_type, examples in self.issues_found.items():
            if not examples:
                continue

            affected_sessions = len(set(e['session_id'] for e in examples))
            frequency_pct = (affected_sessions / self.total_sessions * 100) if self.total_sessions > 0 else 0

            # Determine severity
            if frequency_pct > 10:
                severity = "🔴 CRITICAL"
                score = 3
            elif frequency_pct > 5:
                severity = "🟠 HIGH"
                score = 2
            elif frequency_pct > 2:
                severity = "🟡 MEDIUM"
                score = 1
            else:
                severity = "🟢 LOW"
                score = 0.5

            priorities.append({
                'issue': issue_type.replace('_', ' ').title(),
                'severity': severity,
                'frequency_pct': frequency_pct,
                'affected_sessions': affected_sessions,
                'total_occurrences': len(examples),
                'score': score * frequency_pct
            })

        # Sort by score
        priorities.sort(key=lambda x: x['score'], reverse=True)

        if not priorities:
            print("✅ No significant issues found in last {} hours!".format(self.hours))
            return

        print("Issues ranked by impact (frequency × severity):\n")

        for rank, issue in enumerate(priorities, 1):
            print(f"{rank}. {issue['severity']} {issue['issue']}")
            print(f"   Frequency: {issue['affected_sessions']} sessions ({issue['frequency_pct']:.1f}%)")
            print(f"   Total occurrences: {issue['total_occurrences']}")
            print(f"   Impact score: {issue['score']:.1f}")
            print()

    def run(self):
        """Run full analysis"""
        print("="*80)
        print("DEEP CONVERSATION ANALYSIS - PART 2")
        print("="*80)
        print(f"Analyzing conversations from last {self.hours} hours")
        print()

        # Get total sessions
        self.get_total_sessions()
        print(f"Total sessions to analyze: {self.total_sessions}")

        if self.total_sessions == 0:
            print("\n⚠️  No sessions found in the specified time window.")
            return

        # Run all analyses
        self.analyze_error_messages()
        self.analyze_yes_no_confusion()
        self.analyze_numeric_failures()
        self.analyze_eligibility_calculation()
        self.analyze_context_management()

        # Generate summary
        self.generate_prioritization_summary()

        print("\n" + "="*80)
        print("ANALYSIS COMPLETE")
        print("="*80)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Deep conversation analysis with terminal examples')
    parser.add_argument('--hours', type=int, default=24, help='Hours to analyze (default: 24)')

    args = parser.parse_args()

    analyzer = DeepAnalyzer(hours=args.hours)
    analyzer.run()


if __name__ == '__main__':
    main()
