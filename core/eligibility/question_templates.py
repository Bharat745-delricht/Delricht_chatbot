"""Question templates for prescreening conversations"""
from typing import Dict, List
from models.schemas import PrescreeningQuestion


class QuestionTemplates:
    """Manages prescreening question templates"""
    
    def __init__(self):
        self.questions = self._initialize_questions()
        self.condition_specific_questions = self._initialize_condition_questions()
    
    def _initialize_questions(self) -> Dict[str, PrescreeningQuestion]:
        """Initialize common prescreening questions"""
        return {
            "age": PrescreeningQuestion(
                key="age",
                text="First, could you tell me your age?",
                type="age",
                validation_pattern=r"\d{1,3}",
                clarification_text="I need a specific age to check the eligibility criteria. Could you tell me your exact age in years?",
                required=True
            ),
            
            "diagnosis": PrescreeningQuestion(
                key="diagnosis",
                text="Do you currently have a diagnosis of {condition} from a healthcare provider?",
                type="yes_no",
                clarification_text="Have you been formally diagnosed with {condition} by a doctor?",
                required=True
            ),
            
            "medications": PrescreeningQuestion(
                key="medications",
                text="Are you currently taking any medications for your {condition}?",
                type="yes_no",
                clarification_text="Are you on any prescription medications to manage your {condition}?",
                required=True
            ),
            
            "medication_list": PrescreeningQuestion(
                key="medication_list",
                text="What medications are you currently taking for your {condition}?",
                type="medications",
                clarification_text="Could you list the names of your current medications?",
                required=False
            ),
            
            "location": PrescreeningQuestion(
                key="location",
                text="What city or state are you located in?",
                type="location",
                clarification_text="Could you tell me your location so I can find trials near you?",
                required=True
            ),
            
            "condition": PrescreeningQuestion(
                key="condition",
                text="What medical condition are you interested in finding trials for?",
                type="condition",
                clarification_text="Which health condition would you like to explore clinical trials for?",
                required=True
            ),
            
            "other_conditions": PrescreeningQuestion(
                key="other_conditions",
                text="Do you have any other major health conditions, particularly heart, kidney, or liver disease?",
                type="yes_no",
                clarification_text="Do you have any other significant medical conditions I should know about?",
                required=True
            ),
            
            "pregnancy": PrescreeningQuestion(
                key="pregnancy",
                text="Are you currently pregnant or nursing?",
                type="yes_no",
                clarification_text="Are you pregnant or breastfeeding?",
                required=True
            )
        }
    
    def _initialize_condition_questions(self) -> Dict[str, List[PrescreeningQuestion]]:
        """Initialize condition-specific questions"""
        return {
            "gout": [
                PrescreeningQuestion(
                    key="gout_flares",
                    text="How many gout flares or attacks have you had in the past 12 months?",
                    type="number",
                    clarification_text="How many gout attacks have you experienced in the last year? Please give me a number.",
                    required=True
                ),
                PrescreeningQuestion(
                    key="uric_acid",
                    text="Do you know your most recent serum uric acid level?",
                    type="number",
                    clarification_text="What was your last uric acid blood test result? (Usually a number like 6.5 or 8.0)",
                    required=False
                )
            ],
            
            "diabetes": [
                PrescreeningQuestion(
                    key="diabetes_type",
                    text="Have you been diagnosed with Type 2 Diabetes?",
                    type="yes_no",
                    clarification_text="Is your diabetes specifically Type 2 (not Type 1)?",
                    required=True
                ),
                PrescreeningQuestion(
                    key="hba1c",
                    text="What was your most recent HbA1c level, if you know it?",
                    type="number",
                    clarification_text="HbA1c is usually expressed as a percentage, like 7.5% or 8.2%. Do you recall the number from your last test?",
                    required=False
                ),
                PrescreeningQuestion(
                    key="diabetes_duration",
                    text="How long have you had diabetes?",
                    type="text",
                    clarification_text="When were you first diagnosed with diabetes?",
                    required=False
                )
            ],
            
            "hypertension": [
                PrescreeningQuestion(
                    key="blood_pressure",
                    text="What was your most recent blood pressure reading?",
                    type="text",
                    clarification_text="Do you remember your last blood pressure numbers? (like 140/90)",
                    required=False
                ),
                PrescreeningQuestion(
                    key="bp_controlled",
                    text="Is your blood pressure currently well-controlled with medication?",
                    type="yes_no",
                    clarification_text="Would you say your blood pressure is under control with your current treatment?",
                    required=True
                )
            ]
        }
    
    def get_question(self, key: str, **kwargs) -> PrescreeningQuestion:
        """Get a question template by key with variable substitution"""
        # Check common questions first
        question = self.questions.get(key)
        
        # If not found in common questions, check condition-specific questions
        if not question:
            condition = kwargs.get('condition')
            if condition:
                condition_questions = self.get_condition_questions(condition)
                for cq in condition_questions:
                    if cq.key == key:
                        question = cq
                        break
        
        if question:
            # Create a copy and substitute variables
            q_copy = question.copy()
            q_copy.text = q_copy.text.format(**kwargs)
            if q_copy.clarification_text:
                q_copy.clarification_text = q_copy.clarification_text.format(**kwargs)
            return q_copy
        return None
    
    def get_condition_questions(self, condition: str) -> List[PrescreeningQuestion]:
        """Get condition-specific questions"""
        condition_lower = condition.lower()
        
        # Check direct match
        if condition_lower in self.condition_specific_questions:
            return self.condition_specific_questions[condition_lower]
        
        # Check partial matches
        for key, questions in self.condition_specific_questions.items():
            if key in condition_lower or condition_lower in key:
                return questions
        
        return []
    
    def get_standard_flow(self, condition: str = None) -> List[str]:
        """Get standard question flow for a condition"""
        base_flow = ["age", "diagnosis"]
        
        if condition:
            base_flow.extend(["medications"])
            # Add condition-specific questions
            condition_questions = self.get_condition_questions(condition)
            base_flow.extend([q.key for q in condition_questions])
        
        base_flow.extend(["other_conditions"])
        
        return base_flow