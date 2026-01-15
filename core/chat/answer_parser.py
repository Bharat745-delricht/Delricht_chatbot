"""Parser for extracting structured data from user answers"""
import re
from typing import Optional, Union, Dict, Any
import logging

logger = logging.getLogger(__name__)


class AnswerParser:
    """Parses various answer formats during prescreening"""
    
    def __init__(self):
        # Age patterns
        self.age_patterns = [
            (r"(?:i'?m |i am )?(\d{1,3})(?: years?)?(?:\s*old)?", 1),
            (r"my age is (\d{1,3})", 1),
            (r"(\d{1,3}) years? old", 1),
            (r"^(\d{1,3})$", 1),  # Just a number
        ]
        
        # Yes patterns
        self.yes_patterns = [
            r"^(?:yes|yeah|yep|yup|correct|right|true|sure|absolutely|definitely)(?:\.|!)?$",
            r"^(?:i do|i am|i have|i did)(?:\.|!)?$",
            r"^(?:that'?s correct|that'?s right)(?:\.|!)?$",
        ]
        
        # No patterns
        self.no_patterns = [
            r"^(?:no|nope|not|negative|false|incorrect|wrong)(?:\.|!)?$",
            r"^(?:i don'?t|i do not|i haven'?t|i have not|i'?m not)(?:\.|!)?$",
            r"^(?:that'?s incorrect|that'?s wrong)(?:\.|!)?$",
        ]
        
        # Number patterns
        self.number_patterns = [
            (r"^(\d+(?:\.\d+)?)$", 1),  # Just a number
            (r"(?:it'?s |about |around |approximately )?(\d+(?:\.\d+)?)", 1),
            (r"(\d+(?:\.\d+)?)\s*(?:times?|flares?|attacks?)", 1),
        ]
    
    def parse_age(self, text: str) -> Optional[int]:
        """Extract age from text"""
        text = text.lower().strip()
        
        for pattern, group in self.age_patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    age = int(match.group(group))
                    if 0 < age < 150:  # Reasonable age range
                        return age
                except ValueError:
                    continue
        
        # Handle written numbers
        written_numbers = {
            "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
            "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90
        }
        
        for word, value in written_numbers.items():
            if word in text:
                # Handle "fifty-two", "sixty five", etc.
                match = re.search(rf"{word}[\s-]?(\w+)?", text)
                if match and match.group(1):
                    ones = {
                        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                        "six": 6, "seven": 7, "eight": 8, "nine": 9
                    }
                    ones_word = match.group(1)
                    if ones_word in ones:
                        return value + ones[ones_word]
                return value
        
        return None
    
    def parse_yes_no(self, text: str) -> Optional[bool]:
        """Parse yes/no response with improved typo handling"""
        text = text.lower().strip()
        
        # Handle common typos and multiple responses
        text = text.replace("qm", "am").replace("yea", "yes").replace("yeh", "yes")
        
        # Handle multiple yes responses like "yes, yes i am"
        if re.search(r'\byes\b.*\byes\b', text) or re.search(r'\byeah\b.*\byeah\b', text):
            return True
        
        # Check yes patterns (updated to handle multiple responses)
        enhanced_yes_patterns = self.yes_patterns + [
            r"^yes,?\s*yes\b",  # "yes, yes"
            r"^yeah,?\s*yeah\b",  # "yeah, yeah"
            r"^yes.*i\s+am\b",  # "yes i am"
            r"^yeah.*i\s+am\b",  # "yeah i am"
        ]
        
        for pattern in enhanced_yes_patterns:
            if re.match(pattern, text):
                return True
        
        # Check no patterns
        for pattern in self.no_patterns:
            if re.match(pattern, text):
                return False
        
        # Check for yes/no with additional context
        if text.startswith(("yes,", "yes ", "yeah,", "yeah ")):
            return True
        if text.startswith(("no,", "no ", "nope,", "nope ")):
            return False
        
        return None
    
    def parse_number(self, text: str) -> Optional[float]:
        """Extract a number from text"""
        text = text.lower().strip()
        
        for pattern, group in self.number_patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return float(match.group(group))
                except ValueError:
                    continue
        
        # Handle written numbers
        written = {
            "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12
        }
        
        for word, value in written.items():
            if word in text:
                return float(value)
        
        return None
    
    def parse_condition(self, text: str) -> Optional[str]:
        """Extract medical condition from text"""
        text = text.lower().strip()
        
        # First, check if the text itself might be a condition using the registry
        from core.services.condition_registry import condition_registry
        
        if condition_registry.is_medical_condition(text):
            return condition_registry.normalize_condition(text)
        
        # Common symptom patterns that might indicate conditions
        symptom_patterns = {
            r"foot.*(hurt|pain|sore|ache)": "gout",
            r"toe.*(hurt|pain|sore|ache)": "gout",
            r"joint.*(hurt|pain|sore|ache)": "arthritis",
            r"(hurt|pain|sore|ache).*foot": "gout",
            r"(hurt|pain|sore|ache).*toe": "gout",
            r"head.*(hurt|pain|ache)": "migraine",
            r"(hurt|pain|ache).*head": "migraine",
        }
        
        # First, check for symptom descriptions
        for pattern, condition in symptom_patterns.items():
            if re.search(pattern, text):
                return condition
        
        # Common patterns for stating conditions
        patterns = [
            r"i have (.+?)(?:\.|$)",
            r"diagnosed with (.+?)(?:\.|$)",
            r"suffering from (.+?)(?:\.|$)",
            r"(.+) trial",  # Extract condition from "gout trial" etc.
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                extracted = match.group(1).strip()
                # Clean up common phrases
                extracted = re.sub(r"^(a |an |the )", "", extracted)
                
                # Check if it's a medical condition
                if condition_registry.is_medical_condition(extracted):
                    return condition_registry.normalize_condition(extracted)
        
        # If we can't identify a specific condition, return None
        # This forces the chatbot to ask for clarification
        return None
    
    def parse_location(self, text: str) -> Optional[str]:
        """Extract location from text"""
        text = text.strip()
        
        # Remove question words and trial-related phrases first
        cleaned = re.sub(r"^(what about|how about|any)\s+", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"\?", "", cleaned)
        cleaned = cleaned.strip()
        
        # Extract location from "X trial in Y" patterns first
        trial_pattern = r"(?:trial|trials|study|studies)\s+in\s+([a-zA-Z\s]+?)(?:\s+please)?(?:\.|,|\?|$)"
        match = re.search(trial_pattern, cleaned, re.IGNORECASE)
        if match:
            location = match.group(1).strip()
            # Remove trailing words like "please"
            location = re.sub(r"\s+(please|thanks|thank you)$", "", location, flags=re.IGNORECASE)
            return location
        
        # Now remove trial-related words for other patterns
        text = re.sub(r"(?:trial|trials|study|studies).*$", "", cleaned, flags=re.IGNORECASE)
        text = text.strip()
        
        # Common patterns for stating location
        patterns = [
            r"(?:i'?m in|i live in|i am in|from) ([a-zA-Z\s]+?)(?:\.|,|\?|$)",
            r"\bin ([a-zA-Z\s]+?)(?:\.|,|\?|$)",  # Match "in [location]"
            r"^([a-zA-Z][a-zA-Z\s]+?)$",  # Just the location name (must start with letter)
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                location = match.group(1).strip()
                
                # Clean up location
                location = re.sub(r"\b(for|in)\b", "", location, flags=re.IGNORECASE)
                location = location.strip()
                
                # Normalize capitalization - capitalize first letter of each word
                location = location.title()
                
                # Validate it looks like a location (not too long, contains letters)
                if location and len(location) < 50 and re.search(r"[a-zA-Z]", location):
                    return location
        
        # If the cleaned text is short and looks like a location name, return it
        if text and len(text) < 30 and re.match(r"^[a-zA-Z][a-zA-Z\s]*$", text):
            return text.title()
        
        return None
    
    def parse_medication_list(self, text: str) -> Optional[list]:
        """Extract list of medications from text"""
        text = text.lower().strip()
        
        # Handle "I take X and Y" patterns
        if "i take" in text or "i'm taking" in text or "i am taking" in text:
            text = re.sub(r"i'?m? tak(?:e|ing) ", "", text)
        
        # Split by common delimiters
        meds = re.split(r",|\sand\s|\splus\s|;", text)
        meds = [med.strip() for med in meds if med.strip()]
        
        return meds if meds else None
    
    def parse(self, text: str, expected_type: str) -> Optional[Any]:
        """Parse text based on expected type"""
        if expected_type == "age":
            return self.parse_age(text)
        elif expected_type == "yes_no":
            return self.parse_yes_no(text)
        elif expected_type == "number":
            return self.parse_number(text)
        elif expected_type == "condition":
            return self.parse_condition(text)
        elif expected_type == "location":
            return self.parse_location(text)
        elif expected_type == "medications":
            return self.parse_medication_list(text)
        else:
            return text