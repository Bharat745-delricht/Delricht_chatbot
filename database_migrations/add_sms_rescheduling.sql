-- =====================================================
-- SMS RESCHEDULING SYSTEM - DATABASE MIGRATION
-- =====================================================
-- Description: Adds tables and fields for SMS-based automated patient rescheduling
-- Date: 2025-01-19
-- Version: 1.0
--
-- Tables Created:
-- - sms_conversations: Track all SMS messages
-- - reschedule_requests: Individual reschedule requests
-- - reschedule_batches: Batch processing tracking
-- - appointment_reschedule_history: Audit trail for rescheduled appointments
--
-- Schema Changes:
-- - patient_contact_info: Add SMS opt-in fields
-- =====================================================

BEGIN;

-- =====================================================
-- 1. SMS CONVERSATIONS TABLE
-- =====================================================
-- Stores all inbound and outbound SMS messages
-- Links to conversation_context for session tracking

CREATE TABLE IF NOT EXISTS sms_conversations (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100),
    phone_number VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    message_text TEXT NOT NULL,
    twilio_message_sid VARCHAR(100) UNIQUE,
    status VARCHAR(50) DEFAULT 'sent',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb,

    -- Foreign key to conversation_context (optional, may not exist for all SMS)
    CONSTRAINT fk_sms_session FOREIGN KEY (session_id)
        REFERENCES conversation_context(session_id) ON DELETE SET NULL
);

-- Indexes for fast lookups
CREATE INDEX idx_sms_session ON sms_conversations(session_id);
CREATE INDEX idx_sms_phone ON sms_conversations(phone_number);
CREATE INDEX idx_sms_created ON sms_conversations(created_at DESC);
CREATE INDEX idx_sms_direction ON sms_conversations(direction);
CREATE INDEX idx_sms_status ON sms_conversations(status);

COMMENT ON TABLE sms_conversations IS 'Stores all SMS messages for two-way patient communication';
COMMENT ON COLUMN sms_conversations.direction IS 'inbound = patient to system, outbound = system to patient';
COMMENT ON COLUMN sms_conversations.twilio_message_sid IS 'Twilio unique message identifier';
COMMENT ON COLUMN sms_conversations.metadata IS 'Flexible JSON storage for patient_id, appointment_id, etc.';

-- =====================================================
-- 2. ADD SMS FIELDS TO PATIENT_CONTACT_INFO
-- =====================================================
-- Track SMS consent and preferences

ALTER TABLE patient_contact_info
ADD COLUMN IF NOT EXISTS sms_enabled BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS sms_opt_in_date TIMESTAMP,
ADD COLUMN IF NOT EXISTS sms_opt_out_date TIMESTAMP,
ADD COLUMN IF NOT EXISTS last_sms_sent TIMESTAMP;

COMMENT ON COLUMN patient_contact_info.sms_enabled IS 'Patient has consented to SMS communication';
COMMENT ON COLUMN patient_contact_info.sms_opt_in_date IS 'When patient opted in to SMS';
COMMENT ON COLUMN patient_contact_info.sms_opt_out_date IS 'When patient opted out (sent STOP)';
COMMENT ON COLUMN patient_contact_info.last_sms_sent IS 'Last outbound SMS timestamp (for rate limiting)';

-- =====================================================
-- 3. RESCHEDULE BATCHES TABLE
-- =====================================================
-- Track batch processing jobs (Excel uploads)

CREATE TABLE IF NOT EXISTS reschedule_batches (
    id SERIAL PRIMARY KEY,
    batch_name VARCHAR(255),
    uploaded_by VARCHAR(100) NOT NULL,
    total_patients INT NOT NULL DEFAULT 0,
    processed_patients INT DEFAULT 0,
    successful_reschedules INT DEFAULT 0,
    failed_reschedules INT DEFAULT 0,
    pending_patients INT DEFAULT 0,
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'failed', 'cancelled')),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_batch_status ON reschedule_batches(status);
CREATE INDEX idx_batch_created ON reschedule_batches(created_at DESC);
CREATE INDEX idx_batch_uploaded_by ON reschedule_batches(uploaded_by);

COMMENT ON TABLE reschedule_batches IS 'Tracks batch reschedule operations from Excel uploads';
COMMENT ON COLUMN reschedule_batches.metadata IS 'Stores filename, upload source, configuration, etc.';

