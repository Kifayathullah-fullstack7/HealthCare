CREATE DATABASE IF NOT EXISTS symptom_triage;
USE symptom_triage;

CREATE TABLE IF NOT EXISTS reports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    uuid VARCHAR(36) UNIQUE NOT NULL,
    symptoms_text TEXT NOT NULL,
    duration VARCHAR(50) NOT NULL,
    severity INT NOT NULL,
    recommended_department VARCHAR(100) NOT NULL,
    urgency_level ENUM('low', 'medium', 'high') NOT NULL,
    ai_explanation TEXT NOT NULL,
    heart_rate INT DEFAULT NULL,
    sbar_text TEXT DEFAULT NULL,
    red_flags TEXT DEFAULT NULL,
    co_occurring TEXT DEFAULT NULL,
    assigned_facility VARCHAR(150) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
