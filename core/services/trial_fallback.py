"""Service for handling cases where trials aren't found"""
from typing import List, Dict, Any, Optional
from core.database import db
from core.services.condition_normalizer import condition_normalizer
import logging

logger = logging.getLogger(__name__)


class TrialFallbackService:
    """Handle scenarios where specific trials aren't found"""
    
    def suggest_alternatives(
        self, 
        condition: str, 
        location: str, 
        context: Dict[str, Any]
    ) -> str:
        """Generate helpful suggestions when no trials are found"""
        
        # Get all condition variants
        condition_variants = condition_normalizer.get_condition_variants(condition)
        
        # Check nearby locations
        nearby_trials = self._find_trials_nearby(condition_variants, location)
        
        # Check related conditions
        related_trials = self._find_related_condition_trials(condition, location)
        
        # Get all available conditions in the location
        available_conditions = self._get_available_conditions(location)
        
        # Build response
        response_parts = []
        
        # Main message
        response_parts.append(f"I couldn't find a specific {condition} trial in {location}.")
        
        # Debug: log what we searched for
        logger.info(f"Fallback: searched for '{condition}' in '{location}', variants: {condition_variants}")
        
        # Suggest nearby locations if available
        if nearby_trials:
            locations_with_trials = list(set([t['site_location'] for t in nearby_trials[:3]]))
            response_parts.append(f"\nHowever, I found {condition} trials in these nearby locations:")
            for loc in locations_with_trials:
                response_parts.append(f"• {loc}")
            response_parts.append("\nWould you like to learn more about trials in any of these locations?")
        
        # Suggest available conditions if no nearby trials
        elif available_conditions:
            response_parts.append(f"\nIn {location}, we currently have trials for:")
            for cond in available_conditions[:5]:
                response_parts.append(f"• {cond}")
            response_parts.append("\nWould you like to check your eligibility for any of these conditions?")
        
        # General fallback
        else:
            response_parts.append("\nWould you like me to search in a different location, or are you interested in trials for a different condition?")
        
        return "\n".join(response_parts)
    
    def _find_trials_nearby(
        self, 
        condition_variants: List[str], 
        exclude_location: str
    ) -> List[Dict[str, Any]]:
        """Find trials for the condition in other locations"""
        
        # Build WHERE clause for condition variants
        condition_clauses = " OR ".join(["LOWER(ct.conditions) LIKE LOWER(%s)"] * len(condition_variants))
        
        query = f"""
            SELECT DISTINCT 
                ct.id,
                ct.conditions,
                ti.site_location,
                ti.investigator_name
            FROM clinical_trials ct
            JOIN trial_investigators ti ON ct.id = ti.trial_id
            WHERE ({condition_clauses})
            AND LOWER(ti.site_location) NOT LIKE LOWER(%s)
            LIMIT 10
        """
        
        params = [f"%{variant}%" for variant in condition_variants]
        params.append(f"%{exclude_location}%")
        
        return db.execute_query(query, tuple(params))
    
    def _find_related_condition_trials(
        self, 
        condition: str, 
        location: str
    ) -> List[Dict[str, Any]]:
        """Find trials for related conditions in the same location"""
        
        # Map conditions to related categories
        condition_categories = {
            "diabetes": ["metabolic", "endocrine"],
            "hypertension": ["cardiovascular", "heart"],
            "depression": ["mental health", "psychiatric"],
            "anxiety": ["mental health", "psychiatric"],
            "copd": ["respiratory", "lung"],
            "asthma": ["respiratory", "lung"],
            "arthritis": ["rheumatologic", "joint"],
            "ibs": ["gastrointestinal", "digestive"],
            "migraine": ["neurological", "headache"],
            "epilepsy": ["neurological", "seizure"]
        }
        
        # Get category for the condition
        condition_lower = condition.lower()
        categories = []
        for key, cats in condition_categories.items():
            if key in condition_lower:
                categories.extend(cats)
        
        if not categories:
            return []
        
        # Find trials in related categories
        # This is a simplified approach - in production you'd have better categorization
        return []
    
    def _get_available_conditions(self, location: str) -> List[str]:
        """Get all available conditions in a location"""
        
        results = db.execute_query("""
            SELECT DISTINCT ct.conditions
            FROM clinical_trials ct
            JOIN trial_investigators ti ON ct.id = ti.trial_id
            WHERE LOWER(ti.site_location) LIKE LOWER(%s)
            ORDER BY ct.conditions
            LIMIT 10
        """, (f"%{location}%",))
        
        return [r['conditions'] for r in results] if results else []
    
    def handle_ambiguous_request(
        self, 
        message: str, 
        context: Dict[str, Any]
    ) -> str:
        """Handle requests that are ambiguous or unclear"""
        
        # Check what information we have from context
        has_location = bool(context.get("user_location") or context.get("focus_location"))
        has_condition = bool(context.get("focus_condition"))
        recent_trials = context.get("last_shown_trials", [])
        
        if recent_trials and not has_condition:
            return "I'd be happy to help! Which specific trial from the list above would you like to know more about?"
        elif has_location and not has_condition:
            return f"I can help you find trials in {context.get('user_location') or context.get('focus_location')}. What medical condition are you interested in?"
        elif has_condition and not has_location:
            return f"I can help you find {context.get('focus_condition')} trials. What location works best for you?"
        else:
            return "I'd be happy to help you find clinical trials! Could you tell me what medical condition you're interested in, or what location you'd prefer?"


# Singleton instance
trial_fallback = TrialFallbackService()