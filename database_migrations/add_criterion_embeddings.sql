-- Add semantic embeddings to trial_criteria for intelligent comparison
-- Uses pgvector extension for efficient vector similarity search

-- Enable pgvector extension (if not already enabled)
CREATE EXTENSION IF NOT EXISTS vector;

-- Add embedding column to trial_criteria
ALTER TABLE trial_criteria
ADD COLUMN IF NOT EXISTS semantic_embedding vector(768);

-- Add index for fast similarity search
CREATE INDEX IF NOT EXISTS idx_trial_criteria_embedding
ON trial_criteria USING ivfflat (semantic_embedding vector_cosine_ops)
WITH (lists = 100);

-- Add metadata columns
ALTER TABLE trial_criteria
ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(50) DEFAULT 'text-embedding-004';

-- Create table for user corrections (feedback loop)
CREATE TABLE IF NOT EXISTS criterion_similarity_corrections (
    id SERIAL PRIMARY KEY,

    -- The criteria being compared
    criterion_1_id INTEGER REFERENCES trial_criteria(id),
    criterion_2_id INTEGER REFERENCES trial_criteria(id),

    -- Similarity scores
    embedding_similarity DECIMAL(5,4),  -- 0.0000 to 1.0000

    -- User decision
    user_action VARCHAR(50),  -- 'merge', 'separate', 'adjust_threshold'
    correction_reason TEXT,

    -- Context
    comparison_id UUID,
    concept_type VARCHAR(100),

    -- Applied adjustments
    threshold_adjustment DECIMAL(5,4),  -- How much to adjust threshold
    applied_at TIMESTAMP,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100)
);

-- Indexes for corrections
CREATE INDEX IF NOT EXISTS idx_corrections_criterion_1 ON criterion_similarity_corrections(criterion_1_id);
CREATE INDEX IF NOT EXISTS idx_corrections_criterion_2 ON criterion_similarity_corrections(criterion_2_id);
CREATE INDEX IF NOT EXISTS idx_corrections_comparison ON criterion_similarity_corrections(comparison_id);

-- Comments
COMMENT ON COLUMN trial_criteria.semantic_embedding IS 'Gemini text-embedding-004 vector (768 dimensions) for semantic similarity matching';
COMMENT ON TABLE criterion_similarity_corrections IS 'User corrections to improve similarity matching thresholds via feedback loop';
