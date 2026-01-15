"""Gemini-powered response generator for natural conversations"""
import os
import logging
from typing import Dict, Any, Optional, List
from core.database import db
from core.services.gemini_service import gemini_service

logger = logging.getLogger(__name__)


class GeminiResponder:
    """Generate natural language responses using Gemini"""
    
    def __init__(self):
        """Initialize Gemini service"""
        self.gemini = gemini_service

    async def generate_response(
        self,
        message: str,
        intent: Dict[str, Any],
        context: Dict[str, Any],
        trials_data: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """Generate contextual response using Gemini"""
        
        # Build system prompt based on intent
        system_prompt = self._build_system_prompt(intent, context)
        
        # Build user prompt with context
        user_prompt = self._build_user_prompt(message, intent, context, trials_data)
        
        # Combine system and user prompts for Gemini
        full_prompt = f"{system_prompt}\n\nUser Query: {user_prompt}"
        
        try:
            response = await self.gemini.generate_text(full_prompt, max_tokens=500)
            return response
            
        except Exception as e:
            logger.error(f"Gemini API error: {str(e)}")
            return self.fallback_response(intent, context, trials_data)
    
    def _build_system_prompt(self, intent: Dict[str, Any], context: Dict[str, Any]) -> str:
        """Build system prompt based on intent type"""
        
        base_prompt = """You are a helpful clinical trials assistant. Your role is to help users find clinical trials and check their eligibility. Be conversational, empathetic, and informative.

Key guidelines:
- Be warm and supportive, understanding that users may be dealing with health challenges
- Provide clear, accurate information about clinical trials
- Never provide medical advice or diagnoses
- Always emphasize that eligibility determinations are preliminary
- Encourage users to contact trial sites for definitive screening
- When users first engage, ask what condition they're interested in OR where they're located
- Accept either condition-based or location-based searches as starting points
"""
        
        intent_type = intent.get("type", "general")
        
        if intent_type == "trial_search":
            return base_prompt + """
For trial searches:
- List available trials clearly with key details
- Mention the location and principal investigator
- Ask if they'd like to check eligibility for any specific trial
- Be encouraging about the availability of options
"""
        
        elif intent_type == "personal_condition":
            return base_prompt + """
The user has mentioned a medical condition. Your response should:
- Acknowledge their condition with empathy
- Mention that trials are available for their condition
- Offer to help check eligibility
- Be supportive and encouraging
"""
        
        elif context.get("prescreening_active"):
            return base_prompt + """
You are currently in a prescreening conversation. The user is answering eligibility questions.
- Acknowledge their answer appropriately
- The next question will be asked by the system
- Be encouraging and supportive
- Keep responses brief during prescreening
"""
        
        return base_prompt
    
    def _build_user_prompt(
        self, 
        message: str, 
        intent: Dict[str, Any],
        context: Dict[str, Any],
        trials_data: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """Build user prompt with relevant context"""
        
        prompt = f"User message: {message}\n\n"
        
        # Add intent information
        if intent.get("entities"):
            entities = intent["entities"]
            if entities.get("condition"):
                prompt += f"Detected condition: {entities['condition']}\n"
            if entities.get("location"):
                prompt += f"Detected location: {entities['location']}\n"
        
        # Add trials data if available
        if trials_data:
            prompt += f"\nAvailable trials ({len(trials_data)} found):\n"
            for trial in trials_data[:3]:  # Limit to 3 trials
                prompt += f"- {trial['trial_name']} (Protocol: {trial.get('protocol_number', 'N/A')})\n"
                if trial.get('investigator_name'):
                    prompt += f"  PI: {trial['investigator_name']} in {trial.get('site_location', 'N/A')}\n"
        
        # Add context if relevant
        if context.get("focus_condition"):
            prompt += f"\nUser is interested in: {context['focus_condition']}\n"
        
        if context.get("prescreening_active"):
            prompt += "\nNote: User is currently in prescreening flow. Keep response brief and supportive.\n"
        
        return prompt
    
    def fallback_response(self, intent: Dict[str, Any], context: Dict[str, Any], trials_data: Optional[List[Dict[str, Any]]] = None) -> str:
        """Fallback response if Gemini fails"""
        
        intent_type = intent.get("type", "general")
        
        if intent_type == "trial_search" and trials_data:
            response = f"I found {len(trials_data)} clinical trials that might be relevant:\n\n"
            for i, trial in enumerate(trials_data[:3], 1):
                response += f"{i}. {trial['trial_name']}\n"
            response += "\nWould you like to check your eligibility for any of these trials?"
            return response
        
        elif intent_type == "personal_condition":
            condition = intent.get("entities", {}).get("condition", "your condition")
            return f"I understand you have {condition}. I can help you find relevant clinical trials and check if you might be eligible. Would you like me to search for trials?"
        
        elif intent_type == "eligibility":
            return "I'd be happy to help you check your eligibility for clinical trials. Let me ask you a few questions to better understand your situation."
        
        else:
            return "I'm here to help you find clinical trials and check eligibility. What condition are you interested in, or where are you located?"


