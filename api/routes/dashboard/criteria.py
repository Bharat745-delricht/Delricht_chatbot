"""Criteria management endpoints for the dashboard"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import json

from core.database import db

logger = logging.getLogger(__name__)
router = APIRouter()


class TrialCriterion(BaseModel):
    """Model for trial criterion data"""
    trial_id: int
    criterion_type: str  # 'inclusion' or 'exclusion'
    criterion_text: str
    is_required: bool = True
    category: Optional[str] = None


class CriterionUpdate(BaseModel):
    """Model for updating a criterion"""
    criterion_text: Optional[str] = None
    is_required: Optional[bool] = None
    category: Optional[str] = None


@router.get("/trials/{trial_id}/criteria")
async def get_trial_criteria(trial_id: int):
    """Get all criteria for a specific trial."""
    try:
        # Check if trial exists
        trial = db.execute_query("""
            SELECT id, trial_name as title, protocol_number as protocol_no 
            FROM clinical_trials 
            WHERE id = %s
        """, (trial_id,))
        
        if not trial:
            raise HTTPException(status_code=404, detail=f"Trial with ID {trial_id} not found")
        
        # Get criteria
        criteria = db.execute_query("""
            SELECT 
                id,
                trial_id,
                criterion_type,
                criterion_text,
                is_required,
                category,
                parsed_json,
                created_at
            FROM trial_criteria
            WHERE trial_id = %s
            ORDER BY criterion_type, id
        """, (trial_id,))
        
        # Organize by type
        inclusion_criteria = []
        exclusion_criteria = []
        
        if criteria:
            for criterion in criteria:
                if criterion['criterion_type'] == 'inclusion':
                    inclusion_criteria.append(criterion)
                else:
                    exclusion_criteria.append(criterion)
        
        return {
            "trial": trial[0],
            "inclusion_criteria": inclusion_criteria,
            "exclusion_criteria": exclusion_criteria,
            "total_criteria": len(criteria) if criteria else 0
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trial criteria: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting trial criteria: {str(e)}")


@router.post("/trials/{trial_id}/criteria")
async def create_trial_criterion(trial_id: int, criterion: TrialCriterion):
    """Create a new criterion for a trial."""
    try:
        # Check if trial exists
        trial = db.execute_query("""
            SELECT id FROM clinical_trials WHERE id = %s
        """, (trial_id,))
        
        if not trial:
            raise HTTPException(status_code=404, detail=f"Trial with ID {trial_id} not found")
        
        # Validate criterion type
        if criterion.criterion_type not in ['inclusion', 'exclusion']:
            raise HTTPException(status_code=400, detail="Criterion type must be 'inclusion' or 'exclusion'")
        
        # Create criterion
        logger.info(f"Creating criterion for trial_id={trial_id}, type={criterion.criterion_type}")
        try:
            result = db.execute_insert_returning("""
                INSERT INTO trial_criteria 
                (trial_id, criterion_type, criterion_text, is_required, category)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (
            trial_id,
            criterion.criterion_type,
            criterion.criterion_text,
            criterion.is_required,
            criterion.category
        ))
            logger.info(f"Criteria insert result: {result}")
        except Exception as e:
            logger.error(f"Error creating criterion: {str(e)}")
            logger.error(f"Parameters: trial_id={trial_id}, type={criterion.criterion_type}")
            raise
        
        if result:
            return {
                "message": "Criterion created successfully",
                "id": result['id'],
                "trial_id": trial_id,
                "criterion_type": criterion.criterion_type,
                "criterion_text": criterion.criterion_text,
                "is_required": criterion.is_required,
                "created_at": result['created_at']
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create criterion")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating trial criterion: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating trial criterion: {str(e)}")


