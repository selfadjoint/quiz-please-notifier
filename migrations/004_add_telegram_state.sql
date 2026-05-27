CREATE TABLE IF NOT EXISTS quizplease.telegram_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

COMMENT ON TABLE quizplease.telegram_state IS 'Key-value store for Telegram bot state (e.g. getUpdates offset)';