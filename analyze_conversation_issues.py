#!/usr/bin/env python3
"""
Conversation Analysis & Code Issue Detection Script

Analyzes recent chat conversations to identify code-level issues and correlates them
with specific parts of the codebase.

Usage:
    python analyze_conversation_issues.py [--hours HOURS] [--format FORMAT] [--verbose]

Arguments:
    --hours HOURS    : Hours to look back (default: 6)
    --format FORMAT  : Output format: 'markdown', 'json', or 'both' (default: both)
    --verbose        : Show detailed per-session analysis
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict

# Import database connection
from core.database import db


class ConversationAnalyzer:
    """Main analyzer for detecting code issues in conversations"""

    def __init__(self, time_window_hours: int = 6, verbose: bool = False):
        self.time_window_hours = time_window_hours
        self.verbose = verbose

        # Issue severity mapping
        self.severity_weights = {
            'CRITICAL': 3,
            'HIGH': 2,
            'MEDIUM': 1,
            'LOW': 0.5
        }

    def fetch_recent_sessions(self) -> List[Dict]:
        """Fetch all sessions from the last N hours"""
        print(f"\n📊 Fetching conversations from last {self.time_window_hours} hours...")

        query = """
            SELECT
                cl.session_id,
                cl.user_id,
                COUNT(DISTINCT cl.id) as message_count,
                MIN(cl.timestamp) as started_at,
                MAX(cl.timestamp) as last_message_at,
                COALESCE(MAX(cc.current_state), 'unknown') as conversation_state,
                MAX(cc.focus_condition) as focus_condition,
                MAX(cc.focus_location) as focus_location,
                MAX(cc.focus_trial_id) as focus_trial_id
            FROM chat_logs cl
            LEFT JOIN conversation_context cc
                ON cl.session_id = cc.session_id AND cc.active = true
            WHERE cl.timestamp > NOW() - INTERVAL '%s hours'
                AND cl.session_id IS NOT NULL
                AND cl.session_id NOT LIKE 'AUTO_TEST_%%'
            GROUP BY cl.session_id, cl.user_id
            ORDER BY MAX(cl.timestamp) DESC
        """

        results = db.execute_query(query, (self.time_window_hours,))
        print(f"✅ Found {len(results)} sessions to analyze\n")
        return results

    def fetch_session_details(self, session_id: str) -> Dict:
        """Fetch detailed conversation data for a session"""

        # Get all messages
        messages_query = """
            SELECT
                cl.id,
                cl.user_message,
                cl.bot_response,
                cl.intent_detected,
                cl.confidence_score,
                cl.timestamp,
                cl.context_data,
                cc.current_state,
                cc.context_data as full_context
            FROM chat_logs cl
            LEFT JOIN conversation_context cc ON cl.session_id = cc.session_id
            WHERE cl.session_id = %s
            ORDER BY cl.timestamp ASC
        """
        messages = db.execute_query(messages_query, (session_id,))

        # Get prescreening data
        prescreening_query = """
            SELECT
                ps.trial_id,
                ps.status,
                ps.total_questions,
                ps.answered_questions,
                ps.eligible,
                ps.eligibility_result,
                ps.started_at,
                ps.completed_at,
                ct.trial_name,
                ct.conditions
            FROM prescreening_sessions ps
            LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
            WHERE ps.session_id = %s
            ORDER BY ps.started_at DESC
            LIMIT 1
        """
        prescreening = db.execute_query(prescreening_query, (session_id,))

        # Get contact info
        contact_query = """
            SELECT
                first_name,
                last_name,
                phone_number,
                email,
                eligibility_status,
                created_at
            FROM patient_contact_info
            WHERE session_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        """
        contact = db.execute_query(contact_query, (session_id,))

        # Get SMS errors
        sms_errors_query = """
            SELECT
                phone_number,
                message_text,
                status,
                error_message,
                created_at
            FROM sms_conversations
            WHERE session_id = %s
                AND status NOT IN ('sent', 'delivered', 'read')
            ORDER BY created_at DESC
        """
        sms_errors = db.execute_query(sms_errors_query, (session_id,))

        return {
            'session_id': session_id,
            'turns': messages or [],
            'prescreening': prescreening[0] if prescreening else None,
            'contact_info': contact[0] if contact else None,
            'sms_errors': sms_errors or []
        }

    def detect_contact_collection_issue(self, details: Dict) -> Dict:
        """
        Detect if contact collection started but failed to capture first name.

        Pattern: Bot asks for consent → user provides name → no contact record OR missing first_name
        """
        indicators = {
            'bot_asked_for_consent': False,
            'user_provided_name_like_response': False,
            'bot_confused_by_name': False,
            'contact_record_incomplete': False,
            'state_was_collecting_first_name': False
        }

        turns = details.get('turns', [])

        for i, turn in enumerate(turns):
            bot_lower = turn.get('bot_response', '').lower()
            user_msg = turn.get('user_message', '').strip()
            current_state = turn.get('current_state', '')

            # Check if bot asked for consent
            if any(phrase in bot_lower for phrase in [
                "may i have your contact",
                "share your contact",
                "can i get your name",
                "get your first name"
            ]):
                indicators['bot_asked_for_consent'] = True

            # Check if user provided name-like response
            if indicators['bot_asked_for_consent'] and user_msg:
                # Single word that looks like a name
                words = user_msg.split()
                if 1 <= len(words) <= 3 and words[0][0].isupper():
                    indicators['user_provided_name_like_response'] = True

                    # Check next bot response
                    if i + 1 < len(turns):
                        next_bot = turns[i + 1].get('bot_response', '').lower()
                        if any(phrase in next_bot for phrase in [
                            "didn't understand",
                            "thank you for your time",
                            "unclear",
                            "could you clarify"
                        ]):
                            indicators['bot_confused_by_name'] = True

            # Check state
            if 'collecting_first_name' in current_state.lower():
                indicators['state_was_collecting_first_name'] = True

        # Check contact record
        contact_info = details.get('contact_info')
        if contact_info:
            if not contact_info.get('first_name'):
                indicators['contact_record_incomplete'] = True
        elif indicators['bot_asked_for_consent']:
            # No record created at all
            indicators['contact_record_incomplete'] = True

        # Calculate confidence
        indicator_values = list(indicators.values())
        confidence = sum(1 for v in indicator_values if v) / len(indicator_values)

        detected = confidence >= 0.4 and indicators['bot_asked_for_consent']

        return {
            'detected': detected,
            'confidence': confidence,
            'indicators': indicators,
            'code_location': 'core/conversation/gemini_conversation_manager.py:1904',
            'issue_type': 'contact_partial_data_not_initialized',
            'severity': 'HIGH',
            'description': 'Contact collection loses first name - contact_partial_data dict may not be initialized'
        }

    def detect_wrong_trial_issue(self, details: Dict) -> Dict:
        """
        Detect if user searched for condition X but got questions for condition Y.

        Pattern: User searches condition → bot shows trials → bot asks questions about different condition
        """
        indicators = {
            'user_mentioned_condition': None,
            'bot_showed_trials': False,
            'prescreening_started': False,
            'condition_mismatch': False
        }

        turns = details.get('turns', [])
        prescreening = details.get('prescreening')

        # Extract user's search condition
        for turn in turns:
            user_msg = turn.get('user_message', '').lower()
            intent = turn.get('intent_detected', '')

            if intent == 'trial_search' or 'trial' in user_msg or 'study' in user_msg:
                # Look for condition in message or context
                if turn.get('full_context'):
                    try:
                        context_data = turn['full_context']
                        if isinstance(context_data, str):
                            context_data = json.loads(context_data)
                        indicators['user_mentioned_condition'] = context_data.get('focus_condition', '')
                    except:
                        pass
                break

        # Check if bot showed trials
        for turn in turns:
            bot_lower = turn.get('bot_response', '').lower()
            if ('found' in bot_lower or 'here' in bot_lower) and 'trial' in bot_lower:
                indicators['bot_showed_trials'] = True
                break

        # Check prescreening
        if prescreening:
            indicators['prescreening_started'] = True
            trial_conditions = prescreening.get('conditions', '').lower()

            # Check if trial condition matches user's search
            if indicators['user_mentioned_condition']:
                user_cond = indicators['user_mentioned_condition'].lower()
                # Check for mismatch (user searched X, got trial for Y)
                if user_cond and trial_conditions:
                    # Allow partial matches, but detect clear mismatches
                    if user_cond not in trial_conditions and trial_conditions not in user_cond:
                        # Check if they're completely different conditions
                        common_conditions = ['gout', 'psoriasis', 'diabetes', 'obesity', 'arthritis',
                                            'dermatitis', 'alopecia', 'acne', 'hidradenitis']
                        user_cond_type = next((c for c in common_conditions if c in user_cond), None)
                        trial_cond_type = next((c for c in common_conditions if c in trial_conditions), None)

                        if user_cond_type and trial_cond_type and user_cond_type != trial_cond_type:
                            indicators['condition_mismatch'] = True

        # Calculate confidence
        required_indicators = [
            indicators['user_mentioned_condition'] is not None,
            indicators['bot_showed_trials'],
            indicators['prescreening_started'],
            indicators['condition_mismatch']
        ]
        confidence = sum(required_indicators) / len(required_indicators)

        detected = indicators['condition_mismatch']

        return {
            'detected': detected,
            'confidence': confidence,
            'indicators': indicators,
            'code_location': 'core/conversation/gemini_conversation_manager.py:1363-1375',
            'issue_type': 'wrong_trial_selected',
            'severity': 'CRITICAL',
            'description': 'User searches condition X but gets prescreening for condition Y'
        }

    def detect_availability_issue(self, details: Dict) -> Dict:
        """
        Detect if user was eligible but never saw availability/appointment options.

        Pattern: Prescreening completed + eligible → no availability mentioned → conversation ended
        """
        indicators = {
            'prescreening_completed': False,
            'eligible_status': False,
            'availability_mentioned': False,
            'scheduling_mentioned': False,
            'error_after_eligibility': False,
            'conversation_ended_without_scheduling': False
        }

        prescreening = details.get('prescreening')
        turns = details.get('turns', [])

        # Check prescreening status
        if prescreening:
            status = prescreening.get('status', '')
            eligible = prescreening.get('eligible')

            if status == 'completed':
                indicators['prescreening_completed'] = True

            if eligible is True or prescreening.get('eligibility_result') in ['potentially_eligible', 'eligible']:
                indicators['eligible_status'] = True

        # Check for availability/scheduling mentions
        eligibility_turn_index = None
        for i, turn in enumerate(turns):
            bot_lower = turn.get('bot_response', '').lower()

            # Find when eligibility was determined
            if 'you meet' in bot_lower or 'you may be eligible' in bot_lower or 'good news' in bot_lower:
                eligibility_turn_index = i

            # Check for availability mentions
            if any(word in bot_lower for word in ['available', 'availability', 'appointment', 'schedule', 'slot']):
                indicators['availability_mentioned'] = True

            # Check for scheduling mentions
            if 'coordinator will reach out' in bot_lower or 'contact you' in bot_lower:
                indicators['scheduling_mentioned'] = True

            # Check for errors after eligibility
            if eligibility_turn_index and i > eligibility_turn_index:
                if 'trouble' in bot_lower or 'error' in bot_lower or 'apologize' in bot_lower:
                    indicators['error_after_eligibility'] = True

        # Check if conversation ended without scheduling
        if indicators['eligible_status'] and not indicators['scheduling_mentioned']:
            indicators['conversation_ended_without_scheduling'] = True

        # Calculate confidence
        required_indicators = [
            indicators['prescreening_completed'],
            indicators['eligible_status'],
            not indicators['availability_mentioned'],
            indicators['conversation_ended_without_scheduling']
        ]
        confidence = sum(required_indicators) / len(required_indicators)

        detected = (indicators['eligible_status'] and
                   not indicators['availability_mentioned'] and
                   not indicators['scheduling_mentioned'])

        return {
            'detected': detected,
            'confidence': confidence,
            'indicators': indicators,
            'code_location': 'core/conversation/gemini_conversation_manager.py:2276-2329',
            'issue_type': 'availability_not_shown',
            'severity': 'MEDIUM',
            'description': 'Eligible users not shown availability due to cascading failures'
        }

    def detect_question_type_mismatch(self, details: Dict) -> Dict:
        """
        Detect if bot asked numeric question but expected yes/no, or vice versa.

        Pattern: Bot asks "How many X" → user provides number → "trouble processing answer"
        """
        indicators = {
            'numeric_question_indices': [],
            'user_provided_number_indices': [],
            'processing_error_indices': [],
            'mismatches_detected': 0
        }

        numeric_keywords = ['how many', 'number of', 'frequency', 'times per', 'episodes', 'flares', 'attacks']
        turns = details.get('turns', [])

        for i, turn in enumerate(turns):
            bot_lower = turn.get('bot_response', '').lower()

            # Check if question has numeric wording
            has_numeric_keyword = any(keyword in bot_lower for keyword in numeric_keywords)

            if 'question' in bot_lower and has_numeric_keyword:
                indicators['numeric_question_indices'].append(i)

                # Check user's response
                if i + 1 < len(turns):
                    user_response = turns[i + 1].get('user_message', '')

                    # Check if user provided a number
                    if any(char.isdigit() for char in user_response):
                        indicators['user_provided_number_indices'].append(i)

                        # Check if bot had trouble processing
                        if i + 2 < len(turns):
                            next_bot = turns[i + 2].get('bot_response', '').lower()
                            if 'trouble processing' in next_bot or "didn't understand" in next_bot:
                                indicators['processing_error_indices'].append(i)
                                indicators['mismatches_detected'] += 1

        detected = indicators['mismatches_detected'] > 0
        confidence = indicators['mismatches_detected'] / max(len(indicators['numeric_question_indices']), 1)

        return {
            'detected': detected,
            'confidence': confidence,
            'indicators': indicators,
            'code_location': 'core/prescreening/gemini_prescreening_manager.py (answer parsing)',
            'issue_type': 'question_type_mismatch',
            'severity': 'MEDIUM',
            'description': 'Question wording expects numeric answer but type expects yes/no'
        }

    def detect_async_errors(self, details: Dict) -> Dict:
        """
        Detect errors caused by missing await on async operations.

        Pattern: Explicit error messages about processing answers or evaluating eligibility
        """
        indicators = {
            'trouble_processing_answer_indices': [],
            'trouble_evaluating_eligibility_indices': [],
            'error_during_prescreening': False,
            'error_count': 0
        }

        turns = details.get('turns', [])
        prescreening = details.get('prescreening')

        for i, turn in enumerate(turns):
            bot_lower = turn.get('bot_response', '').lower()

            # Specific error messages from code
            if 'trouble processing your answer' in bot_lower:
                indicators['trouble_processing_answer_indices'].append(i)
                indicators['error_count'] += 1
                indicators['error_during_prescreening'] = True

            if 'trouble evaluating your eligibility' in bot_lower:
                indicators['trouble_evaluating_eligibility_indices'].append(i)
                indicators['error_count'] += 1

        detected = indicators['error_count'] > 0
        confidence = 1.0 if detected else 0.0  # These error messages are explicit

        return {
            'detected': detected,
            'confidence': confidence,
            'indicators': indicators,
            'code_location': 'core/conversation/gemini_conversation_manager.py (async operations)',
            'issue_type': 'async_operation_error',
            'severity': 'CRITICAL',
            'description': 'Missing await on async operations or unhandled async exceptions'
        }

    def analyze_session(self, session_id: str) -> Dict:
        """Run all detectors on a single session"""
        if self.verbose:
            print(f"  Analyzing session: {session_id}")

        details = self.fetch_session_details(session_id)

        detections = {
            'contact_partial_data_not_initialized': self.detect_contact_collection_issue(details),
            'wrong_trial_selected': self.detect_wrong_trial_issue(details),
            'availability_not_shown': self.detect_availability_issue(details),
            'question_type_mismatch': self.detect_question_type_mismatch(details),
            'async_operation_error': self.detect_async_errors(details)
        }

        return detections

    def correlate_patterns_to_code(self, all_detections: Dict) -> Dict:
        """Aggregate detections and create prioritized issue summary"""
        issue_summary = {
            'contact_partial_data_not_initialized': {
                'sessions_affected': [],
                'frequency': 0,
                'severity': 'HIGH',
                'code_file': 'core/conversation/gemini_conversation_manager.py',
                'code_lines': [1904, 1930, 1949, 1968, 1987],
                'description': 'contact_partial_data dict accessed before initialization',
                'fix_recommendation': 'Add defensive checks before accessing dict, ensure initialization'
            },
            'wrong_trial_selected': {
                'sessions_affected': [],
                'frequency': 0,
                'severity': 'CRITICAL',
                'code_file': 'core/conversation/gemini_conversation_manager.py',
                'code_lines': [1363, 1375],
                'description': 'User searches condition X but gets prescreening for condition Y',
                'fix_recommendation': 'Verify condition matching logic is working correctly'
            },
            'availability_not_shown': {
                'sessions_affected': [],
                'frequency': 0,
                'severity': 'MEDIUM',
                'code_file': 'core/conversation/gemini_conversation_manager.py',
                'code_lines': [2276, 2329],
                'description': 'Eligible users not shown availability due to cascading failures',
                'fix_recommendation': 'Add error handling and fallback flow in eligibility completion'
            },
            'question_type_mismatch': {
                'sessions_affected': [],
                'frequency': 0,
                'severity': 'MEDIUM',
                'code_file': 'core/prescreening/gemini_prescreening_manager.py',
                'code_lines': ['answer parsing logic'],
                'description': 'Question type doesn\'t match expected answer format',
                'fix_recommendation': 'Improve question type detection based on keywords'
            },
            'async_operation_error': {
                'sessions_affected': [],
                'frequency': 0,
                'severity': 'CRITICAL',
                'code_file': 'core/conversation/gemini_conversation_manager.py',
                'code_lines': ['async function calls'],
                'description': 'Missing await on async operations or unhandled exceptions',
                'fix_recommendation': 'Audit all async function calls for proper await'
            }
        }

        # Aggregate detections
        for session_id, detections in all_detections.items():
            for issue_type, detection in detections.items():
                if detection['detected']:
                    issue_summary[issue_type]['sessions_affected'].append({
                        'session_id': session_id,
                        'confidence': detection['confidence'],
                        'indicators': detection['indicators']
                    })
                    issue_summary[issue_type]['frequency'] += 1

        # Calculate impact scores
        for issue_type, summary in issue_summary.items():
            summary['impact_score'] = (
                summary['frequency'] *
                self.severity_weights[summary['severity']]
            )

        # Sort by impact score
        sorted_issues = sorted(
            issue_summary.items(),
            key=lambda x: x[1]['impact_score'],
            reverse=True
        )

        return dict(sorted_issues)

    def generate_markdown_report(self, issue_summary: Dict, total_sessions: int) -> str:
        """Generate comprehensive markdown report"""

        # Calculate statistics
        total_issues = sum(issue['frequency'] for issue in issue_summary.values())
        affected_sessions_set = set()
        for issue in issue_summary.values():
            for session in issue['sessions_affected']:
                affected_sessions_set.add(session['session_id'])
        affected_sessions = len(affected_sessions_set)

        critical_issues = sum(1 for i in issue_summary.values() if i['severity'] == 'CRITICAL' and i['frequency'] > 0)
        high_issues = sum(1 for i in issue_summary.values() if i['severity'] == 'HIGH' and i['frequency'] > 0)

        report = f"""# Conversation Analysis Report
