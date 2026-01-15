"""Enhanced conversation memory and context management"""
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
from core.database import db
import logging

logger = logging.getLogger(__name__)


class ConversationMemory:
    """Service for managing conversation context and memory across turns"""
    
    def __init__(self):
        self.max_context_age = timedelta(hours=24)  # Context expires after 24 hours
    
    def get_enhanced_context(self, session_id: str) -> Dict[str, Any]:
        """Get enhanced context with recent conversation history"""
        # Get basic context
        context = self._get_basic_context(session_id)
        
        # Add recent conversation history
        history = self._get_recent_history(session_id)
        context["conversation_history"] = history
        
        # Add derived context
        context.update(self._derive_context_from_history(history))
        
        return context
    
    def _get_basic_context(self, session_id: str) -> Dict[str, Any]:
        """Get basic conversation context from database"""
        results = db.execute_query("""
            SELECT context_data, focus_condition, focus_location, updated_at
            FROM conversation_context
            WHERE session_id = %s AND active = true
            ORDER BY updated_at DESC
            LIMIT 1
        """, (session_id,))
        
        if results:
            row = results[0]
            # Check if context is still fresh
            if row["updated_at"] and (datetime.now(timezone.utc) - row["updated_at"]) < self.max_context_age:
                context = row.get("context_data", {}) or {}
                
                # Add focus fields
                if row.get("focus_condition"):
                    context["focus_condition"] = row["focus_condition"]
                if row.get("focus_location"):
                    context["focus_location"] = row["focus_location"]
                    
                return context
        
        return {}
    
    def _get_recent_history(self, session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
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
        return list(reversed(results)) if results else []
    
    def _derive_context_from_history(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Derive additional context from conversation history"""
        derived = {}
        
        if not history:
            return derived
        
        # Find mentioned trials and conditions
        mentioned_conditions = set()
        mentioned_locations = set()
        mentioned_trials = []
        
        for turn in history:
            context_data = turn.get("context_data", {})
            if isinstance(context_data, str):
                try:
                    context_data = json.loads(context_data)
                except:
                    context_data = {}
            
            # Extract intent data
            intent = context_data.get("intent", {})
            entities = intent.get("entities", {})
            
            if entities.get("condition"):
                mentioned_conditions.add(entities["condition"])
            if entities.get("location"):
                mentioned_locations.add(entities["location"])
            
            # Extract metadata
            metadata = context_data.get("metadata", {})
            if metadata.get("last_shown_trials"):
                mentioned_trials.extend(metadata["last_shown_trials"])
        
        # Add to derived context
        if mentioned_conditions:
            derived["all_mentioned_conditions"] = list(mentioned_conditions)
            derived["last_mentioned_condition"] = list(mentioned_conditions)[-1]
        
        if mentioned_locations:
            derived["all_mentioned_locations"] = list(mentioned_locations)
            derived["last_mentioned_location"] = list(mentioned_locations)[-1]
        
        if mentioned_trials:
            derived["all_shown_trials"] = list(set(mentioned_trials))
        
        # Check if user is in middle of a trial discussion
        if history:
            last_turn = history[-1]
            last_response = last_turn.get("bot_response", "").lower()
            
            # Detect if we just showed trial info
            if any(phrase in last_response for phrase in [
                "trial available in", "found", "clinical trial", 
                "would you like to", "check your eligibility",
                "trials available in", "i found", "great!"
            ]):
                derived["just_showed_trials"] = True
                
            # Also check for specific trial listing patterns
            if "**" in last_response and "trial" in last_response:
                derived["just_showed_trials"] = True
            
            # Detect if user might be continuing a discussion
            user_msg = last_turn.get("user_message", "").lower()
            if any(word in user_msg for word in ["that", "the", "this", "it"]):
                derived["possible_continuation"] = True
        
        return derived
    
    def update_context_with_memory(
        self, 
        session_id: str, 
        updates: Dict[str, Any],
        preserve_existing: bool = True
    ):
        """Update context while preserving important memory"""
        current = self.get_enhanced_context(session_id)
        
        if preserve_existing:
            # Preserve important fields
            for field in ["all_mentioned_conditions", "all_mentioned_locations", "all_shown_trials"]:
                if field in current and field not in updates:
                    updates[field] = current[field]
        
        # Add timestamp
        updates["last_updated"] = datetime.now(timezone.utc).isoformat()
        
        # Extract focus fields
        focus_condition = updates.get("focus_condition")
        focus_location = updates.get("focus_location")
        
        # Update database (fixed constraint reference)
        db.execute_update("""
            INSERT INTO conversation_context 
            (session_id, user_id, context_data, focus_condition, focus_location, active)
            VALUES (%s, %s, %s, %s, %s, true)
            ON CONFLICT (session_id)
            DO UPDATE SET 
                context_data = EXCLUDED.context_data,
                focus_condition = EXCLUDED.focus_condition,
                active = EXCLUDED.active,
                focus_location = EXCLUDED.focus_location,
                updated_at = NOW()
        """, (session_id, 'system', json.dumps(updates), focus_condition, focus_location))
    
    def infer_missing_context(
        self, 
        message: str, 
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Infer missing context from message and history"""
        inferred = {}
        
        message_lower = message.lower()
        
        # Check for referential language
        if any(ref in message_lower for ref in ["that", "the", "this", "it"]):
            # User might be referring to something previously mentioned
            if context.get("last_mentioned_condition"):
                inferred["likely_condition"] = context["last_mentioned_condition"]
            if context.get("last_mentioned_location"):
                inferred["likely_location"] = context["last_mentioned_location"]
        
        # Check for trial references without explicit condition
        if "trial" in message_lower:
            # If we recently showed trials, user might be referring to them
            if context.get("just_showed_trials") and context.get("focus_condition"):
                inferred["likely_condition"] = context["focus_condition"]
            
            # Special handling for "the IBS trial" type patterns
            if "the" in message_lower and "trial" in message_lower:
                # Extract what's between "the" and "trial"
                import re
                pattern = r"the\s+([a-zA-Z\s]+)\s+trial"
                match = re.search(pattern, message_lower)
                if match:
                    potential_condition = match.group(1).strip()
                    # This will be normalized by the classifier
                    inferred["explicit_condition"] = potential_condition
        
        return inferred


# Singleton instance
conversation_memory = ConversationMemory()