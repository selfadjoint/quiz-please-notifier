-- Add answer_image_urls column to store image URLs for the answer/comment part of a question.
-- Stored as a JSON array of URL strings, e.g. '["https://...jpg"]'. NULL means no images.
ALTER TABLE quizplease.chgk_sent_questions
    ADD COLUMN IF NOT EXISTS answer_image_urls TEXT DEFAULT NULL;

