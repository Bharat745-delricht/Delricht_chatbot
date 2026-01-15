"""
Lightweight Gemini parsing endpoint for simple JSON extraction tasks.

This endpoint bypasses the full conversation system for simple parsing tasks
like availability title parsing. It provides fast, direct access to Gemini
without database lookups, context management, or conversation state.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
import json
import re

from core.services.gemini_service import GeminiService

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize Gemini service
gemini_service = GeminiService()


class ParseRequest(BaseModel):
    """Simple parse request model"""
    prompt: str
    timeout: Optional[int] = 10  # Default 10 second timeout
    temperature: Optional[float] = 0.1  # Low temperature for consistent parsing


class ParseResponse(BaseModel):
    """Simple parse response model"""
    result: Dict[str, Any]
    raw_response: Optional[str] = None


@router.post("/parse", response_model=ParseResponse)
async def gemini_parse_endpoint(request: ParseRequest):
    """
    Lightweight Gemini parsing endpoint.

    This endpoint provides direct access to Gemini for simple parsing tasks
    without going through the full conversation system. Ideal for:
    - JSON extraction from text
    - Calendar event parsing
    - Simple classification tasks

    The response is expected to be valid JSON.
    """

    logger.info("🔍 GEMINI PARSE - NEW REQUEST")
    logger.info(f"Prompt length: {len(request.prompt)} chars")
    logger.info(f"Timeout: {request.timeout}s")

    try:
        # Call Gemini directly through the service
        response = await gemini_service.generate_text(
            prompt=request.prompt,
            max_tokens=500  # Simple parsing shouldn't need many tokens
        )

        if not response:
            raise HTTPException(
                status_code=500,
                detail="Gemini service returned empty response"
            )

        # Extract the text response
        response_text = response.strip()
        logger.info(f"Raw Gemini response: {response_text[:200]}...")

        # Try to parse JSON from the response
        parsed_result = None

        # Method 1: Try direct JSON parse
        try:
            parsed_result = json.loads(response_text)
        except json.JSONDecodeError:
            # Method 2: Try to extract JSON from markdown code blocks
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', response_text)
            if json_match:
                try:
                    parsed_result = json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    pass

            # Method 3: Try to find any JSON object in the text
            if not parsed_result:
                json_match = re.search(r'\{[\s\S]*\}', response_text)
                if json_match:
                    try:
                        parsed_result = json.loads(json_match.group(0))
                    except json.JSONDecodeError:
                        pass

        if not parsed_result:
            logger.error(f"Failed to extract JSON from response: {response_text}")
            raise HTTPException(
                status_code=500,
                detail="Could not parse JSON from Gemini response"
            )

        logger.info(f"✅ Successfully parsed JSON: {parsed_result}")

        return ParseResponse(
            result=parsed_result,
            raw_response=response_text
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Gemini parsing failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Gemini parsing error: {str(e)}"
        )


@router.get("/health")
async def parse_health_check():
    """Health check for the parse endpoint"""
    try:
        # Quick check if Gemini service is initialized
        is_healthy = gemini_service is not None
        return {
            "status": "healthy" if is_healthy else "unhealthy",
            "service": "Gemini Parse Service",
            "message": "Lightweight parsing endpoint operational"
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return {
            "status": "unhealthy",
            "service": "Gemini Parse Service",
            "error": str(e)
        }
