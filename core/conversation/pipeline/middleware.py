"""
Middleware components for the conversation pipeline.

This module provides middleware that can be inserted into the processing
pipeline for cross-cutting concerns like logging, validation, and monitoring.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, Callable, Optional, List, Tuple
from datetime import datetime
from functools import wraps

logger = logging.getLogger(__name__)


class Middleware(ABC):
    """Abstract base class for pipeline middleware"""
    
    @abstractmethod
    def process(self, data: Dict[str, Any], next_handler: Callable) -> Dict[str, Any]:
        """
        Process data and call next handler in chain.
        
        Args:
            data: Data being processed
            next_handler: Next middleware or final handler
            
        Returns:
            Processed data
        """
        pass


class LoggingMiddleware(Middleware):
    """Logs pipeline processing steps"""
    
    def __init__(self, log_level: int = logging.INFO):
        self.log_level = log_level
        
    def process(self, data: Dict[str, Any], next_handler: Callable) -> Dict[str, Any]:
        """Log before and after processing"""
        # Log input
        logger.log(
            self.log_level,
            "Processing message",
            extra={
                "session_id": data.get("session_id"),
                "message_preview": data.get("message", "")[:50],
                "current_state": data.get("context", {}).get("conversation_state")
            }
        )
        
        # Process
        start_time = time.time()
        result = next_handler(data)
        processing_time = (time.time() - start_time) * 1000
        
        # Log output
        logger.log(
            self.log_level,
            "Message processed",
            extra={
                "session_id": data.get("session_id"),
                "success": result.get("success"),
                "intent_type": result.get("intent", {}).get("type"),
                "processing_time_ms": processing_time,
                "handler_used": result.get("metadata", {}).get("handler_name")
            }
        )
        
        return result


class ValidationMiddleware(Middleware):
    """Validates input data before processing"""
    
    def process(self, data: Dict[str, Any], next_handler: Callable) -> Dict[str, Any]:
        """Validate input data"""
        errors = []
        
        # Required fields
        if not data.get("message"):
            errors.append("Message is required")
            
        if not data.get("session_id"):
            errors.append("Session ID is required")
            
        # Message length
        message = data.get("message", "")
        if len(message) > 1000:
            errors.append("Message too long (max 1000 characters)")
            
        # If validation fails, return error
        if errors:
            return {
                "success": False,
                "error": "Validation failed",
                "validation_errors": errors,
                "response": "I couldn't process your message. Please check your input and try again."
            }
        
        # Continue processing
        return next_handler(data)


class RateLimitingMiddleware(Middleware):
    """Implements rate limiting per session"""
    
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.request_counts: Dict[str, List[datetime]] = {}
        
    def process(self, data: Dict[str, Any], next_handler: Callable) -> Dict[str, Any]:
        """Check rate limit before processing"""
        session_id = data.get("session_id", "unknown")
        now = datetime.now()
        
        # Clean old requests
        if session_id in self.request_counts:
            cutoff_time = now.timestamp() - self.window_seconds
            self.request_counts[session_id] = [
                ts for ts in self.request_counts[session_id]
                if ts.timestamp() > cutoff_time
            ]
        else:
            self.request_counts[session_id] = []
        
        # Check limit
        if len(self.request_counts[session_id]) >= self.max_requests:
            return {
                "success": False,
                "error": "Rate limit exceeded",
                "response": "You're sending messages too quickly. Please wait a moment and try again.",
                "retry_after": self.window_seconds
            }
        
        # Record request
        self.request_counts[session_id].append(now)
        
        # Continue processing
        return next_handler(data)


class MetricsMiddleware(Middleware):
    """Collects metrics about pipeline processing"""
    
    def __init__(self):
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "intent_counts": {},
            "avg_processing_time": 0,
            "error_types": {}
        }
        
    def process(self, data: Dict[str, Any], next_handler: Callable) -> Dict[str, Any]:
        """Collect metrics during processing"""
        self.metrics["total_requests"] += 1
        
        # Process
        start_time = time.time()
        result = next_handler(data)
        processing_time = (time.time() - start_time) * 1000
        
        # Update metrics
        if result.get("success"):
            self.metrics["successful_requests"] += 1
            
            # Track intent types
            intent_type = result.get("intent", {}).get("type", "unknown")
            self.metrics["intent_counts"][intent_type] = \
                self.metrics["intent_counts"].get(intent_type, 0) + 1
        else:
            self.metrics["failed_requests"] += 1
            
            # Track error types
            error_type = result.get("error", "unknown")
            self.metrics["error_types"][error_type] = \
                self.metrics["error_types"].get(error_type, 0) + 1
        
        # Update average processing time
        current_avg = self.metrics["avg_processing_time"]
        total = self.metrics["total_requests"]
        self.metrics["avg_processing_time"] = (
            (current_avg * (total - 1) + processing_time) / total
        )
        
        return result
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get collected metrics"""
        return self.metrics.copy()
    
    def reset_metrics(self):
        """Reset metrics"""
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "intent_counts": {},
            "avg_processing_time": 0,
            "error_types": {}
        }


