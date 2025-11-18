-- Polyphony Database Schema
-- PostgreSQL initialization script

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Manuscripts table
CREATE TABLE IF NOT EXISTS manuscripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    author VARCHAR(255),
    content_hash VARCHAR(64) UNIQUE, -- SHA256 of content
    file_path VARCHAR(1000),
    word_count INTEGER,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    status VARCHAR(50) DEFAULT 'pending' -- pending, processing, completed, failed
);

-- Characters table
CREATE TABLE IF NOT EXISTS characters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manuscript_id UUID REFERENCES manuscripts(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    personality_traits JSONB, -- {"brave": true, "witty": true}
    voice_characteristics JSONB, -- {"avg_sentence_length": 15, "uses_contractions": true}
    dialogue_count INTEGER DEFAULT 0,
    indexed_at TIMESTAMP,
    qdrant_collection_name VARCHAR(255),
    UNIQUE(manuscript_id, name)
);

-- Character content chunks (for tracking/debugging)
CREATE TABLE IF NOT EXISTS character_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID REFERENCES characters(id) ON DELETE CASCADE,
    chunk_type VARCHAR(50), -- dialogue, action, thought, description
    content TEXT NOT NULL,
    source_location VARCHAR(500), -- chapter, page, scene
    embedding_id VARCHAR(255), -- Qdrant point ID
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scene generation history
CREATE TABLE IF NOT EXISTS scenes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    manuscript_id UUID REFERENCES manuscripts(id),
    scene_request JSONB NOT NULL, -- full request payload
    generated_content TEXT,
    characters_involved VARCHAR(255)[], -- array of character names
    generation_time_ms INTEGER,
    evaluation_scores JSONB, -- voice_consistency, coherence, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scene beats (sub-parts of scenes)
CREATE TABLE IF NOT EXISTS scene_beats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id UUID REFERENCES scenes(id) ON DELETE CASCADE,
    beat_index INTEGER NOT NULL,
    beat_description TEXT,
    characters_involved VARCHAR(255)[],
    content TEXT,
    generation_time_ms INTEGER
);

-- API usage tracking
CREATE TABLE IF NOT EXISTS api_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    endpoint VARCHAR(255),
    tokens_used INTEGER,
    cost_usd DECIMAL(10, 6),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_manuscripts_user_id ON manuscripts(user_id);
CREATE INDEX IF NOT EXISTS idx_manuscripts_status ON manuscripts(status);
CREATE INDEX IF NOT EXISTS idx_characters_manuscript_id ON characters(manuscript_id);
CREATE INDEX IF NOT EXISTS idx_scenes_user_id ON scenes(user_id);
CREATE INDEX IF NOT EXISTS idx_scenes_manuscript_id ON scenes(manuscript_id);
CREATE INDEX IF NOT EXISTS idx_scenes_created_at ON scenes(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_character_chunks_character_id ON character_chunks(character_id);
CREATE INDEX IF NOT EXISTS idx_scene_beats_scene_id ON scene_beats(scene_id);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for users table
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Sample data for testing (optional - can be removed for production)
-- Uncomment to add test user
/*
INSERT INTO users (email, hashed_password, full_name)
VALUES (
    'test@polyphony.ai',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYPvGzKjFHu', -- password: test123
    'Test User'
) ON CONFLICT (email) DO NOTHING;
*/
