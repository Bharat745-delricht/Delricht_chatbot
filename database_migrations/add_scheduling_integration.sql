-- ============================================================================
-- Scheduling Integration Database Migration
-- Adds support for CRIO patient creation and appointment booking from chatbot
-- ============================================================================

-- Location-to-Site Mapping Table
-- Maps user-friendly location names to CRIO site IDs with specialty support
CREATE TABLE IF NOT EXISTS location_site_mappings (
    id SERIAL PRIMARY KEY,
    location_name VARCHAR(200) NOT NULL,     -- "Dallas", "New Orleans", etc.
    city_code VARCHAR(10) NOT NULL,          -- "DAL", "NO", etc.
    site_id VARCHAR(10) NOT NULL,            -- CRIO site ID
    site_name VARCHAR(255) NOT NULL,         -- Full site name from CRIO
    specialty VARCHAR(100),                   -- "General Medicine", "Dermatology", etc.
    is_default BOOLEAN DEFAULT FALSE,        -- Default site for this city
    priority INTEGER DEFAULT 0,              -- Higher = preferred (for ranking)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_location_mappings_location ON location_site_mappings(location_name);
CREATE INDEX IF NOT EXISTS idx_location_mappings_city ON location_site_mappings(city_code);
CREATE INDEX IF NOT EXISTS idx_location_mappings_site ON location_site_mappings(site_id);

-- Add foreign key constraint to site_coordinators (if table exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'site_coordinators') THEN
        ALTER TABLE location_site_mappings
        ADD CONSTRAINT fk_location_site_coordinator
        FOREIGN KEY (site_id)
        REFERENCES site_coordinators(site_id)
        ON DELETE CASCADE;
    END IF;
END $$;

-- ============================================================================
-- Seed Location Mappings Data
-- ============================================================================

INSERT INTO location_site_mappings (location_name, city_code, site_id, site_name, specialty, is_default, priority) VALUES
-- Atlanta sites
('Atlanta', 'ATL', '2327', 'ATL - General Medicine', 'General Medicine', TRUE, 10),
('Atlanta', 'ATL', '3863', 'ATL - Psych', 'Psychiatry', FALSE, 5),
('Atlanta', 'ATL', '2054', 'ATL - Dermatology', 'Dermatology', FALSE, 5),

-- New Orleans sites
('New Orleans', 'NO', '1261', 'NO - General Medicine', 'General Medicine', TRUE, 10),
('New Orleans', 'NO', '1262', 'NO - Dermatology', 'Dermatology', FALSE, 5),
('New Orleans', 'NO', '1263', 'NO - Urology', 'Urology', FALSE, 5),
('New Orleans', 'NO', '3834', 'NO - Psych/ Rheum', 'Psychiatry', FALSE, 5),
('New Orleans', 'NO', '5088', 'NO - Ophthalmology', 'Ophthalmology', FALSE, 5),
('New Orleans', 'NO', '1264', 'NO - Vax', 'Vaccine', FALSE, 3),

-- Dallas sites
('Dallas', 'DAL', '1867', 'DAL - General Medicine', 'General Medicine', TRUE, 10),
('Dallas', 'DAL', '2373', 'DAL - Dermatology', 'Dermatology', FALSE, 5),
('Dallas', 'DAL', '4913', 'DAL - Psych', 'Psychiatry', FALSE, 5),

-- Baton Rouge sites
('Baton Rouge', 'BR', '1265', 'BR - General Medicine', 'General Medicine', TRUE, 10),
('Baton Rouge', 'BR', '1266', 'BR - Dermatology', 'Dermatology', FALSE, 5),
('Baton Rouge', 'BR', '3957', 'BR - Psych', 'Psychiatry', FALSE, 5),
('Baton Rouge', 'BR', '1316', 'BR - Neuro Medical', 'Neurology', FALSE, 5),

-- Nashville sites
('Nashville', 'NAS', '2518', 'NAS - General Medicine', 'General Medicine', TRUE, 10),
('Nashville', 'NAS', '2881', 'NAS - Dermatology', 'Dermatology', FALSE, 5),

-- St Louis sites
('St Louis', 'STL', '2840', 'STL - General Medicine', 'General Medicine', TRUE, 10),
('St Louis', 'STL', '2071', 'STL - Dermatology', 'Dermatology', FALSE, 5),

-- Charleston sites
('Charleston', 'CHS', '2693', 'CHS - General Medicine', 'General Medicine', TRUE, 10),
('Charleston', 'CHS', '4556', 'CHS - Dermatology', 'Dermatology', FALSE, 5),

-- Norfolk sites
('Norfolk', 'NS', '2181', 'NS - General Medicine', 'General Medicine', TRUE, 10),
('Norfolk', 'NS', '1642', 'NS - Dermatology', 'Dermatology', FALSE, 5),
('Norfolk', 'NS', '2372', 'NS - Neurology', 'Neurology', FALSE, 5),

-- Tulsa sites
('Tulsa', 'TUL', '1305', 'TUL - General Medicine', 'General Medicine', TRUE, 10),
('Tulsa', 'TUL', '1409', 'TUL - Dermatology', 'Dermatology', FALSE, 5),
('Tulsa', 'TUL', '3941', 'TUL - Psych', 'Psychiatry', FALSE, 5),

-- Other single-site cities
('Bethesda', 'BET', '2842', 'BET - General Medicine', 'General Medicine', TRUE, 10),
('Bethesda', 'BET', '5383', 'BET - Psych', 'Psychiatry', FALSE, 5),
('Cincinnati', 'CIN', '4886', 'CIN - General Medicine', 'General Medicine', TRUE, 10),
('Charlotte', 'CLT', '3466', 'CLT - General Medicine', 'General Medicine', TRUE, 10),
('Charlotte', 'CLT', '5384', 'CLT - Psych', 'Psychiatry', FALSE, 5),
('Gulfport', 'GU', '2306', 'GU - General Medicine (Kerby)', 'General Medicine', TRUE, 10),
('Gulfport', 'GU', '1853', 'GU - General Medicine (Tamboli)', 'General Medicine', FALSE, 8),
('Houma', 'HMA', '1884', 'HMA - Dermatology', 'Dermatology', TRUE, 10),
('Indianapolis', 'IND', '4957', 'IND - General Medicine', 'General Medicine', TRUE, 10),
('Louisville', 'LOU', '3500', 'LOU - General Medicine', 'General Medicine', TRUE, 10),
('Overland Park', 'OVP', '3468', 'OVP - General Medicine', 'General Medicine', TRUE, 10),
('Salt Lake City', 'SLC', '5282', 'SLC - Gen Med', 'General Medicine', TRUE, 10),
('Springfield', 'SPR', '2517', 'SPR - General Medicine', 'General Medicine', TRUE, 10),
('Steubenville', 'STE', '3502', 'STE - General Medicine', 'General Medicine', TRUE, 10),
('Master Site', 'MASTER', '1818', 'Master Site', 'General Medicine', FALSE, 0)

ON CONFLICT DO NOTHING;

-- ============================================================================
-- CRIO Patient Mappings Table
-- Maps chatbot sessions to CRIO patient IDs
-- ============================================================================

CREATE TABLE IF NOT EXISTS crio_patient_mappings (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) NOT NULL,
    contact_info_id INTEGER REFERENCES patient_contact_info(id) ON DELETE CASCADE,
    crio_patient_id VARCHAR(100) NOT NULL,
    crio_site_id VARCHAR(50) NOT NULL,
    crio_study_id VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, crio_site_id, crio_study_id)
);

