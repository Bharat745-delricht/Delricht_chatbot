"""
Intelligent Trial Matching Service

This service provides sophisticated trial matching using multiple strategies:
- Exact protocol number matching (95% confidence)
- Similar protocol + sponsor matching (85% confidence) 
- Title similarity + phase matching (75% confidence)
- NCT number matching (99% confidence)
- Sponsor + phase combination (70% confidence)

Includes automatic processor selection and confidence scoring for manual review workflows.
"""

import re
import logging
import hashlib
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from difflib import SequenceMatcher
import uuid

from core.database import db
from core.services.gemini_service import gemini_service

logger = logging.getLogger(__name__)


class IntelligentTrialMatcher:
    """Service for intelligent trial matching with confidence scoring"""
    
    def __init__(self):
        self.confidence_thresholds = self._load_confidence_thresholds()
        self.processor_selection_rules = self._load_processor_selection_rules()
    
    def _load_confidence_thresholds(self) -> Dict[str, float]:
        """Load confidence thresholds from database configuration"""
        try:
            config = db.execute_query("""
                SELECT config_value FROM intelligent_processing_config 
                WHERE config_key = 'matching_confidence_thresholds' AND is_active = true
            """)
            
            if config:
                import json
                config_value = config[0]['config_value']
                if isinstance(config_value, dict):
                    return config_value
                return json.loads(config_value)
            else:
                # Default thresholds
                return {
                    "exact_protocol": 0.95,
                    "similar_protocol": 0.85, 
                    "nct_match": 0.99,
                    "title_similarity": 0.75,
                    "sponsor_phase": 0.70
                }
        except Exception as e:
            logger.error(f"Error loading confidence thresholds: {e}")
            return {
                "exact_protocol": 0.95,
                "similar_protocol": 0.85,
                "nct_match": 0.99, 
                "title_similarity": 0.75,
                "sponsor_phase": 0.70
            }
    
    def _load_processor_selection_rules(self) -> Dict[str, Any]:
        """Load processor selection rules from database configuration"""
        try:
            config = db.execute_query("""
                SELECT config_value FROM intelligent_processing_config 
                WHERE config_key = 'processor_selection_rules' AND is_active = true
            """)
            
            if config:
                import json
                config_value = config[0]['config_value']
                if isinstance(config_value, dict):
                    return config_value
                return json.loads(config_value)
            else:
                # Default rules
                return {
                    "protocol_keywords": ["protocol", "clinical trial", "study"],
                    "form_keywords": ["consent", "case report"],
                    "fallback": "general_processor"
                }
        except Exception as e:
            logger.error(f"Error loading processor selection rules: {e}")
            return {
                "protocol_keywords": ["protocol", "clinical trial", "study"],
                "form_keywords": ["consent", "case report"], 
                "fallback": "general_processor"
            }
    
    async def find_matching_trials(self, extracted_data: Dict[str, Any], file_path: str, 
                                 processing_job_id: str) -> Dict[str, Any]:
        """
        Find matching trials using multiple strategies with confidence scoring
        
        Args:
            extracted_data: Data extracted from protocol document
            file_path: Path to the source file
            processing_job_id: UUID of the processing job
            
        Returns:
            Dictionary containing best match and alternatives with confidence scores
        """
        try:
            logger.info(f"Starting intelligent trial matching for job {processing_job_id}")
            
            # Extract key identifiers from the data
            protocol_number = self._extract_protocol_number(extracted_data)
            title = self._extract_title(extracted_data)
            sponsor = self._extract_sponsor(extracted_data)
            nct_number = self._extract_nct_number(extracted_data)
            phase = self._extract_phase(extracted_data)
            
            # Calculate file hash for duplicate detection
            file_hash = self._calculate_file_hash(file_path)
            
            logger.info(f"Extracted identifiers - Protocol: {protocol_number}, NCT: {nct_number}, "
                       f"Sponsor: {sponsor}, Phase: {phase}")
            
            # Apply matching strategies in order of confidence
            matches = []
            
            # Strategy 1: Exact protocol number match (95% confidence)
            if protocol_number:
                exact_matches = await self._find_exact_protocol_matches(protocol_number)
                for match in exact_matches:
                    matches.append({
                        **match,
                        "match_type": "exact_protocol",
                        "confidence_score": self.confidence_thresholds["exact_protocol"],
                        "match_reasons": {
                            "primary": f"Exact protocol number match: {protocol_number}",
                            "details": ["Protocol numbers are identical", "Highest confidence match type"]
                        }
                    })
            
            # Strategy 2: NCT number match (99% confidence)
            if nct_number:
                nct_matches = await self._find_nct_matches(nct_number)
                for match in nct_matches:
                    matches.append({
                        **match,
                        "match_type": "nct_match",
                        "confidence_score": self.confidence_thresholds["nct_match"],
                        "match_reasons": {
                            "primary": f"NCT number match: {nct_number}",
                            "details": ["NCT numbers are identical", "Official clinical trials registry match"]
                        }
                    })
            
            # Strategy 3: Similar protocol + sponsor match (85% confidence)
            if protocol_number and sponsor:
                similar_matches = await self._find_similar_protocol_sponsor_matches(protocol_number, sponsor)
                for match in similar_matches:
                    matches.append({
                        **match,
                        "match_type": "similar_protocol",
                        "confidence_score": self.confidence_thresholds["similar_protocol"],
                        "match_reasons": {
                            "primary": f"Similar protocol ({match['protocol_similarity']:.2f}) + sponsor match",
                            "details": [
                                f"Protocol similarity: {match['protocol_similarity']:.2f}",
                                f"Sponsor match: {sponsor}"
                            ]
                        }
                    })
            
            # Strategy 4: Title similarity + phase match (75% confidence)
            if title and phase:
                title_matches = await self._find_title_phase_matches(title, phase)
                for match in title_matches:
                    matches.append({
                        **match,
                        "match_type": "title_similarity",
                        "confidence_score": self.confidence_thresholds["title_similarity"] * match['title_similarity'],
                        "match_reasons": {
                            "primary": f"Title similarity ({match['title_similarity']:.2f}) + phase match",
                            "details": [
                                f"Title similarity: {match['title_similarity']:.2f}",
                                f"Phase match: {phase}"
                            ]
                        }
                    })
            
            # Strategy 5: Sponsor + phase combination (70% confidence)
            if sponsor and phase:
                sponsor_matches = await self._find_sponsor_phase_matches(sponsor, phase)
                for match in sponsor_matches:
                    matches.append({
                        **match,
                        "match_type": "sponsor_phase",
                        "confidence_score": self.confidence_thresholds["sponsor_phase"],
                        "match_reasons": {
                            "primary": f"Sponsor + phase combination: {sponsor}, {phase}",
                            "details": [
                                f"Sponsor match: {sponsor}",
                                f"Phase match: {phase}"
                            ]
                        }
                    })
            
            # Remove duplicates and sort by confidence
            unique_matches = self._deduplicate_matches(matches)
            sorted_matches = sorted(unique_matches, key=lambda x: x['confidence_score'], reverse=True)
            
            # Determine best match and alternatives
            best_match = sorted_matches[0] if sorted_matches else None
            alternatives = sorted_matches[1:6] if len(sorted_matches) > 1 else []  # Top 5 alternatives
            
            # Store match results in database
            match_record_id = await self._store_match_results(
                processing_job_id, extracted_data, file_path, file_hash,
                best_match, alternatives
            )
            
            # Determine review status based on confidence
            review_status = self._determine_review_status(best_match)
            
            result = {
                "success": True,
                "match_record_id": match_record_id,
                "best_match": best_match,
                "alternatives": alternatives,
                "total_candidates": len(sorted_matches),
                "review_status": review_status,
                "file_hash": file_hash,
                "processing_timestamp": datetime.now().isoformat()
            }
            
            logger.info(f"Trial matching completed - Best match confidence: "
                       f"{best_match['confidence_score']:.2f if best_match else 'None'}, "
                       f"Review status: {review_status}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error in intelligent trial matching: {e}")
            return {
                "success": False,
                "error": str(e),
                "processing_timestamp": datetime.now().isoformat()
            }
    
    def _extract_protocol_number(self, extracted_data: Dict[str, Any]) -> Optional[str]:
        """Extract protocol number from various possible locations"""
        # Try multiple locations in the extracted data
        locations = [
            extracted_data.get("protocol_metadata", {}).get("protocol_number"),
            extracted_data.get("clinical_trial_fields", {}).get("protocol_number"),
            extracted_data.get("protocol_number")
        ]
        
        for location in locations:
            if location and isinstance(location, str) and len(location.strip()) > 0:
                return location.strip().upper()
        
        return None
    
    def _extract_title(self, extracted_data: Dict[str, Any]) -> Optional[str]:
        """Extract trial title from various possible locations"""
        locations = [
            extracted_data.get("protocol_metadata", {}).get("trial_title"),
            extracted_data.get("clinical_trial_fields", {}).get("trial_name"),
            extracted_data.get("trial_title")
        ]
        
        for location in locations:
            if location and isinstance(location, str) and len(location.strip()) > 10:
                return location.strip()
        
        return None
    
    def _extract_sponsor(self, extracted_data: Dict[str, Any]) -> Optional[str]:
        """Extract sponsor from various possible locations"""
        locations = [
            extracted_data.get("clinical_trial_fields", {}).get("sponsor"),
            extracted_data.get("protocol_metadata", {}).get("sponsor"),
            extracted_data.get("sponsor")
        ]
        
        for location in locations:
            if location and isinstance(location, str) and len(location.strip()) > 0:
                return location.strip()
        
        return None
    
    def _extract_nct_number(self, extracted_data: Dict[str, Any]) -> Optional[str]:
        """Extract NCT number from various possible locations"""
        # Look for NCT numbers in text fields
        text_fields = [
            extracted_data.get("full_text", ""),
            str(extracted_data.get("protocol_metadata", {})),
            str(extracted_data.get("clinical_trial_fields", {}))
        ]
        
        nct_pattern = r'NCT\d{8}'
        
        for text in text_fields:
            if text:
                matches = re.findall(nct_pattern, text.upper())
                if matches:
                    return matches[0]
        
        return None
    
    def _extract_phase(self, extracted_data: Dict[str, Any]) -> Optional[str]:
        """Extract study phase from various possible locations"""
        locations = [
            extracted_data.get("clinical_trial_fields", {}).get("phase"),
            extracted_data.get("protocol_metadata", {}).get("phase"),
            extracted_data.get("phase")
        ]
        
        for location in locations:
            if location and isinstance(location, str) and len(location.strip()) > 0:
                # Normalize phase format
                phase = location.strip().upper()
                if not phase.startswith("PHASE"):
                    phase = f"PHASE {phase}"
                return phase
        
        return None
    
    def _calculate_file_hash(self, file_path: str) -> str:
        """Calculate SHA256 hash of file for duplicate detection"""
        try:
            with open(file_path, 'rb') as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            return file_hash
        except Exception as e:
            logger.warning(f"Could not calculate file hash for {file_path}: {e}")
            return ""
    
    async def _find_exact_protocol_matches(self, protocol_number: str) -> List[Dict[str, Any]]:
        """Find trials with exact protocol number match"""
        try:
            matches = db.execute_query("""
                SELECT id, protocol_number, trial_name, sponsor, phase, conditions
                FROM clinical_trials
                WHERE UPPER(protocol_number) = UPPER(%s)
                ORDER BY updated_at DESC
            """, (protocol_number,))
            
            return matches if matches else []
            
        except Exception as e:
            logger.error(f"Error finding exact protocol matches: {e}")
            return []
    
    async def _find_nct_matches(self, nct_number: str) -> List[Dict[str, Any]]:
        """Find trials with matching NCT number"""
        try:
            matches = db.execute_query("""
                SELECT id, protocol_number, trial_name, sponsor, phase, conditions, nct_number
                FROM clinical_trials
                WHERE UPPER(nct_number) = UPPER(%s)
                ORDER BY updated_at DESC
            """, (nct_number,))
            
            return matches if matches else []
            
        except Exception as e:
            logger.error(f"Error finding NCT matches: {e}")
            return []
    
    async def _find_similar_protocol_sponsor_matches(self, protocol_number: str, sponsor: str) -> List[Dict[str, Any]]:
        """Find trials with similar protocol numbers and matching sponsor"""
        try:
            # Get all trials from the same sponsor
            candidates = db.execute_query("""
                SELECT id, protocol_number, trial_name, sponsor, phase, conditions
                FROM clinical_trials
                WHERE UPPER(sponsor) LIKE UPPER(%s)
                ORDER BY updated_at DESC
                LIMIT 50
            """, (f"%{sponsor}%",))
            
            if not candidates:
                return []
            
            # Calculate protocol number similarity
            matches = []
            for candidate in candidates:
                similarity = self._calculate_protocol_similarity(protocol_number, candidate['protocol_number'])
                if similarity >= 0.7:  # 70% similarity threshold
                    candidate['protocol_similarity'] = similarity
                    matches.append(candidate)
            
            return sorted(matches, key=lambda x: x['protocol_similarity'], reverse=True)
            
        except Exception as e:
            logger.error(f"Error finding similar protocol matches: {e}")
            return []
    
    async def _find_title_phase_matches(self, title: str, phase: str) -> List[Dict[str, Any]]:
        """Find trials with similar titles and matching phase"""
        try:
            # Get trials with matching phase
            candidates = db.execute_query("""
                SELECT id, protocol_number, trial_name, sponsor, phase, conditions
                FROM clinical_trials
                WHERE UPPER(phase) = UPPER(%s)
                ORDER BY updated_at DESC
                LIMIT 50
            """, (phase,))
            
            if not candidates:
                return []
            
            # Calculate title similarity
            matches = []
            for candidate in candidates:
                similarity = self._calculate_text_similarity(title, candidate['trial_name'])
                if similarity >= 0.6:  # 60% similarity threshold
                    candidate['title_similarity'] = similarity
                    matches.append(candidate)
            
            return sorted(matches, key=lambda x: x['title_similarity'], reverse=True)
            
        except Exception as e:
            logger.error(f"Error finding title/phase matches: {e}")
            return []
    
    async def _find_sponsor_phase_matches(self, sponsor: str, phase: str) -> List[Dict[str, Any]]:
        """Find trials with matching sponsor and phase"""
        try:
            matches = db.execute_query("""
                SELECT id, protocol_number, trial_name, sponsor, phase, conditions
                FROM clinical_trials
                WHERE UPPER(sponsor) LIKE UPPER(%s) AND UPPER(phase) = UPPER(%s)
                ORDER BY updated_at DESC
                LIMIT 20
            """, (f"%{sponsor}%", phase))
            
            return matches if matches else []
            
        except Exception as e:
            logger.error(f"Error finding sponsor/phase matches: {e}")
            return []
    
    def _calculate_protocol_similarity(self, protocol1: str, protocol2: str) -> float:
        """Calculate similarity between two protocol numbers"""
        if not protocol1 or not protocol2:
            return 0.0
        
        # Normalize protocol numbers
        p1 = protocol1.upper().strip()
        p2 = protocol2.upper().strip()
        
        # Exact match
        if p1 == p2:
            return 1.0
        
        # Extract base protocol number (remove version suffixes)
        p1_base = re.sub(r'[_\-]?(V|VER|VERSION|AMEND|AMD)?\d+(\.\d+)?$', '', p1)
        p2_base = re.sub(r'[_\-]?(V|VER|VERSION|AMEND|AMD)?\d+(\.\d+)?$', '', p2)
        
        # Check if base protocols match
        if p1_base == p2_base and p1_base:
            return 0.9  # High similarity for version differences
        
        # Use sequence matcher for general similarity
        return SequenceMatcher(None, p1, p2).ratio()
    
    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity between two text strings"""
        if not text1 or not text2:
            return 0.0
        
        # Normalize text
        t1 = re.sub(r'[^\w\s]', ' ', text1.lower()).strip()
        t2 = re.sub(r'[^\w\s]', ' ', text2.lower()).strip()
        
        # Use sequence matcher
        return SequenceMatcher(None, t1, t2).ratio()
    
    def _deduplicate_matches(self, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate matches keeping the highest confidence"""
        seen_trials = {}
        
        for match in matches:
            trial_id = match.get('id')
            if trial_id:
                if trial_id not in seen_trials or match['confidence_score'] > seen_trials[trial_id]['confidence_score']:
                    seen_trials[trial_id] = match
        
        return list(seen_trials.values())
    
    async def _store_match_results(self, processing_job_id: str, extracted_data: Dict[str, Any],
                                 file_path: str, file_hash: str, best_match: Optional[Dict[str, Any]],
                                 alternatives: List[Dict[str, Any]]) -> Optional[int]:
        """Store matching results in the database"""
        try:
            # Prepare match data
            matched_trial_id = best_match.get('id') if best_match else None
            match_type = best_match.get('match_type') if best_match else None
            confidence_score = best_match.get('confidence_score', 0.0) if best_match else 0.0
            match_reasons = best_match.get('match_reasons', {}) if best_match else {}
            
            # Store in intelligent_trial_matches table
            result = db.execute_insert_returning("""
                INSERT INTO intelligent_trial_matches 
                (processing_job_id, extracted_protocol_number, extracted_title, extracted_sponsor,
                 extracted_nct_number, extracted_phase, file_path, file_hash,
                 matched_trial_id, match_type, confidence_score, match_reasons, alternative_matches)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                processing_job_id,
                self._extract_protocol_number(extracted_data),
                self._extract_title(extracted_data),
                self._extract_sponsor(extracted_data),
                self._extract_nct_number(extracted_data),
                self._extract_phase(extracted_data),
                file_path,
                file_hash,
                matched_trial_id,
                match_type,
                confidence_score,
                match_reasons,
                alternatives
            ))
            
            if result:
                return result['id']
            
        except Exception as e:
            logger.error(f"Error storing match results: {e}")
        
        return None
    
    def _determine_review_status(self, best_match: Optional[Dict[str, Any]]) -> str:
        """Determine if manual review is needed based on confidence thresholds"""
        if not best_match:
            return "needs_manual_review"
        
        confidence = best_match.get('confidence_score', 0.0)
        
        # Load manual review thresholds
        try:
            config = db.execute_query("""
                SELECT config_value FROM intelligent_processing_config 
                WHERE config_key = 'manual_review_thresholds' AND is_active = true
            """)
            
            if config:
                import json
                thresholds = json.loads(config[0]['config_value'])
                low_confidence_threshold = thresholds.get('low_confidence', 0.70)
            else:
                low_confidence_threshold = 0.70
        except Exception:
            low_confidence_threshold = 0.70
        
        if confidence >= 0.90:
            return "approved"  # High confidence, auto-approve
        elif confidence >= low_confidence_threshold:
            return "pending"  # Medium confidence, pending review
        else:
            return "needs_manual_review"  # Low confidence, requires manual review
    
    def select_optimal_processor(self, file_path: str, extracted_text: str = None) -> str:
        """
        Select optimal Document AI processor based on file content
        
        Args:
            file_path: Path to the file
            extracted_text: Optional extracted text content
            
        Returns:
            Processor type: 'clinical_trial', 'form_parser', or 'general_processor'
        """
        try:
            # Get filename and content indicators
            filename = file_path.lower() if file_path else ""
            content = (extracted_text or "").lower()
            
            # Clinical trial protocol indicators
            protocol_indicators = [
                "protocol", "clinical trial", "study protocol", "investigational",
                "inclusion criteria", "exclusion criteria", "primary endpoint",
                "secondary endpoint", "adverse event", "sponsor"
            ]
            
            # Form document indicators  
            form_indicators = [
                "consent form", "informed consent", "case report form", "crf",
                "patient reported outcome", "questionnaire", "survey"
            ]
            
            # Count indicators
            protocol_score = sum(1 for indicator in protocol_indicators 
                               if indicator in filename or indicator in content)
            form_score = sum(1 for indicator in form_indicators
                           if indicator in filename or indicator in content)
            
            logger.info(f"Processor selection scores - Protocol: {protocol_score}, Form: {form_score}")
            
            # Select based on highest score
            if protocol_score >= form_score and protocol_score > 0:
                return "clinical_trial"
            elif form_score > 0:
                return "form_parser"
            else:
                return "general_processor"
                
        except Exception as e:
            logger.error(f"Error selecting processor: {e}")
            return "general_processor"
    
    async def get_match_status(self, processing_job_id: str) -> Dict[str, Any]:
        """Get the current matching status for a processing job"""
        try:
            match_data = db.execute_query("""
                SELECT * FROM intelligent_trial_matches 
                WHERE processing_job_id = %s
                ORDER BY confidence_score DESC
                LIMIT 1
            """, (processing_job_id,))
            
            if match_data:
                match = match_data[0]
                return {
                    "success": True,
                    "has_match": match['matched_trial_id'] is not None,
                    "match_type": match['match_type'],
                    "confidence_score": float(match['confidence_score']) if match['confidence_score'] else 0.0,
                    "review_status": match['review_status'],
                    "matched_trial_id": match['matched_trial_id'],
                    "match_reasons": match['match_reasons'],
                    "alternative_matches": match['alternative_matches'] or []
                }
            else:
                return {
                    "success": True,
                    "has_match": False,
                    "message": "No matching results found for this job"
                }
                
        except Exception as e:
            logger.error(f"Error getting match status: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def approve_match(self, match_id: int, reviewed_by: str, review_notes: str = None) -> Dict[str, Any]:
        """Approve a trial match manually"""
        try:
            # Update the match record
            db.execute_update("""
                UPDATE intelligent_trial_matches 
                SET review_status = 'approved', reviewed_by = %s, review_notes = %s,
                    reviewed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (reviewed_by, review_notes, match_id))
            
            logger.info(f"Match {match_id} approved by {reviewed_by}")
            
            return {
                "success": True,
                "message": "Match approved successfully",
                "match_id": match_id,
                "reviewed_by": reviewed_by
            }
            
        except Exception as e:
            logger.error(f"Error approving match: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def reject_match(self, match_id: int, reviewed_by: str, review_notes: str = None) -> Dict[str, Any]:
        """Reject a trial match manually"""
        try:
            # Update the match record
            db.execute_update("""
                UPDATE intelligent_trial_matches 
                SET review_status = 'rejected', reviewed_by = %s, review_notes = %s,
                    reviewed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (reviewed_by, review_notes, match_id))
            
            logger.info(f"Match {match_id} rejected by {reviewed_by}")
            
            return {
                "success": True,
                "message": "Match rejected successfully",
                "match_id": match_id,
                "reviewed_by": reviewed_by
            }
            
        except Exception as e:
            logger.error(f"Error rejecting match: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Global instance
intelligent_trial_matcher = IntelligentTrialMatcher()