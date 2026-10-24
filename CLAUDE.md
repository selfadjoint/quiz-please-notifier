# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Lint
ruff check src/main.py
ruff format src/main.py

# Install vendored deps (required for local runs and Lambda packaging)
pip install --upgrade --target ./src -r src/requirements.txt

# Run locally (set all env vars first)
export BOT_TOKEN="..." GROUP_ID="..." DB_HOST="..." DB_PORT="5432" DB_NAME="..." DB_USER="..." DB_PASSWORD="..."
python src/main.py

# Deploy
cd terraform
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

There is no test suite. `test.py` is a scratch file; `test_gq.py` may contain ad-hoc experiments.

## Architecture

Single Lambda function (`src/main.py`, handler `main.lambda_handler`) triggered by two CloudWatch cron rules:

- **06:00 UTC** (`send_question`): queries PostgreSQL for today's game, fetches a trivia question, sends combined Telegram message
- **12:00 UTC** (`send_answer`): sends the answer as a reply to the morning message

`edit_message.py` is a local-only CLI utility — not part of the Lambda.

### Question sources (tried in order)

1. `db.chgk.info` XML API — primary; retries up to 10×, skips already-sent questions
2. `gotquestions.online` HTML scraping — parses Next.js RSC Flight payload; retries up to 5×
3. DB pool — random question from `chgk_sent_questions` sent >30 days ago (fallback)

Source is recorded in `chgk_sent_questions.question_source`.

### Database schema (`quizplease` schema)

- `game_registration_overview` — view; queried for `game_date = today AND is_poll_created = TRUE`
- `chgk_sent_questions` — tracks sent questions; PK is `question_id`; stores `message_id` for reply, `answer_sent` flag, `answer_image_urls` (JSON array), `question_source`

Migrations in `migrations/` are run manually against the DB.

### Telegram sending logic

`send_question_message` / `send_answer_message` follow the same strategy:
- No images → `sendMessage`
- 1 image + text ≤ 1024 chars → `sendPhoto` with caption
- Otherwise → `sendPhoto`/`sendMediaGroup` first, then `sendMessage`

## Key conventions

- **Date/time**: use `pendulum` (`pdl`), never `datetime`. E.g. `pdl.today()`, `.to_date_string()`, `.format('dddd', locale='ru')`.
- **psycopg2** is imported lazily inside `get_db_connection()` to give a clear error when running locally without deps installed.
- **Deps are vendored into `src/`** — `pip install --target ./src`. Do not use a virtualenv for Lambda.
- **Ruff**: single quotes, 120-char lines, 4-space indent. Rules: `E, F, B, UP, SIM, I, N`.
- Telegram messages are in **Russian**.
- `question_id` prefixed with `gq-` for gotquestions.online questions (e.g. `gq-5000`); bare `TextId` for db.chgk.info (e.g. `tour.001-1`).