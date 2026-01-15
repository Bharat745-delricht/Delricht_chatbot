import os
import json
import asyncio
import hashlib
import time
import logging
import aiohttp
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class GeminiService:
    def __init__(self):
        # Direct REST API configuration
        self.api_key = os.getenv('GEMINI_API_KEY')
        self.base_url = "https://generativelanguage.googleapis.com/v1"
        
        # Safety settings for healthcare context
        self.safety_settings = [
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ]
        
        # Performance optimizations
        self._cache = {}  # Simple in-memory cache
        self._cache_ttl = 300  # 5 minutes cache TTL
        self._request_timeout = 15  # 15 second timeout for chat
        self._protocol_timeout = 120  # 2 minute timeout for protocol processing
        self._max_retries = 2

    def _get_cache_key(self, prompt: str, max_tokens: int) -> str:
        """Generate cache key for prompt"""
        content = f"{prompt}:{max_tokens}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def _is_cache_valid(self, timestamp: float) -> bool:
        """Check if cache entry is still valid"""
        return time.time() - timestamp < self._cache_ttl
    
    async def generate_text(self, prompt: str, max_tokens: int = 1000) -> str:
        """Generate text using Gemini 1.5 Pro via direct REST API with caching and timeout"""
        # Check cache first
        cache_key = self._get_cache_key(prompt, max_tokens)
        if cache_key in self._cache:
            cached_data, timestamp = self._cache[cache_key]
            if self._is_cache_valid(timestamp):
                return cached_data
            else:
                # Remove expired cache entry
                del self._cache[cache_key]
        
        # Prepare request payload
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.1,  # Low temperature for medical context
            },
            "safetySettings": self.safety_settings
        }
        
        # Attempt generation with retries and timeout
        for attempt in range(self._max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.base_url}/models/gemini-2.5-pro:generateContent?key={self.api_key}"
                    
                    async with session.post(
                        url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=self._request_timeout),
                        headers={"Content-Type": "application/json"}
                    ) as response:
                        
                        if response.status == 200:
                            data = await response.json()
                            
                            # Extract text from response
                            if "candidates" in data and len(data["candidates"]) > 0:
                                candidate = data["candidates"][0]
                                if "content" in candidate and "parts" in candidate["content"]:
                                    result = candidate["content"]["parts"][0]["text"]
                                    
                                    # Cache successful response
                                    self._cache[cache_key] = (result, time.time())
                                    
                                    # Limit cache size to prevent memory issues
                                    if len(self._cache) > 100:
                                        # Remove oldest entries
                                        oldest_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k][1])[:20]
                                        for key in oldest_keys:
                                            del self._cache[key]
                                    
                                    return result
                            
                            # If no valid content found
                            print(f"No valid content in Gemini response: {data}")
                            
                        else:
                            error_text = await response.text()
                            print(f"Gemini API error {response.status}: {error_text}")
                
            except asyncio.TimeoutError:
                print(f"Gemini API timeout (attempt {attempt + 1}/{self._max_retries + 1})")
                if attempt == self._max_retries:
                    return "I apologize, but my response is taking longer than expected. Please try a shorter message or try again in a moment."
                    
            except Exception as e:
                print(f"Gemini text generation error (attempt {attempt + 1}/{self._max_retries + 1}): {e}")
                if attempt == self._max_retries:
                    return "I apologize, but I'm having trouble processing your request right now. Please try again in a moment."
                
                # Wait before retry
                await asyncio.sleep(1)
        
        return "I apologize, but I'm having trouble processing your request right now."
    
    async def generate_protocol_text(self, prompt: str, max_tokens: int = 8000) -> str:
        """Generate text specifically for protocol processing with extended timeout and no caching"""
        # Use extended timeout and more retries for protocol processing
        max_retries = 3
        timeout = self._protocol_timeout
        
        for attempt in range(max_retries + 1):
            try:
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": max_tokens,
                        "temperature": 0.05,  # Very low temperature for consistency
                    },
                    "safetySettings": self.safety_settings
                }
                
                async with aiohttp.ClientSession() as session:
                    url = f"{self.base_url}/models/gemini-2.5-pro:generateContent?key={self.api_key}"
                    
                    async with session.post(
                        url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                        headers={"Content-Type": "application/json"}
                    ) as response:
                        
                        if response.status == 200:
                            data = await response.json()
                            
                            # Extract text from response
                            if "candidates" in data and len(data["candidates"]) > 0:
                                candidate = data["candidates"][0]
                                if "content" in candidate and "parts" in candidate["content"]:
                                    result = candidate["content"]["parts"][0]["text"]
                                    return result
                            
                            # If no valid content found
                            print(f"No valid content in Gemini response: {data}")
                            
                        elif response.status == 429:
                            # Rate limit - wait longer before retry
                            wait_time = (2 ** attempt) * 10  # 10s, 20s, 40s, 80s
                            print(f"Rate limit hit, waiting {wait_time}s before retry...")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            error_text = await response.text()
                            print(f"Gemini API error {response.status}: {error_text}")
                
            except asyncio.TimeoutError:
                print(f"Protocol processing timeout ({timeout}s) - attempt {attempt + 1}/{max_retries + 1}")
                if attempt < max_retries:
                    # Increase timeout for next attempt
                    timeout = min(timeout + 30, 180)  # Max 3 minutes
                    await asyncio.sleep(5)
                    continue
                else:
                    return None  # Return None to indicate failure
                    
            except Exception as e:
                print(f"Protocol processing error (attempt {attempt + 1}/{max_retries + 1}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(5)
                    continue
                else:
                    return None  # Return None to indicate failure
        
        return None  # All attempts failed
    
    async def _generate_text_with_timeout(self, prompt: str, max_tokens: int = 1000, timeout_seconds: int = 15) -> str:
        """Generate text with configurable timeout for specialized tasks like criteria extraction"""
        for attempt in range(self._max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "contents": [{
                            "parts": [{"text": prompt}]
                        }],
                        "generationConfig": {
                            "maxOutputTokens": max_tokens,
                            "temperature": 0.1,
                            "topP": 0.8,
                            "topK": 40
                        },
                        "safetySettings": self.safety_settings
                    }
                    
                    url = f"{self.base_url}/models/gemini-2.5-pro:generateContent?key={self.api_key}"
                    
                    async with session.post(
                        url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                        headers={"Content-Type": "application/json"}
                    ) as response:
                        
                        if response.status == 200:
                            data = await response.json()
                            if "candidates" in data and len(data["candidates"]) > 0:
                                content = data["candidates"][0]["content"]["parts"][0]["text"]
                                return content.strip()
                        else:
                            error_text = await response.text()
                            print(f"Gemini API error {response.status}: {error_text}")
                
            except asyncio.TimeoutError:
                print(f"Gemini API timeout (attempt {attempt + 1}/{self._max_retries + 1}) - {timeout_seconds}s timeout")
                if attempt == self._max_retries:
                    return "I apologize, but my response is taking longer than expected. Please try a shorter message or try again in a moment."
                    
            except Exception as e:
                print(f"Gemini text generation error (attempt {attempt + 1}/{self._max_retries + 1}): {e}")
                if attempt == self._max_retries:
                    return "I apologize, but I'm having trouble processing your request right now."
                
                # Wait before retry
                await asyncio.sleep(1)
        
        return "I apologize, but I'm having trouble processing your request right now."

    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using Gemini text-embedding-004 via direct REST API"""
        try:
            embeddings = []
            # Create SSL context that doesn't verify certificates for development
            # In production, SSL verification should be enabled
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                for text in texts:
                    url = f"{self.base_url}/models/text-embedding-004:embedContent?key={self.api_key}"
                    payload = {
                        "model": "models/text-embedding-004",
                        "content": {"parts": [{"text": text}]},
                        "taskType": "RETRIEVAL_DOCUMENT"
                    }

                    async with session.post(
                        url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=30),
                        headers={"Content-Type": "application/json"}
                    ) as response:
                        
                        if response.status == 200:
                            data = await response.json()
                            if "embedding" in data and "values" in data["embedding"]:
                                embeddings.append(data["embedding"]["values"])
                            else:
                                embeddings.append([0.0] * 768)
                        else:
                            embeddings.append([0.0] * 768)
                            
            return embeddings
        except Exception as e:
            print(f"Gemini embedding error: {e}")
            # Return empty list to trigger proper keyword search fallback
            # DO NOT return zero vectors as they match with everything!
            return []

    async def extract_json(self, prompt: str, text: str, timeout_seconds: int = 15) -> Dict:
        """Extract structured JSON from text using Gemini with configurable timeout"""
        full_prompt = f"""
        {prompt}
        
        Text to analyze:
        {text}
        
        Return only valid JSON without any markdown formatting or additional text.
        """
        
        try:
            # Use direct API call with configurable timeout for large document processing
            response = await self._generate_text_with_timeout(full_prompt, max_tokens=2000, timeout_seconds=timeout_seconds)
            
            # Handle timeout/error responses gracefully
            if "I apologize" in response and "trouble processing" in response:
                return {
                    "error": "timeout",
                    "intent": "general_inquiry",
                    "confidence": 0.1,
                    "entities": {},
                    "next_action": "general_response",
                    "reasoning": "Gemini API timeout"
                }

            # Handle empty responses
            if not response or response.strip() == "":
                return {
                    "error": "empty_response",
                    "intent": "general_inquiry",
                    "confidence": 0.1,
                    "entities": {},
                    "next_action": "general_response",
                    "reasoning": "Empty response from Gemini"
                }
            
            # Clean response and parse JSON
            cleaned_response = response.strip()
            if cleaned_response.startswith('```json'):
                cleaned_response = cleaned_response[7:]
            if cleaned_response.endswith('```'):
                cleaned_response = cleaned_response[:-3]
            
            cleaned_response = cleaned_response.strip()
            
            # Final check for empty content after cleaning
            if not cleaned_response:
                return {
                    "error": "empty_after_cleaning",
                    "intent": "general_inquiry",
                    "confidence": 0.1,
                    "entities": {},
                    "next_action": "general_response",
                    "reasoning": "Empty content after cleaning markdown"
                }
            
            # Try parsing JSON with robust error handling
            parsed_json = self._parse_json_robustly(cleaned_response)
            return parsed_json
            
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parsing error for Gemini response: {e}. Response length: {len(response)}")
            logger.debug(f"Raw response preview: {response[:200]}...")
            # Try to repair the JSON
            repaired_json = self._repair_json(cleaned_response)
            if repaired_json:
                logger.info("Successfully repaired JSON response")
                return repaired_json
            # Return a minimal valid response for fallback
            logger.info("Using fallback response due to JSON parsing failure")
            return {
                "intent": "general_inquiry",
                "confidence": 0.5,
                "entities": {},
                "next_action": "general_response",
                "reasoning": "JSON parsing failed, could not repair"
            }
        except Exception as e:
            logger.error(f"Unexpected error in JSON extraction: {e}")
            # Return a minimal valid response for fallback
            return {
                "intent": "general_inquiry",
                "confidence": 0.5,
                "entities": {},
                "next_action": "general_response",
                "reasoning": f"Unexpected error: {str(e)}"
            }
    
    def _parse_json_robustly(self, json_string: str) -> Dict:
        """Parse JSON with multiple strategies for robustness"""
        try:
            # First attempt: direct parsing
            return json.loads(json_string)
        except json.JSONDecodeError:
            # Second attempt: try to find and extract valid JSON
            return self._extract_valid_json(json_string)
    
    def _extract_valid_json(self, text: str) -> Dict:
        """Extract valid JSON from potentially malformed text"""
        import re
        
        # Look for JSON object patterns
        json_patterns = [
            r'\{[^{}]*"inclusion_criteria"[^{}]*"exclusion_criteria"[^{}]*\}',
            r'\{.*?"inclusion_criteria".*?"exclusion_criteria".*?\}',
            r'\{.*?\}',  # Any JSON object
        ]
        
        for pattern in json_patterns:
            matches = re.findall(pattern, text, re.DOTALL)
            for match in matches:
                try:
                    # Try to parse each potential JSON match
                    return json.loads(match)
                except json.JSONDecodeError:
                    continue
        
        # If no valid JSON found, return structured fallback
        return {"inclusion_criteria": [], "exclusion_criteria": [], "error": "json_extraction_failed"}
    
    def _repair_json(self, broken_json: str) -> Optional[Dict]:
        """Attempt to repair common JSON issues"""
        try:
            # Common repairs for Gemini-generated JSON
            repaired = broken_json
            
            # Fix unterminated strings at end
            if repaired.count('"') % 2 == 1:  # Odd number of quotes
                # Find the last quote and see if it needs termination
                last_quote_pos = repaired.rfind('"')
                if last_quote_pos > 0:
                    # Look for patterns that suggest unterminated strings
                    after_quote = repaired[last_quote_pos + 1:].strip()
                    if after_quote.endswith(']') or after_quote.endswith('}'):
                        # Add missing quote before the closing bracket
                        repaired = repaired[:last_quote_pos + 1] + '"' + after_quote
            
            # Fix missing closing brackets
            open_braces = repaired.count('{')
            close_braces = repaired.count('}')
            if open_braces > close_braces:
                repaired += '}' * (open_braces - close_braces)
            
            open_brackets = repaired.count('[')
            close_brackets = repaired.count(']')
            if open_brackets > close_brackets:
                repaired += ']' * (open_brackets - close_brackets)
            
            # Fix trailing commas
            import re
            repaired = re.sub(r',(\s*[}\]])', r'\1', repaired)
            
            # Try parsing the repaired JSON
            return json.loads(repaired)
            
        except Exception as e:
            logger.warning(f"JSON repair failed: {e}")
            # Return structured fallback for criteria extraction
            return {
                "inclusion_criteria": [],
                "exclusion_criteria": [],
                "error": "json_repair_failed",
                "original_error": str(e)
            }
    
    def get_cache_stats(self) -> Dict:
        """Get cache performance statistics"""
        return {
            "cache_size": len(self._cache),
            "cache_ttl": self._cache_ttl,
            "request_timeout": self._request_timeout,
            "max_retries": self._max_retries
        }
    
    def clear_cache(self) -> None:
        """Clear the response cache"""
        self._cache.clear()

# Global instance
gemini_service = GeminiService()