"""
Location-to-Site Mapping Service
Maps user-provided locations (e.g., "Dallas") to CRIO site IDs
Uses database-driven mappings with intelligent matching
"""

from core.database import db
from typing import Optional, Dict, List
import logging
import re

logger = logging.getLogger(__name__)


class LocationSiteMapper:
    """
    Maps focus_location and focus_condition to appropriate CRIO site
    Handles fuzzy matching, specialty alignment, and multi-site cities
    """

    # City name variations for fuzzy matching
    CITY_ALIASES = {
        'ATL': ['atlanta', 'atl'],
        'NO': ['new orleans', 'nola', 'no', 'n.o.', 'new orleans'],
        'BR': ['baton rouge', 'br', 'baton'],
        'BET': ['bethesda', 'bet'],
        'CHS': ['charleston', 'chs'],
        'CIN': ['cincinnati', 'cincy', 'cin'],
        'CLT': ['charlotte', 'clt'],
        'DAL': ['dallas', 'dal', 'dfw'],
        'GU': ['gulfport', 'gu', 'gulf port'],
        'HMA': ['houma', 'hma'],
        'IND': ['indianapolis', 'indy', 'ind'],
        'LOU': ['louisville', 'lou'],
        'NAS': ['nashville', 'nas', 'nash'],
        'NS': ['norfolk', 'ns'],
        'OVP': ['overland park', 'ovp'],
        'SLC': ['salt lake city', 'slc', 'salt lake'],
        'SPR': ['springfield', 'spr'],
        'STE': ['steubenville', 'ste'],
        'STL': ['st louis', 'saint louis', 'stl', 'st. louis'],
        'TUL': ['tulsa', 'tul']
    }

    # Specialty keywords for condition matching
    SPECIALTY_KEYWORDS = {
        'dermatology': ['acne', 'eczema', 'psoriasis', 'skin', 'rash', 'dermat', 'atopic'],
        'psychiatry': ['depression', 'anxiety', 'adhd', 'bipolar', 'schizophrenia', 'mental', 'psych'],
        'neurology': ['migraine', 'headache', 'epilepsy', 'seizure', 'neuropathy', 'neuro', 'alzheimer'],
        'urology': ['prostate', 'bladder', 'kidney', 'incontinence', 'uro', 'urinary'],
        'ophthalmology': ['glaucoma', 'cataract', 'macular', 'vision', 'eye', 'ophthal'],
        'vaccine': ['vaccine', 'vax', 'vaccination', 'immunization'],
        'rheumatology': ['arthritis', 'rheumatoid', 'lupus', 'rheum'],
        'general medicine': ['diabetes', 'hypertension', 'cholesterol', 'covid', 'general']
    }

    def get_site_for_location(
        self,
        focus_location: str,
        focus_condition: Optional[str] = None,
        trial_id: Optional[int] = None
    ) -> Optional[Dict]:
        """
        Find best matching CRIO site for location/condition

        Args:
            focus_location: User's location (e.g., "Dallas", "New Orleans")
            focus_condition: Medical condition (e.g., "migraine", "diabetes")
            trial_id: Optional trial ID for site availability check

        Returns:
            {
                'site_id': '1867',
                'site_name': 'DAL - General Medicine',
                'coordinator_email': 'dalgenmed@delricht.com',
                'coordinator_user_key': '312958',
                'specialty': 'General Medicine',
                'city_code': 'DAL'
            }
        """

        # Step 1: Normalize location to city code
        city_code = self._normalize_location(focus_location)
        if not city_code:
            logger.warning(f"Could not normalize location: {focus_location}")
            # Try direct database lookup without normalization
            sites = self._get_sites_by_location_name(focus_location)
            if not sites:
                return None
        else:
            # Step 2: Get all sites for this city code
            sites = self._get_sites_for_city(city_code)
            if not sites:
                logger.warning(f"No sites found for city code: {city_code}")
                return None

        # Step 3: If only one site, return it
        if len(sites) == 1:
            logger.info(f"Single site found: {sites[0]['site_name']}")
            return sites[0]

        # Step 4: Rank by condition specialty match
        if focus_condition:
            ranked_sites = self._rank_by_specialty(sites, focus_condition)
            logger.info(f"Matched to specialty site: {ranked_sites[0]['site_name']}")
            return ranked_sites[0]

        # Step 5: Return default site (highest priority)
        default_site = next((s for s in sites if s.get('is_default')), sites[0])
        logger.info(f"Using default site: {default_site['site_name']}")
        return default_site

    def _normalize_location(self, location: str) -> Optional[str]:
        """Convert location string to city code (e.g., 'Dallas' → 'DAL')"""
        if not location:
            return None

        location_lower = location.lower().strip()

        # Check aliases
        for city_code, aliases in self.CITY_ALIASES.items():
            if location_lower in aliases:
                logger.debug(f"Normalized '{location}' → '{city_code}'")
                return city_code

        # Try extracting first word (e.g., "New Orleans, LA" → "new")
        first_word = location_lower.split()[0] if location_lower else ''
        for city_code, aliases in self.CITY_ALIASES.items():
            if first_word in aliases:
                logger.debug(f"Normalized '{location}' → '{city_code}' (first word match)")
                return city_code

        return None

    def _get_sites_for_city(self, city_code: str) -> List[Dict]:
        """Get all sites for a city code from database"""
        query = """
            SELECT
                sc.site_id,
                sc.site_name,
                sc.coordinator_email,
                sc.coordinator_user_key,
                COALESCE(lsm.specialty, 'General Medicine') as specialty,
                COALESCE(lsm.is_default, false) as is_default,
                COALESCE(lsm.priority, 0) as priority,
                lsm.city_code
            FROM site_coordinators sc
            LEFT JOIN location_site_mappings lsm ON sc.site_id = lsm.site_id
            WHERE (lsm.city_code = %s OR sc.site_name LIKE %s)
              AND sc.is_active = TRUE
            ORDER BY lsm.priority DESC, lsm.is_default DESC, sc.site_name
        """

        city_pattern = f"{city_code} -%"
        results = db.execute_query(query, (city_code, city_pattern))

        if not results:
            logger.warning(f"No sites found for city code: {city_code}")

        return results

    def _get_sites_by_location_name(self, location: str) -> List[Dict]:
        """Direct database lookup by location name (fallback method)"""
        query = """
            SELECT
                sc.site_id,
                sc.site_name,
                sc.coordinator_email,
                sc.coordinator_user_key,
                COALESCE(lsm.specialty, 'General Medicine') as specialty,
                COALESCE(lsm.is_default, false) as is_default,
                COALESCE(lsm.priority, 0) as priority
            FROM site_coordinators sc
            LEFT JOIN location_site_mappings lsm ON sc.site_id = lsm.site_id
            WHERE lsm.location_name ILIKE %s
              AND sc.is_active = TRUE
            ORDER BY lsm.priority DESC, lsm.is_default DESC
        """

        results = db.execute_query(query, (f"%{location}%",))
        return results

    def _rank_by_specialty(self, sites: List[Dict], condition: str) -> List[Dict]:
        """Rank sites by specialty match to condition"""
        if not condition:
            return sites

        condition_lower = condition.lower()

        # Score each site
        scored_sites = []
        for site in sites:
            score = site.get('priority', 0)  # Base score from database priority

            specialty = site.get('specialty', '').lower()

            # Check for specialty match
            for spec_name, keywords in self.SPECIALTY_KEYWORDS.items():
                if spec_name in specialty:
                    # Check if any keywords match the condition
                    if any(kw in condition_lower for kw in keywords):
                        score += 20  # Large boost for specialty match
                        logger.debug(f"Specialty match: {specialty} matches {condition}")
                        break

            # Small boost for General Medicine as fallback
            if 'general' in specialty and score == site.get('priority', 0):
                score += 5

            scored_sites.append((score, site))

        # Sort by score descending
        scored_sites.sort(key=lambda x: x[0], reverse=True)

        return [site for _, site in scored_sites]

    def get_all_city_codes(self) -> List[str]:
        """Get list of all available city codes"""
        query = """
            SELECT DISTINCT city_code
            FROM location_site_mappings
            WHERE city_code IS NOT NULL
            ORDER BY city_code
        """
        results = db.execute_query(query)
        return [r['city_code'] for r in results]

    def get_sites_by_specialty(self, specialty: str) -> List[Dict]:
        """Get all sites matching a specific specialty"""
        query = """
            SELECT
                sc.site_id,
                sc.site_name,
                sc.coordinator_email,
                lsm.specialty,
                lsm.city_code,
                lsm.location_name
            FROM site_coordinators sc
            JOIN location_site_mappings lsm ON sc.site_id = lsm.site_id
            WHERE lsm.specialty ILIKE %s
              AND sc.is_active = TRUE
            ORDER BY lsm.location_name, sc.site_name
        """

        results = db.execute_query(query, (f"%{specialty}%",))
        return results


# Singleton instance
location_site_mapper = LocationSiteMapper()
