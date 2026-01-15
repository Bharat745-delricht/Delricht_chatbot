-- Performance optimization indexes for dashboard conversations endpoint
-- These indexes address the 300+ second timeout issues

-- Critical indexes for chat_logs table
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chat_logs_timestamp ON chat_logs(timestamp);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chat_logs_session_id ON chat_logs(session_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chat_logs_session_timestamp ON chat_logs(session_id, timestamp);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chat_logs_session_user ON chat_logs(session_id, user_id);

-- Critical indexes for conversation_context table  
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversation_context_session_active ON conversation_context(session_id, active);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversation_context_session_id ON conversation_context(session_id);

-- Indexes for prescreening tables to fix N+1 query issues
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_prescreening_sessions_session_id ON prescreening_sessions(session_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_prescreening_sessions_session_started ON prescreening_sessions(session_id, started_at);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_prescreening_answers_session_id ON prescreening_answers(session_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_prescreening_answers_session_created ON prescreening_answers(session_id, created_at);

-- Additional optimization indexes
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_clinical_trials_id ON clinical_trials(id);

-- Comments for documentation
COMMENT ON INDEX idx_chat_logs_timestamp IS 'Optimizes date filtering in conversations endpoint';
COMMENT ON INDEX idx_chat_logs_session_timestamp IS 'Compound index for session grouping and date filtering';
COMMENT ON INDEX idx_conversation_context_session_active IS 'Optimizes LEFT JOIN with active context filtering';
COMMENT ON INDEX idx_prescreening_sessions_session_started IS 'Optimizes prescreening data retrieval per conversation';