"""Visit Mappings API - Dynamic Study-Visit ID Management for Patient Scheduling"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Optional
import json
import asyncio
import httpx
from datetime import datetime
import logging

from core.database import Database

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/visit-mappings", tags=["visit-mappings"])

# Database manager
db = Database()

# =============================================================================
# Visit Mapping Database Schema (Auto-create if needed)
# =============================================================================

VISIT_MAPPINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS study_visit_mappings (
    id SERIAL PRIMARY KEY,
    study_id VARCHAR(20) NOT NULL,
    site_id VARCHAR(10) NOT NULL,
    protocol_number VARCHAR(100),
    site_name VARCHAR(255),
    recruitment_visit_id VARCHAR(20) NOT NULL,
    visit_name VARCHAR(100),
    visit_number VARCHAR(10),
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_verified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE(study_id, site_id)
);

CREATE INDEX IF NOT EXISTS idx_study_visit_mappings_study_id ON study_visit_mappings(study_id);
CREATE INDEX IF NOT EXISTS idx_study_visit_mappings_site_id ON study_visit_mappings(site_id);
"""

# Initialize database table
def init_visit_mappings_table():
    """Create visit mappings table if it doesn't exist"""
    try:
        db.execute_update(VISIT_MAPPINGS_SCHEMA)
        logger.info("Visit mappings table initialized")
    except Exception as e:
        logger.error(f"Failed to initialize visit mappings table: {e}")

# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/mapping/{study_id}")
async def get_visit_mapping(study_id: str) -> Dict[str, Any]:
    """Get recruitment visit ID for a specific study"""
    try:
        result = db.execute_query(
            "SELECT recruitment_visit_id, visit_name, protocol_number, site_name, discovered_at FROM study_visit_mappings WHERE study_id = %s AND is_active = TRUE",
            (study_id,)
        )

        if result:
            return {
                "studyId": study_id,
                "recruitmentVisitId": result[0][0],
                "visitName": result[0][1],
                "protocolNumber": result[0][2],
                "siteName": result[0][3],
                "discoveredAt": result[0][4].isoformat(),
                "found": True
            }
        else:
            return {"studyId": study_id, "found": False}

    except Exception as e:
        logger.error(f"Error getting visit mapping for study {study_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

@router.post("/discover/{study_id}")
async def discover_visit_mapping(study_id: str, site_id: Optional[str] = None) -> Dict[str, Any]:
    """Dynamically discover and cache visit mapping for a study"""
    try:
        # Use provided site_id or try to find the study at any site
        if not site_id:
            # Find which site has this study
            sites_response = await httpx.get(
                "https://scheduling-dashboard-proxy-480267397633.us-central1.run.app/crio/production/sites"
            )
            sites_data = json.loads(sites_response.json()["message"])

            study_site = None
            for site in sites_data:
                study_found = next((s for s in site.get("studies", []) if s["studyKey"] == study_id), None)
                if study_found:
                    site_id = str(site["siteId"])
                    study_site = site
                    break

            if not site_id:
                return {"error": f"Study {study_id} not found at any site", "discovered": False}

        # Get visit structure from CRIO
        availability_data = {
            "siteId": site_id,
            "studyId": study_id,
            "startDate": "19-OCT-2025 00:00",
            "endDate": "20-OCT-2025 23:59",
            "calendars": ["thastings@delricht.com"]
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://scheduling-dashboard-proxy-480267397633.us-central1.run.app/crio/production/calendar/availability",
                json=availability_data,
                timeout=30.0
            )

            if response.status_code == 200:
                visit_data = response.json()

                if visit_data.get("study") and visit_data["study"].get("visits"):
                    visits = visit_data["study"]["visits"]

                    # Find recruitment visit
                    recruitment_visit = next((
                        v for v in visits if
                        v.get("name", "").lower().find("recruitment") >= 0 or
                        v.get("number") == "0"
                    ), None)

                    if recruitment_visit:
                        # Save to database
                        db.execute_update(
                            """INSERT INTO study_visit_mappings
                               (study_id, site_id, recruitment_visit_id, visit_name, visit_number, protocol_number, site_name)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (study_id, site_id) DO UPDATE SET
                               recruitment_visit_id = EXCLUDED.recruitment_visit_id,
                               visit_name = EXCLUDED.visit_name,
                               visit_number = EXCLUDED.visit_number,
                               last_verified = CURRENT_TIMESTAMP""",
                            (
                                study_id,
                                site_id,
                                recruitment_visit["studyVisitId"],
                                recruitment_visit["name"],
                                recruitment_visit.get("number"),
                                study_site.get("protocolNumber") if study_site else None,
                                study_site.get("name") if study_site else None
                            )
                        )

                        return {
                            "studyId": study_id,
                            "siteId": site_id,
                            "recruitmentVisitId": recruitment_visit["studyVisitId"],
                            "visitName": recruitment_visit["name"],
                            "visitNumber": recruitment_visit.get("number"),
                            "discovered": True,
                            "cached": True
                        }
                    else:
                        return {"error": f"No recruitment visit found in {len(visits)} visits", "discovered": False}
                else:
                    return {"error": "No visit structure returned from CRIO", "discovered": False}
            else:
                return {"error": f"CRIO API error: {response.status_code}", "discovered": False}

    except Exception as e:
        logger.error(f"Error discovering visit mapping for study {study_id}: {e}")
        return {"error": str(e), "discovered": False}

@router.get("/all")
async def get_all_mappings() -> Dict[str, Any]:
    """Get all cached visit mappings"""
    try:
        result = db.execute_query(
            "SELECT study_id, recruitment_visit_id, protocol_number, site_name, discovered_at FROM study_visit_mappings WHERE is_active = TRUE ORDER BY discovered_at DESC"
        )

        mappings = {}
        for row in result:
            mappings[row[0]] = {
                "visitId": row[1],
                "protocol": row[2],
                "siteName": row[3],
                "discoveredAt": row[4].isoformat()
            }

        return {
            "mappings": mappings,
            "totalMappings": len(mappings),
            "lastUpdated": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error getting all visit mappings: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

@router.post("/bulk-discovery")
async def bulk_discover_mappings(site_ids: Optional[list] = None) -> Dict[str, Any]:
    """Discover visit mappings for all enrolling studies at specified sites"""
    try:
        if not site_ids:
            site_ids = ['2327', '1305', '2306', '1261', '1266']  # Major DelRicht sites

        discovered = 0
        failed = 0

        # Get all sites and studies
        async with httpx.AsyncClient() as client:
            sites_response = await client.get(
                "https://scheduling-dashboard-proxy-480267397633.us-central1.run.app/crio/production/sites"
            )
            sites_data = json.loads(sites_response.json()["message"])

        for site in sites_data:
            if str(site["siteId"]) in site_ids:
                enrolling_studies = [s for s in site.get("studies", []) if s["status"] == "ENROLLING"]

                for study in enrolling_studies:
                    try:
                        result = await discover_visit_mapping(study["studyKey"], str(site["siteId"]))
                        if result.get("discovered"):
                            discovered += 1
                        else:
                            failed += 1

                        # Small delay to avoid rate limiting
                        await asyncio.sleep(0.2)

                    except Exception as e:
                        logger.error(f"Failed to discover mapping for study {study['studyKey']}: {e}")
                        failed += 1

        return {
            "bulkDiscovery": True,
            "discovered": discovered,
            "failed": failed,
            "sitesProcessed": site_ids,
            "completedAt": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error in bulk discovery: {e}")
        raise HTTPException(status_code=500, detail=f"Bulk discovery error: {e}")

# Initialize table on module load
init_visit_mappings_table()