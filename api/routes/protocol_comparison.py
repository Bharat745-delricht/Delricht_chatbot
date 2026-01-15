"""
Protocol Comparison API

Compares multiple clinical trial protocols side-by-side to:
1. Identify similar criteria across protocols
2. Calculate restrictiveness scores
3. Highlight unique/different criteria
4. Provide recruitment viability insights

Supports 2-5 protocol comparison with semantic AI-powered criteria matching.
"""

import logging
import json
import uuid
import time
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from difflib import SequenceMatcher

from core.database import db
from core.services.gemini_service import gemini_service
from core.services.criterion_embedding_service import criterion_embedding_service

logger = logging.getLogger(__name__)
router = APIRouter()

# Comparison job status tracking
comparison_jobs = {}  # In-memory storage for job status

# =====================================================
# Pydantic Models
# =====================================================

class CompareProtocolsRequest(BaseModel):
    """Request to compare multiple protocols"""
    trial_ids: List[int]  # 2-5 trial IDs to compare
    include_ai_insights: bool = True  # Generate AI summary
    group_semantically: bool = True  # Use AI to group similar criteria

class SimilarProtocolsRequest(BaseModel):
    """Request to find similar protocols"""
    trial_id: int
    max_results: int = 5
    similarity_threshold: float = 0.6  # 0.0 to 1.0

# =====================================================
# Helper Functions
# =====================================================

def normalize_criterion_text(text: str) -> str:
    """
    Normalize criterion text for better matching
    Removes articles, common prefixes, and standardizes phrases
    """
    normalized = text.lower().strip()

    # Remove articles
    normalized = normalized.replace(' a ', ' ').replace(' an ', ' ').replace(' the ', ' ')

    # Remove common prefixes that don't change meaning
    prefixes_to_remove = [
        'receipt of ',
        'received ',
        'receiving ',
        'known ',
        'documented ',
        'confirmed ',
        'current ',
        'active ',
        'history of ',
        'known history of '
    ]

    for prefix in prefixes_to_remove:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]

    return normalized.strip()

