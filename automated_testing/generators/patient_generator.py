"""
Patient Profile Generator for Automated Testing

Generates synthetic patient profiles with three types:
1. Random - Realistic random patients
2. Targeted - Designed to match specific trial criteria
3. Edge Case - Extreme or unusual cases to test system robustness
"""

import random
import uuid
from typing import Dict, List, Any
from dataclasses import dataclass, asdict
import json


@dataclass
class PatientProfile:
    """Represents a synthetic patient for testing"""
    patient_id: str
    profile_type: str  # random, targeted, edge_case
    demographics: Dict[str, Any]
    medical_history: Dict[str, Any]
    expected_behavior: Dict[str, Any]

    def to_dict(self):
        return asdict(self)


class PatientGenerator:
    """Generates synthetic patient profiles for automated testing"""

    # REAL conditions from your database (as of Dec 2025)
    CONDITIONS = {
        "hidradenitis_suppurativa": {
            "trial_count": 5,  # Most common!
            "display_name": "Hidradenitis Suppurativa",
            "medications": ["Humira", "Adalimumab", "Antibiotics", "Spironolactone"],
            "related_conditions": ["Acne", "Obesity", "Depression"],
            "typical_age_range": (18, 65),
            "severity_indicators": ["hurley_stage", "abscess_count"],
        },
        "gout": {
            "trial_count": 4,
            "display_name": "Gout",
            "medications": ["Allopurinol", "Febuxostat", "Colchicine", "Probenecid"],
            "related_conditions": ["Kidney Disease", "Obesity"],
            "typical_age_range": (30, 70),
            "severity_indicators": ["flares_per_year", "uric_acid_level"],
        },
        "major_depressive_disorder": {
            "trial_count": 3,
            "display_name": "Major Depressive Disorder",
            "medications": ["Sertraline", "Escitalopram", "Bupropion", "Venlafaxine"],
            "related_conditions": ["Anxiety", "Sleep Disorders", "Chronic Pain"],
            "typical_age_range": (18, 70),
            "severity_indicators": ["PHQ9_score", "duration_months"],
        },
        "androgenetic_alopecia": {
            "trial_count": 3,
            "display_name": "Androgenetic Alopecia",
            "medications": ["Minoxidil", "Finasteride", "Biotin"],
            "related_conditions": [],
            "typical_age_range": (25, 65),
            "severity_indicators": ["ludwig_scale", "duration_years"],
        },
        "overweight": {
            "trial_count": 2,
            "display_name": "Overweight",
            "medications": [],
            "related_conditions": ["Type 2 Diabetes", "Hypertension", "Sleep Apnea"],
            "typical_age_range": (25, 70),
            "severity_indicators": ["BMI", "waist_circumference"],
        },
        "acne_vulgaris": {
            "trial_count": 2,
            "display_name": "Acne Vulgaris",
            "medications": ["Tretinoin", "Benzoyl Peroxide", "Doxycycline", "Isotretinoin"],
            "related_conditions": [],
            "typical_age_range": (13, 40),
            "severity_indicators": ["severity_grade", "lesion_count"],
        },
        "type_2_diabetes": {
            "trial_count": 1,
            "display_name": "Type 2 Diabetes",
            "medications": ["Metformin", "Insulin", "Glipizide", "Jardiance", "Ozempic"],
            "related_conditions": ["Hypertension", "High Cholesterol", "Obesity"],
            "typical_age_range": (25, 75),
            "severity_indicators": ["A1C", "blood_glucose"],
        },
        "plaque_psoriasis": {
            "trial_count": 1,
            "display_name": "Plaque Psoriasis",
            "medications": ["Methotrexate", "Biologics", "Topical Corticosteroids"],
            "related_conditions": ["Psoriatic Arthritis"],
            "typical_age_range": (18, 75),
            "severity_indicators": ["PASI_score", "BSA_percentage"],
        },
        "osteoarthritis_knee": {
            "trial_count": 1,
            "display_name": "Osteoarthritis of the Knee",
            "medications": ["NSAIDs", "Acetaminophen", "Cortisone Injections"],
            "related_conditions": ["Obesity", "Previous Knee Injury"],
            "typical_age_range": (45, 80),
            "severity_indicators": ["pain_scale", "mobility_score"],
        },
        "bipolar_depression": {
            "trial_count": 1,
            "display_name": "Bipolar Depression",
            "medications": ["Lithium", "Quetiapine", "Lamotrigine", "Valproate"],
            "related_conditions": ["Anxiety", "Substance Use"],
            "typical_age_range": (18, 65),
            "severity_indicators": ["episode_frequency", "severity"],
        },
    }

    # REAL DelRicht locations
    LOCATIONS = [
        "Atlanta, GA",       # ATL - General Medicine (2327)
        "Baton Rouge, LA",   # BR - Dermatology (1266)
        "New Orleans, LA",   # NO - Multiple sites
        "Jackson, MS",       # MS sites
        "Memphis, TN",       # TN sites
        "Birmingham, AL",    # AL sites
        "Charlotte, NC",     # NC sites
        "Nashville, TN",     # TN sites
    ]

    GENDERS = ["male", "female", "non-binary"]

    FIRST_NAMES_MALE = ["James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph", "Thomas", "Charles"]
    FIRST_NAMES_FEMALE = ["Mary", "Patricia", "Jennifer", "Linda", "Barbara", "Elizabeth", "Susan", "Jessica", "Sarah", "Karen"]
    LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]

    def __init__(self):
        random.seed()  # Use system time for true randomness

    def generate_batch(self, total: int) -> List[PatientProfile]:
        """
        Generate a batch of patient profiles with mixed types

        Args:
            total: Total number of profiles to generate

        Returns:
            List of PatientProfile objects
        """
        import sys
        print(f"[DEBUG-GEN] generate_batch called with total={total}", flush=True)
        sys.stdout.flush()

        profiles = []

        # Calculate distribution (40% random, 40% targeted, 20% edge case)
        num_random = int(total * 0.4)
        num_targeted = int(total * 0.4)
        num_edge = total - num_random - num_targeted

        print(f"[DEBUG-GEN] Distribution: random={num_random}, targeted={num_targeted}, edge={num_edge}", flush=True)

        # Generate each type
        print(f"[DEBUG-GEN] Generating {num_random} random patients...", flush=True)
        for i in range(num_random):
            profiles.append(self._generate_random_patient(i))
        print(f"[DEBUG-GEN] Random patients complete", flush=True)

        print(f"[DEBUG-GEN] Generating {num_targeted} targeted patients...", flush=True)
        for i in range(num_targeted):
            profiles.append(self._generate_targeted_patient(i))
        print(f"[DEBUG-GEN] Targeted patients complete", flush=True)

        print(f"[DEBUG-GEN] Generating {num_edge} edge case patients...", flush=True)
        for i in range(num_edge):
            profiles.append(self._generate_edge_case_patient(i))
        print(f"[DEBUG-GEN] Edge case patients complete", flush=True)

        # Shuffle to mix types
        print(f"[DEBUG-GEN] Shuffling {len(profiles)} profiles...", flush=True)
        random.shuffle(profiles)

        print(f"[DEBUG-GEN] Returning {len(profiles)} profiles", flush=True)
        return profiles

    def _generate_random_patient(self, index: int) -> PatientProfile:
        """Generate a realistic random patient"""

        # Pick primary condition
        primary_condition = random.choice(list(self.CONDITIONS.keys()))
        condition_data = self.CONDITIONS[primary_condition]

        # Demographics
        age_min, age_max = condition_data["typical_age_range"]
        age = random.randint(age_min, age_max)
        gender = random.choice(self.GENDERS)
        location = random.choice(self.LOCATIONS)

        # Name based on gender
        if gender == "male":
            first_name = random.choice(self.FIRST_NAMES_MALE)
        else:
            first_name = random.choice(self.FIRST_NAMES_FEMALE)
        last_name = random.choice(self.LAST_NAMES)

        # Medical history
        num_medications = random.randint(1, 3)
        medications = random.sample(condition_data["medications"], min(num_medications, len(condition_data["medications"])))

        # Add some related conditions (30% chance)
        display_name = condition_data["display_name"]
        conditions = [display_name]
        if random.random() < 0.3 and condition_data["related_conditions"]:
            num_related = random.randint(1, 2)
            related = random.sample(condition_data["related_conditions"], min(num_related, len(condition_data["related_conditions"])))
            conditions.extend(related)

        return PatientProfile(
            patient_id=f"AUTO_TEST_random_{index:03d}",
            profile_type="random",
            demographics={
                "age": age,
                "gender": gender,
                "location": location,
                "name": f"{first_name} {last_name}",
                "first_name": first_name,
                "last_name": last_name,
            },
            medical_history={
                "primary_condition": display_name,
                "conditions": conditions,
                "medications": medications if medications else [],  # Empty list if no medications
                "duration_years": random.randint(1, 10),
            },
            expected_behavior={
                "should_find_trials": True,
                "likely_eligible": True,  # Random patients should generally be eligible
                "expected_question_count_range": (4, 8),
            }
        )

    def _generate_targeted_patient(self, index: int) -> PatientProfile:
        """Generate patient targeted to specific trial criteria"""

        # Create patients that specifically match known trial types
        trial_types = [
            self._create_gout_trial_patient,
            self._create_psoriasis_trial_patient,
            self._create_diabetes_trial_patient,
            self._create_alzheimers_caregiver_patient,
        ]

        creator = random.choice(trial_types)
        return creator(index)

    def _create_gout_trial_patient(self, index: int) -> PatientProfile:
        """Create patient for gout trials"""
        age = random.randint(30, 65)
        gender = random.choice(["male", "female"])

        # Gout trials typically require:
        # - Diagnosis of gout
        # - Multiple flares
        # - May exclude those on certain medications

        return PatientProfile(
            patient_id=f"AUTO_TEST_targeted_gout_{index:03d}",
            profile_type="targeted",
            demographics={
                "age": age,
                "gender": gender,
                "location": random.choice(self.LOCATIONS),
                "name": "Test Gout Patient",
                "first_name": "Test",
                "last_name": "Patient",
            },
            medical_history={
                "primary_condition": "Gout",
                "conditions": ["Gout", "Hypertension"],
                "medications": ["Lisinopril"],  # Not on gout meds (common exclusion)
                "duration_years": 3,
                "flares_per_year": 4,
                "uric_acid_level": 8.5,
            },
            expected_behavior={
                "should_find_trials": True,
                "likely_eligible": True,
                "expected_question_count_range": (5, 8),
                "target_trial_keywords": ["gout"],
            }
        )

    def _create_psoriasis_trial_patient(self, index: int) -> PatientProfile:
        """Create patient for psoriasis trials"""
        age = random.randint(25, 60)

        return PatientProfile(
            patient_id=f"AUTO_TEST_targeted_psoriasis_{index:03d}",
            profile_type="targeted",
            demographics={
                "age": age,
                "gender": random.choice(["male", "female"]),
                "location": random.choice(self.LOCATIONS),
                "name": "Test Psoriasis Patient",
                "first_name": "Test",
                "last_name": "Patient",
            },
            medical_history={
                "primary_condition": "Psoriasis",
                "conditions": ["Plaque Psoriasis"],
                "medications": ["Topical Corticosteroids"],
                "duration_years": 5,
                "pasi_score": 15,
                "body_surface_area": "20%",
            },
            expected_behavior={
                "should_find_trials": True,
                "likely_eligible": True,
                "expected_question_count_range": (4, 7),
                "target_trial_keywords": ["psoriasis", "dermatology"],
            }
        )

    def _create_diabetes_trial_patient(self, index: int) -> PatientProfile:
        """Create patient for diabetes trials"""
        age = random.randint(30, 70)

        return PatientProfile(
            patient_id=f"AUTO_TEST_targeted_diabetes_{index:03d}",
            profile_type="targeted",
            demographics={
                "age": age,
                "gender": random.choice(["male", "female"]),
                "location": random.choice(self.LOCATIONS),
                "name": "Test Diabetes Patient",
                "first_name": "Test",
                "last_name": "Patient",
            },
            medical_history={
                "primary_condition": "Type 2 Diabetes",
                "conditions": ["Type 2 Diabetes", "Hypertension"],
                "medications": ["Metformin", "Lisinopril"],
                "duration_years": 7,
                "a1c": 8.2,
                "blood_glucose": 180,
            },
            expected_behavior={
                "should_find_trials": True,
                "likely_eligible": True,
                "expected_question_count_range": (5, 9),
                "target_trial_keywords": ["diabetes"],
            }
        )

    def _create_alzheimers_caregiver_patient(self, index: int) -> PatientProfile:
        """Create caregiver patient for Alzheimer's trials"""
        age = random.randint(40, 70)

        return PatientProfile(
            patient_id=f"AUTO_TEST_targeted_alzheimers_{index:03d}",
            profile_type="targeted",
            demographics={
                "age": age,
                "gender": random.choice(["male", "female"]),
                "location": random.choice(self.LOCATIONS),
                "name": "Test Caregiver",
                "first_name": "Test",
                "last_name": "Caregiver",
            },
            medical_history={
                "primary_condition": "Caregiver for Alzheimer's Patient",
                "conditions": ["Caring for Alzheimer's Patient"],
                "medications": [],
                "duration_years": 2,
                "patient_stage": "Mild to Moderate",
                "caregiver_hours_per_week": 30,
            },
            expected_behavior={
                "should_find_trials": True,
                "likely_eligible": True,
                "expected_question_count_range": (4, 7),
                "target_trial_keywords": ["alzheimer", "caregiver"],
            }
        )

    def _generate_edge_case_patient(self, index: int) -> PatientProfile:
        """Generate edge case patient to test system robustness"""

        edge_case_types = [
            self._create_extreme_age_patient,
            self._create_multi_morbidity_patient,
            self._create_contradictory_patient,
            self._create_unclear_responder_patient,
        ]

        creator = random.choice(edge_case_types)
        return creator(index)

    def _create_extreme_age_patient(self, index: int) -> PatientProfile:
        """Patient with extreme age (very young or very old)"""
        age = random.choice([18, 19, 75, 80, 90, 100])

        return PatientProfile(
            patient_id=f"AUTO_TEST_edge_age_{index:03d}",
            profile_type="edge_case",
            demographics={
                "age": age,
                "gender": random.choice(self.GENDERS),
                "location": random.choice(self.LOCATIONS),
                "name": "Edge Age Patient",
                "first_name": "Edge",
                "last_name": "Patient",
            },
            medical_history={
                "primary_condition": "Hypertension",
                "conditions": ["Hypertension"],
                "medications": ["Lisinopril"],
                "duration_years": 1,
            },
            expected_behavior={
                "should_find_trials": True,
                "likely_eligible": False if age > 75 else True,  # Many trials exclude >75
                "expected_question_count_range": (3, 8),
                "edge_case_type": "extreme_age",
            }
        )

    def _create_multi_morbidity_patient(self, index: int) -> PatientProfile:
        """Patient with many conditions and medications"""

        # Pick 4-6 conditions
        all_conditions = list(self.CONDITIONS.keys())
        num_conditions = random.randint(4, 6)
        selected_conditions = random.sample(all_conditions, num_conditions)

        # Gather medications from all conditions
        all_meds = []
        for condition in selected_conditions:
            all_meds.extend(self.CONDITIONS[condition]["medications"])

        # Pick 5-8 medications
        num_meds = random.randint(5, min(8, len(all_meds)))
        medications = random.sample(all_meds, num_meds)

        return PatientProfile(
            patient_id=f"AUTO_TEST_edge_multimorbid_{index:03d}",
            profile_type="edge_case",
            demographics={
                "age": random.randint(55, 75),
                "gender": random.choice(self.GENDERS),
                "location": random.choice(self.LOCATIONS),
                "name": "Multi Condition Patient",
                "first_name": "Multi",
                "last_name": "Patient",
            },
            medical_history={
                "primary_condition": selected_conditions[0].replace("_", " ").title(),
                "conditions": [c.replace("_", " ").title() for c in selected_conditions],
                "medications": medications,
                "duration_years": 10,
            },
            expected_behavior={
                "should_find_trials": True,
                "likely_eligible": False,  # Complex patients often excluded
                "expected_question_count_range": (5, 10),
                "edge_case_type": "multi_morbidity",
            }
        )

    def _create_contradictory_patient(self, index: int) -> PatientProfile:
        """Patient with contradictory information"""

        return PatientProfile(
            patient_id=f"AUTO_TEST_edge_contradictory_{index:03d}",
            profile_type="edge_case",
            demographics={
                "age": 45,
                "gender": "male",
                "location": random.choice(self.LOCATIONS),
                "name": "Contradictory Patient",
                "first_name": "Contradictory",
                "last_name": "Patient",
            },
            medical_history={
                "primary_condition": "Diabetes",
                "conditions": [],  # Says no conditions but takes diabetes meds
                "medications": ["Metformin", "Insulin"],
                "duration_years": 0,  # Says just started but has complications
                "contradictions": {
                    "says_no_diabetes": True,
                    "but_takes_diabetes_meds": True,
                }
            },
            expected_behavior={
                "should_find_trials": True,
                "likely_eligible": "unclear",
                "expected_question_count_range": (4, 8),
                "edge_case_type": "contradictory_info",
                "should_handle_gracefully": True,
            }
        )

    def _create_unclear_responder_patient(self, index: int) -> PatientProfile:
        """Patient who gives unclear/ambiguous answers"""

        return PatientProfile(
            patient_id=f"AUTO_TEST_edge_unclear_{index:03d}",
            profile_type="edge_case",
            demographics={
                "age": 50,
                "gender": "female",
                "location": random.choice(self.LOCATIONS),
                "name": "Unclear Patient",
                "first_name": "Unclear",
                "last_name": "Patient",
            },
            medical_history={
                "primary_condition": "Hypertension",
                "conditions": ["Hypertension"],
                "medications": ["Lisinopril"],
                "duration_years": 5,
                "response_style": "unclear",  # Will use "maybe", "I don't know", etc.
            },
            expected_behavior={
                "should_find_trials": True,
                "likely_eligible": "unclear",
                "expected_question_count_range": (5, 12),  # May need clarifications
                "edge_case_type": "unclear_responses",
                "should_ask_for_clarification": True,
            }
        )

    def save_profiles_to_file(self, profiles: List[PatientProfile], filepath: str):
        """Save generated profiles to JSON file for review/reuse"""
        with open(filepath, 'w') as f:
            json.dump([p.to_dict() for p in profiles], f, indent=2)

    def load_profiles_from_file(self, filepath: str) -> List[PatientProfile]:
        """Load profiles from JSON file"""
        with open(filepath, 'r') as f:
            data = json.load(f)
            return [PatientProfile(**p) for p in data]


if __name__ == "__main__":
    # Test the generator
    generator = PatientGenerator()

    print("🧬 Testing Patient Generator\n")

    # Generate small batch
    profiles = generator.generate_batch(10)

    print(f"Generated {len(profiles)} patient profiles:\n")

    for profile in profiles:
        print(f"ID: {profile.patient_id}")
        print(f"Type: {profile.profile_type}")
        print(f"Age: {profile.demographics['age']}, Gender: {profile.demographics['gender']}")
        print(f"Location: {profile.demographics['location']}")
        print(f"Primary Condition: {profile.medical_history['primary_condition']}")
        print(f"Medications: {', '.join(profile.medical_history['medications'])}")
        print()

    # Count by type
    types = {}
    for p in profiles:
        types[p.profile_type] = types.get(p.profile_type, 0) + 1

    print(f"\nDistribution: {types}")
