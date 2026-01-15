-- ============================================================================
-- Shared CRIO Session Storage
-- Allows V3 Dashboard and Chatbot Backend to share authentication session
-- ============================================================================

CREATE TABLE IF NOT EXISTS crio_shared_session (
    id SERIAL PRIMARY KEY,

    -- Session tokens
    session_id TEXT NOT NULL,
    csrf_token TEXT NOT NULL,

    -- Session metadata
    authenticated_by VARCHAR(255), -- Email of user who logged in
    authenticated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL, -- When tokens expire (8 hours from auth)
    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Usage tracking
    used_by_chatbot_count INTEGER DEFAULT 0,
    used_by_dashboard_count INTEGER DEFAULT 0,

    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    invalidated_at TIMESTAMP,
    invalidation_reason TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Only keep the most recent active session
CREATE UNIQUE INDEX idx_crio_session_active
ON crio_shared_session(is_active)
WHERE is_active = TRUE;

-- Index for expiry checks
CREATE INDEX idx_crio_session_expires ON crio_shared_session(expires_at);

-- ============================================================================
-- Helper function to get current valid session
-- ============================================================================
CREATE OR REPLACE FUNCTION get_valid_crio_session()
RETURNS TABLE(session_id TEXT, csrf_token TEXT) AS $$
BEGIN
    RETURN QUERY
    SELECT s.session_id, s.csrf_token
    FROM crio_shared_session s
    WHERE s.is_active = TRUE
      AND s.expires_at > NOW()
    ORDER BY s.authenticated_at DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Helper function to update last_used timestamp
-- ============================================================================
CREATE OR REPLACE FUNCTION update_session_usage(
    source TEXT -- 'chatbot' or 'dashboard'
)
RETURNS VOID AS $$
BEGIN
    UPDATE crio_shared_session
    SET
        last_used_at = NOW(),
        used_by_chatbot_count = CASE WHEN source = 'chatbot'
            THEN used_by_chatbot_count + 1
            ELSE used_by_chatbot_count END,
        used_by_dashboard_count = CASE WHEN source = 'dashboard'
            THEN used_by_dashboard_count + 1
            ELSE used_by_dashboard_count END
    WHERE is_active = TRUE
      AND expires_at > NOW();
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Comments for documentation
-- ============================================================================
COMMENT ON TABLE crio_shared_session IS
'Shared CRIO authentication session used by both V3 Dashboard and Chatbot Backend. Only one active session at a time. Session is created when user logs into V3 Dashboard and used by chatbot for availability lookups.';

COMMENT ON COLUMN crio_shared_session.expires_at IS
'CRIO sessions expire after 8 hours. V3 Dashboard sets this to authenticated_at + 8 hours. Chatbot checks this before using tokens.';

COMMENT ON COLUMN crio_shared_session.is_active IS
'Only one session can be active at a time. New login invalidates previous session.';