@router.get("/trials/criteria/{criterion_id}")
async def get_trial_criterion(criterion_id: int):
    """Get a specific criterion by ID."""
    try:
        criterion = db.execute_query("""
            SELECT 
                tc.*,
                ct.trial_name as trial_title,
                ct.protocol_number as protocol_no
            FROM trial_criteria tc
            JOIN clinical_trials ct ON tc.trial_id = ct.id
            WHERE tc.id = %s
        """, (criterion_id,))
        
        if not criterion:
            raise HTTPException(status_code=404, detail="Criterion not found")
        
        return criterion[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting criterion: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting criterion: {str(e)}")


@router.put("/trials/criteria/{criterion_id}")
async def update_trial_criterion(criterion_id: int, update: CriterionUpdate):
    """Update a criterion."""
    try:
        # Check if criterion exists
        existing = db.execute_query("""
            SELECT id FROM trial_criteria WHERE id = %s
        """, (criterion_id,))
        
        if not existing:
            raise HTTPException(status_code=404, detail="Criterion not found")
        
        # Build update query dynamically
        update_fields = []
        update_values = []
        
        if update.criterion_text is not None:
            update_fields.append("criterion_text = %s")
            update_values.append(update.criterion_text)
        
        if update.is_required is not None:
            update_fields.append("is_required = %s")
            update_values.append(update.is_required)
        
        if update.category is not None:
            update_fields.append("category = %s")
            update_values.append(update.category)
        
        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        # Add criterion_id to values
        update_values.append(criterion_id)
        
        # Execute update
        query = f"""
            UPDATE trial_criteria
            SET {', '.join(update_fields)}
            WHERE id = %s
            RETURNING id
        """
        
        logger.info(f"Updating criterion {criterion_id} with fields: {update_fields}")
        try:
            result = db.execute_insert_returning(query, update_values)
            logger.info(f"Criteria update result: {result}")
        except Exception as e:
            logger.error(f"Error updating criterion {criterion_id}: {str(e)}")
            logger.error(f"Update query: {query}")
            logger.error(f"Update values: {update_values}")
            raise
        
        if result:
            return {
                "message": "Criterion updated successfully",
                "id": result[0]['id'],
                "updated_at": result[0]['updated_at']
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to update criterion")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating criterion: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating criterion: {str(e)}")


@router.delete("/trials/criteria/{criterion_id}")
async def delete_trial_criterion(criterion_id: int):
    """Delete a criterion."""
    try:
        # Check if criterion exists
        existing = db.execute_query("""
            SELECT id, trial_id, criterion_text, criterion_type 
            FROM trial_criteria 
            WHERE id = %s
        """, (criterion_id,))
        
        if not existing:
            raise HTTPException(status_code=404, detail="Criterion not found")
        
        # Delete criterion
        db.execute_update("""
            DELETE FROM trial_criteria WHERE id = %s
        """, (criterion_id,))
        
        return {
            "message": "Criterion deleted successfully",
            "deleted_criterion": existing[0]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting criterion: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting criterion: {str(e)}")


class QuickUpdate(BaseModel):
    is_required: bool


@router.post("/criteria/quick-update/{criterion_id}")
async def quick_update_criterion(criterion_id: int, update: QuickUpdate):
    """Quick update for criterion's is_required status."""
    try:
        # Check if criterion exists
        existing = db.execute_query("""
            SELECT id FROM trial_criteria WHERE id = %s
        """, (criterion_id,))
        
        if not existing:
            raise HTTPException(status_code=404, detail="Criterion not found")
        
        # Update is_required
        db.execute_update("""
            UPDATE trial_criteria
            SET is_required = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (update.is_required, criterion_id))
        
        return {
            "message": "Criterion requirement status updated",
            "id": criterion_id,
            "is_required": update.is_required
        }
    except Exception as e:
        logger.error(f"Error in quick update: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error in quick update: {str(e)}")


@router.get("/criteria/categories")
async def get_criteria_categories():
    """Get all unique criterion categories."""
    try:
        categories = db.execute_query("""
            SELECT DISTINCT category
            FROM trial_criteria
            WHERE category IS NOT NULL
            ORDER BY category
        """)
        
        return {
            "categories": [c['category'] for c in categories] if categories else []
        }
    except Exception as e:
        logger.error(f"Error getting categories: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting categories: {str(e)}")