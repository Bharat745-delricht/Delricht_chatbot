"""
Handler for trial information requests.

This handler processes requests for detailed information about specific trials,
managing trial lookups and information presentation.
"""

import logging
from typing import Dict, Any, List, Optional

from core.conversation.handlers.base import BaseHandler, HandlerResponse
from core.conversation.understanding import IntentType, DetectedIntent, ExtractedEntity, EntityType
from core.conversation.context import ConversationContext
from core.conversation.orchestration import ConversationStateManager
from core.services.trial_search import trial_search
from models.schemas import ConversationState

logger = logging.getLogger(__name__)


class TrialInfoHandler(BaseHandler):
    """
    Handles trial information requests.
    
    This handler manages:
    - Requests for specific trial details
    - Trial information presentation
    - Follow-up questions about trials
    - Context management for trial discussions
    """
    
    def can_handle(self, intent: DetectedIntent, context: ConversationContext) -> bool:
        """Check if this handler can process the intent"""
        return intent.intent_type == IntentType.TRIAL_INFO_REQUEST
    
    def handle(self, intent: DetectedIntent, entities: Dict[EntityType, ExtractedEntity],
              context: ConversationContext, state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle trial information request"""
        
        # Extract condition and location
        condition = self.get_condition(entities, context)
        location = self.get_location(entities, context)
        
        # Check if we need more information
        if not condition:
            return self._handle_missing_condition(location, context, state_manager)
        
        if not location:
            return self._handle_missing_location(condition, context, state_manager)
        
        # Search for the trial
        return self._handle_trial_lookup(condition, location, context, state_manager)
    
    
    def _handle_missing_condition(self, location: Optional[str],
                                context: ConversationContext,
                                state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle case where condition is missing"""
        
        if context.last_shown_trials:
            # User might be asking about a shown trial
            message = "Which trial would you like to know more about? "
            message += "You can tell me the number from the list or the condition name."
            
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "awaiting_trial_specification": True,
                        "has_location": location is not None,
                        "location": location
                    }
                }
            ]
            
            return HandlerResponse(
                success=True,
                message=message,
                metadata={"context": "shown_trials"},
                actions=actions
            )
        
        # Ask for condition
        message = "I'd be happy to provide information about clinical trials. "
        message += "Which medical condition are you interested in?"
        
        # Transition to awaiting condition
        state_manager.transition_to(
            ConversationState.AWAITING_CONDITION,
            reason="Need condition for trial info"
        )
        
        actions = [
            {
                "type": "update_context",
                "data": {
                    "awaiting_condition": True,
                    "trial_info_intent": True,
                    "location": location
                }
            }
        ]
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={"needs_condition": True},
            next_state=ConversationState.AWAITING_CONDITION.value,
            actions=actions
        )
    
    def _handle_missing_location(self, condition: str,
                               context: ConversationContext,
                               state_manager: ConversationStateManager) -> HandlerResponse:
        """Handle case where location is missing"""
        
        message = f"I can provide information about {condition} trials. "
        message += "Which location are you interested in?"
        
        # Transition to awaiting location
        state_manager.transition_to(
            ConversationState.AWAITING_LOCATION,
            reason="Need location for trial info"
        )
        
        actions = [
            {
                "type": "update_context",
                "data": {
                    "awaiting_location": True,
                    "trial_info_intent": True,
                    "focus_condition": condition
                }
            }
        ]
        
        return HandlerResponse(
            success=True,
            message=message,
            metadata={
                "needs_location": True,
                "condition": condition
            },
            next_state=ConversationState.AWAITING_LOCATION.value,
            actions=actions
        )
    
    def _handle_trial_lookup(self, condition: str, location: str,
                           context: ConversationContext,
                           state_manager: ConversationStateManager) -> HandlerResponse:
        """Look up and present trial information"""
        
        try:
            # Search for trials
            all_trials = trial_search.get_trials_by_location(location)
            
            # Filter by condition
            trials = []
            if all_trials:
                for trial in all_trials:
                    if condition.lower() in trial.get("conditions", "").lower():
                        trials.append(trial)
            
            if not trials:
                message = f"I couldn't find any {condition} trials in {location}. "
                message += "Would you like me to search in nearby locations or for related conditions?"
                
                return HandlerResponse(
                    success=True,
                    message=message,
                    metadata={
                        "no_trials_found": True,
                        "condition": condition,
                        "location": location
                    }
                )
            
            # Use the first trial (we don't have a separate get_trial_details method)
            trial = trials[0]
            trial_details = trial
            
            # Format detailed information
            message = self._format_trial_details(trial_details, condition, location)
            
            # Update context
            actions = [
                {
                    "type": "update_context",
                    "data": {
                        "focus_condition": condition,
                        "focus_location": location,
                        "last_shown_trial": {
                            "id": trial.get("id"),
                            "name": trial.get("trial_name"),
                            "condition": condition,
                            "location": location
                        },
                        "just_showed_trial_info": True
                    }
                },
                {
                    "type": "log_trial_view",
                    "data": {
                        "trial_id": trial.get("id"),
                        "trial_name": trial.get("trial_name"),
                        "condition": condition,
                        "location": location
                    }
                }
            ]
            
            # Add more trials info if multiple found
            if len(trials) > 1:
                message += f"\n\nI also found {len(trials) - 1} other {condition} trial{'s' if len(trials) > 2 else ''} in {location}. "
                message += "Would you like to see those as well?"
                
                actions[0]["data"]["other_trials"] = [
                    {"id": t.get("id"), "name": t.get("trial_name")}
                    for t in trials[1:4]  # Store up to 3 more
                ]
            
            return HandlerResponse(
                success=True,
                message=message,
                metadata={
                    "trial_count": len(trials),
                    "showing_trial_id": trial.get("id"),
                    "condition": condition,
                    "location": location
                },
                actions=actions
            )
            
        except Exception as e:
            logger.error(f"Error looking up trial info: {str(e)}")
            return HandlerResponse(
                success=False,
                message="I encountered an error while looking up trial information. Please try again.",
                error=str(e)
            )
    
    def _format_trial_details(self, trial: Dict[str, Any], 
                            condition: str, location: str) -> str:
        """Format detailed trial information"""
        name = trial.get("trial_name", f"{condition} Clinical Trial")
        
        message = f"## {name}\n\n"
        
        # Brief summary
        if trial.get("brief_summary"):
            message += f"**Overview:** {trial['brief_summary']}\n\n"
        
        # Phase and status
        if trial.get("phase"):
            message += f"**Phase:** {trial['phase']}\n"
        if trial.get("status"):
            message += f"**Status:** {trial['status']}\n"
        
        message += "\n"
        
        # Eligibility criteria
        if trial.get("eligibility_criteria"):
            message += "**Key Eligibility Criteria:**\n"
            criteria = trial["eligibility_criteria"]
            
            # Age range
            if criteria.get("min_age") or criteria.get("max_age"):
                age_range = []
                if criteria.get("min_age"):
                    age_range.append(f"at least {criteria['min_age']} years old")
                if criteria.get("max_age"):
                    age_range.append(f"no older than {criteria['max_age']} years")
                message += f"- Age: {' and '.join(age_range)}\n"
            
            # Gender
            if criteria.get("gender") and criteria["gender"] != "All":
                message += f"- Gender: {criteria['gender']}\n"
            
            # Key inclusion criteria
            if criteria.get("inclusion_criteria"):
                message += "- Must have: "
                inclusions = criteria["inclusion_criteria"][:3]  # Show top 3
                message += ", ".join(inclusions)
                if len(criteria["inclusion_criteria"]) > 3:
                    message += f" (and {len(criteria['inclusion_criteria']) - 3} more)"
                message += "\n"
            
            # Key exclusion criteria
            if criteria.get("exclusion_criteria"):
                message += "- Cannot have: "
                exclusions = criteria["exclusion_criteria"][:2]  # Show top 2
                message += ", ".join(exclusions)
                if len(criteria["exclusion_criteria"]) > 2:
                    message += f" (and {len(criteria['exclusion_criteria']) - 2} more)"
                message += "\n"
            
            message += "\n"
        
        # Study details
        if trial.get("study_type"):
            message += f"**Study Type:** {trial['study_type']}\n"
        
        if trial.get("primary_outcome"):
            message += f"**Primary Goal:** {trial['primary_outcome']}\n"
        
        if trial.get("estimated_enrollment"):
            message += f"**Participants Needed:** {trial['estimated_enrollment']}\n"
        
        if trial.get("study_duration"):
            message += f"**Study Duration:** {trial['study_duration']}\n"
        
        message += "\n"
        
        # Location details
        if trial.get("locations"):
            location_info = trial["locations"][0]  # Primary location
            message += f"**Location:** {location_info.get('facility', location)}\n"
            if location_info.get("city") and location_info.get("state"):
                message += f"**Address:** {location_info['city']}, {location_info['state']}\n"
        else:
            message += f"**Location:** {location}\n"
        
        # Principal investigator
        if trial.get("principal_investigator"):
            message += f"**Principal Investigator:** {trial['principal_investigator']}\n"
        
        # Sponsor
        if trial.get("sponsor"):
            message += f"**Sponsor:** {trial['sponsor']}\n"
        
        message += "\n"
        
        # Call to action
        message += "Would you like to check if you're eligible for this trial?"
        
        return message