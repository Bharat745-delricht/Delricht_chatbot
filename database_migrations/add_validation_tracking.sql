-- Migration: Add validation tracking and message feedback for ML training
-- Date: 2026-01-05
-- Purpose: Enable collection of training data for future ML models

-- ============================================================================
-- Part 1: Add validation columns to prescreening_answers
-- ============================================================================

-- Track human validation of answers for training dataset
ALTER TABLE prescreening_answers
ADD COLUMN IF NOT EXISTS human_validated BOOLEAN DEFAULT FALSE;

-- Store corrected values when coordinator fixes incorrect parses
ALTER TABLE prescreening_answers
ADD COLUMN IF NOT EXISTS corrected_value TEXT;

-- Track who validated and when
ALTER TABLE prescreening_answers
ADD COLUMN IF NOT EXISTS validated_by VARCHAR(100);

ALTER TABLE prescreening_answers
ADD COLUMN IF NOT EXISTS validated_at TIMESTAMP;

-- Create index for finding unvalidated answers (for review queue)
CREATE INDEX IF NOT EXISTS idx_answers_validation
ON prescreening_answers(human_validated, created_at)
WHERE human_validated = FALSE;

-- ============================================================================
-- Part 2: Create message feedback table for thumbs up/down
-- ============================================================================

-- Track user feedback on bot responses (thumbs up/down)
CREATE TABLE IF NOT EXISTS message_feedback (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR NOT NULL,
    chat_log_id INTEGER REFERENCES chat_logs(id) ON DELETE CASCADE,
    feedback_type VARCHAR NOT NULL CHECK (feedback_type IN ('positive', 'negative')),
    intent_type VARCHAR,
    bot_response TEXT,
    user_message TEXT,
    response_time_ms INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),

    -- Prevent duplicate feedback on same message
    UNIQUE(chat_log_id, feedback_type)
);

-- Indexes for analytics queries
CREATE INDEX IF NOT EXISTS idx_feedback_type
ON message_feedback(feedback_type, created_at);

CREATE INDEX IF NOT EXISTS idx_feedback_session
ON message_feedback(session_id);

CREATE INDEX IF NOT EXISTS idx_feedback_intent
ON message_feedback(intent_type, feedback_type);

-- ============================================================================
-- Part 3: Add analytics views for quick insights
-- ============================================================================

-- View for feedback statistics by intent
CREATE OR REPLACE VIEW feedback_stats_by_intent AS
SELECT
    intent_type,
    COUNT(*) as total_feedback,
    COUNT(*) FILTER (WHERE feedback_type = 'positive') as thumbs_up,
    COUNT(*) FILTER (WHERE feedback_type = 'negative') as thumbs_down,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE feedback_type = 'positive') / COUNT(*),
        1
    ) as satisfaction_rate
FROM message_feedback
WHERE intent_type IS NOT NULL
GROUP BY intent_type
ORDER BY total_feedback DESC;

-- View for unvalidated answers needing review
CREATE OR REPLACE VIEW answers_pending_validation AS
SELECT
    pa.id,
    pa.session_id,
    pa.question_text,
    pa.user_answer,
    pa.parsed_value,
    pa.confidence_score,
    pa.created_at,
    ps.condition,
    ps.trial_id,
    ct.trial_name
FROM prescreening_answers pa
LEFT JOIN prescreening_sessions ps ON pa.session_id = ps.session_id
LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
WHERE pa.human_validated = FALSE
  AND pa.created_at >= NOW() - INTERVAL '30 days'
ORDER BY pa.created_at DESC;

-- Success message
SELECT 'Data collection migration complete!' as message,
       'Ready to collect training data for ML' as status;