class ErrorHandlingMiddleware(Middleware):
    """Handles errors gracefully in the pipeline"""
    
    def __init__(self, fallback_handler: Optional[Callable] = None):
        self.fallback_handler = fallback_handler
        
    def process(self, data: Dict[str, Any], next_handler: Callable) -> Dict[str, Any]:
        """Handle errors during processing"""
        try:
            return next_handler(data)
        except Exception as e:
            logger.error(
                f"Pipeline error: {str(e)}",
                extra={
                    "session_id": data.get("session_id"),
                    "error_type": type(e).__name__
                },
                exc_info=True
            )
            
            # Try fallback handler
            if self.fallback_handler:
                try:
                    return self.fallback_handler(data, e)
                except Exception as fallback_error:
                    logger.error(f"Fallback handler failed: {str(fallback_error)}")
            
            # Return error response
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
                "response": "I encountered an error processing your message. Please try again.",
                "metadata": {
                    "session_id": data.get("session_id"),
                    "timestamp": datetime.now().isoformat()
                }
            }


class CachingMiddleware(Middleware):
    """Caches responses for repeated queries"""
    
    def __init__(self, cache_ttl_seconds: int = 300):
        self.cache_ttl = cache_ttl_seconds
        self.cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
        
    def _get_cache_key(self, data: Dict[str, Any]) -> str:
        """Generate cache key from request data"""
        # Include message and key context fields
        message = data.get("message", "").lower().strip()
        context = data.get("context", {})
        
        key_parts = [
            message,
            context.get("focus_condition", ""),
            context.get("focus_location", ""),
            context.get("conversation_state", "")
        ]
        
        return "|".join(key_parts)
    
    def process(self, data: Dict[str, Any], next_handler: Callable) -> Dict[str, Any]:
        """Check cache before processing"""
        cache_key = self._get_cache_key(data)
        now = time.time()
        
        # Check cache
        if cache_key in self.cache:
            cached_result, cache_time = self.cache[cache_key]
            if now - cache_time < self.cache_ttl:
                logger.debug(f"Cache hit for key: {cache_key[:50]}...")
                cached_result["metadata"]["from_cache"] = True
                return cached_result
        
        # Process and cache result
        result = next_handler(data)
        
        # Only cache successful responses
        if result.get("success"):
            self.cache[cache_key] = (result.copy(), now)
        
        # Clean old cache entries periodically
        if len(self.cache) > 1000:
            self._clean_cache(now)
        
        return result
    
    def _clean_cache(self, current_time: float):
        """Remove expired cache entries"""
        expired_keys = []
        for key, (_, cache_time) in self.cache.items():
            if current_time - cache_time > self.cache_ttl:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.cache[key]


class MiddlewarePipeline:
    """Manages a pipeline of middleware"""
    
    def __init__(self):
        self.middleware: List[Middleware] = []
        
    def add(self, middleware: Middleware) -> 'MiddlewarePipeline':
        """Add middleware to pipeline"""
        self.middleware.append(middleware)
        return self
    
    def build(self, final_handler: Callable) -> Callable:
        """Build the middleware chain"""
        def create_handler(middleware: Middleware, next_handler: Callable) -> Callable:
            return lambda data: middleware.process(data, next_handler)
        
        # Build chain in reverse order
        handler = final_handler
        for mw in reversed(self.middleware):
            handler = create_handler(mw, handler)
        
        return handler
    
    def clear(self):
        """Clear all middleware"""
        self.middleware.clear()