def extract_criterion_concept(criterion_text: str) -> str:
    """
    Extract the key concept from a criterion with hierarchy support

    Handles patterns like:
    - "History of cancer" → "cancer" (not "known history of")
    - "Receipt of vaccine" → "prior_vaccination"
    - "Known diabetes" → "diabetes"
    """
    text = criterion_text.lower()
    normalized_text = normalize_criterion_text(criterion_text)

    # Define concept keywords in priority order (COMPREHENSIVE medical concepts)
    concepts = {
        # Study participation
        'concurrent_study': ['participation in another', 'enrolled in another', 'currently enrolled',
                            'concurrent participation', 'another clinical study', 'another clinical trial',
                            'participation at the time', 'planned participation', 'investigational product',
                            'another study involving', 'enrollment in another'],

        # Prior COVID infection/disease
        'prior_covid_infection': ['covid-19 infection', 'sars-cov-2 infection', 'history covid-19',
                                  'confirmed sars-cov-2', 'covid-19 disease', 'coronavirus infection',
                                  'history of covid', 'covid infection', 'sars-cov-2 disease'],

        # Vaccination/immunization (separate from infection)
        'prior_vaccination': ['prior vaccination', 'previously vaccinated', 'vaccination status',
                             'receipt of vaccine', 'receipt of investigational vaccine', 'receipt of licensed vaccine',
                             'unvaccinated', 'vaccinated with', 'vaccination history', 'covid-19 vaccine',
                             'immunization', 'prior immunization', 'vaccine within', 'vaccine >'],

        # Illness at time of procedure
        'fever_illness': ['febrile', 'fever', 'acute illness', 'unstable illness', 'body temperature',
                         'acute disease', 'illness at vaccination', 'illness at the time',
                         'temperature ≥', 'temperature >', 'acute medical condition',
                         'unstable acute illness', 'illness at time'],

        # Immunocompromised conditions
        'immunocompromised': ['immunocompromised', 'immunosuppressed', 'immune deficiency', 'immunodeficiency',
                             'hiv', 'human immunodeficiency', 'immune disorder', 'immunosuppressive therapy',
                             'transplant recipient', 'chemotherapy', 'immune system'],

        # Cancer/malignancy
        'cancer': ['cancer', 'malignancy', 'malignant', 'tumor', 'carcinoma', 'neoplasm',
                  'oncologic', 'chemotherapy', 'radiation therapy', 'history of cancer'],

        # Chronic diseases (grouped by system)
        'cardiac_disease': ['cardiac', 'heart', 'cardiovascular', 'myocardial', 'coronary',
                           'heart failure', 'arrhythmia', 'myocardial infarction', 'angina',
                           'cardiac disease', 'cardiovascular disease', 'myocarditis', 'pericarditis',
                           'heart condition', 'heart disease'],

        'lung_disease': ['chronic lung', 'pulmonary', 'copd', 'asthma', 'respiratory',
                        'lung disease', 'chronic obstructive', 'cystic fibrosis'],

        'cerebrovascular': ['cerebrovascular', 'stroke', 'tia', 'transient ischemic',
                           'cerebral', 'intracranial'],

        'neurological': ['neurological', 'neurologic', 'seizure', 'epilepsy', 'neuropathy',
                        'multiple sclerosis', 'parkinson', 'dementia', 'nervous system'],

        # Blood/hematology
        'bleeding_disorder': ['bleeding', 'coagulation', 'hemophilia', 'blood disorder',
                             'thrombocytopenia', 'anticoagulant', 'clotting', 'hemorrhage'],

        'blood_disease': ['sickle cell', 'thalassemia', 'anemia', 'hemoglobin disorder',
                         'blood disorder', 'hematologic'],

        # Basic demographics
        'age': ['age', 'years old', 'year of age', 'years of age', 'age at', 'aged'],

        'gender': ['male', 'female', 'sex', 'gender', 'men', 'women', 'man', 'woman'],

        # Pregnancy/reproductive
        'pregnancy': ['pregnant', 'pregnancy', 'breastfeeding', 'nursing', 'childbearing',
                     'lactating', 'potential for pregnancy', 'women of childbearing',
                     'reproductive potential', 'nursing mother'],

        'contraception': ['contraception', 'birth control', 'contraceptive', 'barrier method',
                         'highly effective contraception'],

        # Organ function
        'liver': ['liver', 'hepatic', 'alt', 'ast', 'bilirubin', 'liver function',
                 'transaminase', 'liver disease', 'cirrhosis', 'hepatitis'],

        'kidney': ['kidney', 'renal', 'creatinine', 'egfr', 'renal function',
                  'chronic kidney', 'renal disease', 'dialysis', 'nephropathy'],

        # Metabolic
        'diabetes': ['diabetes', 'diabetic', 'hba1c', 'glucose', 'blood sugar',
                    'glycemic', 'insulin', 'type 1 diabetes', 'type 2 diabetes'],

        'bmi': ['bmi', 'body mass index', 'weight', 'obesity', 'obese', 'overweight'],

        'blood_pressure': ['blood pressure', 'hypertension', 'systolic', 'diastolic',
                          'bp', 'hypertensive', 'antihypertensive'],

        # Allergies/sensitivities
        'allergy': ['allergy', 'allergic', 'hypersensitivity', 'hypersensitive',
                   'sensitivity to', 'anaphylaxis', 'allergic reaction'],

        # Lab values
        'lab_values': ['laboratory', 'lab values', 'screening visit', 'lab test',
                      'laboratory values', 'clinical laboratory'],

        # Medications
        'medication': ['medication', 'drug', 'treatment', 'therapy', 'concomitant medication',
                      'prohibited medication', 'restricted medication', 'systemic'],

        # Disease characteristics
        'disease_duration': ['disease duration', 'diagnosis for', 'diagnosed for', 'duration of',
                            'time since diagnosis'],

        'disease_activity': ['disease activity', 'active disease', 'flare', 'exacerbation',
                            'disease severity', 'moderate to severe'],

        # Study procedures
        'consent': ['informed consent', 'consent form', 'icf', 'signing', 'sign informed consent',
                   'willing to sign', 'able to provide consent', 'provide written informed consent',
                   'able to understand and provide', 'written informed consent', 'understand and provide'],

        'washout': ['washout', 'discontinue', 'stopped taking', 'withdrawal', 'cessation',
                   'prior to screening', 'prior to baseline', 'stopped treatment'],

        'specimen_collection': ['blood sample', 'specimen', 'biopsy', 'tissue sample',
                               'sample collection', 'biological sample'],

        # Mental health
        'mental_health': ['mental health', 'psychiatric', 'depression', 'anxiety',
                         'schizophrenia', 'bipolar', 'psychological', 'mental disorder',
                         'mood disorder'],

        # Infectious diseases
        'tuberculosis': ['tuberculosis', 'tb ', ' tb', 'mycobacterium'],

        'hepatitis': ['hepatitis', 'hep b', 'hep c', 'hepatitis b', 'hepatitis c'],

        # Body measurements
        'obesity': ['obesity', 'obese', 'severe obesity', 'bmi ≥', 'bmi >',
                   'body mass index ≥', 'overweight'],

        # General exclusions
        'underlying_conditions': ['underlying condition', 'underlying medical condition',
                                 'pre-existing condition', 'comorbid', 'comorbidity',
                                 'without underlying', 'chronic medical condition'],

        # Disability/functional status
        'disability': ['disability', 'disabled', 'disabilities', 'functional limitation',
                      'wheelchair', 'assistive device'],

        # Organ transplant
        'transplant': ['transplant', 'organ transplant', 'transplant recipient',
                      'stem cell transplant', 'bone marrow transplant'],

        # Lifestyle/behavioral
        'smoking': ['smoking', 'tobacco', 'cigarette', 'current smoker', 'tobacco use'],

        'substance_abuse': ['drug abuse', 'alcohol abuse', 'substance abuse', 'addiction',
                           'alcoholism', 'drug dependence'],

        # Infections
        'active_infection': ['active infection', 'acute infection', 'infectious disease',
                            'infection requiring', 'systemic infection'],

        # Study compliance
        'study_compliance': ['willing to comply', 'able to comply', 'compliance with',
                            'adhere to protocol', 'follow study procedures'],

        # Study personnel
        'study_personnel': ['study team', 'study staff', 'investigator', 'study personnel',
                           'site personnel', 'study site']
    }

    # Check for concept matches on BOTH original and normalized text
    # Check longer phrases first for better accuracy
    sorted_concepts = sorted(concepts.items(), key=lambda x: max(len(k) for k in x[1]), reverse=True)

    for concept, keywords in sorted_concepts:
        # Check both original and normalized text
        if any(keyword in text for keyword in keywords) or any(keyword in normalized_text for keyword in keywords):
            return concept

    # Default to first few meaningful words as concept (skip common words)
    words = [w for w in text.split()[:4] if w not in ['a', 'an', 'the', 'of', 'or', 'and']]
    return ' '.join(words[:3]) if words else 'other'

def calculate_text_similarity(text1: str, text2: str) -> float:
    """Calculate similarity between two text strings (0.0 to 1.0)"""
    if not text1 or not text2:
        return 0.0

    # Normalize texts
    t1 = text1.lower().strip()
    t2 = text2.lower().strip()

    # Check if they share the same concept
    concept1 = extract_criterion_concept(text1)
    concept2 = extract_criterion_concept(text2)

    # If same concept, boost similarity
    if concept1 == concept2 and concept1 != 'other':
        base_similarity = SequenceMatcher(None, t1, t2).ratio()
        # Boost by 30% if same concept
        return min(1.0, base_similarity + 0.3)

    # Use SequenceMatcher for fuzzy matching
    return SequenceMatcher(None, t1, t2).ratio()