CREATE INDEX IF NOT EXISTS idx_crio_patient_session ON crio_patient_mappings(session_id);
CREATE INDEX IF NOT EXISTS idx_crio_patient_id ON crio_patient_mappings(crio_patient_id);
CREATE INDEX IF NOT EXISTS idx_crio_patient_contact ON crio_patient_mappings(contact_info_id);

COMMENT ON TABLE crio_patient_mappings IS 'Maps chatbot sessions to CRIO patient IDs for tracking';
COMMENT ON COLUMN crio_patient_mappings.session_id IS 'Conversation session ID from chatbot';
COMMENT ON COLUMN crio_patient_mappings.crio_patient_id IS 'Patient ID returned by CRIO API';

-- ============================================================================
-- Appointments Table
-- Tracks appointments created from chatbot
-- ============================================================================

CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL PRIMARY KEY,
    crio_appointment_id VARCHAR(100) NOT NULL UNIQUE,
    crio_patient_id VARCHAR(100) NOT NULL,
    session_id VARCHAR(100),
    site_id VARCHAR(50) NOT NULL,
    study_id VARCHAR(50) NOT NULL,
    visit_id VARCHAR(50) NOT NULL,
    coordinator_email VARCHAR(200),
    appointment_date TIMESTAMP NOT NULL,
    duration_minutes INTEGER DEFAULT 60,
    status VARCHAR(50) DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'completed', 'cancelled', 'no_show', 'rescheduled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    FOREIGN KEY (session_id) REFERENCES conversation_context(session_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_appointments_session ON appointments(session_id);
