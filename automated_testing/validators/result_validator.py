"""
Result Validator for Automated Testing

Validates test results against objective metrics:
1. Crash Prevention (40%)
2. Eligibility Logic Accuracy (30%)
3. Conversation Quality (20%)
4. Edge Case Handling (10%)
"""

import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import traceback


@dataclass
class ValidationResult:
    """Result of validating a single test"""
    passed: bool
    overall_score: float
    crash_prevention: Dict[str, Any]
    eligibility_accuracy: Dict[str, Any]
    conversation_quality: Dict[str, Any]
    edge_case_handling: Dict[str, Any]
    performance: Dict[str, Any]
    failure_points: List[Dict[str, Any]]
    recommendations: List[str]

    def to_dict(self):
        return {
            "passed": self.passed,
            "overall_score": self.overall_score,
            "crash_prevention": self.crash_prevention,
            "eligibility_accuracy": self.eligibility_accuracy,
            "conversation_quality": self.conversation_quality,
            "edge_case_handling": self.edge_case_handling,
            "performance": self.performance,
            "failure_points": self.failure_points,
            "recommendations": self.recommendations,
        }


class ResultValidator:
    """
    Validates test results against objective metrics
    """

    # Thresholds
    RESPONSE_TIME_PASS = 10.0  # seconds
    RESPONSE_TIME_WARN = 20.0
    MIN_OVERALL_SCORE = 85.0

    def __init__(self):
        pass

    def validate_test(
        self,
        conversation_result: Dict[str, Any],
        patient_profile: Dict[str, Any]
    ) -> ValidationResult:
        """
        Validate a single test conversation

        Args:
            conversation_result: Result from ConversationSimulator
            patient_profile: Original patient profile

        Returns:
            ValidationResult with scores and failure points
        """

        # Run all validation checks
        crash_result = self._validate_crash_prevention(conversation_result)
        eligibility_result = self._validate_eligibility_accuracy(conversation_result, patient_profile)
        quality_result = self._validate_conversation_quality(conversation_result, patient_profile)
        edge_case_result = self._validate_edge_case_handling(conversation_result, patient_profile)
        performance_result = self._validate_performance(conversation_result)

        # Calculate weighted overall score
        overall_score = (
            crash_result["score"] * 0.40 +
            eligibility_result["score"] * 0.30 +
            quality_result["score"] * 0.20 +
            edge_case_result["score"] * 0.10
        )

        # Determine pass/fail
        passed = (
            overall_score >= self.MIN_OVERALL_SCORE and
            crash_result["passed"] and
            eligibility_result["passed"]
        )

        # Collect failure points
        failure_points = []
        failure_points.extend(crash_result.get("failures", []))
        failure_points.extend(eligibility_result.get("failures", []))
        failure_points.extend(quality_result.get("failures", []))
        failure_points.extend(edge_case_result.get("failures", []))
        failure_points.extend(performance_result.get("failures", []))

        # Generate recommendations
        recommendations = self._generate_recommendations(
            crash_result, eligibility_result, quality_result,
            edge_case_result, performance_result
        )

        return ValidationResult(
            passed=passed,
            overall_score=overall_score,
            crash_prevention=crash_result,
            eligibility_accuracy=eligibility_result,
            conversation_quality=quality_result,
            edge_case_handling=edge_case_result,
            performance=performance_result,
            failure_points=failure_points,
            recommendations=recommendations,
        )

    def _validate_crash_prevention(self, conversation_result: Dict) -> Dict:
        """
        Metric 1: Crash Prevention (Critical)

        Pass: No HTTP 500 errors, no uncaught exceptions
        Fail: Any crash, database error, timeout
        """
        errors = conversation_result.get("errors", [])
        final_state = conversation_result.get("final_state", "unknown")

        # Check for critical errors
        critical_errors = [
            err for err in errors
            if err.get("type") in ["api_error", "timeout", "conversation_failure"]
        ]

        passed = len(critical_errors) == 0 and final_state != "failed"
        score = 100.0 if passed else 0.0

        failures = []
        if critical_errors:
            for error in critical_errors:
                failures.append({
                    "type": "crash",
                    "error_type": error.get("type"),
                    "error_message": error.get("error", "Unknown error"),
                    "likely_location": self._trace_error_to_code(error),
                    "severity": "critical",
                })

        return {
            "passed": passed,
            "score": score,
            "error_count": len(critical_errors),
            "errors": critical_errors,
            "failures": failures,
        }

    def _validate_eligibility_accuracy(
        self,
        conversation_result: Dict,
        patient_profile: Dict
    ) -> Dict:
        """
        Metric 2: Eligibility Logic Accuracy (High)

        Validates that eligibility determination makes sense
        given patient profile
        """

        conversation_log = conversation_result.get("conversation_log", [])
        final_state = conversation_result.get("final_state", "unknown")

        # Check if prescreening completed
        if final_state not in ["prescreening_complete", "contact_collected", "completed"]:
            return {
                "passed": False,
                "score": 0.0,
                "reason": "Prescreening did not complete",
                "failures": [{
                    "type": "eligibility_logic_error",
                    "reason": "Prescreening flow did not complete",
                    "final_state": final_state,
                    "likely_location": "core/conversation/gemini_conversation_manager.py or core/prescreening/gemini_prescreening_manager.py",
                    "severity": "high",
                }]
            }

        # Extract eligibility result from conversation
        eligibility_result = self._extract_eligibility_result(conversation_log)

        if not eligibility_result:
            return {
                "passed": False,
                "score": 0.0,
                "reason": "Could not extract eligibility result",
                "failures": [{
                    "type": "eligibility_logic_error",
                    "reason": "No eligibility determination found in conversation",
                    "likely_location": "core/prescreening/gemini_prescreening_manager.py:evaluate_eligibility()",
                    "severity": "high",
                }]
            }

        # Validate logic based on patient profile
        expected_result = self._determine_expected_eligibility(patient_profile)
        actual_result = eligibility_result["status"]

        # For random and targeted patients, we expect them to be eligible
        # For edge cases, depends on the specific case
        logic_makes_sense = self._eligibility_logic_check(
            expected_result, actual_result, patient_profile
        )

        score = 100.0 if logic_makes_sense else 50.0
        passed = logic_makes_sense

        failures = []
        if not logic_makes_sense:
            failures.append({
                "type": "eligibility_logic_error",
                "expected": expected_result,
                "actual": actual_result,
                "patient_age": patient_profile["demographics"]["age"],
                "patient_conditions": patient_profile["medical_history"].get("conditions", []),
                "patient_medications": patient_profile["medical_history"].get("medications", []),
                "likely_location": "core/prescreening/gemini_prescreening_manager.py:evaluate_eligibility() around line 340",
                "severity": "high",
                "recommendation": "Review eligibility evaluation logic for this patient profile"
            })

        return {
            "passed": passed,
            "score": score,
            "expected": expected_result,
            "actual": actual_result,
            "eligibility_result": eligibility_result,
            "failures": failures,
        }

    def _validate_conversation_quality(
        self,
        conversation_result: Dict,
        patient_profile: Dict
    ) -> Dict:
        """
        Metric 3: Conversation Quality (Medium)

        Checks:
        - All required questions asked
        - Questions in logical order
        - No repeated questions
        - Natural responses (no error messages visible to user)
        """

        conversation_log = conversation_result.get("conversation_log", [])
        total_questions = conversation_result.get("total_questions_asked", 0)

        issues = []
        quality_score = 100.0

        # Check 1: Minimum number of questions asked
        expected_min = patient_profile.get("expected_behavior", {}).get("expected_question_count_range", [4, 8])[0]
        if total_questions < expected_min:
            issues.append(f"Too few questions asked ({total_questions}, expected at least {expected_min})")
            quality_score -= 20

        # Check 2: No repeated questions
        questions_asked = []
        for turn in conversation_log:
            bot_response = turn.get("bot_response", "")
            if self._is_question(bot_response):
                question_normalized = self._normalize_question(bot_response)
                if question_normalized in questions_asked:
                    issues.append(f"Repeated question: {bot_response[:50]}...")
                    quality_score -= 15
                questions_asked.append(question_normalized)

        # Check 3: No error messages in bot responses
        for turn in conversation_log:
            bot_response = turn.get("bot_response", "")
            if self._contains_error_message(bot_response):
                issues.append(f"Error message visible to user: {bot_response[:100]}...")
                quality_score -= 25

        # Check 4: Responses are reasonably natural (not too short, not just "Error")
        for turn in conversation_log:
            bot_response = turn.get("bot_response", "")
            if len(bot_response) < 10 and bot_response.lower() not in ["yes", "no", "ok"]:
                issues.append(f"Unnaturally short response: '{bot_response}'")
                quality_score -= 10

        quality_score = max(0.0, quality_score)
        passed = quality_score >= 70.0

        failures = []
        if issues:
            for issue in issues:
                failures.append({
                    "type": "conversation_quality_issue",
                    "issue": issue,
                    "severity": "medium",
                    "likely_location": "core/conversation/gemini_conversation_manager.py or core/prescreening/gemini_prescreening_manager.py",
                })

        return {
            "passed": passed,
            "score": quality_score,
            "total_questions_asked": total_questions,
            "issues": issues,
            "failures": failures,
        }

    def _validate_edge_case_handling(
        self,
        conversation_result: Dict,
        patient_profile: Dict
    ) -> Dict:
        """
        Metric 4: Edge Case Handling (Medium)

        Only applies to edge case patients
        Checks if system handles edge cases gracefully
        """

        profile_type = patient_profile.get("profile_type", "random")

        # Only validate for edge case patients
        if profile_type != "edge_case":
            return {
                "passed": True,
                "score": 100.0,
                "applicable": False,
                "failures": [],
            }

        edge_case_type = patient_profile.get("expected_behavior", {}).get("edge_case_type", "unknown")
        conversation_log = conversation_result.get("conversation_log", [])
        errors = conversation_result.get("errors", [])
        final_state = conversation_result.get("final_state", "unknown")

        # Did it complete without crashing?
        completed_successfully = final_state in ["prescreening_complete", "contact_collected", "completed"]

        # For edge cases, we primarily care that it doesn't crash
        score = 100.0 if completed_successfully else 50.0

        # Check for specific edge case handling
        if edge_case_type == "unclear_responses":
            # Should ask for clarification
            asked_for_clarification = any(
                "clarif" in turn.get("bot_response", "").lower() or
                "not sure" in turn.get("bot_response", "").lower()
                for turn in conversation_log
            )
            if not asked_for_clarification:
                score -= 20

        failures = []
        if not completed_successfully:
            failures.append({
                "type": "edge_case_handling_failure",
                "edge_case_type": edge_case_type,
                "final_state": final_state,
                "reason": "Edge case caused conversation to fail",
                "severity": "medium",
                "likely_location": "core/conversation/gemini_conversation_manager.py or core/prescreening/gemini_prescreening_manager.py",
            })

        passed = score >= 70.0

        return {
            "passed": passed,
            "score": score,
            "applicable": True,
            "edge_case_type": edge_case_type,
            "completed": completed_successfully,
            "failures": failures,
        }

    def _validate_performance(self, conversation_result: Dict) -> Dict:
        """
        Metric 5: Performance (Low Priority)

        Checks response times
        """

        conversation_log = conversation_result.get("conversation_log", [])

        if not conversation_log:
            return {
                "passed": True,
                "score": 100.0,
                "failures": [],
            }

        response_times = [turn.get("response_time", 0) for turn in conversation_log]
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        max_response_time = max(response_times) if response_times else 0

        # Scoring
        if avg_response_time < self.RESPONSE_TIME_PASS:
            score = 100.0
            status = "excellent"
        elif avg_response_time < self.RESPONSE_TIME_WARN:
            score = 70.0
            status = "acceptable"
        else:
            score = 40.0
            status = "slow"

        passed = avg_response_time < self.RESPONSE_TIME_WARN

        failures = []
        if not passed:
            failures.append({
                "type": "performance_issue",
                "avg_response_time": avg_response_time,
                "max_response_time": max_response_time,
                "severity": "low",
                "recommendation": "Investigate slow API responses or Gemini API delays",
            })

        return {
            "passed": passed,
            "score": score,
            "avg_response_time": avg_response_time,
            "max_response_time": max_response_time,
            "status": status,
            "failures": failures,
        }

    # Helper methods

    def _trace_error_to_code(self, error: Dict) -> str:
        """Attempt to identify likely code location from error"""
        error_type = error.get("type", "")
        error_msg = error.get("error", "")

        # Common error patterns
        if "timeout" in error_type.lower():
            return "aiohttp timeout in conversation_simulator.py or slow Gemini API response"

        if "database" in error_msg.lower() or "psycopg2" in error_msg.lower():
            return "core/database.py or core/conversation/context/storage.py"

        if "prescreening" in error_msg.lower():
            return "core/prescreening/gemini_prescreening_manager.py"

        if "context" in error_msg.lower():
            return "core/conversation/context/manager.py"

        if "gemini" in error_msg.lower() or "api" in error_msg.lower():
            return "core/services/gemini_service.py"

        return "Unknown - check full error trace"

    def _extract_eligibility_result(self, conversation_log: List[Dict]) -> Optional[Dict]:
        """Extract eligibility determination from conversation log"""

        # Look for eligibility result in last few turns
        for turn in reversed(conversation_log[-5:]):
            bot_response = turn.get("bot_response", "")

            if "likely eligible" in bot_response.lower():
                return {"status": "likely_eligible", "text": bot_response}
            elif "likely ineligible" in bot_response.lower():
                return {"status": "likely_ineligible", "text": bot_response}
            elif "potentially eligible" in bot_response.lower():
                return {"status": "potentially_eligible", "text": bot_response}

        return None

    def _determine_expected_eligibility(self, patient_profile: Dict) -> str:
        """Determine expected eligibility based on patient profile"""
        expected_behavior = patient_profile.get("expected_behavior", {})
        likely_eligible = expected_behavior.get("likely_eligible", True)

        if likely_eligible is True:
            return "likely_eligible"
        elif likely_eligible is False:
            return "likely_ineligible"
        else:
            return "unclear"

    def _eligibility_logic_check(
        self,
        expected: str,
        actual: str,
        patient_profile: Dict
    ) -> bool:
        """Check if eligibility logic makes sense"""

        # If expected is unclear, any result is acceptable
        if expected == "unclear":
            return True

        # If expected matches actual, perfect
        if expected == actual:
            return True

        # Potentially eligible is a middle ground - acceptable for most cases
        if actual == "potentially_eligible":
            return True

        # Otherwise, logic might be wrong
        return False

    def _is_question(self, text: str) -> bool:
        """Check if text is a question"""
        return "?" in text or re.search(r"\*\*Question \d+", text) is not None

    def _normalize_question(self, question: str) -> str:
        """Normalize question for comparison"""
        # Remove question numbers
        normalized = re.sub(r"\*\*Question \d+ of \d+:\*\*", "", question)
        # Lowercase and strip
        normalized = normalized.lower().strip()
        return normalized

    def _contains_error_message(self, text: str) -> bool:
        """Check if text contains error messages"""
        error_markers = [
            "error:",
            "exception:",
            "traceback",
            "none returned",
            "failed to",
            "could not",
            "internal server error",
        ]
        text_lower = text.lower()
        return any(marker in text_lower for marker in error_markers)

    def _generate_recommendations(
        self,
        crash_result: Dict,
        eligibility_result: Dict,
        quality_result: Dict,
        edge_case_result: Dict,
        performance_result: Dict
    ) -> List[str]:
        """Generate actionable recommendations based on failures"""

        recommendations = []

        # Crash prevention
        if not crash_result["passed"]:
            recommendations.append(
                "🚨 CRITICAL: Fix crashes and exceptions. Review error logs and add error handling."
            )

        # Eligibility accuracy
        if not eligibility_result["passed"]:
            recommendations.append(
                "⚠️ HIGH: Review eligibility evaluation logic in gemini_prescreening_manager.py"
            )

        # Conversation quality
        if not quality_result["passed"]:
            if quality_result["score"] < 50:
                recommendations.append(
                    "⚠️ MEDIUM: Major conversation quality issues. Review question generation and response handling."
                )
            else:
                recommendations.append(
                    "ℹ️ LOW: Minor conversation quality issues. Consider refining prompts and validation."
                )

        # Edge cases
        if edge_case_result.get("applicable") and not edge_case_result["passed"]:
            recommendations.append(
                "⚠️ MEDIUM: Improve edge case handling. Add input validation and clarification prompts."
            )

        # Performance
        if not performance_result["passed"]:
            recommendations.append(
                "ℹ️ LOW: Slow response times. Optimize API calls or consider caching."
            )

        if not recommendations:
            recommendations.append("✅ All metrics passed! No issues detected.")

        return recommendations


if __name__ == "__main__":
    # Test the validator
    print("✅ Result Validator module created successfully")
    print("This module validates test results against objective metrics")