**Time Window:** Last {self.time_window_hours} hours
**Total Sessions Analyzed:** {total_sessions}
**Report Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## Executive Summary

- **Total Issues Detected:** {total_issues}
- **Sessions Affected:** {affected_sessions}/{total_sessions} ({affected_sessions/total_sessions*100:.1f}% if total_sessions > 0 else 0)
- **Critical Issues:** {critical_issues}
- **High Priority Issues:** {high_issues}

---

## Prioritized Issues
"""

        # Add each issue
        severity_emoji = {
            'CRITICAL': '🔴',
            'HIGH': '🟠',
            'MEDIUM': '🟡',
            'LOW': '🔵'
        }

        rank = 1
        for issue_type, summary in issue_summary.items():
            if summary['frequency'] == 0:
                continue

            report += f"""
### {rank}. {severity_emoji[summary['severity']]} {issue_type.replace('_', ' ').title()}

**Severity:** {summary['severity']}
**Frequency:** {summary['frequency']} occurrence(s)
**Sessions Affected:** {len(summary['sessions_affected'])}
**Impact Score:** {summary['impact_score']}

**Description:**
{summary['description']}

**Code Location:**
- File: `{summary['code_file']}`
- Lines: {', '.join(map(str, summary['code_lines']))}

**Fix Recommendation:**
{summary['fix_recommendation']}

