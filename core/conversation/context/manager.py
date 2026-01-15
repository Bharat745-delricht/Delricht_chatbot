"""
Unified context management for conversations.

This module consolidates context operations including retrieval, updates,
validation, enrichment, and persistence. It manages both session context
and conversation memory.
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from enum import Enum

from core.database import db

logger = logging.getLogger(__name__)


class ContextField(str, Enum):
    """Standard context fields"""
    SESSION_ID = "session_id"
    USER_ID = "user_id"
    CONVERSATION_STATE = "conversation_state"
    STATE_DATA = "state_data"
    FOCUS_CONDITION = "focus_condition"
    FOCUS_LOCATION = "focus_location"
    TRIAL_ID = "trial_id"
    TRIAL_NAME = "trial_name"
    PRESCREENING_DATA = "prescreening_data"
    COLLECTED_DATA = "collected_data"
    REMAINING_QUESTIONS = "remaining_questions"
    CURRENT_QUESTION_KEY = "current_question_key"
    LAST_SHOWN_TRIALS = "last_shown_trials"
    JUST_SHOWED_TRIAL_INFO = "just_showed_trial_info"
    MENTIONED_CONDITIONS = "mentioned_conditions"
    MENTIONED_LOCATIONS = "mentioned_locations"
    CONVERSATION_HISTORY = "conversation_history"
    LAST_UPDATED = "last_updated"
    CREATED_AT = "created_at"


@dataclass
class ConversationContext:
    """Structured conversation context"""
    session_id: str
    user_id: str = "anonymous"
    conversation_state: Optional[str] = None
    state_data: Dict[str, Any] = field(default_factory=dict)
    focus_condition: Optional[str] = None
    focus_location: Optional[str] = None
    trial_id: Optional[int] = None
    trial_name: Optional[str] = None
    prescreening_data: Dict[str, Any] = field(default_factory=dict)
    collected_data: Dict[str, Any] = field(default_factory=dict)
    remaining_questions: List[str] = field(default_factory=list)
    current_question_key: Optional[str] = None
    last_shown_trials: List[Dict[str, Any]] = field(default_factory=list)
    just_showed_trial_info: bool = False
    mentioned_conditions: Set[str] = field(default_factory=set)
    mentioned_locations: Set[str] = field(default_factory=set)
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    last_updated: Optional[datetime] = None
    created_at: Optional[datetime] = None

    # Booking flow attributes (must be persisted across messages)
    booking_data: Dict[str, Any] = field(default_factory=dict)  # name, phone, email, dob
    selected_slot: Optional[Dict[str, Any]] = None  # Selected time slot
    booking_site_info: Optional[Dict[str, Any]] = None  # Site details
    booking_trial_id: Optional[int] = None  # Trial ID for booking
    presented_slots: List[Dict[str, Any]] = field(default_factory=list)  # Available slots shown

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence"""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "conversation_state": self.conversation_state,
            "state_data": self.state_data,
            "focus_condition": self.focus_condition,
            "focus_location": self.focus_location,
            "trial_id": self.trial_id,
            "trial_name": self.trial_name,
            "prescreening_data": self.prescreening_data,
            "collected_data": self.collected_data,
            "remaining_questions": self.remaining_questions,
            "current_question_key": self.current_question_key,
            "last_shown_trials": self.last_shown_trials,
            "just_showed_trial_info": self.just_showed_trial_info,
            "mentioned_conditions": list(self.mentioned_conditions),
            "mentioned_locations": list(self.mentioned_locations),
            "conversation_history": self.conversation_history,
            "metadata": self.metadata,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            # Booking flow attributes - serialize datetime values
            "booking_data": self.booking_data,
            "selected_slot": self._serialize_slot(self.selected_slot),
            "booking_site_info": self.booking_site_info,
            "booking_trial_id": self.booking_trial_id,
            "presented_slots": [self._serialize_slot(s) for s in self.presented_slots] if self.presented_slots else [],
        }

    def _serialize_slot(self, slot: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Serialize a slot dict, converting datetime to ISO string"""
        if not slot:
            return slot
        result = dict(slot)
        if 'datetime' in result:
            dt = result['datetime']
            if hasattr(dt, 'isoformat'):
                result['datetime'] = dt.isoformat()
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConversationContext':
        """Create from dictionary"""
        # Handle datetime conversion
        last_updated = data.get("last_updated")
        if last_updated and isinstance(last_updated, str):
            last_updated = datetime.fromisoformat(last_updated)
            # Ensure timezone-aware
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)
        elif last_updated and isinstance(last_updated, datetime):
            # Handle datetime objects from database
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)

        created_at = data.get("created_at")
        if created_at and isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
            # Ensure timezone-aware
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        elif created_at and isinstance(created_at, datetime):
            # Handle datetime objects from database
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        
        # Handle set conversion
        mentioned_conditions = set(data.get("mentioned_conditions", []))
        mentioned_locations = set(data.get("mentioned_locations", []))
        
        return cls(
            session_id=data["session_id"],
            user_id=data.get("user_id", "anonymous"),
            conversation_state=data.get("conversation_state"),
            state_data=data.get("state_data", {}),
            focus_condition=data.get("focus_condition"),
            focus_location=data.get("focus_location"),
            trial_id=data.get("trial_id"),
            trial_name=data.get("trial_name"),
            prescreening_data=data.get("prescreening_data", {}),
            collected_data=data.get("collected_data", {}),
            remaining_questions=data.get("remaining_questions", []),
            current_question_key=data.get("current_question_key"),
            last_shown_trials=data.get("last_shown_trials", []),
            just_showed_trial_info=data.get("just_showed_trial_info", False),
            mentioned_conditions=mentioned_conditions,
            mentioned_locations=mentioned_locations,
            conversation_history=data.get("conversation_history", []),
            metadata=data.get("metadata", {}),
            last_updated=last_updated,
            created_at=created_at,
            # Booking flow attributes
            booking_data=data.get("booking_data", {}),
            selected_slot=data.get("selected_slot"),
            booking_site_info=data.get("booking_site_info"),
            booking_trial_id=data.get("booking_trial_id"),
            presented_slots=data.get("presented_slots", []),
        )


class ContextManager:
    """
    Unified context manager for conversation state and memory.
    
    This class consolidates all context operations including retrieval,
    updates, validation, enrichment, and persistence.
    """
    
    def __init__(self, max_context_age: timedelta = timedelta(hours=24)):
        self.max_context_age = max_context_age
        self._context_cache: Dict[str, ConversationContext] = {}
        
    def get_context(self, session_id: str, include_history: bool = True) -> ConversationContext:
        """
        Get complete conversation context.
        
        Args:
            session_id: Session identifier
            include_history: Whether to include conversation history
            
        Returns:
            Complete conversation context
        """
        # Check cache first
        if session_id in self._context_cache:
            cached = self._context_cache[session_id]
            if self._is_context_fresh(cached):
                return cached
        
        # Load from database
        context = self._load_context_from_db(session_id)
        
        # Add conversation history if requested
        if include_history:
            context.conversation_history = self._get_conversation_history(session_id)
            
        # Enrich context with derived information
        self._enrich_context(context)
        
        # Cache the context
        self._context_cache[session_id] = context
        
        return context
    
    def update_context(self, session_id: str, updates: Dict[str, Any], 
                      preserve_memory: bool = True) -> ConversationContext:
        """
        Update conversation context.
        
        Args:
            session_id: Session identifier
            updates: Updates to apply
            preserve_memory: Whether to preserve memory fields
            
        Returns:
            Updated context
        """
        # Get current context
        context = self.get_context(session_id)
        
        # Apply updates
        for key, value in updates.items():
            if hasattr(context, key):
                # Handle special fields
                if key in ["mentioned_conditions", "mentioned_locations"] and isinstance(value, list):
                    # Add to existing set
                    getattr(context, key).update(value)
                elif key == "last_shown_trials" and isinstance(value, list):
                    # Append new trials
                    context.last_shown_trials.extend(value)
                else:
                    setattr(context, key, value)
        
        # Update timestamp
        context.last_updated = datetime.now(timezone.utc)
        
        # Persist to database
        self._persist_context(context)
        
        # Update cache
        self._context_cache[session_id] = context
        
        return context
    
    def clear_context(self, session_id: str):
        """Clear context for a session"""
        # Remove from cache
        if session_id in self._context_cache:
            del self._context_cache[session_id]
            
        # Mark as inactive in database
        db.execute_update("""
            UPDATE conversation_context 
            SET active = false 
            WHERE session_id = %s
        """, (session_id,))
        
    def validate_context(self, context: ConversationContext) -> List[str]:
        """
        Validate context consistency.
        
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        
        # Check required fields
        if not context.session_id:
            errors.append("Missing session_id")
            
        # Check state consistency
        if context.conversation_state == "PRESCREENING_ACTIVE":
            if not context.trial_id and not (context.focus_condition and context.focus_location):
                errors.append("Prescreening requires trial_id or condition+location")
                
        # Check data consistency
        if context.collected_data and not context.prescreening_data:
            errors.append("Collected data without prescreening data")
            
        # Check age validity
        if not self._is_context_fresh(context):
            errors.append("Context has expired")
            
        return errors
    
    def _load_context_from_db(self, session_id: str) -> ConversationContext:
        """Load context from database"""
        results = db.execute_query("""
            SELECT 
                session_id,
                user_id,
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
            row = results[0]
            context_data = row.get("context_data", {})
            if isinstance(context_data, str):
                context_data = json.loads(context_data)
                
            # Merge database fields with context data
            context_dict = {
                "session_id": session_id,
                "user_id": row.get("user_id", "anonymous"),
                "focus_condition": row.get("focus_condition"),
                "focus_location": row.get("focus_location"),
                "created_at": row.get("created_at"),
                "last_updated": row.get("updated_at"),
            }
            context_dict.update(context_data)

            # 🐛 DEBUG: Log what prescreening_data we loaded from DB
            if "prescreening_data" in context_dict:
                p_data = context_dict["prescreening_data"]
                if p_data:
                    logger.error(f"📥 LOADING FROM DB - Session: {session_id}")
                    logger.error(f"   Prescreening Index loaded: {p_data.get('current_question_index', 'N/A')}")
                    logger.error(f"   Questions in DB: {len(p_data.get('questions', []))}")
                else:
                    logger.error(f"⚠️  LOADED EMPTY prescreening_data from DB - Session: {session_id}")
            else:
                logger.error(f"⚠️  NO prescreening_data in DB context - Session: {session_id}")

            return ConversationContext.from_dict(context_dict)
        else:
            # Create new context
            return ConversationContext(
                session_id=session_id,
                conversation_state="idle",
                created_at=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc)
            )
    
    def _persist_context(self, context: ConversationContext):
        """Persist context to database"""
        context_dict = context.to_dict()

        # 🐛 DEBUG: Log booking attributes before persisting
        logger.error(f"💾 _persist_context called for session: {context.session_id}")
        logger.error(f"   booking_data: {context.booking_data}")
        logger.error(f"   presented_slots: {len(context.presented_slots)} slots")
        logger.error(f"   selected_slot: {'Present' if context.selected_slot else 'None'}")
        logger.error(f"   booking_site_info: {'Present' if context.booking_site_info else 'None'}")
        logger.error(f"   booking_trial_id: {context.booking_trial_id}")

        # 🐛 DEBUG: Log what we're about to persist
        if "prescreening_data" in context_dict:
            p_data = context_dict["prescreening_data"]
            if p_data and isinstance(p_data, dict):
                logger.error(f"💿 _persist_context - prescreening_data in to_dict(): {len(p_data)} keys")
                logger.error(f"   Index: {p_data.get('current_question_index', 'N/A')}")
            else:
                logger.error(f"💿 _persist_context - prescreening_data is EMPTY in to_dict()")
        else:
            logger.error(f"💿 _persist_context - NO prescreening_data key in to_dict()")

        # Extract fields stored as columns
        focus_condition = context_dict.pop("focus_condition", None)
        focus_location = context_dict.pop("focus_location", None)
        user_id = context_dict.pop("user_id", "anonymous")
        session_id = context_dict.pop("session_id")

        # Convert to JSON
        context_json = json.dumps(context_dict)

        # 🐛 DEBUG: Check if booking attributes survived JSON serialization
        check_dict = json.loads(context_json)
        logger.error(f"📋 JSON KEYS BEING SAVED: {list(check_dict.keys())}")
        logger.error(f"   booking_data in JSON: {check_dict.get('booking_data')}")
        logger.error(f"   presented_slots in JSON: {len(check_dict.get('presented_slots', []))} slots")
        logger.error(f"   selected_slot in JSON: {'Present' if check_dict.get('selected_slot') else 'None'}")
        logger.error(f"   booking_site_info in JSON: {'Present' if check_dict.get('booking_site_info') else 'None'}")

        if "prescreening_data" in check_dict and check_dict["prescreening_data"]:
            logger.error(f"✅ prescreening_data IN JSON being saved to DB")
        else:
            logger.error(f"❌ prescreening_data NOT in JSON or is empty!")
        
        # Upsert to database with enhanced error handling
        try:
            db.execute_update("""
                INSERT INTO conversation_context 
                (session_id, user_id, context_data, focus_condition, focus_location, active)
                VALUES (%s, %s, %s::jsonb, %s, %s, true)
                ON CONFLICT (session_id)
                DO UPDATE SET 
                    context_data = EXCLUDED.context_data,
                    focus_condition = EXCLUDED.focus_condition,
                    focus_location = EXCLUDED.focus_location,
                    active = EXCLUDED.active,
                    updated_at = NOW()
            """, (session_id, user_id, context_json, focus_condition, focus_location))

            # 🐛 DEBUG: Verify what was actually saved
            verify = db.execute_query("SELECT context_data FROM conversation_context WHERE session_id = %s", (session_id,))
            if verify:
                saved_data = verify[0]['context_data'] if isinstance(verify[0]['context_data'], dict) else json.loads(verify[0]['context_data'])
                logger.error(f"📀 VERIFIED IN DB IMMEDIATELY AFTER SAVE:")
                logger.error(f"   presented_slots: {len(saved_data.get('presented_slots', []))} slots")
                logger.error(f"   booking_site_info: {'Present' if saved_data.get('booking_site_info') else 'MISSING!'}")
                logger.error(f"   booking_data: {saved_data.get('booking_data')}")
        except Exception as e:
            logger.error(f"Database constraint error saving context for session {session_id}: {e}")
            # Try a simpler update query as fallback
            try:
                db.execute_update("""
                    UPDATE conversation_context 
                    SET context_data = %s::jsonb, focus_condition = %s, focus_location = %s, 
                        active = true, updated_at = NOW()
                    WHERE session_id = %s
                """, (context_json, focus_condition, focus_location, session_id))
                logger.info(f"Successfully updated existing context for session {session_id}")
            except Exception as e2:
                logger.error(f"Fallback update also failed for session {session_id}: {e2}")
                raise
        
    def _get_conversation_history(self, session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent conversation history"""
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
        
        # Reverse to get chronological order
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
    
    def _enrich_context(self, context: ConversationContext):
        """Enrich context with derived information"""
        if not context.conversation_history:
            return
            
        # Extract information from history
        for turn in context.conversation_history:
            turn_context = turn.get("context", {})
            intent = turn_context.get("intent", {})
            entities = intent.get("entities", {})
            
            # Collect mentioned conditions and locations
            if entities.get("condition"):
                context.mentioned_conditions.add(entities["condition"])
            if entities.get("location"):
                context.mentioned_locations.add(entities["location"])
                
            # Track shown trials
            metadata = turn_context.get("metadata", {})
            if metadata.get("last_shown_trials"):
                for trial in metadata["last_shown_trials"]:
                    if trial not in context.last_shown_trials:
                        context.last_shown_trials.append(trial)
        
        # Detect conversation patterns
        if context.conversation_history:
            last_turn = context.conversation_history[-1]
            last_response = last_turn.get("bot_response", "").lower()
            
            # Check if we just showed trials
            trial_indicators = [
                "trial available in", "found", "clinical trial",
                "would you like to", "check your eligibility",
                "trials available in"
            ]
            if any(phrase in last_response for phrase in trial_indicators):
                context.state_data["just_showed_trials"] = True
                
            # Check for continuation patterns
            user_msg = last_turn.get("user_message", "").lower()
            if any(word in user_msg for word in ["that", "the", "this", "it"]):
                context.state_data["possible_continuation"] = True
    
    def _is_context_fresh(self, context: ConversationContext) -> bool:
        """Check if context is still fresh"""
        if not context.last_updated:
            return True
            
        age = datetime.now(timezone.utc) - context.last_updated
        return age < self.max_context_age
    
    def infer_missing_context(self, message: str, context: ConversationContext) -> Dict[str, Any]:
        """
        Infer missing context from message and history.
        
        Args:
            message: User message
            context: Current context
            
        Returns:
            Inferred context elements
        """
        inferred = {}
        message_lower = message.lower()
        
        # Check for referential language
        referential_words = ["that", "the", "this", "it", "these", "those"]
        if any(ref in message_lower for ref in referential_words):
            # Infer from recent mentions
            if context.mentioned_conditions:
                # Get most recent condition
                conditions_list = list(context.mentioned_conditions)
                if conditions_list:
                    inferred["likely_condition"] = conditions_list[-1]
                    
            if context.mentioned_locations:
                # Get most recent location
                locations_list = list(context.mentioned_locations)
                if locations_list:
                    inferred["likely_location"] = locations_list[-1]
                    
            # Check if referring to shown trials
            if context.last_shown_trials and "trial" in message_lower:
                inferred["referring_to_shown_trials"] = True
                inferred["shown_trials"] = context.last_shown_trials
        
        # Check for implicit trial references
        if "trial" in message_lower:
            # Pattern: "the [condition] trial"
            pattern = r"the\s+([a-zA-Z\s]+)\s+trial"
            match = re.search(pattern, message_lower)
            if match:
                condition = match.group(1).strip()
                inferred["explicit_condition"] = condition
                
        # Check for location patterns without explicit "in"
        location_patterns = [
            r"trials?\s+(?:at|near)\s+([A-Z][a-zA-Z\s]+)",
            r"([A-Z][a-zA-Z\s]+)\s+trials?",
        ]
        for pattern in location_patterns:
            match = re.search(pattern, message)
            if match:
                potential_location = match.group(1).strip()
                if len(potential_location) > 2:  # Avoid false positives
                    inferred["potential_location"] = potential_location
        
        return inferred