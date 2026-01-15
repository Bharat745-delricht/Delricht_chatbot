"""Trial search service for location-based queries"""
from typing import List, Dict, Any, Tuple, Optional
from core.database import db
import logging
import time
import json
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TrialSearchService:
    """Service for searching and formatting trial information"""

    def __init__(self):
        """Initialize with cache"""
        # Cache: {(condition, location): (results, timestamp)}
        self._search_cache: Dict[Tuple[str, str], Tuple[List[Dict[str, Any]], datetime]] = {}
        self._cache_ttl = timedelta(minutes=5)  # 5 minute cache
        self._max_cache_size = 100

    def _get_cache_key(self, condition: str = None, location: str = None) -> Tuple[str, str]:
        """Generate cache key from condition and location"""
        return (
            (condition or "").lower().strip(),
            (location or "").lower().strip()
        )

    def _get_from_cache(self, condition: str = None, location: str = None):
        """Get results from cache if not expired"""
        cache_key = self._get_cache_key(condition, location)

        if cache_key in self._search_cache:
            results, timestamp = self._search_cache[cache_key]

            # Check if cache is still fresh
            if datetime.now() - timestamp < self._cache_ttl:
                logger.info(f"Cache hit for search: condition={condition}, location={location}")
                return results
            else:
                # Expired, remove it
                del self._search_cache[cache_key]

        return None

    def _save_to_cache(self, results: List[Dict[str, Any]], condition: str = None, location: str = None):
        """Save results to cache"""
        cache_key = self._get_cache_key(condition, location)

        # Simple LRU: if cache is full, remove oldest entry
        if len(self._search_cache) >= self._max_cache_size:
            oldest_key = min(self._search_cache.keys(), key=lambda k: self._search_cache[k][1])
            del self._search_cache[oldest_key]

        self._search_cache[cache_key] = (results, datetime.now())
        logger.info(f"Cached search results: condition={condition}, location={location}, count={len(results)}")

    def get_trials_by_location(self, location: str) -> List[Dict[str, Any]]:
        """Get all trials available in a specific location"""

        # Check cache first
        cached_results = self._get_from_cache(condition=None, location=location)
        if cached_results is not None:
            return cached_results

        trials = db.execute_query("""
            SELECT DISTINCT
                ct.id,
                ct.trial_name,
                ct.conditions,
                ti.investigator_name,
                ti.site_location,
                pm.protocol_summary,
                site_counts.total_sites
            FROM clinical_trials ct
            JOIN trial_investigators ti ON ct.id = ti.trial_id
            LEFT JOIN (
                SELECT DISTINCT ON (trial_id) 
                trial_id, protocol_summary 
                FROM protocol_metadata 
                ORDER BY trial_id, created_at DESC
            ) pm ON ct.id = pm.trial_id
            LEFT JOIN (
                SELECT trial_id, COUNT(*) as total_sites
                FROM trial_investigators
                GROUP BY trial_id
            ) site_counts ON ct.id = site_counts.trial_id
            WHERE ct.id IS NOT NULL
            AND LOWER(ti.site_location) LIKE LOWER(%s)
            ORDER BY ct.conditions, ct.trial_name
        """, (f"%{location}%",))

        # Save to cache
        self._save_to_cache(trials, condition=None, location=location)

        return trials
    
    def format_location_trials_message(self, location: str, trials: List[Dict[str, Any]]) -> str:
        """Format trials into a readable message"""
        
        if not trials:
            return f"I couldn't find any clinical trials in {location}. Would you like to search in a nearby location?"
        
        # Check if all trials have the same investigator
        investigators = list(set([trial['investigator_name'] for trial in trials]))
        all_same_investigator = len(investigators) == 1
        
        # Format message
        if len(trials) == 1:
            trial = trials[0]
            message = f"I found 1 clinical trial in {location}:\n\n"
            message += f"**{trial['conditions']}** trial\n"
            message += f"*Investigator: {trial['investigator_name']}*"
        else:
            message = f"Great! I found {len(trials)} clinical trials available in {location}:\n\n"
            
            # Group by condition for better readability
            by_condition = {}
            for trial in trials:
                # Normalize condition name (remove extra spaces)
                cond = ' '.join(trial['conditions'].split())
                if cond not in by_condition:
                    by_condition[cond] = []
                by_condition[cond].append(trial)
            
            # List conditions
            for condition, condition_trials in by_condition.items():
                # Use singular/plural appropriately
                trial_word = "trial" if len(condition_trials) == 1 else "trials"
                message += f"**{condition}** ({len(condition_trials)} {trial_word})\n"
                
                # Only list investigators if not all the same
                if not all_same_investigator:
                    investigators = list(set([t['investigator_name'] for t in condition_trials]))
                    for inv in investigators:
                        message += f"• {inv}\n"
            
            # If all trials have same investigator, mention at the end
            if all_same_investigator:
                message += f"\nAll trials are conducted by {investigators[0]}.\n"
        
        # Add call to action
        message += "\nWould you like to:\n"
        message += "1. Check your eligibility for any of these trials?\n"
        message += "2. Learn more about a specific condition?\n"
        message += "3. See trials in a different location?"
        
        return message
    
    def get_trial_count_by_location(self, location: str) -> int:
        """Get count of trials in a location"""
        
        result = db.execute_query("""
            SELECT COUNT(DISTINCT ct.id) as count
            FROM clinical_trials ct
            JOIN trial_investigators ti ON ct.id = ti.trial_id
            WHERE ct.id IS NOT NULL
            AND LOWER(ti.site_location) LIKE LOWER(%s)
        """, (f"%{location}%",))
        
        return result[0]['count'] if result else 0
    
    def get_trials_by_condition_and_location(self, condition: str, location: str) -> List[Dict[str, Any]]:
        """Get trials filtered by both condition and location"""
        # Get all trials in the location
        all_trials = self.get_trials_by_location(location)
        
        if not all_trials:
            return []
        
        # Filter by condition
        matching_trials = []
        for trial in all_trials:
            trial_conditions = trial.get("conditions", "").lower()
            if condition.lower() in trial_conditions:
                matching_trials.append(trial)
        
        return matching_trials
    
    def search_trials(self, condition: str = None, location: str = None,
                     session_id: str = None) -> List[Dict[str, Any]]:
        """Search for trials by condition and/or location with analytics tracking"""

        # Check cache first
        cached_results = self._get_from_cache(condition=condition, location=location)
        if cached_results is not None:
            return cached_results

        # Start timing for analytics
        start_time = time.time()
        
        # Build dynamic query based on provided parameters
        base_query = """
            SELECT DISTINCT
                ct.id,
                ct.trial_name,
                ct.conditions,
                ct.description,
                ti.investigator_name,
                ti.site_location,
                pm.protocol_summary,
                site_counts.total_sites
            FROM clinical_trials ct
            JOIN trial_investigators ti ON ct.id = ti.trial_id
            LEFT JOIN (
                SELECT DISTINCT ON (trial_id) 
                trial_id, protocol_summary 
                FROM protocol_metadata 
                ORDER BY trial_id, created_at DESC
            ) pm ON ct.id = pm.trial_id
            LEFT JOIN (
                SELECT trial_id, COUNT(*) as total_sites
                FROM trial_investigators
                GROUP BY trial_id
            ) site_counts ON ct.id = site_counts.trial_id
        """
        
        conditions = []
        params = []

        # Always add a basic WHERE clause for data consistency
        conditions.append("ct.id IS NOT NULL")

        # Exclude orphaned trials (trials without proper site assignment)
        # These have site_location text but no site_id, can't be booked
        conditions.append("ti.site_id IS NOT NULL")
        
        if condition:
            # Normalize condition for better matching (handle plurals, common variations)
            normalized_condition = self._normalize_condition(condition)
            conditions.append("LOWER(ct.conditions) LIKE LOWER(%s)")
            params.append(f"%{normalized_condition}%")
        
        if location:
            # Normalize location for better matching
            normalized_location = self._normalize_location(location)
            conditions.append("LOWER(ti.site_location) LIKE LOWER(%s)")
            params.append(f"%{normalized_location}%")
        
        base_query += " WHERE " + " AND ".join(conditions)
        base_query += " ORDER BY ct.conditions, ct.trial_name"
        
        logger.info(f"Searching trials with condition='{condition}', location='{location}'")
        
        try:
            trials = db.execute_query(base_query, tuple(params))
            logger.info(f"Found {len(trials)} trials matching search criteria")

            # Log search analytics if session_id provided
            if session_id:
                self._log_search_analytics(
                    session_id=session_id,
                    condition=condition,
                    location=location,
                    trials=trials,
                    start_time=start_time,
                    search_type='keyword'
                )

            # Save to cache
            self._save_to_cache(trials, condition=condition, location=location)

            return trials
        except Exception as e:
            logger.error(f"Error searching trials: {e}")
            return []

    async def search_trials_semantic(
        self,
        condition: str = None,
        location: str = None,
        session_id: str = None,
        similarity_threshold: float = 0.70
    ) -> List[Dict[str, Any]]:
        """
        Search trials using semantic embeddings for condition matching.
        Combines vector similarity with location filtering for best results.

        Args:
            condition: Medical condition to search for
            location: Geographic location to filter by
            session_id: Session ID for analytics tracking
            similarity_threshold: Minimum cosine similarity (default: 0.70)

        Returns:
            List of trials ranked by semantic similarity
        """
        from core.services.gemini_service import gemini_service

        if not condition:
            logger.warning("Semantic search called without condition")
            return []

        start_time = time.time()

        try:
            # Generate query embedding
            logger.info(f"Generating embedding for condition: '{condition}'")
            condition_embedding = await gemini_service.generate_embeddings([condition])

            if not condition_embedding or len(condition_embedding) == 0:
                logger.error("Failed to generate embedding - falling back to keyword search")
                return []

            # Build query with hybrid approach:
            # 1. Filter by location (keyword - fast and precise)
            # 2. Rank by semantic similarity (vector - catches variations)
            query = """
                SELECT DISTINCT
                    ct.id,
                    ct.trial_name,
                    ct.conditions,
                    ct.description,
                    ti.investigator_name,
                    ti.site_location,
                    pm.protocol_summary,
                    site_counts.total_sites,
                    (1 - (ct.semantic_embedding <=> %s::vector)) as similarity_score
                FROM clinical_trials ct
                JOIN trial_investigators ti ON ct.id = ti.trial_id
                LEFT JOIN (
                    SELECT DISTINCT ON (trial_id)
                    trial_id, protocol_summary
                    FROM protocol_metadata
                    ORDER BY trial_id, created_at DESC
                ) pm ON ct.id = pm.trial_id
                LEFT JOIN (
                    SELECT trial_id, COUNT(*) as total_sites
                    FROM trial_investigators
                    GROUP BY trial_id
                ) site_counts ON ct.id = site_counts.trial_id
                WHERE ct.id IS NOT NULL
                  AND ti.site_id IS NOT NULL
                  AND ct.semantic_embedding IS NOT NULL
            """

            params = [condition_embedding[0]]

            # Add location filter if provided
            if location:
                normalized_location = self._normalize_location(location)
                query += " AND LOWER(ti.site_location) LIKE LOWER(%s)"
                params.append(f"%{normalized_location}%")

            # Filter by similarity threshold and order by relevance
            query += """
                AND (1 - (ct.semantic_embedding <=> %s::vector)) > %s
                ORDER BY similarity_score DESC
                LIMIT 20
            """
            params.extend([condition_embedding[0], similarity_threshold])

            trials = db.execute_query(query, tuple(params))

            logger.info(f"Semantic search found {len(trials)} trials (threshold: {similarity_threshold})")

            # CRITICAL VALIDATION: Filter out trials that don't actually match the condition
            # Semantic search can return false positives, so validate with keyword matching
            if trials and condition:
                validated_trials = []
                condition_lower = condition.lower()
                condition_words = set(word for word in condition_lower.split() if len(word) > 3)

                for trial in trials:
                    trial_conditions = trial.get('conditions', '').lower()

                    # Check if there's a reasonable keyword match
                    is_valid = (
                        condition_lower in trial_conditions or
                        trial_conditions in condition_lower or
                        any(word in trial_conditions for word in condition_words)
                    )

                    if is_valid:
                        validated_trials.append(trial)
                    else:
                        logger.warning(f"⚠️  Semantic search returned Trial {trial['id']} ({trial['conditions']}) for '{condition}' - filtering out as irrelevant")

                logger.info(f"After validation: {len(validated_trials)}/{len(trials)} trials are relevant")
                trials = validated_trials

            # Log analytics
            if session_id:
                self._log_search_analytics(
                    session_id=session_id,
                    condition=condition,
                    location=location,
                    trials=trials,
                    start_time=start_time,
                    search_type='semantic_vector'
                )

            # Save to cache
            self._save_to_cache(trials, condition=condition, location=location)

            return trials

        except Exception as e:
            logger.error(f"Error in semantic search: {str(e)}")
            return []

    async def search_trials_hybrid(
        self,
        condition: str = None,
        location: str = None,
        session_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search: Try semantic first, fallback to keyword if needed.
        Best of both worlds - semantic catches variations, keyword is reliable fallback.

        Args:
            condition: Medical condition to search for
            location: Geographic location
            session_id: Session ID for analytics

        Returns:
            List of matching trials
        """
        # Try semantic search first
        semantic_results = await self.search_trials_semantic(
            condition=condition,
            location=location,
            session_id=session_id,
            similarity_threshold=0.70
        )

        # If semantic found results, use them
        if semantic_results and len(semantic_results) > 0:
            logger.info(f"✓ Semantic search successful: {len(semantic_results)} trials found")
            return semantic_results

        # Fallback to keyword search
        logger.info("Semantic search found nothing - falling back to keyword search")
        keyword_results = self.search_trials(
            condition=condition,
            location=location,
            session_id=session_id
        )

        return keyword_results

    def _normalize_location(self, location: str) -> str:
        """Normalize location strings for better matching"""
        if not location:
            return location
            
        # Convert common state name variations to abbreviations
        state_mapping = {
            'louisiana': 'LA',
            'texas': 'TX', 
            'georgia': 'GA',
            'tennessee': 'TN',
            'north carolina': 'NC',
            'south carolina': 'SC',
            'mississippi': 'MS',
            'kentucky': 'KY',
            'kansas': 'KS',
            'ohio': 'OH',
            'indiana': 'IN',
            'maryland': 'MD',
            'missouri': 'MO',
            'oklahoma': 'OK'
        }
        
        location_lower = location.lower()
        
        # Check for state name replacements
        for state_name, state_abbr in state_mapping.items():
            if state_name in location_lower:
                location = location_lower.replace(state_name, state_abbr)
                break
        
        # Remove common separators and extra spaces
        location = location.replace(',', '').strip()
        
        # If it ends with just the state abbreviation, try just the city
        parts = location.split()
        if len(parts) >= 2 and parts[-1].upper() in state_mapping.values():
            # Try both full location and just city name
            city_only = ' '.join(parts[:-1])
            return city_only
        
        return location
    
    def _log_search_analytics(self, session_id: str, condition: str, location: str, 
                             trials: List[Dict[str, Any]], start_time: float, 
                             search_type: str = 'keyword'):
        """Log search analytics for business intelligence"""
        try:
            # Calculate search duration
            search_duration = int((time.time() - start_time) * 1000)
            
            # Build similarity scores (mock for keyword search)
            similarity_scores = {}
            matched_trials = []
            
            for trial in trials:
                trial_id = str(trial['id'])
                # For keyword search, similarity is based on exact match
                similarity = 1.0 if condition and condition.lower() in trial['conditions'].lower() else 0.5
                similarity_scores[trial_id] = similarity
                
                matched_trials.append({
                    'id': trial['id'],
                    'name': trial['trial_name'],
                    'conditions': trial['conditions'],
                    'relevance': similarity
                })
            
            # Insert search analytics
            db.execute_update("""
                INSERT INTO search_analytics 
                (session_id, search_type, query_condition, query_location, 
                 similarity_scores, matched_trials, search_duration_ms, results_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                session_id,
                search_type,
                condition or '',
                location or '',
                json.dumps(similarity_scores),
                json.dumps(matched_trials),
                search_duration,
                len(trials)
            ))
            
            logger.info(f"Logged search analytics: {len(trials)} results in {search_duration}ms")
            
        except Exception as e:
            logger.warning(f"Failed to log search analytics: {str(e)}")
    
    def semantic_search_trials(self, condition: str, location: str = None, 
                             session_id: str = None) -> List[Dict[str, Any]]:
        """Perform semantic search with enhanced similarity scoring"""
        
        # Start timing for analytics
        start_time = time.time()
        
        # For now, use enhanced keyword matching with semantic-like scoring
        # In a real implementation, this would use embeddings/vector search
        
        base_query = """
            SELECT DISTINCT
                ct.id,
                ct.trial_name,
                ct.conditions,
                ct.description,
                ti.investigator_name,
                ti.site_location,
                pm.protocol_summary,
                COUNT(*) OVER (PARTITION BY ct.id) as total_sites
            FROM clinical_trials ct
            JOIN trial_investigators ti ON ct.id = ti.trial_id
            LEFT JOIN protocol_metadata pm ON ct.id = pm.trial_id
            WHERE ct.id IS NOT NULL
        """
        
        params = []
        
        # Enhanced condition matching
        if condition:
            base_query += " AND (LOWER(ct.conditions) LIKE LOWER(%s) OR LOWER(ct.description) LIKE LOWER(%s))"
            params.extend([f"%{condition}%", f"%{condition}%"])
        
        if location:
            normalized_location = self._normalize_location(location)
            base_query += " AND LOWER(ti.site_location) LIKE LOWER(%s)"
            params.append(f"%{normalized_location}%")
        
        base_query += " ORDER BY ct.conditions, ct.trial_name"
        
        try:
            trials = db.execute_query(base_query, tuple(params))
            
            # Add semantic similarity scoring
            enhanced_trials = []
            for trial in trials:
                # Calculate semantic similarity score
                similarity_score = self._calculate_semantic_similarity(condition, trial)
                trial['similarity'] = similarity_score
                enhanced_trials.append(trial)
            
            # Sort by similarity score
            enhanced_trials.sort(key=lambda x: x['similarity'], reverse=True)
            
            # Log search analytics if session_id provided
            if session_id:
                self._log_search_analytics(
                    session_id=session_id,
                    condition=condition,
                    location=location,
                    trials=enhanced_trials,
                    start_time=start_time,
                    search_type='semantic'
                )
            
            logger.info(f"Semantic search found {len(enhanced_trials)} trials")
            return enhanced_trials
            
        except Exception as e:
            logger.error(f"Error in semantic search: {e}")
            return []
    
    def _calculate_semantic_similarity(self, condition: str, trial: Dict[str, Any]) -> float:
        """Calculate semantic similarity score for trial matching"""
        
        score = 0.0
        condition_lower = condition.lower()
        
        # Exact condition match in conditions field
        if condition_lower in trial['conditions'].lower():
            score += 0.8
        
        # Partial match in conditions
        condition_words = condition_lower.split()
        conditions_text = trial['conditions'].lower()
        for word in condition_words:
            if word in conditions_text:
                score += 0.2
        
        # Match in description
        if trial.get('description'):
            description_text = trial['description'].lower()
            if condition_lower in description_text:
                score += 0.4
            for word in condition_words:
                if word in description_text:
                    score += 0.1
        
        # Match in protocol summary
        if trial.get('protocol_summary'):
            summary_text = trial['protocol_summary'].lower()
            if condition_lower in summary_text:
                score += 0.3
            for word in condition_words:
                if word in summary_text:
                    score += 0.05
        
        # Cap the score at 1.0
        return min(score, 1.0)

    def search_trials_with_multi_trial_detection(self, condition: str, location: str, 
                                               session_id: str = None) -> Dict[str, Any]:
        """Search trials and detect if multiple trials exist for same condition/location"""

        # Get all matching trials
        trials = self.search_trials(condition, location, session_id)

        if len(trials) <= 1:
            # Single trial or no trials - use standard flow
            return {
                "trials": trials,
                "selection_strategy": "single_trial",
                "selected_trial": trials[0] if trials else None,
                "requires_multi_trial_logic": False
            }

        # Multiple trials detected - need completion rate optimization
        logger.info(f"MULTI_TRIAL_DETECTED: Found {len(trials)} {condition} trials in {location}")
        for trial in trials:
            logger.info(f"  - Trial {trial['id']}: {trial['trial_name']}")

        return {
            "trials": trials,
            "selection_strategy": "multi_trial_completion_optimized",
            "selected_trial": None,  # Will be determined by completion rate logic
            "requires_multi_trial_logic": True,
            "condition": condition,
            "location": location
        }
    
    def _normalize_condition(self, condition: str) -> str:
        """Normalize medical condition for better matching"""
        if not condition:
            return condition

        normalized = condition.lower().strip()

        # CRITICAL: Handle common abbreviations and aliases FIRST
        # These map user shorthand to full medical condition names
        condition_aliases = {
            # Skin conditions
            'hs': 'hidradenitis suppurativa',
            'hidradenitis': 'hidradenitis suppurativa',
            'ad': 'atopic dermatitis',
            'atopic': 'atopic dermatitis',
            'eczema': 'atopic dermatitis',
            'pso': 'psoriasis',
            'psa': 'psoriatic arthritis',
            # Metabolic
            't2d': 'type 2 diabetes',
            't2dm': 'type 2 diabetes',
            'type 2': 'type 2 diabetes',
            'dm': 'diabetes',
            # Respiratory
            'copd': 'chronic obstructive pulmonary disease',
            'rsv': 'respiratory syncytial virus',
            # Neurological
            'mdd': 'major depressive disorder',
            'gad': 'generalized anxiety disorder',
            'ms': 'multiple sclerosis',
            'alzheimers': "alzheimer's disease",
            'parkinsons': "parkinson's disease",
            # Cardiovascular
            'hf': 'heart failure',
            'chf': 'congestive heart failure',
            'afib': 'atrial fibrillation',
            'a-fib': 'atrial fibrillation',
            # Autoimmune
            'ra': 'rheumatoid arthritis',
            'sle': 'systemic lupus erythematosus',
            'lupus': 'systemic lupus erythematosus',
            'ibd': 'inflammatory bowel disease',
            'uc': 'ulcerative colitis',
            'crohns': "crohn's disease",
            # Other common
            'csu': 'chronic spontaneous urticaria',
            'hives': 'urticaria',
            'covid': 'covid-19',
            'coronavirus': 'covid-19',
        }

        # Check for alias match
        if normalized in condition_aliases:
            return condition_aliases[normalized]

        # Handle common plural/singular variations
        condition_mappings = {
            'migraines': 'migraine',
            'headaches': 'headache',
            'diabetes': 'diabetes',  # Already singular
            'depression': 'depression',  # Already singular
            'arthritis': 'arthritis',  # Already singular
            'asthma': 'asthma',  # Already singular
            'allergies': 'allergy',
            'cancers': 'cancer',
        }

        # Check if the condition needs normalization
        if normalized in condition_mappings:
            return condition_mappings[normalized]

        # Handle general plural to singular conversion for medical terms
        if normalized.endswith('s') and len(normalized) > 3:
            # Try removing 's' for potential plural forms
            singular_form = normalized[:-1]
            return singular_form

        return normalized
    
    def _normalize_location(self, location: str) -> str:
        """
        Normalize location for better matching.

        Handles:
        - ZIP codes: "30297" → "Atlanta"
        - City+State+ZIP: "Charleston sc 29485" → "Charleston"
        - City+State: "Springfield Missouri" → "Springfield, MO"
        - Common abbreviations: "atl" → "atlanta"
        - Typos: "tulsa o" → "tulsa"
        """
        if not location:
            return location

        original_location = location
        normalized = location.strip()

        logger.info(f"📍 NORMALIZING LOCATION: '{original_location}'")

        # Step 1: Extract city from complex formats FIRST
        normalized = self._extract_city_from_complex_location(normalized)
        if normalized != original_location:
            logger.info(f"   → Complex format extracted: '{normalized}'")

        # Step 2: Handle common location variations and typos
        location_mappings = {
            'new orleans': 'new orleans',
            'nola': 'new orleans',
            'atlanta': 'atlanta',
            'atl': 'atlanta',
            'baton rouge': 'baton rouge',
            'br': 'baton rouge',
            'tulsa': 'tulsa',
            'tul': 'tulsa',
            'st louis': 'st. louis',
            'st. louis': 'st. louis',
            'saint louis': 'st. louis',
        }

        normalized_lower = normalized.lower().strip()

        # Exact match
        if normalized_lower in location_mappings:
            result = location_mappings[normalized_lower]
            logger.info(f"   → Mapped: '{result}'")
            return result

        # Handle partial matches and typos (e.g., "tulsa o" → "tulsa")
        # Remove trailing single letters that might be typos or incomplete words
        if len(normalized_lower) > 3 and normalized_lower[-2] == ' ' and normalized_lower[-1].isalpha():
            without_last = normalized_lower[:-2].strip()
            if without_last in location_mappings:
                result = location_mappings[without_last]
                logger.info(f"   → Typo corrected: '{result}'")
                return result

        # Check if it starts with a known location (e.g., "tulsa ok" → "tulsa")
        for key in location_mappings:
            if normalized_lower.startswith(key + ' '):
                result = location_mappings[key]
                logger.info(f"   → Prefix matched: '{result}'")
                return result

        logger.info(f"   → No normalization applied, using: '{normalized}'")
        return normalized

    def _extract_city_from_complex_location(self, location: str) -> str:
        """
        Extract clean city name from complex location formats.

        Patterns handled:
        - "Charleston sc 29485" → "Charleston"
        - "St Louis Missouri 63116" → "St Louis"
        - "springfield mo.myzip is 65804" → "Springfield"
        - "30297" → "Atlanta" (ZIP only)
        - "Atlanta GA" → "Atlanta"
        """
        import re

        # Remove extra text like "myzip is" or "my zip is"
        location = re.sub(r'\.?my\s*zip\s*(code)?\s*is\s*', ' ', location, flags=re.IGNORECASE)
        location = location.strip()

        logger.debug(f"   🔍 Extracting from: '{location}'")

        # Pattern 1: ZIP code only (5 digits)
        zip_only = re.match(r'^(\d{5})$', location)
        if zip_only:
            zip_code = zip_only.group(1)
            city = self._zip_to_city(zip_code)
            if city:
                logger.info(f"   ✅ ZIP→City: '{zip_code}' → '{city}'")
                return city
            logger.warning(f"   ⚠️  ZIP code '{zip_code}' not recognized")
            return location

        # Pattern 2: City State ZIP (e.g., "Charleston sc 29485", "St Louis Missouri 63116")
        match = re.match(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+([A-Z]{2}|[A-Za-z]+)\s+(\d{5})', location, re.IGNORECASE)
        if match:
            city, state, zip_code = match.groups()
            result = f"{city.title()}"
            logger.info(f"   ✅ City+State+ZIP extracted: '{result}'")
            return result

        # Pattern 3: City State (no ZIP) (e.g., "Springfield Missouri", "Atlanta GA")
        match = re.match(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+([A-Z]{2}|[A-Za-z]+)$', location, re.IGNORECASE)
        if match:
            city, state = match.groups()
            result = f"{city.title()}"
            logger.info(f"   ✅ City+State extracted: '{result}'")
            return result

        # No pattern matched - return as-is
        return location

    def _zip_to_city(self, zip_code: str) -> Optional[str]:
        """
        Convert ZIP code to city name.

        Uses ZIP code prefix mapping based on DelRicht site locations.
        """
        # ZIP code to city mapping (based on US ZIP code prefixes)
        zip_mappings = {
            # Georgia (30xxx)
            '30': 'Atlanta',

            # South Carolina (29xxx)
            '29': 'Charleston',

            # Louisiana (70xxx)
            '700': 'New Orleans',  # 700xx specific to New Orleans
            '701': 'Baton Rouge',  # 701xx specific to Baton Rouge

            # Missouri (63xxx, 64xxx, 65xxx)
            '630': 'St. Louis',
            '631': 'St. Louis',
            '640': 'Kansas City',
            '641': 'Kansas City',
            '658': 'Springfield',

            # Oklahoma (74xxx)
            '74': 'Tulsa',

            # Tennessee (37xxx, 38xxx)
            '37': 'Nashville',
            '38': 'Memphis',

            # Texas (75xxx, 76xxx)
            '750': 'Dallas',
            '751': 'Dallas',
            '752': 'Dallas',
            '76': 'Fort Worth',

            # North Carolina (28xxx)
            '28': 'Charlotte',

            # Ohio (45xxx)
            '45': 'Cincinnati',
        }

        # Try 3-digit prefix first (more specific)
        if zip_code[:3] in zip_mappings:
            return zip_mappings[zip_code[:3]]

        # Try 2-digit prefix
        if zip_code[:2] in zip_mappings:
            return zip_mappings[zip_code[:2]]

        return None

    def _normalize_state_name(self, state: str) -> str:
        """
        Convert state name to standard abbreviation.

        Examples:
            "Missouri" → "MO"
            "mo" → "MO"
            "MO" → "MO"
        """
        state_upper = state.upper().strip()

        # Already an abbreviation
        if len(state_upper) == 2:
            return state_upper

        # State name → abbreviation mapping
        state_name_map = {
            'ALABAMA': 'AL', 'ALASKA': 'AK', 'ARIZONA': 'AZ', 'ARKANSAS': 'AR',
            'CALIFORNIA': 'CA', 'COLORADO': 'CO', 'CONNECTICUT': 'CT', 'DELAWARE': 'DE',
            'FLORIDA': 'FL', 'GEORGIA': 'GA', 'HAWAII': 'HI', 'IDAHO': 'ID',
            'ILLINOIS': 'IL', 'INDIANA': 'IN', 'IOWA': 'IA', 'KANSAS': 'KS',
            'KENTUCKY': 'KY', 'LOUISIANA': 'LA', 'MAINE': 'ME', 'MARYLAND': 'MD',
            'MASSACHUSETTS': 'MA', 'MICHIGAN': 'MI', 'MINNESOTA': 'MN', 'MISSISSIPPI': 'MS',
            'MISSOURI': 'MO', 'MONTANA': 'MT', 'NEBRASKA': 'NE', 'NEVADA': 'NV',
            'NEW HAMPSHIRE': 'NH', 'NEW JERSEY': 'NJ', 'NEW MEXICO': 'NM', 'NEW YORK': 'NY',
            'NORTH CAROLINA': 'NC', 'NORTH DAKOTA': 'ND', 'OHIO': 'OH', 'OKLAHOMA': 'OK',
            'OREGON': 'OR', 'PENNSYLVANIA': 'PA', 'RHODE ISLAND': 'RI', 'SOUTH CAROLINA': 'SC',
            'SOUTH DAKOTA': 'SD', 'TENNESSEE': 'TN', 'TEXAS': 'TX', 'UTAH': 'UT',
            'VERMONT': 'VT', 'VIRGINIA': 'VA', 'WASHINGTON': 'WA', 'WEST VIRGINIA': 'WV',
            'WISCONSIN': 'WI', 'WYOMING': 'WY'
        }

        return state_name_map.get(state_upper, state_upper)

    def _get_metro_area_locations(self, location: str) -> List[str]:
        """Get all cities/locations in a metro area for expanded search.

        Maps major metro area names to their surrounding cities/suburbs where
        DelRicht has sites. This allows users to search "St. Louis" and find
        trials in Wildwood, Town and Country, etc.

        Returns a list of location search terms to try.
        """
        location_lower = location.lower().strip()

        # Metro area mappings - major city → list of nearby cities with sites
        # These are based on actual DelRicht site locations
        metro_area_mappings = {
            # St. Louis Metro Area
            'st. louis': ['st. louis', 'wildwood', 'town and country', 'town & country', 'clayton', 'chesterfield'],
            'st louis': ['st. louis', 'wildwood', 'town and country', 'town & country', 'clayton', 'chesterfield'],
            'saint louis': ['st. louis', 'wildwood', 'town and country', 'town & country', 'clayton', 'chesterfield'],
            'stl': ['st. louis', 'wildwood', 'town and country', 'town & country'],

            # Dallas/Fort Worth Metro Area
            'dallas': ['dallas', 'plano', 'prosper', 'frisco', 'richardson', 'irving'],
            'dfw': ['dallas', 'plano', 'prosper', 'fort worth', 'frisco'],
            'fort worth': ['fort worth', 'dallas', 'arlington'],

            # New Orleans Metro Area
            'new orleans': ['new orleans', 'metairie', 'kenner', 'mandeville', 'covington'],
            'nola': ['new orleans', 'metairie', 'kenner', 'mandeville'],

            # Baton Rouge Metro Area
            'baton rouge': ['baton rouge', 'prairieville', 'gonzales', 'denham springs'],

            # Nashville Metro Area
            'nashville': ['nashville', 'hendersonville', 'smyrna', 'murfreesboro', 'franklin'],

            # Charlotte Metro Area
            'charlotte': ['charlotte', 'huntersville', 'matthews', 'concord'],

            # Cincinnati Metro Area
            'cincinnati': ['cincinnati', 'mason', 'west chester', 'florence'],

            # Charleston Metro Area
            'charleston': ['charleston', 'mt pleasant', 'mount pleasant', 'north charleston'],
        }

        # Check if the location is a known metro area
        for metro_key, cities in metro_area_mappings.items():
            if metro_key in location_lower:
                logger.info(f"Expanding metro area search: '{location}' → {cities}")
                return cities

        # If not a known metro, also check by zip code prefix for Missouri
        # 63xxx = St. Louis area
        if location_lower.startswith('63'):
            return ['st. louis', 'wildwood', 'town and country', 'town & country']

        # Not a metro area - return original location only
        return [location]

    async def find_nearest_location_with_trials(
        self,
        condition: str,
        requested_location: str,
        max_distance_miles: int = 150
    ) -> Optional[Dict[str, Any]]:
        """
        Find the nearest location that has trials for the given condition.

        Args:
            condition: Medical condition to search for
            requested_location: User's requested location (where no trials found)
            max_distance_miles: Maximum distance to search (default: 150 miles)

        Returns:
            Dict with nearest_location, distance_miles, trial_count, or None if nothing within range
        """
        try:
            # Get all locations that have trials for this condition
            locations_with_trials = db.execute_query("""
                SELECT DISTINCT
                    ti.site_location,
                    ti.site_id,
                    COUNT(DISTINCT ct.id) as trial_count
                FROM clinical_trials ct
                JOIN trial_investigators ti ON ct.id = ti.trial_id
                WHERE ti.site_id IS NOT NULL
                  AND LOWER(ct.conditions) LIKE LOWER(%s)
                GROUP BY ti.site_location, ti.site_id
                ORDER BY trial_count DESC
            """, (f"%{condition}%",))

            if not locations_with_trials:
                return None

            # Simple distance heuristic: Check nearby cities
            # For production, you'd use actual geocoding/distance calculation
            nearby_cities = self._get_nearby_cities(requested_location)

            for nearby in nearby_cities:
                for loc in locations_with_trials:
                    site_loc = loc['site_location'].lower()
                    if nearby['city'].lower() in site_loc:
                        return {
                            'nearest_location': loc['site_location'],
                            'distance_miles': nearby['distance_miles'],
                            'trial_count': loc['trial_count'],
                            'condition': condition
                        }

            # If no nearby match, return closest major city with trials
            if locations_with_trials:
                return {
                    'nearest_location': locations_with_trials[0]['site_location'],
                    'distance_miles': None,  # Unknown distance
                    'trial_count': locations_with_trials[0]['trial_count'],
                    'condition': condition
                }

            return None

        except Exception as e:
            logger.error(f"Error finding nearest location: {str(e)}")
            return None

    def _get_nearby_cities(self, location: str) -> List[Dict[str, Any]]:
        """
        Get nearby cities for a given location with estimated distances.
        Returns list of {city, state, distance_miles}
        """
        location_lower = location.lower()

        # Map of locations to nearby cities (expandable)
        nearby_map = {
            'shawnee': [
                {'city': 'Kansas City', 'state': 'MO', 'distance_miles': 30},
                {'city': 'Overland Park', 'state': 'KS', 'distance_miles': 15},
                {'city': 'Lenexa', 'state': 'KS', 'distance_miles': 10}
            ],
            'ozark': [
                {'city': 'Springfield', 'state': 'MO', 'distance_miles': 15},
                {'city': 'Branson', 'state': 'MO', 'distance_miles': 25}
            ],
            'creve coeur': [
                {'city': 'St. Louis', 'state': 'MO', 'distance_miles': 8},
                {'city': 'Wildwood', 'state': 'MO', 'distance_miles': 12},
                {'city': 'Town and Country', 'state': 'MO', 'distance_miles': 5}
            ],
            # Add more as patterns emerge
        }

        # Check if location matches any key
        for key, nearby_list in nearby_map.items():
            if key in location_lower:
                return nearby_list

        # Default: Return major cities in same state if we can extract state
        state_match = self._extract_state(location)
        if state_match:
            return self._get_major_cities_in_state(state_match)

        return []

    def _get_major_cities_in_state(self, state: str) -> List[Dict[str, Any]]:
        """Get major cities for a state"""
        major_cities_by_state = {
            'KS': [
                {'city': 'Overland Park', 'state': 'KS', 'distance_miles': None},
                {'city': 'Kansas City', 'state': 'KS', 'distance_miles': None}
            ],
            'MO': [
                {'city': 'St. Louis', 'state': 'MO', 'distance_miles': None},
                {'city': 'Springfield', 'state': 'MO', 'distance_miles': None}
            ],
            'LA': [
                {'city': 'New Orleans', 'state': 'LA', 'distance_miles': None},
                {'city': 'Baton Rouge', 'state': 'LA', 'distance_miles': None}
            ],
            'GA': [
                {'city': 'Atlanta', 'state': 'GA', 'distance_miles': None}
            ],
            'TX': [
                {'city': 'Dallas', 'state': 'TX', 'distance_miles': None},
                {'city': 'Prosper', 'state': 'TX', 'distance_miles': None}
            ]
        }

        return major_cities_by_state.get(state.upper(), [])

    def _extract_state(self, location: str) -> Optional[str]:
        """Extract state abbreviation from location string"""
        import re

        # Check for state abbreviations
        state_pattern = r'\b([A-Z]{2})\b'
        match = re.search(state_pattern, location.upper())
        if match:
            return match.group(1)

        # Check for full state names
        state_names = {
            'kansas': 'KS', 'missouri': 'MO', 'louisiana': 'LA',
            'georgia': 'GA', 'texas': 'TX', 'oklahoma': 'OK',
            'tennessee': 'TN', 'north carolina': 'NC', 'south carolina': 'SC'
        }

        location_lower = location.lower()
        for name, abbr in state_names.items():
            if name in location_lower:
                return abbr

        return None

    def search_trials_with_metro_expansion(self, condition: str = None, location: str = None,
                                          session_id: str = None) -> List[Dict[str, Any]]:
        """Search for trials with automatic metro area expansion.

        If a user searches for a major metro area (e.g., "St. Louis"), this method
        will automatically expand the search to include surrounding cities where
        we have sites (e.g., Wildwood, Town and Country).
        """
        # First, try the original location
        trials = self.search_trials(condition, location, session_id)

        if trials:
            return trials

        # If no results, try expanding to metro area
        metro_locations = self._get_metro_area_locations(location or '')

        if len(metro_locations) > 1:  # Only if we found expansion cities
            all_trials = []
            searched_locations = set()

            for metro_location in metro_locations:
                if metro_location.lower() in searched_locations:
                    continue
                searched_locations.add(metro_location.lower())

                metro_trials = self.search_trials(condition, metro_location, session_id)
                for trial in metro_trials:
                    # Add metro location info to trial for display
                    if trial not in all_trials:
                        trial['found_in_metro_location'] = metro_location
                        all_trials.append(trial)

            if all_trials:
                logger.info(f"Metro area expansion found {len(all_trials)} trials in {len(searched_locations)} locations")
                return all_trials

        return []


class MultiTrialCompletionSelector:
    """Handles trial selection ONLY when multiple trials exist in same location"""

    def __init__(self):
        # Analytics will be used for enhanced completion rate calculations in future
        self.analytics = None

    def select_from_multiple_trials(self, trials: List[Dict], condition: str, 
                                   location: str) -> Dict[str, Any]:
        """Select best trial from multiple options based on completion rates"""

        if len(trials) <= 1:
            raise ValueError("This method should only be called with multiple trials")

        # Get completion rates for these specific trials
        trial_ids = [t['id'] for t in trials]
        completion_data = self._get_completion_rates_for_trials(trial_ids, condition, location)

        # Merge completion data with trial info
        enhanced_trials = []
        for trial in trials:
            completion_info = next(
                (cd for cd in completion_data if cd['trial_id'] == trial['id']),
                None
            )

            enhanced_trial = dict(trial)
            if completion_info:
                enhanced_trial.update({
                    'completion_rate': completion_info['completion_rate'],
                    'total_sessions': completion_info['total_sessions'],
                    'completed_sessions': completion_info['completed_sessions'],
                    'has_completion_data': True
                })
            else:
                enhanced_trial.update({
                    'completion_rate': 0.0,
                    'total_sessions': 0,
                    'completed_sessions': 0,
                    'has_completion_data': False
                })

            enhanced_trials.append(enhanced_trial)

        # Select best trial using completion rate + fallback logic
        selected_trial = self._select_optimal_trial(enhanced_trials)

        return {
            'selected_trial': selected_trial,
            'all_trials': enhanced_trials,
            'selection_reasoning': self._generate_selection_reasoning(selected_trial, enhanced_trials)
        }

    def _get_completion_rates_for_trials(self, trial_ids: List[int], condition: str, 
                                       location: str) -> List[Dict]:
        """Get completion rates specifically for the provided trial IDs"""

        # Get completion data for these specific trials
        results = db.execute_query("""
            WITH trial_completion_stats AS (
                SELECT 
                    ps.trial_id,
                    COUNT(*) as total_sessions,
                    COUNT(*) FILTER (WHERE ps.status = 'completed') as completed_sessions,
                    COUNT(*) FILTER (WHERE ps.status = 'abandoned') as abandoned_sessions,
                    AVG(ps.answered_questions::DECIMAL / NULLIF(ps.total_questions, 0) * 100) as avg_progress_rate,
                    AVG(EXTRACT(EPOCH FROM (COALESCE(ps.completed_at, ps.created_at) - ps.started_at))/60.0) as avg_session_minutes
                FROM prescreening_sessions ps
                WHERE ps.trial_id = ANY(%s)
                  AND ps.started_at >= NOW() - INTERVAL '45 days'  -- 45 day lookback
                  AND LOWER(ps.condition) LIKE LOWER(%s)
                  AND (ps.location IS NULL OR LOWER(ps.location) LIKE LOWER(%s))
                GROUP BY ps.trial_id
            )
            SELECT 
                trial_id,
                total_sessions,
                completed_sessions,
                abandoned_sessions,
                ROUND(
                    CASE WHEN total_sessions > 0 
                         THEN (completed_sessions::DECIMAL / total_sessions) * 100 
                         ELSE 0 
                    END, 1
                ) as completion_rate,
                ROUND(avg_progress_rate, 1) as avg_progress_rate,
                ROUND(avg_session_minutes, 1) as avg_session_minutes
            FROM trial_completion_stats
        """, (trial_ids, f"%{condition}%", f"%{location}%"))

        return results

    def _select_optimal_trial(self, enhanced_trials: List[Dict]) -> Dict:
        """Select the optimal trial using weighted scoring"""

        scored_trials = []

        for trial in enhanced_trials:
            score = self._calculate_trial_score(trial, enhanced_trials)
            trial['selection_score'] = score
            scored_trials.append(trial)

        # Sort by score (highest first)
        scored_trials.sort(key=lambda x: x['selection_score'], reverse=True)

        return scored_trials[0]

    def _calculate_trial_score(self, trial: Dict, all_trials: List[Dict]) -> float:
        """Calculate weighted score for trial selection"""

        score = 0.0

        # Completion rate (primary factor - 70% weight)
        if trial['has_completion_data'] and trial['total_sessions'] >= 3:
            # Strong data - use actual completion rate + bonus for having data
            completion_score = float(trial['completion_rate']) / 100.0  # Normalize to 0-1
            confidence_multiplier = min(1.0, float(trial['total_sessions']) / 10.0)  # More sessions = higher confidence
            data_bonus = 0.1  # Bonus for having real data
            score += (completion_score * confidence_multiplier * 0.7) + data_bonus

        elif trial['has_completion_data'] and trial['total_sessions'] > 0:
            # Limited data - use with reduced weight + smaller data bonus
            completion_score = float(trial['completion_rate']) / 100.0
            confidence_multiplier = 0.5  # Reduced confidence for small sample
            data_bonus = 0.05  # Smaller bonus for limited data
            score += (completion_score * confidence_multiplier * 0.7) + data_bonus

        else:
            # No data - baseline score
            score += 0.15  # Baseline when no completion data available

        # Trial age/recency (15% weight)
        # Prefer trials that are actively recruiting
        age_score = 0.15  # Default neutral score
        score += age_score

        # Investigator reputation/site quality (15% weight) 
        # For now, use neutral score - could enhance later
        site_score = 0.15
        score += site_score

        return score

    def _generate_selection_reasoning(self, selected_trial: Dict, all_trials: List[Dict]) -> str:
        """Generate human-readable reasoning for trial selection"""

        if not selected_trial['has_completion_data']:
            return f"Selected trial {selected_trial['id']} (no completion data available for comparison)"

        completion_rate = selected_trial['completion_rate']
        sample_size = selected_trial['total_sessions']

        if sample_size >= 5:
            return (f"Selected trial {selected_trial['id']} with {completion_rate}% completion rate "
                   f"based on {sample_size} previous sessions")
        elif sample_size > 0:
            return (f"Selected trial {selected_trial['id']} with {completion_rate}% completion rate "
                   f"(limited data: {sample_size} sessions)")
        else:
            return f"Selected trial {selected_trial['id']} (no prior completion data)"


# Singleton instance
trial_search = TrialSearchService()