**Affected Sessions:**
"""

            # Show top 5 affected sessions
            for session in summary['sessions_affected'][:5]:
                report += f"- `{session['session_id']}` (confidence: {session['confidence']:.2f})\n"

            if len(summary['sessions_affected']) > 5:
                report += f"- ... and {len(summary['sessions_affected']) - 5} more\n"

            report += "\n---\n"
            rank += 1

        if total_issues == 0:
            report += "\n✅ **No significant issues detected in the analyzed conversations!**\n"

        return report

    def generate_json_report(self, issue_summary: Dict, all_detections: Dict, total_sessions: int) -> Dict:
        """Generate JSON report for machine processing"""
        return {
            'summary': issue_summary,
            'all_detections': all_detections,
            'metadata': {
                'total_sessions': total_sessions,
                'time_window_hours': self.time_window_hours,
                'generated_at': datetime.now().isoformat(),
                'total_issues': sum(issue['frequency'] for issue in issue_summary.values())
            }
        }

    def print_console_summary(self, issue_summary: Dict):
        """Print summary to console"""
        print("\n" + "="*80)
        print("ANALYSIS SUMMARY")
        print("="*80)

        has_issues = False
        for issue_type, summary in issue_summary.items():
            if summary['frequency'] > 0:
                has_issues = True
                print(f"\n{summary['severity']:8} | {issue_type:40} | {summary['frequency']:2} occurrences")
                print(f"{'':9} | {summary['code_file']}")
                print(f"{'':9} | Lines: {summary['code_lines']}")

        if not has_issues:
            print("\n✅ No significant issues detected!")

        print("\n" + "="*80)


def main():
    """Main execution function"""
    parser = argparse.ArgumentParser(
        description='Analyze conversations to detect code-level issues'
    )
    parser.add_argument(
        '--hours',
        type=int,
        default=6,
        help='Hours to look back (default: 6)'
    )
    parser.add_argument(
        '--format',
        choices=['markdown', 'json', 'both'],
        default='both',
        help='Output format (default: both)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed per-session analysis'
    )

    args = parser.parse_args()

    print("="*80)
    print("CONVERSATION ANALYSIS & CODE ISSUE DETECTION")
    print("="*80)

    # Initialize analyzer
    analyzer = ConversationAnalyzer(
        time_window_hours=args.hours,
        verbose=args.verbose
    )

    try:
        # Fetch recent sessions
        sessions = analyzer.fetch_recent_sessions()

        if len(sessions) == 0:
            print("⚠️  No conversations found in the specified time window.")
            return

        # Analyze each session
        print(f"🔍 Analyzing {len(sessions)} sessions for code issues...\n")
        all_detections = {}

        for session in sessions:
            session_id = session['session_id']
            detections = analyzer.analyze_session(session_id)
            all_detections[session_id] = detections

        # Correlate and prioritize
        print("\n📊 Correlating patterns to code locations...")
        issue_summary = analyzer.correlate_patterns_to_code(all_detections)

        # Generate reports
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        if args.format in ['markdown', 'both']:
            markdown_report = analyzer.generate_markdown_report(issue_summary, len(sessions))
            markdown_filename = f'conversation_analysis_{timestamp}.md'
            with open(markdown_filename, 'w') as f:
                f.write(markdown_report)
            print(f"\n✅ Markdown report saved to: {markdown_filename}")

        if args.format in ['json', 'both']:
            json_report = analyzer.generate_json_report(issue_summary, all_detections, len(sessions))
            json_filename = f'conversation_analysis_{timestamp}.json'
            with open(json_filename, 'w') as f:
                json.dump(json_report, f, indent=2, default=str)
            print(f"✅ JSON report saved to: {json_filename}")

        # Print console summary
        analyzer.print_console_summary(issue_summary)

        print("\n✅ Analysis complete!\n")

    except Exception as e:
        print(f"\n❌ Error during analysis: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
