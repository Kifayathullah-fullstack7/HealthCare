-- Supabase PostgreSQL Schema for SymptomTriage AI
-- Run this in the Supabase SQL Editor (Dashboard → SQL Editor → New Query)

CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    uuid VARCHAR(36) UNIQUE NOT NULL,
    symptoms_text TEXT NOT NULL,
    duration VARCHAR(50) NOT NULL,
    severity INT NOT NULL,
    recommended_department VARCHAR(100) NOT NULL,
    urgency_level VARCHAR(10) NOT NULL CHECK (urgency_level IN ('low', 'medium', 'high')),
    ai_explanation TEXT NOT NULL,
    heart_rate INT DEFAULT NULL,
    sbar_text TEXT DEFAULT NULL,
    red_flags TEXT DEFAULT NULL,
    co_occurring TEXT DEFAULT NULL,
    assigned_facility VARCHAR(150) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
