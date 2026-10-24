-- Add image_urls column to store image URLs for the question part (handout pictures).
-- Stored as a JSON array of URL strings, e.g. '["https://...jpg"]'. NULL means no images.
-- Needed so the db_pool fallback can resend recycled questions with their images.
ALTER TABLE quizplease.chgk_sent_questions
    ADD COLUMN IF NOT EXISTS image_urls TEXT DEFAULT NULL;