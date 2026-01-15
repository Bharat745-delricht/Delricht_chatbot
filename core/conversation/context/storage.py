"""
Context storage and persistence layer.

This module handles the storage, retrieval, and management of conversation
context in the database, including caching and expiration.
"""

import json
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import asyncio
from concurrent.futures import ThreadPoolExecutor

from core.database import db

logger = logging.getLogger(__name__)


@dataclass
class StorageConfig:
    """Configuration for context storage"""
    max_cache_size: int = 1000
    cache_ttl: timedelta = timedelta(minutes=30)
    max_history_items: int = 50
    cleanup_interval: timedelta = timedelta(hours=1)
    retention_period: timedelta = timedelta(days=30)


class ContextStorage:
    """
    Handles persistence of conversation context.
    
    This class manages database operations for context storage,
    including optimization strategies like batching and caching.
    """
    
    def __init__(self, config: Optional[StorageConfig] = None):
        self.config = config or StorageConfig()
        self._cache: Dict[str, Tuple[Dict[str, Any], datetime]] = {}
        self._write_queue: List[Tuple[str, Dict[str, Any]]] = []
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._cleanup_task = None
        
    def save_context(self, session_id: str, context_data: Dict[str, Any], 
                    focus_condition: Optional[str] = None,
                    focus_location: Optional[str] = None) -> bool:
        """
        Save context to storage.
        
        Args:
            session_id: Session identifier
            context_data: Context data to save
            focus_condition: Current focus condition
            focus_location: Current focus location
            
        Returns:
            Success status
        """
        try:
            logger.info("💾 CONTEXT SAVE - Starting context save operation...")
            logger.info(f"   - Session ID: {session_id}")
            logger.info(f"   - Focus Condition: {focus_condition}")
            logger.info(f"   - Focus Location: {focus_location}")
            logger.info(f"   - Context Data Keys: {list(context_data.keys())}")
            
            # Check for prescreening data in context
            if 'prescreening_data' in context_data:
                p_data = context_data['prescreening_data']
                logger.info("📋 PRESCREENING DATA IN CONTEXT:")
                logger.info(f"   - Questions Count: {len(p_data.get('questions', []))}")
                logger.info(f"   - Current Index: {p_data.get('current_question_index', 'N/A')}")
                logger.info(f"   - Answered Questions: {len(p_data.get('answered_questions', []))}")
                logger.info(f"   - Trial ID: {p_data.get('trial_id', 'N/A')}")
            
            # Add to cache
            self._cache[session_id] = (context_data, datetime.now(timezone.utc))
            logger.info("✅ Added to memory cache")
            
            # Extract user_id from context
            user_id = context_data.get("user_id", "anonymous")
            
            # Prepare JSON
            context_json = json.dumps(context_data)
            logger.info(f"   - JSON Size: {len(context_json)} chars")
            
            # Execute upsert (fixed constraint reference)
            db.execute_update("""
                INSERT INTO conversation_context 
                (session_id, user_id, context_data, focus_condition, focus_location, active)
                VALUES (%s, %s, %s, %s, %s, true)
                ON CONFLICT (session_id)
                DO UPDATE SET 
                    context_data = EXCLUDED.context_data,
                    focus_condition = EXCLUDED.focus_condition,
                    focus_location = EXCLUDED.focus_location,
                    active = EXCLUDED.active,
                    updated_at = NOW()
            """, (session_id, user_id, context_json, focus_condition, focus_location))
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save context for session {session_id}: {str(e)}")
            return False
    
    def load_context(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Load context from storage.
        
        Args:
            session_id: Session identifier
            
        Returns:
            Context data or None if not found
        """
        logger.info("📂 CONTEXT LOAD - Starting context load operation...")
        logger.info(f"   - Session ID: {session_id}")
        
        # Check cache first
        if session_id in self._cache:
            cached_data, cached_time = self._cache[session_id]
            if datetime.now(timezone.utc) - cached_time < self.config.cache_ttl:
                logger.info("✅ Found in memory cache (still valid)")
                logger.info(f"   - Cache Age: {(datetime.now(timezone.utc) - cached_time).total_seconds():.2f}s")
                return cached_data
            else:
                logger.info("⚠️  Found in cache but expired - checking database")
        else:
            logger.info("ℹ️  Not found in cache - checking database")
        
        try:
            results = db.execute_query("""
                SELECT 
                    context_data,
                    focus_condition,
                    focus_location,
                    created_at,
                    updated_at
                FROM conversation_context
                WHERE session_id = %s AND active = true
                ORDER BY updated_at DESC
                LIMIT 1
            """, (session_id,))
            
            if results:
                logger.info("✅ Found context in database")
                row = results[0]
                context_data = row.get("context_data", {})
                
                # Parse JSON if needed
                if isinstance(context_data, str):
                    context_data = json.loads(context_data)
                
                logger.info(f"   - Focus Condition: {row.get('focus_condition', 'None')}")
                logger.info(f"   - Focus Location: {row.get('focus_location', 'None')}")
                logger.info(f"   - Created: {row.get('created_at', 'Unknown')}")
                logger.info(f"   - Updated: {row.get('updated_at', 'Unknown')}")
                
                # Check for prescreening data in loaded context
                if 'prescreening_data' in context_data:
                    p_data = context_data['prescreening_data']
                    logger.info("📋 LOADED PRESCREENING DATA:")
                    logger.info(f"   - Questions Count: {len(p_data.get('questions', []))}")
                    logger.info(f"   - Current Index: {p_data.get('current_question_index', 'N/A')}")
                    logger.info(f"   - Answered Questions: {len(p_data.get('answered_questions', []))}")
                    logger.info(f"   - Trial ID: {p_data.get('trial_id', 'N/A')}")
                
                # Add database fields
                if row.get("focus_condition"):
                    context_data["focus_condition"] = row["focus_condition"]
                if row.get("focus_location"):
                    context_data["focus_location"] = row["focus_location"]
                    
                context_data["created_at"] = row.get("created_at")
                context_data["last_updated"] = row.get("updated_at")
                
                # Update cache
                self._cache[session_id] = (context_data, datetime.now(timezone.utc))
                logger.info("✅ Context loaded successfully and cached")
                
                return context_data
            else:
                logger.info("ℹ️  No context found in database")
                
        except Exception as e:
            logger.error(f"Failed to load context for session {session_id}: {str(e)}")
            
        return None
    
    def save_conversation_turn(self, session_id: str, user_message: str,
                             bot_response: str, context_data: Dict[str, Any]) -> bool:
        """
        Save a conversation turn to history.
        
        Args:
            session_id: Session identifier
            user_message: User's message
            bot_response: Bot's response
            context_data: Context at time of turn
            
        Returns:
            Success status
        """
        try:
            user_id = context_data.get("user_id", "anonymous")
            context_json = json.dumps(context_data)
            
            # Get current timestamp as ISO format string
            from datetime import datetime
            timestamp_str = datetime.utcnow().isoformat() + 'Z'
            
            result = db.execute_insert_returning("""
                INSERT INTO chat_logs
                (session_id, user_id, user_message, bot_response, context_data, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (session_id, user_id, user_message, bot_response, context_json, timestamp_str))

            if result:
                return result['id']  # Return the chat_logs ID
            return None
            
        except Exception as e:
            logger.error(f"Failed to save conversation turn: {str(e)}")
            return False
    
    def get_conversation_history(self, session_id: str, 
                               limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get conversation history for a session.
        
        Args:
            session_id: Session identifier
            limit: Maximum number of turns to retrieve
            
        Returns:
            List of conversation turns
        """
        limit = limit or self.config.max_history_items
        
        try:
            results = db.execute_query("""
                SELECT 
                    user_message,
                    bot_response,
                    context_data,
                    timestamp
                FROM chat_logs
                WHERE session_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (session_id, limit))
            
            # Convert and reverse for chronological order
            history = []
            for row in reversed(results) if results else []:
                context_data = row.get("context_data", {})
                if isinstance(context_data, str):
                    try:
                        context_data = json.loads(context_data)
                    except:
                        context_data = {}
                
                history.append({
                    "user_message": row["user_message"],
                    "bot_response": row["bot_response"],
                    "context": context_data,
                    "timestamp": row["timestamp"]
                })
                
            return history
            
        except Exception as e:
            logger.error(f"Failed to get conversation history: {str(e)}")
            return []
    
    def mark_context_inactive(self, session_id: str) -> bool:
        """
        Mark context as inactive (soft delete).
        
        Args:
            session_id: Session identifier
            
        Returns:
            Success status
        """
        try:
            # Remove from cache
            if session_id in self._cache:
                del self._cache[session_id]
            
            # Mark inactive in database
            db.execute_update("""
                UPDATE conversation_context 
                SET active = false 
                WHERE session_id = %s
            """, (session_id,))
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to mark context inactive: {str(e)}")
            return False
    
    def cleanup_expired_contexts(self) -> int:
        """
        Clean up expired contexts.
        
        Returns:
            Number of contexts cleaned up
        """
        try:
            cutoff_date = datetime.now(timezone.utc) - self.config.retention_period
            
            result = db.execute_update("""
                UPDATE conversation_context 
                SET active = false 
                WHERE active = true 
                AND updated_at < %s
                RETURNING session_id
            """, (cutoff_date,))
            
            # Clear from cache
            if result:
                for row in result:
                    session_id = row["session_id"]
                    if session_id in self._cache:
                        del self._cache[session_id]
            
            return len(result) if result else 0
            
        except Exception as e:
            logger.error(f"Failed to cleanup expired contexts: {str(e)}")
            return 0
    
    def get_active_session_count(self) -> int:
        """Get count of active sessions"""
        try:
            result = db.execute_query("""
                SELECT COUNT(*) as count
                FROM conversation_context
                WHERE active = true
            """)
            
            return result[0]["count"] if result else 0
            
        except Exception as e:
            logger.error(f"Failed to get active session count: {str(e)}")
            return 0
    
    def batch_save_contexts(self, contexts: List[Tuple[str, Dict[str, Any]]]) -> int:
        """
        Batch save multiple contexts.
        
        Args:
            contexts: List of (session_id, context_data) tuples
            
        Returns:
            Number of contexts saved
        """
        if not contexts:
            return 0
            
        try:
            # Prepare batch data
            values = []
            for session_id, context_data in contexts:
                user_id = context_data.get("user_id", "anonymous")
                focus_condition = context_data.get("focus_condition")
                focus_location = context_data.get("focus_location")
                context_json = json.dumps(context_data)
                
                values.append((
                    session_id, user_id, context_json, 
                    focus_condition, focus_location
                ))
            
            # Execute batch insert/update
            # Note: This is a simplified version. In production, you'd want
            # to use proper batch operations or COPY for better performance
            saved = 0
            for value in values:
                try:
                    db.execute_update("""
                        INSERT INTO conversation_context 
                        (session_id, user_id, context_data, focus_condition, focus_location, active)
                        VALUES (%s, %s, %s, %s, %s, true)
                        ON CONFLICT (session_id)
                        DO UPDATE SET 
                            context_data = EXCLUDED.context_data,
                            focus_condition = EXCLUDED.focus_condition,
                            focus_location = EXCLUDED.focus_location,
                            active = EXCLUDED.active,
                            updated_at = NOW()
                    """, value)
                    saved += 1
                except Exception as e:
                    logger.error(f"Failed to save context in batch: {str(e)}")
            
            return saved
            
        except Exception as e:
            logger.error(f"Failed to batch save contexts: {str(e)}")
            return 0
    
    def start_background_cleanup(self):
        """Start background cleanup task"""
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
    
    def stop_background_cleanup(self):
        """Stop background cleanup task"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
    
    async def _periodic_cleanup(self):
        """Periodically clean up expired contexts"""
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval.total_seconds())
                cleaned = self.cleanup_expired_contexts()
                if cleaned > 0:
                    logger.info(f"Cleaned up {cleaned} expired contexts")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic cleanup: {str(e)}")