def calculate_condition_similarity(conditions1: str, conditions2: str) -> float:
    """Calculate similarity between condition strings using fuzzy matching"""
    if not conditions1 or not conditions2:
        return 0.0

    # Split and normalize
    conds1 = set([c.strip().lower() for c in conditions1.split(',') if c.strip()])
    conds2 = set([c.strip().lower() for c in conditions2.split(',') if c.strip()])

    if not conds1 or not conds2:
        return 0.0

    # Calculate Jaccard similarity (exact + fuzzy)
    intersection = conds1 & conds2
    union = conds1 | conds2

    # Exact match score
    exact_score = len(intersection) / len(union) if union else 0.0

    # Fuzzy match - check for partial matches
    fuzzy_matches = 0
    for c1 in conds1:
        for c2 in conds2:
            if c1 != c2:  # Don't double count exact matches
                similarity = SequenceMatcher(None, c1, c2).ratio()
                if similarity >= 0.7:  # High fuzzy match
                    fuzzy_matches += 0.5  # Count as partial match

    fuzzy_score = fuzzy_matches / max(len(conds1), len(conds2)) if (len(conds1) + len(conds2)) > 0 else 0.0

    # Combine exact and fuzzy
    return min(1.0, exact_score + fuzzy_score)

async def analyze_criterion_semantics_ai(criterion_text: str) -> Dict[str, Any]:
    """
    Use Gemini AI to deeply understand what a criterion is testing

    Returns semantic analysis including:
    - core_requirement: What is being tested (e.g., "concurrent study participation")
    - requirement_type: Category (age, medical_history, etc.)
    - restrictiveness_indicators: What makes it strict/loose
    """
    try:
        prompt = f"""Analyze this clinical trial eligibility criterion and extract its semantic meaning:

Criterion: "{criterion_text}"

Respond with JSON containing:
{{
  "core_requirement": "Brief description of what this tests (e.g., 'no concurrent study participation', 'minimum age 18 years')",
  "requirement_type": "age|medical_condition|study_participation|vaccination_status|lab_values|pregnancy|medication|allergy|other",
  "key_concepts": ["list", "of", "key", "medical", "concepts"],
  "restrictiveness_level": "strict|moderate|permissive"
}}

Respond with ONLY valid JSON:"""

        response = await gemini_service.generate_text(prompt, max_tokens=150)

        # Parse JSON
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        else:
            return {
                "core_requirement": criterion_text[:50],
                "requirement_type": "other",
                "key_concepts": [],
                "restrictiveness_level": "moderate"
            }
    except Exception as e:
        logger.warning(f"AI semantic analysis failed: {e}")
        return {
            "core_requirement": criterion_text[:50],
            "requirement_type": extract_criterion_concept(criterion_text),
            "key_concepts": [],
            "restrictiveness_level": "moderate"
        }

async def semantic_similarity_ai(text1: str, text2: str) -> float:
    """Use Gemini AI to determine semantic similarity (0.0 to 1.0)"""
    try:
        prompt = f"""Compare these two clinical trial criteria and determine if they test the SAME medical/eligibility requirement:

Criterion 1: {text1}
Criterion 2: {text2}

Rate their semantic similarity from 0.0 to 1.0, where:
- 1.0 = Testing the exact same requirement (even if worded differently)
- 0.8-0.9 = Testing very similar requirements
- 0.6-0.7 = Testing related but distinct requirements
- 0.4-0.5 = Testing different but somewhat related requirements
- 0.0-0.3 = Testing completely unrelated requirements

Examples:
- "Participation in another study" vs "Currently enrolled in another clinical trial" = 1.0 (same)
- "Age ≥18 years" vs "Age 18-70 years old" = 0.9 (same concept, different range)
- "Fever at vaccination" vs "Febrile before injection" = 1.0 (same)
- "Prior COVID vaccination >90 days" vs "Receipt of vaccine within 14 days" = 0.8 (both about vaccination timing)

Answer with ONLY a number:"""

        response = await gemini_service.generate_text(prompt, max_tokens=10)
        score = float(response.strip())
        return max(0.0, min(1.0, score))  # Clamp between 0 and 1
    except Exception as e:
        logger.warning(f"AI similarity calculation failed: {e}")
        # Fallback to text similarity
        return calculate_text_similarity(text1, text2)

def calculate_restrictiveness_score(criterion_text: str, criterion_type: str) -> int:
    """
    Calculate restrictiveness score for a single criterion (0-100)
    Higher score = more restrictive = harder to recruit
    """
    score = 50  # Base score

    text = criterion_text.lower()

    # Inclusion criteria - look for limiting factors
    if criterion_type == 'inclusion':
        # Age restrictions
        if 'age' in text:
            if '>= 65' in text or '> 65' in text or '65+' in text:
                score += 15  # Elderly only is restrictive
            elif '<= 21' in text or '< 21' in text:
                score += 10  # Young adults only
            elif '18-65' in text or '18 to 65' in text:
                score += 5  # Standard adult range

        # BMI restrictions
        if 'bmi' in text:
            if '>= 30' in text or '> 30' in text:
                score += 10  # Obesity requirement
            elif '<= 25' in text or '< 25' in text:
                score += 10  # Normal weight requirement
            elif 'bmi' in text:
                score += 5  # Any BMI restriction

        # Disease severity
        if any(word in text for word in ['severe', 'moderate to severe', 'active disease']):
            score += 15
        elif 'moderate' in text:
            score += 10
        elif 'mild' in text:
            score += 5

        # Lab values - specific ranges are restrictive
        if any(word in text for word in ['hba1c', 'hemoglobin', 'egfr', 'alt', 'ast']):
            score += 10

        # Documented diagnosis requirement
        if any(word in text for word in ['diagnosed', 'documented', 'confirmed']):
            score += 5

    # Exclusion criteria - each one makes recruitment harder
    elif criterion_type == 'exclusion':
        score += 5  # Base exclusion penalty

        # Common comorbidities
        if any(word in text for word in ['diabetes', 'hypertension', 'cardiovascular', 'cancer']):
            score += 15

        # Medications
        if any(word in text for word in ['medication', 'drug', 'treatment', 'therapy']):
            score += 10

        # Pregnancy/reproductive
        if any(word in text for word in ['pregnant', 'pregnancy', 'breastfeeding', 'childbearing']):
            score += 5

        # Substance use
        if any(word in text for word in ['alcohol', 'drug abuse', 'smoking']):
            score += 5

    return max(0, min(100, score))  # Clamp between 0 and 100

