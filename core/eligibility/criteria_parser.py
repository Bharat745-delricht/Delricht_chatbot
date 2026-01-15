"""Parser for trial criteria to generate dynamic questions"""
from typing import List, Dict, Any, Optional
from models.schemas import PrescreeningQuestion
from core.database import db
import json
import logging

logger = logging.getLogger(__name__)


class CriteriaParser:
    """Converts trial_criteria entries into prescreening questions"""
    
    def __init__(self):
        self.field_to_question_map = {
            "age": self._create_age_question,
            "diagnosis": self._create_diagnosis_question,
            "condition_count": self._create_condition_count_question,
            "lab_value": self._create_lab_value_question,
            "bmi": self._create_bmi_question,
            "condition": self._create_condition_question,
            "gender": self._create_gender_question,
            "kidney_stones": self._create_kidney_stones_question,
        }
    
    def get_trial_criteria_questions(self, trial_id: int) -> List[PrescreeningQuestion]:
        """Fetch and convert trial criteria into prescreening questions"""
        
        # Query required criteria with parsed JSON
        criteria = db.execute_query("""
            SELECT id, criterion_type, criterion_text, parsed_json, category
            FROM trial_criteria
            WHERE trial_id = %s 
            AND is_required = true
            AND parsed_json != '{"field": "unparsed"}'::jsonb
            ORDER BY 
                CASE category 
                    WHEN 'demographic' THEN 1
                    WHEN 'medical' THEN 2
                    WHEN 'laboratory' THEN 3
                    ELSE 4
                END,
                id
        """, (trial_id,))
        
        questions = []
        seen_fields = set()  # Avoid duplicate questions
        
        for criterion in criteria:
            try:
                parsed = criterion['parsed_json']
                field = parsed.get('field')
                
                # Skip if we've already created a question for this field
                if field in seen_fields:
                    continue
                
                # Skip age and diagnosis as they're in the base flow
                if field in ['age', 'diagnosis']:
                    continue
                
                # Create question based on field type
                if field in self.field_to_question_map:
                    question = self.field_to_question_map[field](parsed, criterion)
                    if question:
                        questions.append(question)
                        seen_fields.add(field)
                        
            except Exception as e:
                logger.warning(f"Failed to parse criterion {criterion['id']}: {str(e)}")
                continue
        
        return questions
    
    def _create_condition_count_question(self, parsed: Dict, criterion: Dict) -> Optional[PrescreeningQuestion]:
        """Create question for condition count (e.g., gout flares)"""
        value = parsed.get('value', 2)
        operator = parsed.get('operator', 'greater_than_or_equal')
        
        # Map operators to user-friendly text
        operator_text = {
            'greater_than_or_equal': f'at least {value}',
            'greater_than': f'more than {value}',
            'less_than': f'fewer than {value}',
            'equals': f'exactly {value}'
        }.get(operator, str(value))
        
        return PrescreeningQuestion(
            key="condition_count",
            text=f"How many times have you experienced your condition (flares/episodes) in the past 12 months?",
            type="number",
            validation_pattern=r"\d+",
            clarification_text="Please provide the number of times you've had symptoms or flares in the last year.",
            required=True
        )
    
    def _create_lab_value_question(self, parsed: Dict, criterion: Dict) -> Optional[PrescreeningQuestion]:
        """Create question for lab values"""
        test = parsed.get('test', 'lab test')
        value = parsed.get('value')
        operator = parsed.get('operator')
        unit = parsed.get('unit', '')
        
        # Make test names user-friendly
        test_names = {
            'hemoglobin': 'hemoglobin (Hgb)',
            'hba1c': 'HbA1c (hemoglobin A1c)',
            'egfr': 'eGFR (kidney function)',
            'alt': 'ALT (liver enzyme)',
            'ast': 'AST (liver enzyme)'
        }
        
        friendly_test = test_names.get(test.lower(), test)
        
        return PrescreeningQuestion(
            key=f"lab_{test}",
            text=f"Do you know your most recent {friendly_test} level? If yes, what was it?",
            type="text",
            clarification_text=f"Please provide your {friendly_test} value if you know it (or type 'unknown').",
            required=False  # Lab values are often optional
        )
    
    def _create_bmi_question(self, parsed: Dict, criterion: Dict) -> Optional[PrescreeningQuestion]:
        """Create BMI-related questions"""
        return PrescreeningQuestion(
            key="bmi_info",
            text="What is your current height and weight? (This helps us calculate your BMI)",
            type="text",
            clarification_text="Please provide your height (e.g., 5'10\" or 178cm) and weight (e.g., 180lbs or 82kg).",
            required=True
        )
    
    def _create_condition_question(self, parsed: Dict, criterion: Dict) -> Optional[PrescreeningQuestion]:
        """Create question about specific medical conditions"""
        condition = parsed.get('value', 'this condition')
        
        # Always create the question - evaluation logic will handle inclusion vs exclusion
        return PrescreeningQuestion(
            key=f"has_{condition.replace(' ', '_').lower()}",
            text=f"Do you currently have {condition}?",
            type="yes_no",
            clarification_text=f"Have you been diagnosed with {condition}?",
            required=True
        )
    
    def _create_gender_question(self, parsed: Dict, criterion: Dict) -> Optional[PrescreeningQuestion]:
        """Create gender question if specific gender required"""
        return PrescreeningQuestion(
            key="gender",
            text="What is your biological sex assigned at birth?",
            type="text",
            clarification_text="Please specify male or female (this is required for the trial protocol).",
            required=True
        )
    
    def _create_kidney_stones_question(self, parsed: Dict, criterion: Dict) -> Optional[PrescreeningQuestion]:
        """Create kidney stones question"""
        return PrescreeningQuestion(
            key="kidney_stones",
            text="Have you had kidney stones in the past 6 months?",
            type="yes_no",
            clarification_text="Have you experienced kidney stones recently (within the last 6 months)?",
            required=True
        )
    
    def _create_age_question(self, parsed: Dict, criterion: Dict) -> Optional[PrescreeningQuestion]:
        """Age is handled in base flow"""
        return None
    
    def _create_diagnosis_question(self, parsed: Dict, criterion: Dict) -> Optional[PrescreeningQuestion]:
        """Diagnosis is handled in base flow"""
        return None