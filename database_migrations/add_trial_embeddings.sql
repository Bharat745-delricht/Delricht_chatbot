-- Migration: Add semantic embeddings to clinical_trials table for vector similarity search
-- Date: 2026-01-05
-- Purpose: Enable semantic trial search to handle typos, synonyms, and related conditions

-- Add semantic embedding column (768 dimensions for Gemini text-embedding-004)
ALTER TABLE clinical_trials
ADD COLUMN IF NOT EXISTS semantic_embedding vector(768);

-- Create pgvector index for fast cosine similarity search
-- ivfflat is an approximate nearest neighbor algorithm that's fast for large datasets
CREATE INDEX IF NOT EXISTS idx_clinical_trials_embedding
ON clinical_trials USING ivfflat (semantic_embedding vector_cosine_ops)
WITH (lists = 100);

-- Add index on conditions for hybrid search (keyword + vector)
CREATE INDEX IF NOT EXISTS idx_clinical_trials_conditions
ON clinical_trials(conditions);

-- Verify pgvector extension is enabled
CREATE EXTENSION IF NOT EXISTS vector;
