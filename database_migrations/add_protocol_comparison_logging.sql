-- Protocol Comparison Logging Table
-- Stores comparison results and criteria matching decisions for debugging and improvement

CREATE TABLE IF NOT EXISTS protocol_comparison_logs (
    id SERIAL PRIMARY KEY,

    -- Comparison metadata
    comparison_id UUID NOT NULL,
    compared_trial_ids INTEGER[] NOT NULL,
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Matching results
    total_criteria_analyzed INTEGER,
    groups_created INTEGER,
    ai_calls_made INTEGER,

    -- Grouping decisions (for debugging)
    grouping_decisions JSONB,
    -- Structure: [
    --   {
    --     "group_id": 1,
    --     "concept": "age",
    --     "representative_text": "Age ≥18 years",
    --     "grouped_criteria": [
    --       {"trial_id": 74, "text": "Age ≥18 years", "similarity": 1.0},
    --       {"trial_id": 82, "text": "Age 18-70 years old", "similarity": 0.65}
    --     ]
    --   }
    -- ]

    -- Failed groupings (criteria that should have grouped but didn't)
    failed_groupings JSONB,
    -- Structure: [
    --   {
    --     "criterion_1": {"trial_id": 74, "text": "..."},
    --     "criterion_2": {"trial_id": 82, "text": "..."},
    --     "similarity_score": 0.45,
    --     "reason": "Below threshold"
    --   }
    -- ]

    -- Performance metrics
    processing_time_ms INTEGER,

    -- Results summary
    restrictiveness_scores JSONB,
    comparison_summary TEXT,

    -- User feedback (for future improvement)
    user_rating INTEGER,
    user_feedback TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_comparison_logs_trial_ids ON protocol_comparison_logs USING GIN(compared_trial_ids);
CREATE INDEX IF NOT EXISTS idx_comparison_logs_comparison_id ON protocol_comparison_logs(comparison_id);
CREATE INDEX IF NOT EXISTS idx_comparison_logs_created_at ON protocol_comparison_logs(created_at DESC);

-- Comments
COMMENT ON TABLE protocol_comparison_logs IS 'Logs all protocol comparisons for debugging and improving matching algorithms';
COMMENT ON COLUMN protocol_comparison_logs.grouping_decisions IS 'Detailed decisions about which criteria were grouped together and why';
COMMENT ON COLUMN protocol_comparison_logs.failed_groupings IS 'Criteria pairs that had low similarity but might should have been grouped (for debugging)';
