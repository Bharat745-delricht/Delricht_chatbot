"""Medical condition normalization service"""
from typing import List, Tuple, Optional
import re


class ConditionNormalizer:
    """Service for normalizing medical condition names and handling variations"""
    
    def __init__(self):
        # Condition abbreviation mappings
        self.abbreviation_map = {
            "t2dm": "type 2 diabetes mellitus",
            "t1dm": "type 1 diabetes mellitus", 
            "dm": "diabetes mellitus",
            "ibs": "irritable bowel syndrome",
            "ibs-d": "irritable bowel syndrome with diarrhea",
            "ibs-c": "irritable bowel syndrome with constipation",
            "adhd": "attention-deficit/hyperactivity disorder",
            "add": "attention deficit disorder",
            "mdd": "major depressive disorder",
            "copd": "chronic obstructive pulmonary disease",
            "rsv": "respiratory syncytial virus",
            "ra": "rheumatoid arthritis",
            "hbp": "high blood pressure",
            "bp": "blood pressure",
            "chf": "congestive heart failure",
            "cad": "coronary artery disease",
            "ckd": "chronic kidney disease",
            "gerd": "gastroesophageal reflux disease",
            "uti": "urinary tract infection",
            "mrsa": "methicillin-resistant staphylococcus aureus",
            "afib": "atrial fibrillation",
            "ms": "multiple sclerosis",
            "als": "amyotrophic lateral sclerosis",
            "ptsd": "post-traumatic stress disorder",
            "ocd": "obsessive-compulsive disorder",
            "bph": "benign prostatic hyperplasia",
            "dvt": "deep vein thrombosis",
            "pe": "pulmonary embolism",
            "tbi": "traumatic brain injury",
            "sle": "systemic lupus erythematosus",
            "hiv": "human immunodeficiency virus",
            "aids": "acquired immunodeficiency syndrome",
            "hcv": "hepatitis c virus",
            "hbv": "hepatitis b virus",
            "osa": "obstructive sleep apnea",
            "pcos": "polycystic ovary syndrome",
            "ibd": "inflammatory bowel disease",
            "uc": "ulcerative colitis",
            "cd": "crohn's disease"
        }
        
        # Common condition variations and synonyms
        self.condition_synonyms = {
            "diabetes": ["diabetes mellitus", "dm", "sugar diabetes"],
            "type 2 diabetes": ["type 2 diabetes mellitus", "t2dm", "diabetes type 2", "adult-onset diabetes"],
            "type 1 diabetes": ["type 1 diabetes mellitus", "t1dm", "diabetes type 1", "juvenile diabetes"],
            "high blood pressure": ["hypertension", "htn", "hbp", "elevated blood pressure"],
            "irritable bowel syndrome": ["ibs", "spastic colon", "nervous colon", "irritable colon"],
            "irritable bowel syndrome with diarrhea": ["ibs-d", "ibs with diarrhea"],
            "irritable bowel syndrome with constipation": ["ibs-c", "ibs with constipation"],
            "depression": ["major depressive disorder", "mdd", "clinical depression", "major depression"],
            "adhd": ["attention deficit hyperactivity disorder", "attention-deficit/hyperactivity disorder", "add"],
            "copd": ["chronic obstructive pulmonary disease", "chronic obstructive lung disease"],
            "rheumatoid arthritis": ["ra", "inflammatory arthritis"],
            "gout": ["gouty arthritis", "metabolic arthritis"],
            "migraine": ["migraine headache", "migraines"],
            "asthma": ["bronchial asthma", "reactive airway disease"],
            "lupus": ["sle", "systemic lupus erythematosus"],
            "fibromyalgia": ["fibromyalgia syndrome", "fms"],
            "crohn's disease": ["crohn disease", "cd", "regional enteritis"],
            "ulcerative colitis": ["uc", "inflammatory bowel disease"],
            "heart disease": ["cardiovascular disease", "cvd", "cardiac disease"],
            "kidney disease": ["renal disease", "ckd", "chronic kidney disease"],
            "anxiety": ["anxiety disorder", "generalized anxiety disorder", "gad"],
            "epilepsy": ["seizure disorder", "seizures"],
            "parkinson": ["parkinson's disease", "parkinsons", "pd"],
            "alzheimer": ["alzheimer's disease", "alzheimers", "ad", "dementia"],
            "cancer": ["malignancy", "neoplasm", "tumor", "carcinoma"],
            "rsv": ["respiratory syncytial virus", "respiratory syncytial virus infection"],
            "fungal infection": ["toe fungus", "nail fungus", "foot fungus", "athlete's foot", "fungal nail infection", "onychomycosis"]
        }
        
        # Create reverse mapping for quick lookup
        self.synonym_to_canonical = {}
        for canonical, synonyms in self.condition_synonyms.items():
            for synonym in synonyms:
                self.synonym_to_canonical[synonym.lower()] = canonical
    
    def normalize_condition(self, condition: str) -> str:
        """
        Normalize a medical condition name to its canonical form
        
        Args:
            condition: The condition name to normalize
            
        Returns:
            The normalized condition name
        """
        if not condition:
            return condition
            
        # Clean and lowercase the input
        cleaned = condition.strip().lower()
        
        # First check if it's an abbreviation
        if cleaned in self.abbreviation_map:
            expanded = self.abbreviation_map[cleaned]
            # Check if the expanded form has a canonical name
            if expanded in self.synonym_to_canonical:
                return self.synonym_to_canonical[expanded]
            return expanded
        
        # Check if it's a known synonym
        if cleaned in self.synonym_to_canonical:
            return self.synonym_to_canonical[cleaned]
        
        # Use the condition registry for dynamic normalization if available
        try:
            from core.services.condition_registry import condition_registry
            registry_normalized = condition_registry.normalize_condition(condition)
            if registry_normalized != condition.lower():
                return registry_normalized
        except ImportError:
            # Registry not available, continue with static normalization
            pass
            
        # Return original (with proper casing) if no mapping found
        return condition.strip()
    
    def get_condition_variants(self, condition: str) -> List[str]:
        """
        Get all known variants of a condition for database searching
        
        Args:
            condition: The condition name
            
        Returns:
            List of all known variants including the original
        """
        variants = set()
        
        # Add the original
        variants.add(condition)
        
        # Normalize and add canonical form
        normalized = self.normalize_condition(condition)
        variants.add(normalized)
        
        # Add all synonyms if we have them
        condition_lower = condition.lower()
        normalized_lower = normalized.lower()
        
        # Find all synonym groups that contain this condition
        for canonical, synonyms in self.condition_synonyms.items():
            all_forms = [canonical.lower()] + [s.lower() for s in synonyms]
            if condition_lower in all_forms or normalized_lower in all_forms:
                variants.add(canonical)
                variants.update(synonyms)
        
        # Add abbreviations
        for abbrev, full_form in self.abbreviation_map.items():
            if full_form.lower() == condition_lower or full_form.lower() == normalized_lower:
                variants.add(abbrev)
            elif abbrev == condition_lower:
                variants.add(full_form)
        
        # Also get related conditions from the registry if available
        try:
            from core.services.condition_registry import condition_registry
            related = condition_registry.get_related_conditions(condition)
            variants.update(related)
        except ImportError:
            # Registry not available, continue without related conditions
            pass
        
        # Remove empty strings and return as list
        return [v for v in variants if v]
    
    def build_search_pattern(self, condition: str) -> str:
        """
        Build a SQL LIKE pattern that matches any variant of the condition
        
        Args:
            condition: The condition name
            
        Returns:
            SQL pattern for LIKE queries
        """
        variants = self.get_condition_variants(condition)
        
        # For SQL LIKE, we'll create multiple patterns
        # This will be used with OR conditions in the query
        return variants
    
    def fuzzy_match_condition(self, input_condition: str, target_condition: str) -> bool:
        """
        Check if two conditions are likely the same using fuzzy matching
        
        Args:
            input_condition: User input condition
            target_condition: Database condition to match against
            
        Returns:
            True if conditions likely match
        """
        # Normalize both
        input_normalized = self.normalize_condition(input_condition).lower()
        target_normalized = self.normalize_condition(target_condition).lower()
        
        # Direct match after normalization
        if input_normalized == target_normalized:
            return True
        
        # Check if one contains the other
        if input_normalized in target_normalized or target_normalized in input_normalized:
            return True
        
        # Get all variants for both
        input_variants = {v.lower() for v in self.get_condition_variants(input_condition)}
        target_variants = {v.lower() for v in self.get_condition_variants(target_condition)}
        
        # Check if any variants match
        return bool(input_variants & target_variants)


# Singleton instance
condition_normalizer = ConditionNormalizer()