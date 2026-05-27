ALTER TABLE quizplease.poll_answers ADD COLUMN IF NOT EXISTS update_id BIGINT;

COMMENT ON COLUMN quizplease.poll_answers.update_id IS 'Telegram update_id — monotonically increasing event sequence, used to determine exact vote order within a poll';

DROP VIEW IF EXISTS quizplease.poll_answers_current;
CREATE VIEW quizplease.poll_answers_current AS
SELECT DISTINCT ON (poll_id, user_id)
    id, poll_id, user_id, username, first_name, last_name, option_ids, voted_at, update_id
FROM quizplease.poll_answers
ORDER BY poll_id, user_id, update_id DESC NULLS LAST, id DESC;