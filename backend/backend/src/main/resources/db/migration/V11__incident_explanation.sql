-- Add explanation column to store the LLM-generated summary separately
ALTER TABLE incident ADD COLUMN IF NOT EXISTS explanation TEXT;
