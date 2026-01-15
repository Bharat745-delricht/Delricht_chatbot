"""Trial management endpoints for the dashboard"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

from core.database import db

logger = logging.getLogger(__name__)
router = APIRouter()


class TrialInvestigator(BaseModel):
    """Model for trial investigator data"""
    trial_id: int
    investigator_name: str
    site_location: str
    site_id: Optional[str] = None  # Site ID for proper site assignment


class TrialUpdate(BaseModel):
    """Model for trial update data"""
    trial_name: Optional[str] = None
    conditions: Optional[str] = None
    phase: Optional[str] = None
    status: Optional[str] = None
    sponsor: Optional[str] = None
    description: Optional[str] = None


class CriterionOrderUpdate(BaseModel):
    """Model for updating criterion sort order"""
    criterion_id: int
    sort_order: int


class BulkOrderUpdate(BaseModel):
    """Model for bulk updating criterion order"""
    updates: list[CriterionOrderUpdate]


class BatchInvestigatorAdd(BaseModel):
    """Model for batch adding investigators to a trial"""
    trial_id: int
    investigators: list[dict]  # List of {investigator_name, site_location}


@router.get("/trials/{trial_id}/investigators")
async def get_trial_investigators(trial_id: int):
    """Get all investigators for a specific trial."""
    try:
        # Check if trial exists
        trial = db.execute_query("""
            SELECT id, trial_name as title, protocol_number as protocol_no 
            FROM clinical_trials 
            WHERE id = %s
        """, (trial_id,))
        
        if not trial:
            raise HTTPException(status_code=404, detail=f"Trial with ID {trial_id} not found")
        
        # Get investigators
        investigators = db.execute_query("""
            SELECT 
                id,
                trial_id,
                investigator_name,
                site_location
            FROM trial_investigators
            WHERE trial_id = %s
            ORDER BY investigator_name
        """, (trial_id,))
        
        return {
            "trial": trial[0],
            "investigators": investigators if investigators else []
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trial investigators: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting trial investigators: {str(e)}")


@router.get("/trials/{trial_id}/investigators/available")
async def get_available_investigators(trial_id: int):
    """Get investigators not yet associated with this trial."""
    try:
        # Check if trial exists
        trial = db.execute_query("""
            SELECT id FROM clinical_trials WHERE id = %s
        """, (trial_id,))
        
        if not trial:
            raise HTTPException(status_code=404, detail=f"Trial with ID {trial_id} not found")
        
        # Get all unique investigators not associated with this trial
        available_investigators = db.execute_query("""
            SELECT DISTINCT 
                investigator_name,
                site_location
            FROM trial_investigators ti1
            WHERE NOT EXISTS (
                SELECT 1 FROM trial_investigators ti2 
                WHERE ti2.trial_id = %s 
                AND ti2.investigator_name = ti1.investigator_name 
                AND ti2.site_location = ti1.site_location
            )
            ORDER BY investigator_name, site_location
        """, (trial_id,))
        
        return {
            "trial_id": trial_id,
            "available_investigators": available_investigators if available_investigators else []
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting available investigators: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting available investigators: {str(e)}")


@router.get("/investigators/{investigator_name}/sites")
async def get_investigator_sites(investigator_name: str):
    """
    Get all sites where an investigator works.
    Used for auto-populating site_id when adding investigator to trial.

    Returns:
        - Single site (100% of cases): Auto-populate site_id
        - Multiple sites (rare): Show dropdown
        - No sites (new investigator): Require site selection
    """
    try:
        sites = db.execute_query("""
            SELECT DISTINCT
                ti.site_id,
                sc.site_name,
                sc.coordinator_email,
                sc.coordinator_user_key
            FROM trial_investigators ti
            JOIN site_coordinators sc ON ti.site_id = sc.site_id
            WHERE ti.investigator_name = %s
            AND ti.site_id IS NOT NULL
            AND sc.is_active = true
            ORDER BY sc.site_name
        """, (investigator_name,))

        return {
            "investigator_name": investigator_name,
            "site_count": len(sites),
            "sites": sites if sites else [],
            "auto_populate": len(sites) == 1,  # True if single site
            "suggested_site": sites[0] if len(sites) == 1 else None
        }
    except Exception as e:
        logger.error(f"Error getting investigator sites: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting investigator sites: {str(e)}")


@router.post("/trials/investigators/batch")
async def batch_add_investigators(batch_data: BatchInvestigatorAdd):
    """Batch add investigators to a trial."""
    try:
        # Check if trial exists
        trial = db.execute_query("""
            SELECT id FROM clinical_trials WHERE id = %s
        """, (batch_data.trial_id,))
        
        if not trial:
            raise HTTPException(status_code=404, detail=f"Trial with ID {batch_data.trial_id} not found")
        
        added_count = 0
        skipped_count = 0
        errors = []
        
        for inv_data in batch_data.investigators:
            try:
                investigator_name = inv_data.get('investigator_name', '').strip()
                site_location = inv_data.get('site_location', '').strip()
                
                if not investigator_name or not site_location:
                    errors.append(f"Missing name or location for investigator data: {inv_data}")
                    continue
                
                # Check for duplicate
                existing = db.execute_query("""
                    SELECT id FROM trial_investigators
                    WHERE trial_id = %s AND investigator_name = %s AND site_location = %s
                """, (batch_data.trial_id, investigator_name, site_location))
                
                if existing:
                    skipped_count += 1
                    continue
                
                # Add investigator
                db.execute_insert_returning("""
                    INSERT INTO trial_investigators (trial_id, investigator_name, site_location)
                    VALUES (%s, %s, %s)
                    RETURNING id
                """, (batch_data.trial_id, investigator_name, site_location))
                
                added_count += 1
                
            except Exception as e:
                errors.append(f"Error adding {inv_data}: {str(e)}")
        
        return {
            "message": f"Batch add completed: {added_count} added, {skipped_count} skipped",
            "trial_id": batch_data.trial_id,
            "added_count": added_count,
            "skipped_count": skipped_count,
            "errors": errors
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in batch add investigators: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error in batch add investigators: {str(e)}")


@router.post("/trials/investigators")
async def create_trial_investigator(investigator: TrialInvestigator):
    """Create a new investigator for a trial."""
    try:
        # Check if trial exists
        trial = db.execute_query("""
            SELECT id FROM clinical_trials WHERE id = %s
        """, (investigator.trial_id,))
        
        if not trial:
            raise HTTPException(status_code=404, detail=f"Trial with ID {investigator.trial_id} not found")
        
        # Check for duplicate
        existing = db.execute_query("""
            SELECT id FROM trial_investigators
            WHERE trial_id = %s AND investigator_name = %s AND site_location = %s
        """, (investigator.trial_id, investigator.investigator_name, investigator.site_location))
        
        if existing:
            raise HTTPException(status_code=400, detail="This investigator already exists for this trial")
        
        # Validate site_id is provided (prevent orphaned trials)
        if not investigator.site_id:
            logger.warning(f"Investigator added without site_id: {investigator.investigator_name}")
            # Allow for backward compatibility but log warning

        # Create investigator
        result = db.execute_insert_returning("""
            INSERT INTO trial_investigators (trial_id, investigator_name, site_location, site_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (investigator.trial_id, investigator.investigator_name, investigator.site_location, investigator.site_id))
        
        if result:
            return {
                "message": "Investigator created successfully",
                "id": result['id'],
                "trial_id": investigator.trial_id,
                "investigator_name": investigator.investigator_name,
                "site_location": investigator.site_location
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create investigator")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating trial investigator: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating trial investigator: {str(e)}")


