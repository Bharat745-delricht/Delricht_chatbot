"""Dynamic condition registry that loads from database"""
from typing import Set, List, Dict, Any
from core.database import db
import logging
from functools import lru_cache
import re

logger = logging.getLogger(__name__)


class ConditionRegistry:
    """Registry of medical conditions from actual trials in database"""
    
    def __init__(self):
        self._conditions_cache = None
        self._normalized_cache = {}
        
    @lru_cache(maxsize=1)
    def get_all_conditions(self) -> Set[str]:
        """Get all unique conditions from database"""
        try:
            results = db.execute_query("""
                SELECT DISTINCT LOWER(TRIM(conditions)) as condition
                FROM clinical_trials
                WHERE conditions IS NOT NULL AND conditions != ''
                ORDER BY condition
            """)
            
            conditions = set()
            for row in results:
                if row['condition']:
                    # Add the condition as-is
                    conditions.add(row['condition'])
                    
                    # Also add common variations
                    condition = row['condition']
                    
                    # Handle "X of the Y" patterns (e.g., "Osteoarthritis of the Knee")
                    if " of the " in condition:
                        base = condition.split(" of the ")[0]
                        conditions.add(base.lower())
                    
                    # Handle "X Associated With Y" patterns
                    if " associated with " in condition:
                        parts = condition.split(" associated with ")
                        for part in parts:
                            conditions.add(part.strip().lower())
                    
                    # Handle "Acute X" patterns
                    if condition.startswith("acute "):
                        base = condition[6:]  # Remove "acute "
                        conditions.add(base)
                    
                    # Handle "Pediatric X" patterns
                    if condition.startswith("pediatric "):
                        base = condition[10:]  # Remove "pediatric "
                        conditions.add(base)
                    
                    # Handle "Type 2 X" patterns
                    if "type 2 " in condition:
                        base = condition.replace("type 2 ", "")
                        conditions.add(base)
                        conditions.add("t2" + base[0])  # Add abbreviation like "t2d"
            
            logger.info(f"Loaded {len(conditions)} unique conditions from database")
            return conditions
            
        except Exception as e:
            logger.error(f"Failed to load conditions from database: {e}")
            # Return empty set if database query fails
            return set()
    
    def is_medical_condition(self, text: str) -> bool:
        """Check if text is likely a medical condition"""
        if not text:
            return False
            
        text_lower = text.lower().strip()
        
        # First check exact matches
        all_conditions = self.get_all_conditions()
        if text_lower in all_conditions:
            return True
        
        # Check if any known condition is contained in the text
        for condition in all_conditions:
            if condition in text_lower or text_lower in condition:
                return True
        
        # Check for medical-sounding patterns
        medical_patterns = [
            r'\b(syndrome|disease|disorder|cancer|carcinoma|arthritis|diabetes|hypertension)\b',
            r'\b(acute|chronic|pediatric|adult|type \d+)\s+\w+',
            r'\b\w+\s+(of the|associated with)\s+\w+',
            r'\b(treatment|therapy|condition|infection|inflammation)\b'
        ]
        
        for pattern in medical_patterns:
            if re.search(pattern, text_lower):
                return True
        
        return False
    
    def normalize_condition(self, condition: str) -> str:
        """Normalize condition name for consistency"""
        if not condition:
            return condition
            
        # Check cache first
        if condition in self._normalized_cache:
            return self._normalized_cache[condition]
        
        normalized = condition.lower().strip()
        
        # Remove common medical suffixes for matching
        suffixes_to_remove = [
            " syndrome", " disease", " disorder", 
            " mellitus", " type 2", " type 1",
            " of the knee", " of the hip", " of the shoulder"
        ]
        
        base_condition = normalized
        for suffix in suffixes_to_remove:
            if base_condition.endswith(suffix):
                base_condition = base_condition[:-len(suffix)].strip()
                break
        
        # Check if base condition exists in database
        all_conditions = self.get_all_conditions()
        
        # Try to find the best match
        if normalized in all_conditions:
            result = normalized
        elif base_condition in all_conditions:
            result = base_condition
        else:
            # Look for partial matches
            for db_condition in all_conditions:
                if base_condition in db_condition or db_condition in base_condition:
                    result = db_condition
                    break
            else:
                result = normalized  # Return original if no match
        
        # Cache the result
        self._normalized_cache[condition] = result
        return result
    
    def get_related_conditions(self, condition: str) -> List[str]:
        """Get conditions that might be related"""
        related = []
        condition_lower = condition.lower().strip()
        all_conditions = self.get_all_conditions()
        
        for db_condition in all_conditions:
            # Skip exact matches
            if db_condition == condition_lower:
                continue
                
            # Check for conditions that contain this one
            if condition_lower in db_condition or db_condition in condition_lower:
                related.append(db_condition)
            
            # Check for common words (excluding very common ones)
            condition_words = set(condition_lower.split()) - {'the', 'of', 'and', 'with', 'in', 'or'}
            db_words = set(db_condition.split()) - {'the', 'of', 'and', 'with', 'in', 'or'}
            
            if condition_words & db_words:  # Intersection
                related.append(db_condition)
        
        return list(set(related))[:5]  # Return up to 5 related conditions
    
    def refresh_cache(self):
        """Force refresh of the conditions cache"""
        self.get_all_conditions.cache_clear()
        self._normalized_cache.clear()
        logger.info("Condition registry cache refreshed")


# Singleton instance
condition_registry = ConditionRegistry()