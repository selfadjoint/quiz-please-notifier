ALTER TABLE quizplease.chgk_sent_questions
    ADD COLUMN IF NOT EXISTS question_source TEXT NOT NULL DEFAULT 'db.chgk.info';

COMMENT ON COLUMN quizplease.chgk_sent_questions.question_source IS 'Source of the question: db.chgk.info, gotquestions.online, or db_pool';

