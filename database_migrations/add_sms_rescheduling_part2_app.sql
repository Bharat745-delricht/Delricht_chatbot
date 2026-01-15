-- =====================================================
-- SMS RESCHEDULING SYSTEM - PART 2 (APP USER)
-- =====================================================
-- This part can run as postgres
-- Run this after Part 1
-- =====================================================

BEGIN;

-- =====================================================
-- 1. SMS CONVERSATIONS TABLE
-- =====================================================

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

    CONSTRAINT fk_sms_session FOREIGN KEY (session_id)
        REFERENCES conversation_context(session_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sms_session ON sms_conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_sms_phone ON sms_conversations(phone_number);
CREATE INDEX IF NOT EXISTS idx_sms_created ON sms_conversations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sms_direction ON sms_conversations(direction);
CREATE INDEX IF NOT EXISTS idx_sms_status ON sms_conversations(status);

-- =====================================================
-- 2. RESCHEDULE BATCHES TABLE
-- =====================================================

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

CREATE INDEX IF NOT EXISTS idx_batch_status ON reschedule_batches(status);
CREATE INDEX IF NOT EXISTS idx_batch_created ON reschedule_batches(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_batch_uploaded_by ON reschedule_batches(uploaded_by);

-- =====================================================
-- 3. RESCHEDULE REQUESTS TABLE
-- =====================================================

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

    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN (
        'pending', 'sms_sent', 'patient_responded', 'awaiting_selection',
        'confirmed', 'completed', 'failed', 'escalated'
    )),
    new_appointment_date TIMESTAMP,
    new_appointment_id VARCHAR(100),
    failure_reason TEXT,
    escalated_to_coordinator BOOLEAN DEFAULT FALSE,
    escalation_reason TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    metadata JSONB DEFAULT '{}'::jsonb,

    CONSTRAINT fk_reschedule_session FOREIGN KEY (session_id)
        REFERENCES conversation_context(session_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_reschedule_batch ON reschedule_requests(batch_id);
CREATE INDEX IF NOT EXISTS idx_reschedule_status ON reschedule_requests(status);
CREATE INDEX IF NOT EXISTS idx_reschedule_phone ON reschedule_requests(phone_number);
CREATE INDEX IF NOT EXISTS idx_reschedule_session ON reschedule_requests(session_id);
CREATE INDEX IF NOT EXISTS idx_reschedule_created ON reschedule_requests(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reschedule_escalated ON reschedule_requests(escalated_to_coordinator) WHERE escalated_to_coordinator = TRUE;

-- Trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_reschedule_requests_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_reschedule_requests_updated_at ON reschedule_requests;
CREATE TRIGGER trigger_update_reschedule_requests_updated_at
    BEFORE UPDATE ON reschedule_requests
    FOR EACH ROW
    EXECUTE FUNCTION update_reschedule_requests_updated_at();

-- =====================================================
-- 4. APPOINTMENT RESCHEDULE HISTORY TABLE
-- =====================================================

CREATE TABLE IF NOT EXISTS appointment_reschedule_history (
    id SERIAL PRIMARY KEY,
    appointment_id INT, -- FK to appointments(id) - will be added by admin later
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

CREATE INDEX IF NOT EXISTS idx_reschedule_history_appointment ON appointment_reschedule_history(appointment_id);
CREATE INDEX IF NOT EXISTS idx_reschedule_history_request ON appointment_reschedule_history(reschedule_request_id);
CREATE INDEX IF NOT EXISTS idx_reschedule_history_date ON appointment_reschedule_history(rescheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_reschedule_history_initiated ON appointment_reschedule_history(initiated_by);

-- =====================================================
-- 5. HELPER VIEWS
-- =====================================================

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

COMMIT;

SELECT 'Part 2 (App User) Complete - All SMS tables and views created' as status;
