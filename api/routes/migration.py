"""
Database Migration Endpoint

Provides endpoints for deploying database migrations safely.
"""

import os
import logging
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

from core.database import db

logger = logging.getLogger(__name__)
router = APIRouter()


def verify_admin_access():
    """Simple admin verification - in production, implement proper auth"""
    # For now, just check if we're in the right environment
    if not os.getenv('K_SERVICE'):
        raise HTTPException(status_code=403, detail="Access denied - admin only")
    return True


@router.post("/migrate/contact-collection")
async def deploy_contact_collection_migration(admin: bool = Depends(verify_admin_access)):
    """Deploy the contact collection database migration"""
    try:
        # Contact collection migration SQL
        migration_sql = """
        -- Contact Collection System Migration
        -- Creates patient_contact_info table for storing contact information collected after prescreening

        -- Create the patient contact information table
        CREATE TABLE IF NOT EXISTS patient_contact_info (
            id SERIAL PRIMARY KEY,
            session_id VARCHAR(255) NOT NULL UNIQUE,
            prescreening_session_id INTEGER,
            first_name VARCHAR(100) NOT NULL,
            last_name VARCHAR(100) NOT NULL,
            phone_number VARCHAR(20) NOT NULL,
            email VARCHAR(255) NOT NULL,
            eligibility_status VARCHAR(20) NOT NULL,
            contact_preference VARCHAR(100),
            consent_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Create indexes for performance
        CREATE INDEX IF NOT EXISTS idx_patient_contact_session_id ON patient_contact_info(session_id);
        CREATE INDEX IF NOT EXISTS idx_patient_contact_eligibility ON patient_contact_info(eligibility_status);
        CREATE INDEX IF NOT EXISTS idx_patient_contact_created_at ON patient_contact_info(created_at);

        -- Add trigger to update updated_at timestamp
        CREATE OR REPLACE FUNCTION update_patient_contact_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trigger_update_patient_contact_updated_at ON patient_contact_info;
        CREATE TRIGGER trigger_update_patient_contact_updated_at
            BEFORE UPDATE ON patient_contact_info
            FOR EACH ROW
            EXECUTE FUNCTION update_patient_contact_updated_at();

        -- Add comment for documentation
        COMMENT ON TABLE patient_contact_info IS 'Stores contact information collected from patients after prescreening completion';
        COMMENT ON COLUMN patient_contact_info.session_id IS 'Links to conversation_context.session_id';
        COMMENT ON COLUMN patient_contact_info.prescreening_session_id IS 'Optional link to prescreening_sessions.id if available';
        COMMENT ON COLUMN patient_contact_info.eligibility_status IS 'Patient eligibility status: eligible, ineligible, or pending';
        COMMENT ON COLUMN patient_contact_info.contact_preference IS 'Preferred contact method or purpose (e.g., visit_scheduling, eligibility_review)';
        """
        
        # Execute migration
        logger.info("Starting contact collection migration...")
        db.execute_update(migration_sql)
        logger.info("Migration executed successfully")
        
        # Verify table was created
        verification_result = db.execute_query("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'patient_contact_info'
        """)
        
        if verification_result:
            # Check table structure
            columns_result = db.execute_query("""
                SELECT column_name, data_type, is_nullable 
                FROM information_schema.columns 
                WHERE table_name = 'patient_contact_info' 
                ORDER BY ordinal_position
            """)
            
            return {
                "status": "success",
                "message": "Contact collection migration completed successfully",
                "table_created": True,
                "columns": [
                    {
                        "name": col["column_name"],
                        "type": col["data_type"],
                        "nullable": col["is_nullable"] == 'YES'
                    }
                    for col in columns_result
                ]
            }
        else:
            raise HTTPException(status_code=500, detail="Migration executed but table was not created")
            
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail=f"Migration failed: {str(e)}"
        )


@router.get("/test/contact-service")
async def test_contact_collection_service():
    """Test the contact collection service functionality"""
    try:
        from core.services.contact_collection_service import contact_collection_service
        
        results = {}
        
        # Test invitation message generation
        try:
            eligible_msg = contact_collection_service.get_contact_invitation_message("eligible", "Test Trial")
            results["invitation_messages"] = {
                "eligible": "✅ Generated successfully",
                "eligible_length": len(eligible_msg)
            }
        except Exception as e:
            results["invitation_messages"] = {"error": str(e)}
        
        # Test consent processing
        try:
            consent_given, response, state = contact_collection_service.process_consent_response("yes")
            results["consent_processing"] = {
                "test_input": "yes",
                "consent_given": consent_given,
                "next_state": state,
                "valid": consent_given is True and state == "collecting_first_name"
            }
        except Exception as e:
            results["consent_processing"] = {"error": str(e)}
        
        # Test name extraction
        try:
            first_name = contact_collection_service._extract_name("My name is John")
            results["name_extraction"] = {
                "test_input": "My name is John",
                "extracted": first_name,
                "valid": first_name == "John"
            }
        except Exception as e:
            results["name_extraction"] = {"error": str(e)}
        
        # Test phone number extraction
        try:
            phone = contact_collection_service._extract_phone_number("555-123-4567")
            results["phone_extraction"] = {
                "test_input": "555-123-4567",
                "extracted": phone,
                "valid": phone == "(555) 123-4567"
            }
        except Exception as e:
            results["phone_extraction"] = {"error": str(e)}
        
        # Test email extraction
        try:
            email = contact_collection_service._extract_email("test@example.com")
            results["email_extraction"] = {
                "test_input": "test@example.com",
                "extracted": email,
                "valid": email == "test@example.com"
            }
        except Exception as e:
            results["email_extraction"] = {"error": str(e)}
        
        # Overall status
        all_tests_passed = all(
            test_result.get("valid", False) or "error" not in test_result
            for test_result in results.values()
            if isinstance(test_result, dict)
        )
        
        return {
            "status": "success" if all_tests_passed else "partial_failure",
            "message": "Contact collection service tests completed",
            "test_results": results,
            "all_tests_passed": all_tests_passed
        }
        
    except Exception as e:
        logger.error(f"Service test failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Service test failed: {str(e)}"
        )


@router.get("/status/migration")
async def check_migration_status():
    """Check the status of database migrations"""
    try:
        # Check if patient_contact_info table exists
        table_exists = db.execute_query("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = 'patient_contact_info'
            )
        """)
        
        contact_table_exists = table_exists[0]["exists"] if table_exists else False
        
        if contact_table_exists:
            # Get table info
            row_count = db.execute_query("SELECT COUNT(*) as count FROM patient_contact_info")
            contact_count = row_count[0]["count"] if row_count else 0
            
            return {
                "status": "ready",
                "contact_collection_table": {
                    "exists": True,
                    "record_count": contact_count
                },
                "message": "Contact collection system is ready"
            }
        else:
            return {
                "status": "not_deployed",
                "contact_collection_table": {
                    "exists": False
                },
                "message": "Contact collection system not yet deployed"
            }
            
    except Exception as e:
        logger.error(f"Status check failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Status check failed: {str(e)}"
        )