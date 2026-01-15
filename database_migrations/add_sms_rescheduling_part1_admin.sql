-- =====================================================
-- SMS RESCHEDULING SYSTEM - PART 1 (REQUIRES ADMIN)
-- =====================================================
-- This part requires database admin privileges
-- Run this first as postgres superuser
-- =====================================================

BEGIN;

-- Add SMS fields to existing patient_contact_info table
ALTER TABLE patient_contact_info
ADD COLUMN IF NOT EXISTS sms_enabled BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS sms_opt_in_date TIMESTAMP,
ADD COLUMN IF NOT EXISTS sms_opt_out_date TIMESTAMP,
ADD COLUMN IF NOT EXISTS last_sms_sent TIMESTAMP;

COMMENT ON COLUMN patient_contact_info.sms_enabled IS 'Patient has consented to SMS communication';
COMMENT ON COLUMN patient_contact_info.sms_opt_in_date IS 'When patient opted in to SMS';
COMMENT ON COLUMN patient_contact_info.sms_opt_out_date IS 'When patient opted out (sent STOP)';
COMMENT ON COLUMN patient_contact_info.last_sms_sent IS 'Last outbound SMS timestamp (for rate limiting)';

COMMIT;

SELECT 'Part 1 (Admin) Complete - SMS fields added to patient_contact_info' as status;
