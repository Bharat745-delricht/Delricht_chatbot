-- SMS Lead Outreach Campaign System - Database Schema
-- Created: January 5, 2026
-- Purpose: Enable batch SMS outreach for clinical trial recruitment

-- ============================================================================
-- Table 1: lead_campaigns
-- Stores campaign-level information and statistics
-- ============================================================================

CREATE TABLE IF NOT EXISTS lead_campaigns (
    id SERIAL PRIMARY KEY,

    -- Campaign identification
    campaign_name VARCHAR(255) NOT NULL,

    -- Trial information
    trial_id INTEGER REFERENCES clinical_trials(id),
    trial_name VARCHAR(255) NOT NULL,
    condition VARCHAR(200) NOT NULL,
    location VARCHAR(200) NOT NULL,
    site_id VARCHAR(20),

    -- Message template
    initial_message TEXT NOT NULL,

    -- Status tracking
    status VARCHAR(50) NOT NULL DEFAULT 'draft',
    CHECK (status IN ('draft', 'scheduled', 'active', 'completed', 'paused', 'cancelled')),

    -- Statistics (updated in real-time)
    total_leads INTEGER DEFAULT 0,
    sent_count INTEGER DEFAULT 0,
    responded_count INTEGER DEFAULT 0,
    interested_count INTEGER DEFAULT 0,
    not_interested_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,

    -- Audit fields
    created_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Scheduling
    scheduled_for TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    -- Additional metadata (JSONB for flexibility)
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_lead_campaigns_status ON lead_campaigns(status);
CREATE INDEX IF NOT EXISTS idx_lead_campaigns_trial_id ON lead_campaigns(trial_id);
CREATE INDEX IF NOT EXISTS idx_lead_campaigns_created_at ON lead_campaigns(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_campaigns_condition ON lead_campaigns(condition);

-- ============================================================================
-- Table 2: lead_campaign_contacts
-- Stores individual leads within campaigns
-- ============================================================================

CREATE TABLE IF NOT EXISTS lead_campaign_contacts (
    id SERIAL PRIMARY KEY,

    -- Campaign reference
    campaign_id INTEGER NOT NULL REFERENCES lead_campaigns(id) ON DELETE CASCADE,

    -- Contact information
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    phone_number VARCHAR(20) NOT NULL,
    email VARCHAR(255),

    -- Status tracking
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    CHECK (status IN ('pending', 'sent', 'delivered', 'responded', 'interested',
                      'not_interested', 'opt_out', 'error', 'completed', 'prescreening_active',
                      'prescreening_completed', 'eligible', 'ineligible', 'booked')),

    -- Session linkage (links to conversation_context)
    session_id VARCHAR(255),
    prescreening_session_id INTEGER,

    -- SMS tracking
    initial_message_sid VARCHAR(100),
    sent_at TIMESTAMP,
    responded_at TIMESTAMP,
    last_sms_at TIMESTAMP,

    -- Response classification
    response_type VARCHAR(50),
    CHECK (response_type IN ('interested', 'not_interested', 'need_info', 'unclear', 'opted_out', NULL)),

    -- Eligibility tracking
    eligibility_result VARCHAR(50),
    CHECK (eligibility_result IN ('eligible', 'ineligible', 'pending', 'not_started', NULL)),

    -- Error tracking
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Additional metadata
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_lead_campaign_contacts_campaign_id ON lead_campaign_contacts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_lead_campaign_contacts_status ON lead_campaign_contacts(status);
CREATE INDEX IF NOT EXISTS idx_lead_campaign_contacts_session_id ON lead_campaign_contacts(session_id);
CREATE INDEX IF NOT EXISTS idx_lead_campaign_contacts_phone ON lead_campaign_contacts(phone_number);
CREATE INDEX IF NOT EXISTS idx_lead_campaign_contacts_response_type ON lead_campaign_contacts(response_type);

-- ============================================================================
-- Trigger: Auto-update campaign statistics
-- ============================================================================

CREATE OR REPLACE FUNCTION update_lead_campaign_stats()
RETURNS TRIGGER AS $$
BEGIN
    -- Update parent campaign statistics whenever a lead status changes
    UPDATE lead_campaigns
    SET
        sent_count = (
            SELECT COUNT(*) FROM lead_campaign_contacts
            WHERE campaign_id = NEW.campaign_id AND sent_at IS NOT NULL
        ),
        responded_count = (
            SELECT COUNT(*) FROM lead_campaign_contacts
            WHERE campaign_id = NEW.campaign_id AND responded_at IS NOT NULL
        ),
        interested_count = (
            SELECT COUNT(*) FROM lead_campaign_contacts
            WHERE campaign_id = NEW.campaign_id AND response_type = 'interested'
        ),
        not_interested_count = (
            SELECT COUNT(*) FROM lead_campaign_contacts
            WHERE campaign_id = NEW.campaign_id AND response_type = 'not_interested'
        ),
        error_count = (
            SELECT COUNT(*) FROM lead_campaign_contacts
            WHERE campaign_id = NEW.campaign_id AND status = 'error'
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = NEW.campaign_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach trigger to lead_campaign_contacts table
DROP TRIGGER IF EXISTS trigger_update_campaign_stats ON lead_campaign_contacts;
CREATE TRIGGER trigger_update_campaign_stats
    AFTER INSERT OR UPDATE OF status, response_type, sent_at, responded_at
    ON lead_campaign_contacts
    FOR EACH ROW
    EXECUTE FUNCTION update_lead_campaign_stats();

-- ============================================================================
-- Comments for documentation
-- ============================================================================

COMMENT ON TABLE lead_campaigns IS 'SMS lead outreach campaigns for clinical trial recruitment';
COMMENT ON TABLE lead_campaign_contacts IS 'Individual leads within SMS campaigns';
COMMENT ON COLUMN lead_campaigns.initial_message IS 'SMS message template with {first_name}, {last_name} variables';
COMMENT ON COLUMN lead_campaign_contacts.session_id IS 'Links to conversation_context.session_id when lead responds';
COMMENT ON COLUMN lead_campaign_contacts.prescreening_session_id IS 'Links to prescreening_sessions.id if prescreening started';

-- ============================================================================
-- Grant permissions (if needed)
-- ============================================================================

-- GRANT ALL ON lead_campaigns TO postgres;
-- GRANT ALL ON lead_campaign_contacts TO postgres;
-- GRANT USAGE, SELECT ON SEQUENCE lead_campaigns_id_seq TO postgres;
-- GRANT USAGE, SELECT ON SEQUENCE lead_campaign_contacts_id_seq TO postgres;
