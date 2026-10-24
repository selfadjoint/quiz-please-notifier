# AGENTS.md

## Overview

AWS Lambda (Python 3.11) triggered daily via two CloudWatch cron rules. At 06:00 UTC it queries a PostgreSQL view for today's quiz game and fetches a random "Что? Где? Когда?" trivia question, then sends a combined Russian-language Telegram message to a group chat. At 12:00 UTC it sends the answer as a reply. Infrastructure is managed with Terraform.

## Architecture

- **`src/main.py`** — the entire Lambda: `lambda_handler` dispatches on `event.action` (`send_question` / `send_answer`). Morning flow: `game_today()` (DB query) + `fetch_random_question()` (db.chgk.info XML API) → `send_message()`. Noon flow: `get_pending_answer()` → `send_message()` with `reply_to_message_id`. This is the only deployed source file; everything else in `src/` is vendored dependencies.
- **`terraform/`** — zips `src/` into `lambda.zip`, deploys Lambda + IAM role + two CloudWatch rules (06:00 and 12:00 UTC). State stored in S3 (`backend.hcl`).
- **`migrations/`** — SQL migration files for DB schema changes.
- **`edit_message.py`** — local-only CLI utility to edit a sent Telegram message. Not part of the Lambda package.
- **`test.py`** — scratch/playground file, not a test suite.

## Data Flow

### Morning (06:00 UTC) — `send_question`
1. CloudWatch event (`{"action": "send_question"}`) → `lambda_handler` → `_handle_send_question()`
2. Query `quizplease.game_registration_overview` (PostgreSQL) for `game_date = today AND is_poll_created = TRUE`
3. Fetch a random ЧГК question from `https://db.chgk.info/xml/random/types1/limit1`, retrying up to 10 times if the question was already sent (checked against `quizplease.chgk_sent_questions`)
4. Extract any `(pic: filename.jpg)` image references from the question text; images resolve to `https://db.chgk.info/images/db/{filename}`. Strip the markers from the stored/displayed text.
5. Build a single message (Russian, mentioning `@applebruin`) with game time/venue or "no game today", plus the trivia question. If fetching fails, the notification is sent without the question.
6. Send via `send_question_message()`:
   - No images → `sendMessage`
   - 1 image + message ≤ 1024 chars → `sendPhoto` with caption
   - Otherwise → `sendPhoto`/`sendMediaGroup` for images, then `sendMessage` for text
7. POST to `https://api.telegram.org/bot{token}/sendMessage` (or `sendPhoto`/`sendMediaGroup`)
8. Save question + Telegram `message_id` to `quizplease.chgk_sent_questions`

### Noon (12:00 UTC) — `send_answer`
1. CloudWatch event (`{"action": "send_answer"}`) → `lambda_handler` → `_handle_send_answer()`
2. Query `quizplease.chgk_sent_questions` for today's unanswered question
3. Send the answer (+ optional comment) as a reply to the morning message using `reply_parameters`
4. Mark the question as `answer_sent = TRUE`

## Database

### Tables used
- **`quizplease.game_registration_overview`** (view) — game schedule data
- **`quizplease.chgk_sent_questions`** — tracks sent ЧГК questions to avoid repeats and store `message_id` for reply. Columns: `question_id` (PK, `TextId` from db.chgk.info), `question_text`, `answer_text`, `comment_text`, `message_id`, `sent_at`, `answer_sent`

## Key Conventions

- **Dependencies are vendored into `src/`**, not in a virtualenv. Install with:
  ```bash
  pip install --upgrade --target ./src -r src/requirements.txt
  ```
- **Ruff** for linting and formatting (`ruff.toml`): single quotes, 120 char lines, 4-space indent. Lint rules: `E, F, B, UP, SIM, I, N`.
- **Pendulum** (`pdl`) for all date/time operations — not `datetime`. Use `pdl.today()`, `.to_date_string()`, `.format('dddd', locale='ru')`.
- **`psycopg2`** imported lazily inside `get_db_connection()` with a helpful error if missing locally.
- Telegram messages are in **Russian**.

## Environment Variables

All required at runtime: `BOT_TOKEN`, `GROUP_ID`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`. Set in `terraform/terraform.tfvars` for deployment or exported in shell for local runs.

## Deployment

```bash
cd terraform
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

Terraform automatically re-zips `src/` on every apply (`archive_file` data source with `source_code_hash`).

## Local Run

```bash
export BOT_TOKEN="..." GROUP_ID="..." DB_HOST="..." DB_PORT="5432" DB_NAME="..." DB_USER="..." DB_PASSWORD="..."
python src/main.py
```

## Tools
MCP servers:
- `playwright`: browser
- `postgres-quiz`: backend DB