@router.delete("/trials/investigators/{investigator_id}")
async def delete_trial_investigator(investigator_id: int):
    """Delete an investigator."""
    try:
        # Check if investigator exists
        existing = db.execute_query("""
            SELECT id, trial_id, investigator_name 
            FROM trial_investigators 
            WHERE id = %s
        """, (investigator_id,))
        
        if not existing:
            raise HTTPException(status_code=404, detail="Investigator not found")
        
        # Delete investigator
        db.execute_update("""
            DELETE FROM trial_investigators WHERE id = %s
        """, (investigator_id,))
        
        return {
            "message": "Investigator deleted successfully",
            "deleted_investigator": existing[0]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting trial investigator: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting trial investigator: {str(e)}")


@router.get("/trials")
async def get_all_trials():
    """Get all trials with basic information."""
    try:
        trials = db.execute_query("""
            SELECT 
                ct.id,
                ct.trial_name as title,
                ct.protocol_number as protocol_no,
                ct.conditions,
                ct.uploaded_at,
                COALESCE(investigator_counts.investigator_count, 0) as investigator_count,
                COALESCE(criteria_counts.criteria_count, 0) as criteria_count,
                CASE WHEN pm.id IS NOT NULL THEN pm.id ELSE null END as has_protocol
            FROM clinical_trials ct
            LEFT JOIN (
                SELECT trial_id, COUNT(*) as investigator_count 
                FROM trial_investigators 
                GROUP BY trial_id
            ) investigator_counts ON ct.id = investigator_counts.trial_id
            LEFT JOIN (
                SELECT trial_id, COUNT(*) as criteria_count 
                FROM trial_criteria 
                GROUP BY trial_id
            ) criteria_counts ON ct.id = criteria_counts.trial_id
            LEFT JOIN protocol_metadata pm ON ct.id = pm.trial_id
            ORDER BY ct.uploaded_at DESC
        """)
        
        return {"trials": trials if trials else []}
    except Exception as e:
        logger.error(f"Error getting all trials: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting all trials: {str(e)}")


@router.get("/trials/{trial_id}")
async def get_trial_details(trial_id: int):
    """Get detailed information about a specific trial."""
    try:
        # Get trial details
        trial = db.execute_query("""
            SELECT 
                ct.*,
                pm.id as protocol_metadata_id,
                pm.protocol_summary
            FROM clinical_trials ct
            LEFT JOIN protocol_metadata pm ON ct.id = pm.trial_id
            WHERE ct.id = %s
        """, (trial_id,))
        
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        
        # Get investigator count
        inv_count = db.execute_query("""
            SELECT COUNT(*) as count FROM trial_investigators WHERE trial_id = %s
        """, (trial_id,))
        
        # Get criteria count
        crit_count = db.execute_query("""
            SELECT 
                criterion_type,
                COUNT(*) as count 
            FROM trial_criteria 
            WHERE trial_id = %s
            GROUP BY criterion_type
        """, (trial_id,))
        
        trial_data = trial[0]
        trial_data['investigator_count'] = inv_count[0]['count'] if inv_count else 0
        trial_data['criteria_counts'] = {c['criterion_type']: c['count'] for c in crit_count} if crit_count else {}
        
        return trial_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trial details: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting trial details: {str(e)}")


@router.put("/trials/{trial_id}")
async def update_trial(trial_id: int, trial_update: TrialUpdate):
    """Update trial information."""
    try:
        # Check if trial exists
        existing = db.execute_query("""
            SELECT id, trial_name, conditions, phase, status 
            FROM clinical_trials WHERE id = %s
        """, (trial_id,))
        
        if not existing:
            raise HTTPException(status_code=404, detail="Trial not found")
        
        # Build update query dynamically based on provided fields
        update_fields = []
        update_values = []
        
        if trial_update.trial_name is not None:
            update_fields.append("trial_name = %s")
            update_values.append(trial_update.trial_name)
        
        if trial_update.conditions is not None:
            update_fields.append("conditions = %s")
            update_values.append(trial_update.conditions)
        
        if trial_update.phase is not None:
            update_fields.append("phase = %s")
            update_values.append(trial_update.phase)
        
        if trial_update.status is not None:
            update_fields.append("status = %s")
            update_values.append(trial_update.status)
        
        if trial_update.sponsor is not None:
            update_fields.append("sponsor = %s")
            update_values.append(trial_update.sponsor)
        
        if trial_update.description is not None:
            update_fields.append("description = %s")
            update_values.append(trial_update.description)
        
        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields provided for update")
        
        # Add updated_at timestamp
        update_fields.append("updated_at = NOW()")
        update_values.append(trial_id)
        
        # Execute update
        update_query = f"""
            UPDATE clinical_trials 
            SET {', '.join(update_fields)}
            WHERE id = %s
        """
        
        db.execute_update(update_query, update_values)
        
        # Get updated trial
        updated_trial = db.execute_query("""
            SELECT id, trial_name, conditions, phase, status, sponsor, description, updated_at
            FROM clinical_trials WHERE id = %s
        """, (trial_id,))
        
        return {
            "message": "Trial updated successfully",
            "updated_trial": updated_trial[0] if updated_trial else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating trial: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating trial: {str(e)}")


@router.delete("/trials/{trial_id}")
async def delete_trial(trial_id: int):
    """Delete a trial and all associated data."""
    try:
        # Check if trial exists
        trial = db.execute_query("""
            SELECT id, trial_name, protocol_number 
            FROM clinical_trials WHERE id = %s
        """, (trial_id,))
        
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        
        # Delete in order due to foreign key constraints
        # 1. Delete protocol documents
        db.execute_update("DELETE FROM protocol_documents WHERE trial_id = %s", (trial_id,))
        
        # 2. Delete protocol metadata
        db.execute_update("DELETE FROM protocol_metadata WHERE trial_id = %s", (trial_id,))
        
        # 3. Delete trial investigators
        db.execute_update("DELETE FROM trial_investigators WHERE trial_id = %s", (trial_id,))
        
        # 4. Delete trial criteria
        db.execute_update("DELETE FROM trial_criteria WHERE trial_id = %s", (trial_id,))
        
        # 5. Delete prescreening data
        db.execute_update("DELETE FROM prescreening_answers WHERE trial_id = %s", (trial_id,))
        db.execute_update("DELETE FROM prescreening_sessions WHERE trial_id = %s", (trial_id,))
        
        # 6. Finally delete the trial itself
        db.execute_update("DELETE FROM clinical_trials WHERE id = %s", (trial_id,))
        
        return {
            "message": "Trial deleted successfully",
            "deleted_trial": trial[0]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting trial: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting trial: {str(e)}")


@router.get("/trials/{trial_id}/criteria-order")
async def get_criteria_order(trial_id: int):
    """Get criteria with their current ordering for prescreening questions."""
    try:
        # Check if trial exists
        trial = db.execute_query("SELECT id FROM clinical_trials WHERE id = %s", (trial_id,))
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        
        # Get criteria with current ordering
        criteria = db.execute_query("""
            SELECT 
                id,
                criterion_type,
                criterion_text,
                category,
                is_required,
                sort_order,
                created_at
            FROM trial_criteria 
            WHERE trial_id = %s 
            ORDER BY 
                CASE WHEN sort_order > 0 THEN sort_order ELSE 9999 END,
                CASE 
                    WHEN criterion_type = 'exclusion' THEN 1 
                    WHEN criterion_type = 'inclusion' THEN 2 
                    ELSE 3 
                END,
                CASE 
                    WHEN category = 'demographics' THEN 1
                    WHEN category = 'demographic' THEN 1
                    WHEN category = 'safety' THEN 2
                    WHEN category = 'medical_history' THEN 3
                    WHEN category = 'disease_specific' THEN 4
                    WHEN category = 'laboratory' THEN 5
                    WHEN category = 'medications' THEN 6
                    WHEN category = 'reproductive' THEN 7
                    WHEN category = 'study_procedures' THEN 8
                    WHEN category = 'general' THEN 9
                    ELSE 10
                END,
                id
        """, (trial_id,))
        
        return {
            "trial_id": trial_id,
            "criteria": criteria or [],
            "total_criteria": len(criteria) if criteria else 0
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting criteria order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting criteria order: {str(e)}")


@router.put("/trials/{trial_id}/criteria-order")
async def update_criteria_order(trial_id: int, order_update: BulkOrderUpdate):
    """Update the sort order for multiple criteria to control prescreening question order."""
    try:
        # Check if trial exists
        trial = db.execute_query("SELECT id FROM clinical_trials WHERE id = %s", (trial_id,))
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        
        # Validate all criterion IDs belong to this trial
        criterion_ids = [update.criterion_id for update in order_update.updates]
        if criterion_ids:
            existing_criteria = db.execute_query("""
                SELECT id FROM trial_criteria 
                WHERE trial_id = %s AND id = ANY(%s)
            """, (trial_id, criterion_ids))
            
            existing_ids = [c['id'] for c in existing_criteria] if existing_criteria else []
            invalid_ids = set(criterion_ids) - set(existing_ids)
            
            if invalid_ids:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid criterion IDs for trial {trial_id}: {list(invalid_ids)}"
                )
        
        # Apply the sort order updates
        updated_count = 0
        for update in order_update.updates:
            db.execute_update("""
                UPDATE trial_criteria 
                SET sort_order = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND trial_id = %s
            """, (update.sort_order, update.criterion_id, trial_id))
            updated_count += 1
        
        logger.info(f"Updated sort order for {updated_count} criteria in trial {trial_id}")
        
        return {
            "message": f"Successfully updated sort order for {updated_count} criteria",
            "trial_id": trial_id,
            "updates_applied": updated_count
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating criteria order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating criteria order: {str(e)}")


@router.post("/trials/{trial_id}/criteria-order/reset")
async def reset_criteria_order(trial_id: int):
    """Reset all criteria sort orders to use smart defaults."""
    try:
        # Check if trial exists
        trial = db.execute_query("SELECT id FROM clinical_trials WHERE id = %s", (trial_id,))
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        
        # Reset all sort_order values to 0 (which means use defaults)
        result = db.execute_update("""
            UPDATE trial_criteria 
            SET sort_order = 0, updated_at = CURRENT_TIMESTAMP
            WHERE trial_id = %s
        """, (trial_id,))
        
        # Get count of criteria that were reset
        criteria_count = db.execute_query("""
            SELECT COUNT(*) as count FROM trial_criteria WHERE trial_id = %s
        """, (trial_id,))
        
        reset_count = criteria_count[0]['count'] if criteria_count else 0
        
        logger.info(f"Reset sort order for {reset_count} criteria in trial {trial_id}")
        
        return {
            "message": f"Successfully reset sort order for {reset_count} criteria to smart defaults",
            "trial_id": trial_id,
            "criteria_reset": reset_count
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resetting criteria order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error resetting criteria order: {str(e)}")