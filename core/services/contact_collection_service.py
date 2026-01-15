"""
Contact Collection Service

Handles the collection of patient contact information after prescreening completion.
Integrates with the existing conversation management system and database architecture.
"""

import re
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from core.database import db

logger = logging.getLogger(__name__)


class ContactCollectionService:
    """Service for collecting and managing patient contact information"""
    
    # Contact collection states - following existing conversation state pattern
    STATES = {
        'PRESCREENING_COMPLETE': 'prescreening_complete',
        'AWAITING_CONTACT_CONSENT': 'awaiting_contact_consent',
        'COLLECTING_FIRST_NAME': 'collecting_first_name',
        'COLLECTING_LAST_NAME': 'collecting_last_name',
        'COLLECTING_PHONE': 'collecting_phone',
        'COLLECTING_EMAIL': 'collecting_email',
        'VALIDATING_CONTACT': 'validating_contact',
        'CONTACT_COMPLETE': 'contact_complete',
        'CONTACT_DECLINED': 'contact_declined'
    }
    
    def __init__(self):
        pass
    
    def should_start_contact_collection(self, context_metadata: Dict[str, Any]) -> bool:
        """
        Check if contact collection should be initiated based on prescreening completion.
        
        Args:
            context_metadata: Metadata from conversation context
            
        Returns:
            True if contact collection should start
        """
        return (
            context_metadata.get("prescreening_complete", False) and
            not context_metadata.get("contact_collection_initiated", False)
        )
    
    def get_contact_invitation_message(self, eligibility_status: str, condition: str = None) -> str:
        """
        Generate the initial contact collection invitation message based on eligibility.
        
        Args:
            eligibility_status: 'eligible', 'ineligible', or 'pending'
            condition: Medical condition (used for privacy-safe reference)
            
        Returns:
            Formatted invitation message
        """
        # Use condition-based reference instead of protocol name for privacy
        trial_text = f" regarding the {condition.lower()} trial" if condition else ""

        # Customize message based on eligibility status
        if eligibility_status == "ineligible":
            return f"""While you may not meet all criteria for this specific trial, our research team would still like to review your responses.

They will contact you within 1-2 business days if:
• Other suitable trials become available that you may qualify for
• Your specific situation warrants further evaluation
• Trial criteria are updated or modified

Would you like to provide your contact information for potential follow-up?

Please respond with 'yes' to provide your details, or 'no' if you prefer not to be contacted."""
        else:
            # For eligible or pending status
            return f"""Would you like our research team to contact you{trial_text}?

Our team will call within 1-2 business days to review your results and discuss next steps. Your information will be kept secure.

Please respond with 'yes' to provide your contact information, or 'no' if you prefer not to be contacted."""
    
    def process_consent_response(self, message: str) -> Tuple[bool, str, str]:
        """
        Process user's consent response to contact collection.

        Args:
            message: User's response message

        Returns:
            Tuple of (consent_given, response_message, next_state)
            Note: If user provides a name directly, returns (True, confirmation_message, COLLECTING_LAST_NAME)
                  and the name should be extracted from the original message
        """
        message_lower = message.lower().strip()

        # Positive responses
        positive_responses = [
            "yes", "y", "sure", "ok", "okay", "fine", "absolutely",
            "definitely", "of course", "please", "go ahead", "proceed"
        ]

        # Negative responses
        negative_responses = [
            "no", "n", "nope", "not interested", "don't want", "no thanks",
            "thanks but no", "not now", "maybe later", "decline"
        ]

        # Check for explicit yes/no responses first
        if any(pos in message_lower for pos in positive_responses):
            return True, "Thank you! I'll collect a few pieces of information. First, what is your first name?", self.STATES['COLLECTING_FIRST_NAME']

        elif any(neg in message_lower for neg in negative_responses):
            return False, "I understand. Thank you for your time and for completing the prescreening. If you change your mind in the future, please feel free to reach out to us again.", self.STATES['CONTACT_DECLINED']

        else:
            # Check if user provided a name directly (implicit consent)
            # This handles cases where users provide their name without saying "yes" first
            potential_name = self._extract_name(message)
            if potential_name and len(potential_name) >= 2:
                # User provided their name directly - treat as implicit consent
                # Return special state to indicate we already have the first name
                return True, f"Thank you, {potential_name}! Now, what is your last name?", self.STATES['COLLECTING_LAST_NAME']

            # Unclear response - ask for clarification
            return None, "I didn't understand your response. Please respond with 'yes' if you'd like to provide your contact information, or 'no' if you prefer not to be contacted.", self.STATES['AWAITING_CONTACT_CONSENT']
    
    def collect_first_name(self, message: str) -> Tuple[bool, str, str]:
        """
        Collect and validate first name.
        Handles both single names and full names (auto-splits).

        Args:
            message: User's response containing first name (or full name)

        Returns:
            Tuple of (is_valid, response_message, next_state)
        """
        extracted_name = self._extract_name(message)

        if extracted_name and len(extracted_name) >= 2:
            # Check if user provided full name (contains space)
            if ' ' in extracted_name:
                # Split into first and last name
                parts = extracted_name.split(maxsplit=1)
                first_name = parts[0]
                # Store the last name portion for later retrieval
                # Note: This will be used in the confirmation message
                return True, f"Thank you, {first_name}! Now, what is your last name?", self.STATES['COLLECTING_LAST_NAME']
            else:
                # Single name provided - ask for last name
                return True, f"Thank you, {extracted_name}! Now, what is your last name?", self.STATES['COLLECTING_LAST_NAME']
        else:
            return False, "Please provide your first name so I can address you properly.", self.STATES['COLLECTING_FIRST_NAME']
    
    def collect_last_name(self, message: str) -> Tuple[bool, str, str]:
        """
        Collect and validate last name.
        
        Args:
            message: User's response containing last name
            
        Returns:
            Tuple of (is_valid, response_message, next_state)
        """
        last_name = self._extract_name(message)
        
        if last_name and len(last_name) >= 2:
            return True, "Thank you! Next, please provide your phone number where our team can reach you.", self.STATES['COLLECTING_PHONE']
        else:
            return False, "Please provide your last name.", self.STATES['COLLECTING_LAST_NAME']
    
    def collect_phone_number(self, message: str) -> Tuple[bool, str, str]:
        """
        Collect and validate phone number.
        
        Args:
            message: User's response containing phone number
            
        Returns:
            Tuple of (is_valid, response_message, next_state)
        """
        phone = self._extract_phone_number(message)
        
        if phone:
            return True, "Perfect! Finally, please provide your email address.", self.STATES['COLLECTING_EMAIL']
        else:
            return False, "Please provide a valid phone number (for example: 555-123-4567 or (555) 123-4567).", self.STATES['COLLECTING_PHONE']
    
    def collect_email(self, message: str) -> Tuple[bool, str, str]:
        """
        Collect and validate email address.
        
        Args:
            message: User's response containing email address
            
        Returns:
            Tuple of (is_valid, response_message, next_state)
        """
        email = self._extract_email(message)
        
        if email:
            return True, "Thank you! Let me confirm your contact information with you.", self.STATES['VALIDATING_CONTACT']
        else:
            return False, "Please provide a valid email address (for example: yourname@email.com).", self.STATES['COLLECTING_EMAIL']
    
    def generate_confirmation_message(self, contact_data: Dict[str, Any], eligibility_status: str) -> str:
        """
        Generate confirmation message with collected contact information.
        
        Args:
            contact_data: Dictionary containing collected contact information
            eligibility_status: Patient's eligibility status
            
        Returns:
            Formatted confirmation message
        """
        confirmation = f"""Perfect! Here's the contact information I've collected:

• Name: {contact_data.get('first_name', '')} {contact_data.get('last_name', '')}
• Phone: {contact_data.get('phone_number', '')}
• Email: {contact_data.get('email', '')}

Is this information correct? Please respond with 'yes' to confirm, or 'no' if you need to make any corrections."""
        
        return confirmation
    
    def generate_completion_message(self, eligibility_status: str, condition: str = None) -> str:
        """
        Generate final completion message after contact collection.
        
        Args:
            eligibility_status: Patient's eligibility status
            condition: Medical condition (for privacy-safe trial reference)
            
        Returns:
            Formatted completion message
        """
        # Create condition-based trial reference for privacy - never expose protocol name
        if condition:
            trial_reference = f"the {condition.lower()} trial"
        else:
            trial_reference = "the clinical trial"
        
        trial_text = f" regarding {trial_reference}"
        
        if eligibility_status == "eligible":
            return f"""Excellent! Your contact information has been saved securely. Our clinical research team will contact you within 1-2 business days{trial_text} to schedule a screening visit and discuss the study in more detail.

During this call, they'll:
• Answer any questions you have about the study
• Schedule a convenient time for your screening visit
• Provide you with detailed study information

Thank you for your interest in clinical research! We look forward to speaking with you soon."""
        
        elif eligibility_status == "ineligible":
            return f"""Thank you! Your contact information has been saved securely. Our patient recruitment team will contact you within 1-2 business days to discuss other research opportunities that might be suitable for your condition.

They'll review our current studies and let you know about any that might be a good match for your situation.

Thank you for your interest in clinical research! We appreciate your time and look forward to potentially connecting you with appropriate study opportunities."""
        
        else:  # pending or potentially_eligible
            return f"""Perfect! Your contact information has been saved securely. Our clinical research team will contact you within 1-2 business days{trial_text} to discuss your responses in more detail and determine your eligibility.

During this call, they'll:
• Review your prescreening responses
• Ask any additional questions needed
• Explain the study requirements in detail
• Determine if the study is a good fit for you

Thank you for your interest in clinical research! We look forward to speaking with you soon."""
    
    def save_contact_information(
        self, 
        session_id: str, 
        contact_data: Dict[str, Any], 
        eligibility_status: str,
        prescreening_session_id: Optional[int] = None
    ) -> bool:
        """
        Save collected contact information to the database.
        
        Args:
            session_id: Conversation session ID
            contact_data: Dictionary containing contact information
            eligibility_status: Patient's eligibility status
            prescreening_session_id: Optional prescreening session ID
            
        Returns:
            True if saved successfully, False otherwise
        """
        try:
            db.execute_update("""
                INSERT INTO patient_contact_info 
                (session_id, first_name, last_name, 
                 phone_number, email, eligibility_status, contact_preference, consent_timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                session_id,
                contact_data.get('first_name'),
                contact_data.get('last_name'),
                contact_data.get('phone_number'),
                contact_data.get('email'),
                eligibility_status,
                contact_data.get('contact_preference', 'study_participation'),
                datetime.utcnow()
            ))
            
            logger.info(f"Contact information saved for session {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving contact information for session {session_id}: {str(e)}")
            return False
    
    def get_contact_collection_state(self, context_data: Dict[str, Any]) -> str:
        """
        Get the current contact collection state from conversation context.
        
        Args:
            context_data: Context data from conversation
            
        Returns:
            Current contact collection state
        """
        return context_data.get('contact_collection_state', self.STATES['PRESCREENING_COMPLETE'])
    
    def update_contact_collection_state(self, context_data: Dict[str, Any], new_state: str, **additional_data) -> Dict[str, Any]:
        """
        Update contact collection state in conversation context.
        
        Args:
            context_data: Current context data
            new_state: New contact collection state
            **additional_data: Additional data to store in context
            
        Returns:
            Updated context data
        """
        context_data['contact_collection_state'] = new_state
        
        # Initialize contact_partial_data if not exists
        if 'contact_partial_data' not in context_data:
            context_data['contact_partial_data'] = {}
        
        # Update with any additional data
        for key, value in additional_data.items():
            context_data[key] = value
        
        return context_data
    
    # Private helper methods
    
    def _extract_name(self, message: str) -> Optional[str]:
        """Extract and validate a name from message"""
        # Remove common prefixes and clean the input
        cleaned = message.strip()
        
        # Remove "my name is", "I'm", etc.
        prefixes = ["my name is", "i'm", "i am", "it's", "its", "the name is", "call me"]
        for prefix in prefixes:
            if cleaned.lower().startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
        
        # Basic name validation - letters, spaces, hyphens, apostrophes
        name_pattern = r"^[a-zA-Z\s\-']{2,30}$"
        if re.match(name_pattern, cleaned):
            return cleaned.title()  # Capitalize properly
        
        return None
    
    def _extract_phone_number(self, message: str) -> Optional[str]:
        """Extract and validate phone number from message"""
        # Remove non-digit characters for validation
        digits_only = re.sub(r'\D', '', message)
        
        # US phone number validation (10 digits, optionally starting with 1)
        if len(digits_only) == 10:
            # Format as (XXX) XXX-XXXX
            return f"({digits_only[:3]}) {digits_only[3:6]}-{digits_only[6:]}"
        elif len(digits_only) == 11 and digits_only.startswith('1'):
            # Remove leading 1 and format
            digits_only = digits_only[1:]
            return f"({digits_only[:3]}) {digits_only[3:6]}-{digits_only[6:]}"
        
        return None
    
    def _extract_email(self, message: str) -> Optional[str]:
        """Extract and validate email address from message"""
        # Basic email regex
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        match = re.search(email_pattern, message)
        
        if match:
            email = match.group().lower()
            # Additional validation
            if len(email) <= 255 and '@' in email and '.' in email.split('@')[-1]:
                return email
        
        return None


# Singleton instance for easy access
contact_collection_service = ContactCollectionService()