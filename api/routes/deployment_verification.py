"""
Deployment Verification Endpoint
Confirms what code version is actually deployed
"""

from fastapi import APIRouter
import hashlib
import os
from datetime import datetime

router = APIRouter(prefix="/api/debug", tags=["Debug"])


@router.get("/code-version")
async def check_code_version():
    """
    Check if specific code changes are deployed
    Returns hash of critical functions to verify deployment
    """

    # Read the actual deployed code
    try:
        manager_path = "/app/core/conversation/gemini_conversation_manager.py"

        with open(manager_path, 'r') as f:
            content = f.read()

        # Check for specific markers we added
        has_eligibility_stats_log = "📊 Eligibility Stats:" in content
        has_availability_check = "CHECK FOR AVAILABILITY" in content
        has_60_percent_threshold = "inclusion_percentage >= 60" in content
        has_crio_availability_import = "from core.services.crio_availability_service import CRIOAvailabilityService" in content

        # Count occurrences
        eligibility_stats_count = content.count("📊 Eligibility Stats:")
        availability_check_locations = content.count("CHECK FOR AVAILABILITY")

        # Get line numbers for availability checks
        lines = content.split('\n')
        availability_line_numbers = [
            i + 1 for i, line in enumerate(lines)
            if "CHECK FOR AVAILABILITY" in line or "📊 Eligibility Stats:" in line
        ]

        # File hash
        file_hash = hashlib.md5(content.encode()).hexdigest()

        return {
            "deployment_verified": True,
            "file_path": manager_path,
            "file_size_bytes": len(content),
            "file_hash": file_hash,
            "code_features": {
                "has_eligibility_stats_logging": has_eligibility_stats_log,
                "has_availability_check_comment": has_availability_check,
                "has_60_percent_threshold": has_60_percent_threshold,
                "has_crio_service_import": has_crio_availability_import,
                "eligibility_stats_count": eligibility_stats_count,
                "availability_check_count": availability_check_locations,
                "availability_line_numbers": availability_line_numbers
            },
            "expected_features": {
                "eligibility_stats_count": "Should be 2 (one in post-prescreening, one in completion)",
                "availability_check_count": "Should be 2 (one in post-prescreening, one in completion)",
                "has_60_percent_threshold": "Should be True"
            },
            "deployment_time": os.getenv("DEPLOYMENT_TIME", "unknown"),
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        return {
            "deployment_verified": False,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


@router.get("/availability-feature-status")
async def check_availability_feature():
    """
    Specific check for availability feature deployment
    """

    try:
        manager_path = "/app/core/conversation/gemini_conversation_manager.py"

        with open(manager_path, 'r') as f:
            lines = f.readlines()

        # Find the prescreening completion method
        completion_method_start = None
        completion_method_end = None

        for i, line in enumerate(lines):
            if "async def _complete_prescreening_evaluation" in line:
                completion_method_start = i + 1
            if completion_method_start and i > completion_method_start and line.strip().startswith("async def "):
                completion_method_end = i
                break

        if completion_method_start:
            method_lines = lines[completion_method_start:completion_method_end] if completion_method_end else lines[completion_method_start:]
            method_code = ''.join(method_lines)

            # Check for availability features in this specific method
            has_availability_in_completion = "CRIOAvailabilityService" in method_code
            has_threshold_in_completion = "inclusion_percentage >= 60" in method_code
            has_slots_display = "I can see availability at" in method_code

            return {
                "feature_deployed": has_availability_in_completion and has_threshold_in_completion,
                "completion_method_found": True,
                "completion_method_line": completion_method_start,
                "checks": {
                    "imports_crio_service": has_availability_in_completion,
                    "checks_60_percent_threshold": has_threshold_in_completion,
                    "formats_availability_display": has_slots_display
                },
                "verdict": "✅ Availability feature is deployed" if (has_availability_in_completion and has_threshold_in_completion) else "❌ Availability feature NOT deployed"
            }
        else:
            return {
                "feature_deployed": False,
                "completion_method_found": False,
                "error": "Could not find _complete_prescreening_evaluation method"
            }

    except Exception as e:
        return {
            "feature_deployed": False,
            "error": str(e)
        }