CREATE INDEX IF NOT EXISTS idx_appointments_crio_id ON appointments(crio_appointment_id);
CREATE INDEX IF NOT EXISTS idx_appointments_crio_patient ON appointments(crio_patient_id);
CREATE INDEX IF NOT EXISTS idx_appointments_date ON appointments(appointment_date);
CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(status);
CREATE INDEX IF NOT EXISTS idx_appointments_site ON appointments(site_id);

COMMENT ON TABLE appointments IS 'Appointments created from chatbot scheduling flow';
COMMENT ON COLUMN appointments.crio_appointment_id IS 'Appointment ID returned by CRIO API';
COMMENT ON COLUMN appointments.status IS 'Current appointment status (scheduled, completed, cancelled, no_show, rescheduled)';

-- ============================================================================
-- Extend patient_contact_info Table
-- Add fields needed for CRIO patient creation
-- ============================================================================

ALTER TABLE patient_contact_info
ADD COLUMN IF NOT EXISTS date_of_birth DATE,
ADD COLUMN IF NOT EXISTS gender VARCHAR(10) CHECK (gender IN ('M', 'F', 'Other', 'Prefer not to say'));

COMMENT ON COLUMN patient_contact_info.date_of_birth IS 'Patient date of birth (required for CRIO patient creation)';
COMMENT ON COLUMN patient_contact_info.gender IS 'Patient gender (M/F required for CRIO patient creation)';

-- ============================================================================
-- Summary View - Chatbot Appointments
-- Convenient view for dashboard reporting
-- ============================================================================

CREATE OR REPLACE VIEW chatbot_appointments_summary AS
SELECT
    a.id,
    a.crio_appointment_id,
    a.appointment_date,
    a.status,
    a.site_id,
    sc.site_name,
    a.coordinator_email,
    pci.first_name,
    pci.last_name,
    pci.email,
    pci.phone_number,
    pci.eligibility_status,
    ps.condition,
    ct.trial_name,
    a.created_at as scheduled_at,
    EXTRACT(EPOCH FROM (a.appointment_date - a.created_at)) / 3600 AS hours_to_appointment
FROM appointments a
LEFT JOIN patient_contact_info pci ON a.session_id = pci.session_id
LEFT JOIN prescreening_sessions ps ON pci.prescreening_session_id = ps.id
LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
LEFT JOIN site_coordinators sc ON a.site_id = sc.site_id
ORDER BY a.appointment_date DESC;

COMMENT ON VIEW chatbot_appointments_summary IS 'Summary view of all chatbot-scheduled appointments with patient details';

-- ============================================================================
-- Migration Complete
-- ============================================================================

-- Output summary
DO $$
DECLARE
    location_count INTEGER;
    patient_mapping_count INTEGER;
    appointment_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO location_count FROM location_site_mappings;
    SELECT COUNT(*) INTO patient_mapping_count FROM crio_patient_mappings;
    SELECT COUNT(*) INTO appointment_count FROM appointments;

    RAISE NOTICE '✅ Scheduling integration migration complete';
    RAISE NOTICE '   Location mappings: %', location_count;
    RAISE NOTICE '   Patient mappings: %', patient_mapping_count;
    RAISE NOTICE '   Appointments: %', appointment_count;
END $$;
