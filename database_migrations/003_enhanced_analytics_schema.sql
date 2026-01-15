-- Enhanced Analytics Database Schema
-- Phase 3: Enhanced Data Capture & Storage

-- 1. Add metadata columns to existing prescreening_answers table
ALTER TABLE prescreening_answers ADD COLUMN IF NOT EXISTS auto_evaluated BOOLEAN DEFAULT FALSE;
ALTER TABLE prescreening_answers ADD COLUMN IF NOT EXISTS confidence_score FLOAT;
ALTER TABLE prescreening_answers ADD COLUMN IF NOT EXISTS evaluation_method VARCHAR(50);
ALTER TABLE prescreening_answers ADD COLUMN IF NOT EXISTS calculation_details JSONB;

-- Performance indexes for auto-evaluation tracking
CREATE INDEX IF NOT EXISTS idx_prescreening_auto_eval ON prescreening_answers(auto_evaluated);
CREATE INDEX IF NOT EXISTS idx_prescreening_eval_method ON prescreening_answers(evaluation_method);
CREATE INDEX IF NOT EXISTS idx_prescreening_confidence ON prescreening_answers(confidence_score);

-- 2. Health metrics tracking table
CREATE TABLE IF NOT EXISTS health_metrics (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(255),
    metric_type VARCHAR(50) NOT NULL, -- 'bmi', 'weight', 'height', 'age'
    calculated_value FLOAT NOT NULL,
    input_text TEXT NOT NULL,
    units VARCHAR(20), -- 'kg/m2', 'lbs', 'cm', 'years'
    calculation_method VARCHAR(50), -- 'auto_parsed', 'manual_entry', 'bmi_calculation'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Performance indexes for health metrics
CREATE INDEX IF NOT EXISTS idx_health_metrics_session ON health_metrics(session_id);
CREATE INDEX IF NOT EXISTS idx_health_metrics_type ON health_metrics(metric_type);
CREATE INDEX IF NOT EXISTS idx_health_metrics_method ON health_metrics(calculation_method);
CREATE INDEX IF NOT EXISTS idx_health_metrics_created ON health_metrics(created_at);

-- 3. Search analytics table
CREATE TABLE IF NOT EXISTS search_analytics (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    search_type VARCHAR(50), -- 'semantic', 'keyword', 'fallback'
    query_condition VARCHAR(100) NOT NULL,
    query_location VARCHAR(100),
    similarity_scores JSONB, -- {"trial_11": 0.85, "trial_12": 0.72}
    matched_trials JSONB, -- Array of trial IDs and relevance scores
    search_duration_ms INTEGER,
    results_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Performance indexes for search analytics
CREATE INDEX IF NOT EXISTS idx_search_analytics_condition ON search_analytics(query_condition);
CREATE INDEX IF NOT EXISTS idx_search_analytics_session ON search_analytics(session_id);
CREATE INDEX IF NOT EXISTS idx_search_analytics_type ON search_analytics(search_type);
CREATE INDEX IF NOT EXISTS idx_search_analytics_created ON search_analytics(created_at);

-- 4. Add foreign key constraints where applicable
-- Note: Only add if the referenced tables exist
DO $$
BEGIN
    -- Add foreign key for health_metrics to prescreening_sessions if it exists
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'prescreening_sessions') THEN
        ALTER TABLE health_metrics ADD CONSTRAINT fk_health_metrics_session 
        FOREIGN KEY (session_id) REFERENCES prescreening_sessions(session_id) ON DELETE CASCADE;
    END IF;
    
    -- Add foreign key for search_analytics to prescreening_sessions if it exists
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'prescreening_sessions') THEN
        ALTER TABLE search_analytics ADD CONSTRAINT fk_search_analytics_session 
        FOREIGN KEY (session_id) REFERENCES prescreening_sessions(session_id) ON DELETE CASCADE;
    END IF;
END $$;

-- 5. Create view for analytics dashboard queries
CREATE OR REPLACE VIEW analytics_dashboard_view AS
SELECT 
    ps.session_id,
    ps.user_id,
    ps.status as prescreening_status,
    ps.started_at,
    ps.completed_at,
    COUNT(pa.id) as total_answers,
    COUNT(CASE WHEN pa.auto_evaluated = true THEN 1 END) as auto_evaluated_answers,
    AVG(pa.confidence_score) as avg_confidence,
    COUNT(hm.id) as health_metrics_count,
    COUNT(sa.id) as search_queries_count,
    AVG(sa.search_duration_ms) as avg_search_duration
FROM prescreening_sessions ps
LEFT JOIN prescreening_answers pa ON ps.session_id = pa.session_id
LEFT JOIN health_metrics hm ON ps.session_id = hm.session_id
LEFT JOIN search_analytics sa ON ps.session_id = sa.session_id
GROUP BY ps.session_id, ps.user_id, ps.status, ps.started_at, ps.completed_at;

-- 6. Create indexes on the view for better performance
CREATE INDEX IF NOT EXISTS idx_analytics_dashboard_session ON prescreening_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_analytics_dashboard_status ON prescreening_sessions(status);
CREATE INDEX IF NOT EXISTS idx_analytics_dashboard_started ON prescreening_sessions(started_at);