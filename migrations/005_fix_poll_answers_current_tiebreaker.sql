-- Add id DESC tiebreaker so same-timestamp events resolve deterministically
-- (highest id = last inserted = actual final state from Telegram)
CREATE OR REPLACE VIEW quizplease.poll_answers_current AS
SELECT DISTINCT ON (poll_id, user_id)
    poll_id, user_id, username, first_name, last_name, option_ids, voted_at
FROM quizplease.poll_answers
ORDER BY poll_id, user_id, voted_at DESC, id DESC;