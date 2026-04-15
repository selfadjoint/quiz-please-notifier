CREATE TABLE IF NOT EXISTS quizplease.chgk_sent_questions (
    question_id TEXT PRIMARY KEY,
    question_text TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    comment_text TEXT,
    message_id BIGINT,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    answer_sent BOOLEAN NOT NULL DEFAULT FALSE
);

COMMENT ON TABLE quizplease.chgk_sent_questions IS 'Tracks ЧГК questions sent as daily trivia in the Telegram bot';
COMMENT ON COLUMN quizplease.chgk_sent_questions.question_id IS 'Unique question ID from db.chgk.info (TextId field, e.g. tourname.round-number)';
COMMENT ON COLUMN quizplease.chgk_sent_questions.message_id IS 'Telegram message_id of the sent question, used to reply with the answer';

