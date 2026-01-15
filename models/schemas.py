"""Data models for the prescreening chatbot"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime


class ConversationState(str, Enum):
    """Conversation state machine states"""
    IDLE = "idle"
    PRESCREENING_ACTIVE = "prescreening_active"
    AWAITING_AGE = "awaiting_age"
    AWAITING_DIAGNOSIS = "awaiting_diagnosis"
    AWAITING_MEDICATIONS = "awaiting_medications"
    AWAITING_FLARES = "awaiting_flares"
    AWAITING_LOCATION = "awaiting_location"
    AWAITING_CONDITION = "awaiting_condition"
    AWAITING_SEARCH_CHOICE = "awaiting_search_choice"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    TRIALS_SHOWN = "trials_shown"
    COMPLETED = "completed"

    # SMS Rescheduling states
    RESCHEDULING_INITIATED = "rescheduling_initiated"
    RESCHEDULING_AWAITING_CONFIRMATION = "rescheduling_awaiting_confirmation"
    RESCHEDULING_AWAITING_AVAILABILITY = "rescheduling_awaiting_availability"
    RESCHEDULING_AWAITING_SELECTION = "rescheduling_awaiting_selection"
    RESCHEDULING_CONFIRMING = "rescheduling_confirming"
    RESCHEDULING_COMPLETED = "rescheduling_completed"
    RESCHEDULING_FAILED = "rescheduling_failed"


class PrescreeningSession(BaseModel):
    """Tracks a prescreening conversation"""
    session_id: str
    trial_id: Optional[int] = None
    trial_name: Optional[str] = None
    condition: Optional[str] = None
    location: Optional[str] = None
    current_state: ConversationState = ConversationState.IDLE
    current_question_key: Optional[str] = None
    collected_data: Dict[str, Any] = Field(default_factory=dict)
    remaining_questions: List[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EligibilityResult(BaseModel):
    """Results of eligibility evaluation"""
    eligible: bool
    confidence: float  # 0.0 to 1.0
    criteria_met: List[str] = Field(default_factory=list)
    criteria_not_met: List[str] = Field(default_factory=list)
    criteria_unknown: List[str] = Field(default_factory=list)
    recommendation: str
    next_steps: str


class PrescreeningQuestion(BaseModel):
    """A prescreening question template"""
    key: str
    text: str
    type: str  # age, yes_no, number, text, condition, location
    validation_pattern: Optional[str] = None
    clarification_text: Optional[str] = None
    required: bool = True


class ChatMessage(BaseModel):
    """A chat message"""
    role: str  # user or assistant
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: Optional[Dict[str, Any]] = None