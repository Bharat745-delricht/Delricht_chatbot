"""
Site Coordinator Configuration API
Manages coordinator email mappings for CRIO API integration
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from core.database import db
import logging

router = APIRouter(prefix="/api/site-coordinators", tags=["site-coordinators"])
logger = logging.getLogger(__name__)


class SiteCoordinatorMapping(BaseModel):
    """Site coordinator configuration model"""
    site_id: str
    site_name: str
    coordinator_email: EmailStr
    coordinator_user_key: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    is_active: bool = True
    updated_by: str = "admin"


class SiteCoordinatorUpdate(BaseModel):
    """Update model for coordinator email and address"""
    coordinator_email: EmailStr
    coordinator_user_key: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    updated_by: str = "admin"


@router.get("/", response_model=List[SiteCoordinatorMapping])
async def get_all_site_coordinators():
    """
    Get all site coordinator mappings

    Returns:
        List of all site coordinator configurations
    """
    try:
        query = """
            SELECT
                site_id,
                site_name,
                coordinator_email,
                coordinator_user_key,
                address,
                city,
                state,
                zip_code,
                is_active
            FROM site_coordinators
            WHERE is_active = TRUE
            ORDER BY site_name
        """

        results = db.execute_query(query)

        return [
            SiteCoordinatorMapping(
                site_id=row['site_id'],
                site_name=row['site_name'],
                coordinator_email=row['coordinator_email'],
                coordinator_user_key=row['coordinator_user_key'],
                address=row.get('address'),
                city=row.get('city'),
                state=row.get('state'),
                zip_code=row.get('zip_code'),
                is_active=row['is_active']
            )
            for row in results
        ]

    except Exception as e:
        logger.error(f"Failed to fetch site coordinators: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch site coordinators: {str(e)}")


@router.get("/{site_id}", response_model=SiteCoordinatorMapping)
async def get_site_coordinator(site_id: str):
    """
    Get coordinator mapping for a specific site

    Args:
        site_id: CRIO site ID

    Returns:
        Site coordinator configuration
    """
    try:
        query = """
            SELECT
                site_id,
                site_name,
                coordinator_email,
                coordinator_user_key,
                address,
                city,
                state,
                zip_code,
                is_active
            FROM site_coordinators
            WHERE site_id = %s AND is_active = TRUE
        """

        results = db.execute_query(query, (site_id,))

        if not results:
            raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

        row = results[0]
        return SiteCoordinatorMapping(
            site_id=row['site_id'],
            site_name=row['site_name'],
            coordinator_email=row['coordinator_email'],
            coordinator_user_key=row['coordinator_user_key'],
            address=row.get('address'),
            city=row.get('city'),
            state=row.get('state'),
            zip_code=row.get('zip_code'),
            is_active=row['is_active']
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch site coordinator for {site_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch site coordinator: {str(e)}")


@router.put("/{site_id}")
async def update_site_coordinator(site_id: str, update: SiteCoordinatorUpdate):
    """
    Update coordinator email for a site

    Args:
        site_id: CRIO site ID
        update: New coordinator configuration

    Returns:
        Success message
    """
    try:
        # Check if site exists
        check_query = "SELECT site_id FROM site_coordinators WHERE site_id = %s"
        exists = db.execute_query(check_query, (site_id,))

        if not exists:
            raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

        # Update coordinator and address information
        update_query = """
            UPDATE site_coordinators
            SET
                coordinator_email = %s,
                coordinator_user_key = %s,
                address = %s,
                city = %s,
                state = %s,
                zip_code = %s,
                updated_at = CURRENT_TIMESTAMP,
                updated_by = %s
            WHERE site_id = %s
        """

        db.execute_update(
            update_query,
            (update.coordinator_email, update.coordinator_user_key,
             update.address, update.city, update.state, update.zip_code,
             update.updated_by, site_id)
        )

        logger.info(f"Updated coordinator for site {site_id} to {update.coordinator_email}")

        return {
            "success": True,
            "message": f"Coordinator updated for site {site_id}",
            "site_id": site_id,
            "coordinator_email": update.coordinator_email
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update site coordinator for {site_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update coordinator: {str(e)}")


@router.get("/coordinators/list")
async def get_available_coordinators():
    """
    Get list of all available coordinators across the organization

    Returns:
        List of unique coordinators with their user keys
    """
    try:
        query = """
            SELECT DISTINCT
                coordinator_email,
                coordinator_user_key
            FROM site_coordinators
            WHERE is_active = TRUE
            ORDER BY coordinator_email
        """

        results = db.execute_query(query)

        # Add known coordinators not yet in database
        known_coordinators = [
            {"email": "sohit@grovetrials.com", "user_key": "EXTERNAL", "name": "Grove Trials Call Center"},
        ]

        coordinators = [
            {
                "email": row['coordinator_email'],
                "user_key": row['coordinator_user_key']
            }
            for row in results
        ] + known_coordinators

        return coordinators

    except Exception as e:
        logger.error(f"Failed to fetch available coordinators: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch coordinators: {str(e)}")
