"""
Criterion Embedding Service

Handles semantic embeddings for trial criteria to enable intelligent comparison
without manual keyword maintenance.

Features:
- Generate embeddings for criteria
- Calculate semantic similarity
- Learn from user corrections (feedback loop)
- Auto-adjust matching thresholds
"""

import logging
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from core.database import db
from core.services.gemini_service import gemini_service

logger = logging.getLogger(__name__)

class CriterionEmbeddingService:
    """Service for managing criterion embeddings and semantic similarity"""

    def __init__(self):
        self.default_threshold = 0.75  # Default similarity threshold for grouping
        self.concept_thresholds = {}  # Learned thresholds per concept
        self.embedding_dimension = 768  # Gemini embedding size

    async def generate_and_store_embedding(self, criterion_id: int, criterion_text: str) -> bool:
        """Generate and store embedding for a single criterion"""
        try:
            # Generate embedding using Gemini
            embeddings = await gemini_service.generate_embeddings([criterion_text])

            if not embeddings or len(embeddings) == 0:
                logger.error(f"Failed to generate embedding for criterion {criterion_id}")
                return False

            embedding = embeddings[0]

            # Store in database
            db.execute_update("""
                UPDATE trial_criteria
                SET semantic_embedding = %s::vector,
                    embedding_generated_at = CURRENT_TIMESTAMP,
                    embedding_version = 'text-embedding-004'
                WHERE id = %s
            """, (embedding, criterion_id))

            logger.info(f"✅ Generated embedding for criterion {criterion_id}")
            return True

        except Exception as e:
            logger.error(f"Error generating embedding for criterion {criterion_id}: {e}")
            return False

    async def generate_embeddings_for_trial(self, trial_id: int) -> Dict[str, Any]:
        """Generate embeddings for all criteria in a trial"""
        try:
            # Get all criteria for this trial without embeddings
            criteria = db.execute_query("""
                SELECT id, criterion_text
                FROM trial_criteria
                WHERE trial_id = %s
                AND (semantic_embedding IS NULL OR embedding_generated_at IS NULL)
            """, (trial_id,))

            if not criteria:
                return {"success": True, "message": "All criteria already have embeddings", "count": 0}

            success_count = 0
            for criterion in criteria:
                success = await self.generate_and_store_embedding(
                    criterion['id'],
                    criterion['criterion_text']
                )
                if success:
                    success_count += 1

            return {
                "success": True,
                "total": len(criteria),
                "generated": success_count,
                "message": f"Generated {success_count}/{len(criteria)} embeddings"
            }

        except Exception as e:
            logger.error(f"Error generating trial embeddings: {e}")
            return {"success": False, "error": str(e)}

    def calculate_cosine_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """Calculate cosine similarity between two embeddings"""
        try:
            # Convert to numpy arrays
            vec1 = np.array(embedding1)
            vec2 = np.array(embedding2)

            # Calculate cosine similarity
            dot_product = np.dot(vec1, vec2)
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            similarity = dot_product / (norm1 * norm2)
            return float(similarity)

        except Exception as e:
            logger.error(f"Error calculating cosine similarity: {e}")
            return 0.0

    def get_threshold_for_concept(self, concept: str) -> float:
        """
        Get similarity threshold for a concept (with learned adjustments)

        Starts with default 0.75, adjusts based on user feedback
        """
        # Check if we have learned adjustments for this concept
        if concept in self.concept_thresholds:
            return self.concept_thresholds[concept]

        # Try to load from database
        try:
            adjustments = db.execute_query("""
                SELECT AVG(threshold_adjustment) as avg_adjustment
                FROM criterion_similarity_corrections
                WHERE concept_type = %s
                AND applied_at IS NOT NULL
            """, (concept,))

            if adjustments and adjustments[0]['avg_adjustment']:
                adjusted = self.default_threshold + float(adjustments[0]['avg_adjustment'])
                self.concept_thresholds[concept] = max(0.5, min(0.95, adjusted))
                return self.concept_thresholds[concept]

        except Exception as e:
            logger.warning(f"Could not load threshold adjustments: {e}")

        return self.default_threshold

    async def record_user_correction(self,
                                    criterion_1_id: int,
                                    criterion_2_id: int,
                                    user_action: str,
                                    comparison_id: str,
                                    concept_type: str,
                                    current_similarity: float) -> bool:
        """
        Record user correction for feedback loop

        user_action: 'merge' (should be grouped) or 'separate' (should not be grouped)
        """
        try:
            # Calculate threshold adjustment based on action
            if user_action == 'merge':
                # User wants these grouped but they weren't
                # Lower the threshold for this concept
                adjustment = current_similarity - self.get_threshold_for_concept(concept_type)
                adjustment = max(-0.15, adjustment)  # Don't lower too much
            elif user_action == 'separate':
                # User wants these separated but they were grouped
                # Raise the threshold
                adjustment = 0.05
            else:
                adjustment = 0.0

            # Store correction
            db.execute_update("""
                INSERT INTO criterion_similarity_corrections
                (criterion_1_id, criterion_2_id, embedding_similarity,
                 user_action, comparison_id, concept_type, threshold_adjustment, applied_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """, (criterion_1_id, criterion_2_id, current_similarity,
                  user_action, comparison_id, concept_type, adjustment))

            logger.info(f"📝 Recorded user correction: {user_action} for concept {concept_type}")

            # Update in-memory threshold
            if concept_type in self.concept_thresholds:
                self.concept_thresholds[concept_type] += adjustment
            else:
                self.concept_thresholds[concept_type] = self.default_threshold + adjustment

            return True

        except Exception as e:
            logger.error(f"Error recording correction: {e}")
            return False

    async def compare_criteria_with_embeddings(self,
                                              criteria_1: List[Dict],
                                              criteria_2: List[Dict],
                                              concept: str = None) -> List[Tuple[int, int, float]]:
        """
        Compare criteria using embeddings

        Returns: [(criterion_1_id, criterion_2_id, similarity_score), ...]
        """
        matches = []

        threshold = self.get_threshold_for_concept(concept) if concept else self.default_threshold

        for c1 in criteria_1:
            emb1 = c1.get('semantic_embedding')
            if not emb1:
                continue

            for c2 in criteria_2:
                emb2 = c2.get('semantic_embedding')
                if not emb2:
                    continue

                similarity = self.calculate_cosine_similarity(emb1, emb2)

                if similarity >= threshold:
                    matches.append((c1['id'], c2['id'], similarity))

        return matches


# Singleton instance
criterion_embedding_service = CriterionEmbeddingService()