-- =====================================================
-- 4. RESCHEDULE REQUESTS TABLE
-- =====================================================
-- Individual patient reschedule requests (part of batch or standalone)

CREATE TABLE IF NOT EXISTS reschedule_requests (
    id SERIAL PRIMARY KEY,
    batch_id INT REFERENCES reschedule_batches(id) ON DELETE CASCADE,
    session_id VARCHAR(100),
    patient_name VARCHAR(255),
    phone_number VARCHAR(20) NOT NULL,
    site_id VARCHAR(50) NOT NULL,
    study_id VARCHAR(50) NOT NULL,
    current_appointment_id VARCHAR(100),
    current_appointment_date TIMESTAMP,
    reschedule_after_date DATE NOT NULL,
    patient_availability_notes TEXT,

    -- Tracking fields
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN (
        'pending', 'sms_sent', 'patient_responded', 'awaiting_selection',
        'confirmed', 'completed', 'failed', 'escalated'
    )),
    new_appointment_date TIMESTAMP,
    new_appointment_id VARCHAR(100),
    failure_reason TEXT,
    escalated_to_coordinator BOOLEAN DEFAULT FALSE,
    escalation_reason TEXT,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Flexible metadata storage
    metadata JSONB DEFAULT '{}'::jsonb,

    -- Foreign keys
    CONSTRAINT fk_reschedule_session FOREIGN KEY (session_id)
        REFERENCES conversation_context(session_id) ON DELETE SET NULL
);

-- Indexes for performance
CREATE INDEX idx_reschedule_batch ON reschedule_requests(batch_id);
CREATE INDEX idx_reschedule_status ON reschedule_requests(status);
CREATE INDEX idx_reschedule_phone ON reschedule_requests(phone_number);
CREATE INDEX idx_reschedule_session ON reschedule_requests(session_id);
CREATE INDEX idx_reschedule_created ON reschedule_requests(created_at DESC);
CREATE INDEX idx_reschedule_escalated ON reschedule_requests(escalated_to_coordinator) WHERE escalated_to_coordinator = TRUE;

COMMENT ON TABLE reschedule_requests IS 'Individual patient rescheduling requests with tracking';
COMMENT ON COLUMN reschedule_requests.reschedule_after_date IS 'Minimum date for new appointment (e.g., after Nov 20)';
COMMENT ON COLUMN reschedule_requests.patient_availability_notes IS 'Free text from Excel: afternoons only, not Fridays, etc.';
COMMENT ON COLUMN reschedule_requests.metadata IS 'Stores conversation logs, CRIO responses, slot options, etc.';

-- Trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_reschedule_requests_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_reschedule_requests_updated_at
    BEFORE UPDATE ON reschedule_requests
    FOR EACH ROW
    EXECUTE FUNCTION update_reschedule_requests_updated_at();

-- =====================================================
-- 5. APPOINTMENT RESCHEDULE HISTORY TABLE
-- =====================================================
-- Audit trail for all appointment rescheduling events