# =====================================================
# Main Endpoints
# =====================================================

@router.post("/compare")
async def compare_protocols(request: CompareProtocolsRequest):
    """
    Compare multiple protocols side-by-side

    Returns a structured comparison showing:
    - Protocol metadata (enrollment, sites, etc.)
    - Criteria grouped by category
    - Similarity scores
    - Restrictiveness analysis
    - AI-generated insights
    """
    import uuid
    import time

    comparison_id = str(uuid.uuid4())
    start_time = time.time()

    try:
        if len(request.trial_ids) < 2:
            raise HTTPException(status_code=400, detail="Need at least 2 protocols to compare")

        if len(request.trial_ids) > 5:
            raise HTTPException(status_code=400, detail="Can compare maximum 5 protocols at once")

        # Fetch all protocols with metadata
        protocols = []
        for trial_id in request.trial_ids:
            protocol_data = await _fetch_protocol_full_data(trial_id)
            if not protocol_data:
                raise HTTPException(status_code=404, detail=f"Protocol {trial_id} not found")
            protocols.append(protocol_data)

        # Build comparison structure
        comparison = {
            "protocols": protocols,
            "metadata_comparison": _compare_metadata(protocols),
            "criteria_comparison": await _compare_criteria(
                protocols,
                request.group_semantically
            ),
            "restrictiveness_analysis": _analyze_restrictiveness(protocols),
            "summary_statistics": _calculate_summary_stats(protocols),
        }

        # Add AI insights if requested
        if request.include_ai_insights:
            comparison["ai_insights"] = await _generate_ai_insights(protocols, comparison)

        # Calculate processing time
        processing_time_ms = int((time.time() - start_time) * 1000)

        # LOG TO DATABASE for debugging and improvement
        try:
            grouping_decisions = []
            for category, category_data in comparison["criteria_comparison"].items():
                if 'criteria_groups' in category_data:
                    for group in category_data['criteria_groups']:
                        grouping_decisions.append({
                            "category": category,
                            "concept": group.get('concept'),
                            "protocols_in_group": [p['protocol_number'] for p in group['protocols_with_criterion']],
                            "is_unique": group.get('is_unique'),
                            "matching_decisions": group.get('matching_decisions', [])
                        })

            db.execute_update("""
                INSERT INTO protocol_comparison_logs
                (comparison_id, compared_trial_ids, total_criteria_analyzed, groups_created,
                 ai_calls_made, grouping_decisions, processing_time_ms, restrictiveness_scores)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                comparison_id,
                request.trial_ids,
                sum(len(p['all_criteria']) for p in protocols),
                sum(len(cat_data.get('criteria_groups', [])) for cat_data in comparison["criteria_comparison"].values()),
                comparison["criteria_comparison"].get('demographics', {}).get('debug_info', {}).get('ai_calls_made', 0),
                json.dumps(grouping_decisions),
                processing_time_ms,
                json.dumps(comparison["restrictiveness_analysis"])
            ))

            logger.info(f"✅ Logged comparison {comparison_id} to database")
        except Exception as log_error:
            logger.error(f"Failed to log comparison: {log_error}")

        # Add debug info to response
        comparison["debug_info"] = {
            "comparison_id": comparison_id,
            "processing_time_ms": processing_time_ms,
            "total_ai_calls": sum(
                cat_data.get('debug_info', {}).get('ai_calls_made', 0)
                for cat_data in comparison["criteria_comparison"].values()
            )
        }

        return {
            "success": True,
            "comparison": comparison
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error comparing protocols: {e}")
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")

@router.get("/similar/{trial_id}")
async def find_similar_protocols(trial_id: int, max_results: int = 5):
    """
    Find similar protocols based on conditions and criteria

    Uses fuzzy matching on conditions field and semantic analysis
    """
    try:
        # Get target protocol
        target = await _fetch_protocol_full_data(trial_id)
        if not target:
            raise HTTPException(status_code=404, detail="Protocol not found")

        # Get all other protocols
        all_protocols = db.execute_query("""
            SELECT
                ct.id, ct.protocol_number, ct.trial_name, ct.conditions, ct.sponsor,
                pm.estimated_enrollment,
                COUNT(tc.id) as criteria_count
            FROM clinical_trials ct
            LEFT JOIN protocol_metadata pm ON ct.id = pm.trial_id
            LEFT JOIN trial_criteria tc ON ct.id = tc.trial_id
            WHERE ct.id != %s AND ct.status = 'Active'
            GROUP BY ct.id, ct.protocol_number, ct.trial_name, ct.conditions,
                     ct.sponsor, pm.estimated_enrollment
        """, (trial_id,))

        # Calculate similarity scores
        similar_protocols = []
        for protocol in all_protocols:
            # Condition similarity
            condition_score = calculate_condition_similarity(
                target['conditions'] or '',
                protocol['conditions'] or ''
            )

            # Sponsor matching (same sponsor = likely similar)
            sponsor_score = 0.2 if target['sponsor'] == protocol['sponsor'] else 0.0

            # Criteria count similarity
            target_count = len(target.get('inclusion_criteria', [])) + len(target.get('exclusion_criteria', []))
            protocol_count = protocol['criteria_count'] or 0
            criteria_score = 1.0 - min(abs(target_count - protocol_count) / max(target_count, protocol_count, 1), 1.0)
            criteria_score *= 0.3  # Weight

            # Combined similarity score (condition is primary driver)
            overall_score = condition_score * 0.7 + sponsor_score + criteria_score

            # Only include if there's meaningful similarity
            if overall_score >= 0.5:  # Minimum threshold
                similar_protocols.append({
                    "trial_id": protocol['id'],
                    "protocol_number": protocol['protocol_number'],
                    "trial_name": protocol['trial_name'],
                    "conditions": protocol['conditions'],
                    "sponsor": protocol['sponsor'],
                    "estimated_enrollment": protocol['estimated_enrollment'],
                    "similarity_score": round(overall_score, 3),
                    "condition_match": round(condition_score, 3)
                })

        # Sort by similarity score
        similar_protocols.sort(key=lambda x: x['similarity_score'], reverse=True)

        return {
            "success": True,
            "target_protocol": {
                "trial_id": target['id'],
                "protocol_number": target['protocol_number'],
                "conditions": target['conditions']
            },
            "similar_protocols": similar_protocols[:max_results],
            "total_found": len(similar_protocols)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error finding similar protocols: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

# =====================================================
# Internal Helper Functions
# =====================================================

async def _fetch_protocol_full_data(trial_id: int) -> Optional[Dict[str, Any]]:
    """Fetch complete protocol data including criteria"""
    # Get trial basic info
    trial = db.execute_query("""
        SELECT ct.*, pm.estimated_enrollment, pm.study_duration, pm.target_population,
               pm.protocol_summary
        FROM clinical_trials ct
        LEFT JOIN protocol_metadata pm ON ct.id = pm.trial_id
        WHERE ct.id = %s
    """, (trial_id,))

    if not trial:
        return None

    protocol = trial[0]

    # Get all criteria with embeddings
    criteria = db.execute_query("""
        SELECT id, criterion_type, criterion_text, category, is_required,
               extraction_confidence, semantic_embedding, embedding_generated_at
        FROM trial_criteria
        WHERE trial_id = %s
        ORDER BY criterion_type, category, id
    """, (trial_id,))

    # Separate inclusion and exclusion
    protocol['inclusion_criteria'] = [c for c in criteria if c['criterion_type'] == 'inclusion']
    protocol['exclusion_criteria'] = [c for c in criteria if c['criterion_type'] == 'exclusion']
    protocol['all_criteria'] = criteria

    return protocol

def _compare_metadata(protocols: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare protocol metadata (enrollment, sites, etc.)"""
    return {
        "enrollment_targets": [
            {
                "protocol_id": p['id'],
                "protocol_number": p['protocol_number'],
                "target": p.get('estimated_enrollment')
            } for p in protocols
        ],
        "study_durations": [
            {
                "protocol_id": p['id'],
                "protocol_number": p['protocol_number'],
                "duration": p.get('study_duration')
            } for p in protocols
        ],
        "sponsors": [
            {
                "protocol_id": p['id'],
                "protocol_number": p['protocol_number'],
                "sponsor": p.get('sponsor')
            } for p in protocols
        ]
    }

async def _compare_criteria(protocols: List[Dict[str, Any]], use_semantic: bool) -> Dict[str, Any]:
    """
    Compare criteria across protocols

    Groups similar criteria together and identifies unique ones
    """
    # Collect all unique criteria texts across all protocols
    all_criteria_by_category = {}

    for protocol in protocols:
        for criterion in protocol['all_criteria']:
            category = criterion['category'] or 'general'
            if category not in all_criteria_by_category:
                all_criteria_by_category[category] = []

            all_criteria_by_category[category].append({
                "protocol_id": protocol['id'],
                "protocol_number": protocol['protocol_number'],
                "criterion_id": criterion['id'],
                "text": criterion['criterion_text'],
                "type": criterion['criterion_type'],
                "is_required": criterion['is_required']
            })

    # Build comparison matrix
    comparison_matrix = {}

    for category, criteria_list in all_criteria_by_category.items():
        # Group similar criteria if semantic matching is enabled
        if use_semantic and len(criteria_list) > 1:
            grouped = await _group_similar_criteria(criteria_list)
            comparison_matrix[category] = grouped
        else:
            # Simple listing
            comparison_matrix[category] = {
                "criteria_groups": [
                    {
                        "representative_text": c['text'],
                        "protocols_with_criterion": [{
                            "protocol_id": c['protocol_id'],
                            "protocol_number": c['protocol_number'],
                            "exact_text": c['text'],
                            "type": c['type'],
                            "is_required": c['is_required']
                        }],
                        "similarity_score": 1.0,
                        "is_unique": True
                    } for c in criteria_list
                ]
            }

    return comparison_matrix

async def _group_similar_criteria(criteria_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Group similar criteria using enhanced concept matching

    Returns groups of criteria that are semantically similar
    """
    import uuid
    import time

    start_time = time.time()
    groups = []
    processed = set()
    ai_calls_made = 0
    grouping_log = []

    # HYBRID APPROACH: Keywords for speed + Embeddings for accuracy
    # First pass: Quick keyword grouping
    # Second pass: Refine with embedding similarity

    concept_groups = {}  # concept -> group data
    embedding_refinements = 0

    for i, criterion in enumerate(criteria_list):
        concept = extract_criterion_concept(criterion['text'])

        # Create or add to concept group
        if concept not in concept_groups:
            concept_groups[concept] = {
                "representative_text": criterion['text'],
                "concept": concept,
                "protocols_with_criterion": [],
                "similarity_scores": [],
                "matching_decisions": [],
                "protocol_ids_seen": set(),
                "criteria_in_group": []  # Track all criteria for embedding refinement
            }

        # Add this criterion to the concept group
        # But only if we haven't seen this protocol for this concept yet
        if criterion['protocol_id'] not in concept_groups[concept]['protocol_ids_seen']:
            concept_groups[concept]['protocols_with_criterion'].append({
                "protocol_id": criterion['protocol_id'],
                "protocol_number": criterion['protocol_number'],
                "exact_text": criterion['text'],
                "type": criterion['type'],
                "is_required": criterion['is_required'],
                "criterion_id": criterion.get('criterion_id') or criterion.get('id'),  # Include ID for feedback
                "embedding": criterion.get('embedding')
            })
            concept_groups[concept]['protocol_ids_seen'].add(criterion['protocol_id'])
            concept_groups[concept]['criteria_in_group'].append(criterion)

    # EMBEDDING REFINEMENT PASS
    # Check if criteria in same concept group are ACTUALLY similar
    # This catches cases where keywords grouped incorrectly
    refined_groups = []

    for concept, group_data in concept_groups.items():
        # If all criteria have embeddings, refine the grouping
        criteria_in_group = group_data.get('criteria_in_group', [])
        has_embeddings = all(c.get('embedding') is not None for c in criteria_in_group)

        if has_embeddings and len(criteria_in_group) > 1:
            # Use embeddings to verify grouping
            # Split into sub-groups if embeddings show they're different
            sub_groups = await _refine_group_with_embeddings(group_data, concept)
            refined_groups.extend(sub_groups)
            if len(sub_groups) > 1:
                embedding_refinements += 1
                logger.info(f"🔍 Embedding refinement: Split {concept} into {len(sub_groups)} sub-groups")
        else:
            # No embeddings or single criterion - use as-is
            refined_groups.append(group_data)

    # Convert refined groups to final format
    for group_data in refined_groups:
        # Clean up internal tracking fields
        if 'protocol_ids_seen' in group_data:
            del group_data['protocol_ids_seen']
        if 'criteria_in_group' in group_data:
            del group_data['criteria_in_group']

        # Set metadata
        group_data['average_similarity'] = 1.0  # All in group matched by concept
        group_data['is_unique'] = len(group_data['protocols_with_criterion']) == 1

        # Calculate row-level restrictiveness for each protocol's version
        restrictiveness_by_protocol = {}
        for protocol_criterion in group_data['protocols_with_criterion']:
            score = calculate_restrictiveness_score(
                protocol_criterion['exact_text'],
                protocol_criterion['type']
            )
            restrictiveness_by_protocol[protocol_criterion['protocol_id']] = score
            protocol_criterion['restrictiveness_score'] = score

        # Identify most/least restrictive in this row
        if restrictiveness_by_protocol and len(restrictiveness_by_protocol) > 1:
            max_score = max(restrictiveness_by_protocol.values())
            min_score = min(restrictiveness_by_protocol.values())

            # Mark each criterion
            for protocol_criterion in group_data['protocols_with_criterion']:
                score = protocol_criterion['restrictiveness_score']
                if score == max_score and max_score > min_score:
                    protocol_criterion['is_most_restrictive'] = True
                if score == min_score and max_score > min_score:
                    protocol_criterion['is_least_restrictive'] = True

        # Cleanup internal fields
        if 'similarity_scores' in group_data:
            del group_data['similarity_scores']
        if 'matching_decisions' in group_data:
            del group_data['matching_decisions']

        groups.append(group_data)

    # Create logging summary
    processing_time_ms = int((time.time() - start_time) * 1000)

    logger.info(f"""
    ========================================
    CRITERIA GROUPING ANALYSIS
    ========================================
    Total Criteria: {len(criteria_list)}
    Groups Created: {len(groups)}
    AI Calls Made: {ai_calls_made}
    Processing Time: {processing_time_ms}ms
    ========================================
    """)

    # Log each group for debugging
    for idx, group in enumerate(groups):
        protocols_in_group = [p['protocol_number'] for p in group['protocols_with_criterion']]
        logger.info(f"  Group {idx+1} ({group['concept']}): {len(group['protocols_with_criterion'])} protocols - {protocols_in_group}")

    return {
        "criteria_groups": groups,
        "debug_info": {
            "total_criteria": len(criteria_list),
            "groups_created": len(groups),
            "ai_calls_made": ai_calls_made,
            "embedding_refinements": embedding_refinements,
            "processing_time_ms": processing_time_ms
        }
    }

async def _refine_group_with_embeddings(group_data: Dict[str, Any], concept: str) -> List[Dict[str, Any]]:
    """
    Refine a keyword-based group using embedding similarity

    If criteria in the group have low embedding similarity, split into sub-groups
    """
    criteria = group_data.get('criteria_in_group', [])

    if len(criteria) <= 1:
        return [group_data]

    # Get threshold for this concept (with learned adjustments)
    threshold = criterion_embedding_service.get_threshold_for_concept(concept)

    # Build similarity matrix using embeddings
    sub_groups = []
    processed_indices = set()

    for i, criterion in enumerate(criteria):
        if i in processed_indices:
            continue

        # Start new sub-group
        sub_group = {
            "representative_text": criterion['text'],
            "concept": concept,
            "protocols_with_criterion": [{
                "protocol_id": criterion['protocol_id'],
                "protocol_number": criterion['protocol_number'],
                "exact_text": criterion['text'],
                "type": criterion['type'],
                "is_required": criterion['is_required'],
                "criterion_id": criterion.get('criterion_id') or criterion.get('id')
            }],
            "similarity_scores": [],
            "matching_decisions": [],
            "protocol_ids_seen": {criterion['protocol_id']}
        }

        processed_indices.add(i)

        # Find similar criteria using embeddings
        for j, other_criterion in enumerate(criteria):
            if j in processed_indices or i == j:
                continue

            # Skip if same protocol
            if criterion['protocol_id'] == other_criterion['protocol_id']:
                continue

            # Calculate embedding similarity
            if criterion.get('embedding') and other_criterion.get('embedding'):
                similarity = criterion_embedding_service.calculate_cosine_similarity(
                    criterion['embedding'],
                    other_criterion['embedding']
                )

                if similarity >= threshold:
                    sub_group['protocols_with_criterion'].append({
                        "protocol_id": other_criterion['protocol_id'],
                        "protocol_number": other_criterion['protocol_number'],
                        "exact_text": other_criterion['text'],
                        "type": other_criterion['type'],
                        "is_required": other_criterion['is_required'],
                        "criterion_id": other_criterion.get('criterion_id') or other_criterion.get('id')
                    })
                    sub_group['similarity_scores'].append(similarity)
                    sub_group['protocol_ids_seen'].add(other_criterion['protocol_id'])
                    processed_indices.add(j)

        sub_groups.append(sub_group)

    logger.info(f"  Embedding refinement: {len(criteria)} criteria → {len(sub_groups)} sub-groups")

    return sub_groups

def _analyze_restrictiveness(protocols: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyze restrictiveness of each protocol

    Calculates scores to determine recruitment difficulty
    """
    analysis = []

    for protocol in protocols:
        scores = []

        # Calculate score for each criterion
        for criterion in protocol['all_criteria']:
            score = calculate_restrictiveness_score(
                criterion['criterion_text'],
                criterion['criterion_type']
            )
            scores.append(score)

        # Calculate overall restrictiveness
        if scores:
            avg_score = sum(scores) / len(scores)
            max_score = max(scores)

            # Classify
            if avg_score >= 70:
                difficulty = "Very Difficult"
                difficulty_class = "danger"
            elif avg_score >= 60:
                difficulty = "Difficult"
                difficulty_class = "warning"
            elif avg_score >= 50:
                difficulty = "Moderate"
                difficulty_class = "info"
            else:
                difficulty = "Easier"
                difficulty_class = "success"
        else:
            avg_score = 0
            max_score = 0
            difficulty = "Unknown"
            difficulty_class = "secondary"

        analysis.append({
            "protocol_id": protocol['id'],
            "protocol_number": protocol['protocol_number'],
            "overall_restrictiveness_score": round(avg_score, 1),
            "max_restrictiveness_score": max_score,
            "total_criteria": len(protocol['all_criteria']),
            "inclusion_count": len(protocol['inclusion_criteria']),
            "exclusion_count": len(protocol['exclusion_criteria']),
            "difficulty_rating": difficulty,
            "difficulty_class": difficulty_class
        })

    # Sort by restrictiveness
    analysis.sort(key=lambda x: x['overall_restrictiveness_score'], reverse=True)

    return {
        "protocols": analysis,
        "summary": {
            "most_restrictive": analysis[0] if analysis else None,
            "least_restrictive": analysis[-1] if analysis else None
        }
    }

def _calculate_summary_stats(protocols: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate summary statistics for comparison"""
    return {
        "total_protocols": len(protocols),
        "criteria_counts": [
            {
                "protocol_id": p['id'],
                "protocol_number": p['protocol_number'],
                "total": len(p['all_criteria']),
                "inclusion": len(p['inclusion_criteria']),
                "exclusion": len(p['exclusion_criteria'])
            } for p in protocols
        ],
        "average_criteria_count": round(
            sum(len(p['all_criteria']) for p in protocols) / len(protocols), 1
        ) if protocols else 0
    }

async def _generate_ai_insights(protocols: List[Dict[str, Any]], comparison: Dict[str, Any]) -> Dict[str, Any]:
    """Generate AI-powered insights about the comparison"""
    try:
        # Build summary for AI
        protocol_summaries = []
        for p in protocols:
            protocol_summaries.append(
                f"Protocol {p['protocol_number']} ({p.get('conditions', 'N/A')}): "
                f"{len(p['inclusion_criteria'])} inclusion, {len(p['exclusion_criteria'])} exclusion criteria"
            )

        restrictiveness = comparison['restrictiveness_analysis']['protocols']
        most_restrictive = restrictiveness[0]['protocol_number'] if restrictiveness else "Unknown"
        least_restrictive = restrictiveness[-1]['protocol_number'] if restrictiveness else "Unknown"

        prompt = f"""Analyze this clinical trial protocol comparison and provide recruitment insights:

Protocols being compared:
{chr(10).join(protocol_summaries)}

Restrictiveness Analysis:
- Most restrictive: {most_restrictive} (Score: {restrictiveness[0]['overall_restrictiveness_score'] if restrictiveness else 0})
- Least restrictive: {least_restrictive} (Score: {restrictiveness[-1]['overall_restrictiveness_score'] if restrictiveness else 0})

Provide a brief analysis (3-4 sentences) addressing:
1. Which protocol will be easiest/hardest to recruit for and why
2. Key criteria differences that impact recruitment
3. Practical recommendation for site selection

Keep it concise and actionable."""

        insights_text = await gemini_service.generate_text(prompt, max_tokens=300)

        return {
            "summary": insights_text,
            "generated_at": "now"
        }

    except Exception as e:
        logger.error(f"Error generating AI insights: {e}")
        return {
            "summary": "AI insights unavailable. Please review the comparison data above.",
            "error": str(e)
        }

# =====================================================
# Debugging & Logging Endpoints
# =====================================================

@router.get("/logs/recent")
async def get_recent_comparison_logs(limit: int = 10):
    """Get recent comparison logs for debugging"""
    try:
        logs = db.execute_query("""
            SELECT
                comparison_id,
                compared_trial_ids,
                total_criteria_analyzed,
                groups_created,
                ai_calls_made,
                processing_time_ms,
                created_at
            FROM protocol_comparison_logs
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))

        return {
            "success": True,
            "logs": logs or [],
            "count": len(logs) if logs else 0
        }
    except Exception as e:
        logger.error(f"Error fetching logs: {e}")
        return {
            "success": False,
            "error": str(e),
            "logs": []
        }

@router.get("/logs/{comparison_id}")
async def get_comparison_log_details(comparison_id: str):
    """Get detailed log for a specific comparison"""
    try:
        log = db.execute_query("""
            SELECT *
            FROM protocol_comparison_logs
            WHERE comparison_id = %s
        """, (comparison_id,))

        if not log:
            raise HTTPException(status_code=404, detail="Comparison log not found")

        return {
            "success": True,
            "log": log[0],
            "grouping_decisions": log[0].get('grouping_decisions', []),
            "failed_groupings": log[0].get('failed_groupings', [])
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching log details: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =====================================================
# Feedback Loop Endpoints
# =====================================================

class UserCorrectionRequest(BaseModel):
    """Request to record user correction for feedback loop"""
    criterion_1_id: int
    criterion_2_id: int
    user_action: str  # 'merge' or 'separate'
    comparison_id: str
    concept_type: str
    current_similarity: float
    reason: Optional[str] = None

@router.post("/feedback/correction")
async def record_user_correction(request: UserCorrectionRequest):
    """
    Record user correction to improve future comparisons

    This creates the feedback loop for continuous improvement
    """
    try:
        success = await criterion_embedding_service.record_user_correction(
            request.criterion_1_id,
            request.criterion_2_id,
            request.user_action,
            request.comparison_id,
            request.concept_type,
            request.current_similarity
        )

        if success:
            return {
                "success": True,
                "message": "Correction recorded. Future comparisons will be improved!",
                "threshold_adjusted": True
            }
        else:
            return {
                "success": False,
                "message": "Failed to record correction"
            }

    except Exception as e:
        logger.error(f"Error recording correction: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/feedback/stats")
async def get_feedback_stats():
    """Get statistics about user corrections and system improvements"""
    try:
        stats = db.execute_query("""
            SELECT
                concept_type,
                COUNT(*) as correction_count,
                AVG(threshold_adjustment) as avg_adjustment,
                user_action
            FROM criterion_similarity_corrections
            WHERE applied_at IS NOT NULL
            GROUP BY concept_type, user_action
            ORDER BY correction_count DESC
        """)

        total_corrections = db.execute_query("""
            SELECT COUNT(*) as count
            FROM criterion_similarity_corrections
        """)[0]['count']

        return {
            "success": True,
            "total_corrections": total_corrections,
            "by_concept": stats or [],
            "message": f"System has learned from {total_corrections} user corrections"
        }

    except Exception as e:
        logger.error(f"Error fetching feedback stats: {e}")
        return {"success": False, "error": str(e)}

# =====================================================
# Embedding Management Endpoints
# =====================================================

@router.post("/embeddings/generate-batch")
async def generate_embedding_batch(batch_size: int = 50):
    """
    Generate embeddings for one batch of criteria (synchronous)

    Call repeatedly until all embeddings are generated.
    Designed for Cloud Run where background tasks don't persist.
    """
    try:
        # Get next batch of criteria without embeddings
        criteria = db.execute_query("""
            SELECT id, criterion_text
            FROM trial_criteria
            WHERE semantic_embedding IS NULL
            ORDER BY id
            LIMIT %s
        """, (batch_size,))

        if not criteria:
            return {
                "success": True,
                "message": "All criteria already have embeddings!",
                "processed": 0,
                "remaining": 0,
                "complete": True
            }

        # Process this batch synchronously
        processed = 0
        errors = []

        logger.info(f"🔄 Generating embeddings for batch of {len(criteria)} criteria...")

        for criterion in criteria:
            try:
                success = await criterion_embedding_service.generate_and_store_embedding(
                    criterion['id'],
                    criterion['criterion_text']
                )

                if success:
                    processed += 1
                else:
                    errors.append(f"Criterion {criterion['id']}: generation failed")

            except Exception as e:
                errors.append(f"Criterion {criterion['id']}: {str(e)}")

        # Get remaining count
        remaining = db.execute_query("""
            SELECT COUNT(*) as count
            FROM trial_criteria
            WHERE semantic_embedding IS NULL
        """)[0]['count']

        logger.info(f"✅ Batch complete: {processed}/{len(criteria)} processed, {len(errors)} errors, {remaining} remaining")

        return {
            "success": True,
            "processed": processed,
            "errors": len(errors),
            "remaining": remaining,
            "complete": remaining == 0,
            "message": f"Processed {processed} criteria, {remaining} remaining"
        }

    except Exception as e:
        logger.error(f"Error in embedding batch: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/embeddings/status")
async def get_embedding_status():
    """Get status of embedding generation"""
    try:
        total = db.execute_query("SELECT COUNT(*) as count FROM trial_criteria")[0]['count']
        with_embeddings = db.execute_query("""
            SELECT COUNT(*) as count
            FROM trial_criteria
            WHERE semantic_embedding IS NOT NULL
        """)[0]['count']

        return {
            "success": True,
            "total_criteria": total,
            "with_embeddings": with_embeddings,
            "without_embeddings": total - with_embeddings,
            "completion_percentage": round((with_embeddings / total * 100), 1) if total > 0 else 0
        }

    except Exception as e:
        logger.error(f"Error getting embedding status: {e}")
        return {"success": False, "error": str(e)}
