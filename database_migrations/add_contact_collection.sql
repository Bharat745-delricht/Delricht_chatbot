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
    eligibility_status VARCHAR(20) NOT NULL CHECK (eligibility_status IN ('eligible', 'ineligible', 'pending')),
    contact_preference VARCHAR(100),
    consent_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    CONSTRAINT fk_patient_contact_session FOREIGN KEY (session_id) 
        REFERENCES conversation_context(session_id) ON DELETE CASCADE,
    CONSTRAINT fk_patient_contact_prescreening FOREIGN KEY (prescreening_session_id) 
        REFERENCES prescreening_sessions(id) ON DELETE SET NULL
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

CREATE TRIGGER trigger_update_patient_contact_updated_at
    BEFORE UPDATE ON patient_contact_info
    FOR EACH ROW
    EXECUTE FUNCTION update_patient_contact_updated_at();

-- Grant appropriate permissions (adjust as needed)
-- GRANT SELECT, INSERT, UPDATE ON patient_contact_info TO your_app_user;
-- GRANT USAGE ON SEQUENCE patient_contact_info_id_seq TO your_app_user;

-- Add comment for documentation
COMMENT ON TABLE patient_contact_info IS 'Stores contact information collected from patients after prescreening completion';
COMMENT ON COLUMN patient_contact_info.session_id IS 'Links to conversation_context.session_id';
COMMENT ON COLUMN patient_contact_info.prescreening_session_id IS 'Optional link to prescreening_sessions.id if available';
COMMENT ON COLUMN patient_contact_info.eligibility_status IS 'Patient eligibility status: eligible, ineligible, or pending';
COMMENT ON COLUMN patient_contact_info.contact_preference IS 'Preferred contact method or purpose (e.g., visit_scheduling, eligibility_review)';