CREATE TABLE IF NOT EXISTS appointment_reschedule_history (
    id SERIAL PRIMARY KEY,
    appointment_id INT REFERENCES appointments(id) ON DELETE CASCADE,
    reschedule_request_id INT REFERENCES reschedule_requests(id) ON DELETE SET NULL,
    old_appointment_date TIMESTAMP NOT NULL,
    new_appointment_date TIMESTAMP NOT NULL,
    old_crio_appointment_id VARCHAR(100),
    new_crio_appointment_id VARCHAR(100),
    reason_code VARCHAR(50) CHECK (reason_code IN (
        'patient_request', 'coordinator_request', 'automated_sms',
        'site_conflict', 'study_change', 'other'
    )),
    reason_text TEXT,
    initiated_by VARCHAR(50) CHECK (initiated_by IN ('patient', 'coordinator', 'system')),
    rescheduled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_reschedule_history_appointment ON appointment_reschedule_history(appointment_id);
CREATE INDEX idx_reschedule_history_request ON appointment_reschedule_history(reschedule_request_id);
CREATE INDEX idx_reschedule_history_date ON appointment_reschedule_history(rescheduled_at DESC);
CREATE INDEX idx_reschedule_history_initiated ON appointment_reschedule_history(initiated_by);

COMMENT ON TABLE appointment_reschedule_history IS 'Complete audit trail of all appointment rescheduling events';
COMMENT ON COLUMN appointment_reschedule_history.reason_code IS 'Standardized reason code for analytics';
COMMENT ON COLUMN appointment_reschedule_history.initiated_by IS 'Who initiated the reschedule (patient/coordinator/system)';

-- =====================================================
-- 6. UPDATE EXISTING TABLES
-- =====================================================

-- Add channel tracking to conversation_context (use existing context_data JSONB)
-- No schema change needed - will store in context_data JSON:
-- context_data->>'channel' = 'web' or 'sms'
-- context_data->>'auto_response_disabled' = 'true' or 'false'

COMMENT ON COLUMN conversation_context.context_data IS 'JSONB storage for flexible data including channel (web/sms) and auto_response_disabled flag';

-- =====================================================
-- 7. HELPER VIEWS
-- =====================================================

-- View for active SMS conversations
CREATE OR REPLACE VIEW active_sms_conversations AS
SELECT
    s.session_id,
    s.phone_number,
    r.patient_name,
    r.status as reschedule_status,
    r.site_id,
    r.study_id,
    COUNT(s.id) as message_count,
    MAX(s.created_at) as last_message_at,
    r.escalated_to_coordinator
FROM sms_conversations s
LEFT JOIN reschedule_requests r ON s.session_id = r.session_id
WHERE s.created_at > CURRENT_TIMESTAMP - INTERVAL '7 days'
GROUP BY s.session_id, s.phone_number, r.patient_name, r.status, r.site_id, r.study_id, r.escalated_to_coordinator
ORDER BY last_message_at DESC;

COMMENT ON VIEW active_sms_conversations IS 'Shows active SMS conversations from last 7 days with patient details';

-- View for batch progress
CREATE OR REPLACE VIEW batch_progress_summary AS
SELECT
    b.id as batch_id,
    b.batch_name,
    b.status as batch_status,
    b.total_patients,
    b.processed_patients,
    b.successful_reschedules,
    b.failed_reschedules,
    b.pending_patients,
    ROUND(100.0 * b.successful_reschedules / NULLIF(b.total_patients, 0), 1) as success_rate_pct,
    b.created_at,
    b.started_at,
    b.completed_at,
    EXTRACT(EPOCH FROM (COALESCE(b.completed_at, CURRENT_TIMESTAMP) - b.started_at))/3600 as processing_hours
FROM reschedule_batches b
ORDER BY b.created_at DESC;

COMMENT ON VIEW batch_progress_summary IS 'Summary statistics for batch processing with success rates';

-- =====================================================
-- 8. SAMPLE DATA (FOR TESTING ONLY - REMOVE IN PRODUCTION)
-- =====================================================

-- UNCOMMENT FOR LOCAL TESTING ONLY:
-- INSERT INTO reschedule_batches (batch_name, uploaded_by, total_patients, status)
-- VALUES ('Test Batch November 2025', 'mmorris@delricht.com', 3, 'pending');

-- =====================================================
-- 9. GRANT PERMISSIONS
-- =====================================================

-- Grant access to application user
GRANT ALL PRIVILEGES ON TABLE sms_conversations TO postgres;
GRANT ALL PRIVILEGES ON TABLE reschedule_batches TO postgres;
GRANT ALL PRIVILEGES ON TABLE reschedule_requests TO postgres;
GRANT ALL PRIVILEGES ON TABLE appointment_reschedule_history TO postgres;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO postgres;

GRANT SELECT ON active_sms_conversations TO postgres;
GRANT SELECT ON batch_progress_summary TO postgres;

-- =====================================================
-- MIGRATION COMPLETE
-- =====================================================

COMMIT;

-- Verify tables were created
DO $$
BEGIN
    RAISE NOTICE 'SMS Rescheduling Migration Complete!';
    RAISE NOTICE 'Tables created: sms_conversations, reschedule_batches, reschedule_requests, appointment_reschedule_history';
    RAISE NOTICE 'Views created: active_sms_conversations, batch_progress_summary';
    RAISE NOTICE 'Fields added to patient_contact_info: sms_enabled, sms_opt_in_date, sms_opt_out_date, last_sms_sent';
END $$;
