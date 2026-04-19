import logging
import os
import re
from contextlib import closing
from xml.etree import ElementTree

import pendulum as pdl
import requests as req

# Set up logging
logging.basicConfig(
    level=logging.INFO, format='%(asctime)s.%(msecs)03d %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Set up constants
BOT_TOKEN = os.environ['BOT_TOKEN']
GROUP_ID = os.environ['GROUP_ID']

CHGK_RANDOM_URL = 'https://db.chgk.info/xml/random/types1/limit1'
CHGK_MAX_RETRIES = 10

# gotquestions.online: ~250k questions with sequential IDs; we pick a random ID from 1..MAX_GQ_QUESTION_ID
GQ_QUESTION_URL = 'https://gotquestions.online/question/{qid}'
GQ_MAX_QUESTION_ID = 240_000
GQ_MAX_RETRIES = 5


def get_db_connection():
    try:
        import psycopg2
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            'psycopg2 is not installed. Install src/requirements.txt before running the Lambda locally.'
        ) from exc

    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', '5432'),
        database=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
    )


def game_today():
    """
    Loads today's game time and venue if we have a game today.
    """
    try:
        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT game_time, game_venue
                FROM quizplease.game_registration_overview
                WHERE game_date = %s
                  AND is_poll_created = TRUE
                ORDER BY game_time NULLS LAST, game_id
                LIMIT 1
                """,
                (pdl.today().to_date_string(),),
            )
            game_info = cursor.fetchone()

        if game_info:
            logger.info(f'Today we play at {game_info[0]} at {game_info[1]}')
            return game_info

        logger.info('No game today')
        return None
    except Exception:
        logger.exception('Failed to load game info')
        return None


def send_message(_bot_token, _group_id, _message, reply_to_message_id=None):
    """
    Sends a message to a channel.
    """
    url = f'https://api.telegram.org/bot{_bot_token}/sendMessage'
    body = {'chat_id': _group_id, 'text': _message}
    if reply_to_message_id:
        body['reply_parameters'] = {'message_id': reply_to_message_id}
    response = req.post(url, json=body)

    if response.status_code == 200:
        message_data = response.json()
        logger.info(
            f'Message sent successfully! Message ID: {message_data["result"]["message_id"]}, '
            f'Text: {message_data["result"]["text"]}'
        )
        return message_data['result']
    else:
        logger.error(f'Failed to send message. Status code: {response.status_code}')
        logger.info(f'Response: {response.json()}')
        return None


# ---------------------------------------------------------------------------
# ЧГК question helpers
# ---------------------------------------------------------------------------


def _clean_html(text):
    """Remove simple HTML tags and collapse whitespace."""
    if not text:
        return text
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def fetch_random_question():
    """
    Fetches a random ЧГК question from multiple sources, trying each in order:
    1. db.chgk.info XML API (primary)
    2. gotquestions.online HTML scraping (secondary)
    3. DB pool — a previously fetched but never-sent question (fallback)
    Returns a dict with question_id, question_text, answer_text, comment_text, source or None.
    """
    sources = [
        ('db.chgk.info', _fetch_from_dbchgk),
        ('gotquestions.online', _fetch_from_gotquestions),
        ('db_pool', _fetch_from_db_pool),
    ]
    for source_name, fetcher in sources:
        try:
            question = fetcher()
            if question:
                question['source'] = source_name
                logger.info('Fetched question %s from %s', question['question_id'], source_name)
                return question
        except Exception:
            logger.exception('Source %s failed', source_name)
    logger.error('All question sources exhausted')
    return None


def _fetch_from_dbchgk():
    """Fetch a random ЧГК question from db.chgk.info XML API (original source)."""
    import random as _rnd

    for attempt in range(1, CHGK_MAX_RETRIES + 1):
        try:
            resp = req.get(CHGK_RANDOM_URL, timeout=20)
            resp.raise_for_status()
        except Exception:
            logger.exception('Failed to fetch from db.chgk.info (attempt %d)', attempt)
            if attempt >= 3:
                return None  # give up early — site is likely down
            continue

        root = ElementTree.fromstring(resp.content)
        q_el = root.find('question')
        if q_el is None:
            logger.warning('No <question> element in API response (attempt %d)', attempt)
            continue

        text_id = (q_el.findtext('TextId') or '').strip()
        question_text = _clean_html(q_el.findtext('Question') or '')
        answer_text = _clean_html(q_el.findtext('Answer') or '')

        if not text_id or not question_text or not answer_text:
            logger.warning('Incomplete question data (attempt %d)', attempt)
            continue

        comment_text = _clean_html(q_el.findtext('Comments') or '') or None

        if _is_question_sent(text_id):
            logger.info('Question %s already sent, retrying (attempt %d)', text_id, attempt)
            continue

        return {
            'question_id': text_id,
            'question_text': question_text,
            'answer_text': answer_text,
            'comment_text': comment_text,
        }

    return None


def _fetch_from_gotquestions():
    """Fetch a random question from gotquestions.online by scraping a question page."""
    import random as _rnd

    for attempt in range(1, GQ_MAX_RETRIES + 1):
        qid = _rnd.randint(1, GQ_MAX_QUESTION_ID)
        try:
            resp = req.get(
                GQ_QUESTION_URL.format(qid=qid),
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=20,
            )
            if resp.status_code != 200:
                logger.warning('gotquestions.online returned %d for question %d (attempt %d)', resp.status_code, qid, attempt)
                continue
        except Exception:
            logger.exception('Failed to fetch from gotquestions.online (attempt %d)', attempt)
            if attempt >= 2:
                return None
            continue

        question = _parse_gotquestions_html(resp.text, qid)
        if not question:
            logger.warning('Could not parse question %d from gotquestions.online (attempt %d)', qid, attempt)
            continue

        if _is_question_sent(question['question_id']):
            logger.info('Question %s already sent, retrying (attempt %d)', question['question_id'], attempt)
            continue

        return question

    return None


def _parse_gotquestions_html(html, qid):
    """Parse question data from gotquestions.online RSC payload embedded in HTML.

    The Next.js RSC Flight payload embeds question JSON with escaped quotes:
      \\"question\\":{\\"id\\":5000,\\"number\\":23,\\"text\\":\\"...\\",\\"answer\\":\\"...\\",...}
    We normalise the escaping, extract the JSON object, and parse it.
    """
    import json as _json

    # Locate the question object by its characteristic key sequence
    marker = re.search(r'\\?"question\\?":\{\\?"id\\?":(\d+),\\?"number\\?":', html)
    if not marker:
        return None

    # Walk forward from the opening brace to find the matching close
    start = marker.start() + marker.group().index('{')
    depth, i = 0, start
    while i < len(html):
        ch = html[i]
        if ch == '\\' and i + 1 < len(html):
            i += 2  # skip escaped char
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                break
        i += 1
    raw = html[start : i + 1]

    # Normalise RSC double-escaping: \\" → "  and \\\\n → \\n  etc.
    normalised = raw.replace('\\"', '"').replace('\\\\', '\\')

    try:
        obj = _json.loads(normalised)
    except _json.JSONDecodeError:
        return None

    gq_id = str(obj.get('id', ''))
    question_text = (obj.get('text') or '').strip()
    answer_text = (obj.get('answer') or '').strip()
    comment_text = (obj.get('comment') or '').strip() or None

    if not gq_id or not question_text or not answer_text:
        return None

    return {
        'question_id': f'gq-{gq_id}',
        'question_text': question_text,
        'answer_text': answer_text,
        'comment_text': comment_text,
    }


def _fetch_from_db_pool():
    """Pick a random previously-fetched question that was never sent (DB pool fallback)."""
    with closing(get_db_connection()) as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT question_id, question_text, answer_text, comment_text
            FROM quizplease.chgk_sent_questions
            WHERE answer_sent = TRUE
              AND sent_at < %s - INTERVAL '30 days'
            ORDER BY random()
            LIMIT 1
            """,
            (pdl.today().to_date_string(),),
        )
        row = cursor.fetchone()
        if not row:
            return None

    return {
        'question_id': row[0],
        'question_text': row[1],
        'answer_text': row[2],
        'comment_text': row[3],
    }


def _is_question_sent(question_id):
    """Check if a question has already been sent."""
    with closing(get_db_connection()) as conn, conn.cursor() as cursor:
        cursor.execute('SELECT 1 FROM quizplease.chgk_sent_questions WHERE question_id = %s', (question_id,))
        return cursor.fetchone() is not None


def save_question(question, message_id):
    """Insert a sent question into the tracking table."""
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO quizplease.chgk_sent_questions
                    (question_id, question_text, answer_text, comment_text, message_id, question_source)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (question_id) DO UPDATE
                    SET message_id = EXCLUDED.message_id,
                        sent_at = now(),
                        answer_sent = FALSE,
                        question_source = EXCLUDED.question_source
                """,
                (
                    question['question_id'],
                    question['question_text'],
                    question['answer_text'],
                    question['comment_text'],
                    message_id,
                    question.get('source', 'db.chgk.info'),
                ),
            )
        conn.commit()
    logger.info('Saved question %s (message_id=%s, source=%s)', question['question_id'], message_id, question.get('source'))


def get_pending_answer():
    """
    Get today's unanswered question. Returns a dict with answer_text, comment_text,
    question_id, message_id or None if nothing is pending.
    """
    with closing(get_db_connection()) as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT question_id, answer_text, comment_text, message_id
            FROM quizplease.chgk_sent_questions
            WHERE answer_sent = FALSE
              AND sent_at::date = %s
            ORDER BY sent_at DESC
            LIMIT 1
            """,
            (pdl.today().to_date_string(),),
        )
        row = cursor.fetchone()
        if not row:
            return None

    question_id, answer_text, comment_text, message_id = row
    return {
        'question_id': question_id,
        'answer_text': answer_text,
        'comment_text': comment_text,
        'message_id': message_id,
    }


def mark_answer_sent(question_id):
    """Mark a question's answer as sent."""
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                'UPDATE quizplease.chgk_sent_questions SET answer_sent = TRUE WHERE question_id = %s', (question_id,)
            )
        conn.commit()
    logger.info('Marked answer as sent for question %s', question_id)


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def _handle_send_question(question_only=False):
    """Morning flow: game notification + ЧГК question in one message.

    When *question_only* is True the greeting / game-info part is skipped and
    only the ЧГК question is sent. Useful for ad-hoc Lambda invocations.
    """
    if question_only:
        message = ''
    else:
        message = f'@applebruin Доброе утро! Сегодня {pdl.today().format("dddd", locale="ru")}.'
        game_info = game_today()
        if game_info:
            game_time, game_venue = game_info
            message += f' И мы играем в {game_time} в {game_venue}!'
        else:
            message += ' Квиза сегодня нет :('

    # Try to append a ЧГК question to the notification
    question = None
    try:
        question = fetch_random_question()
        if question:
            q_part = f'❓ Вопрос дня:\n\n{question["question_text"]}\n\n🕐 Ответ будет в 16:00.'
            message = f'{message}\n\n{q_part}'.strip()
        else:
            logger.warning('No ЧГК question fetched today')
    except Exception:
        logger.exception('Failed to fetch ЧГК question, sending notification only')

    if not message:
        logger.warning('Nothing to send')
        return

    result = send_message(BOT_TOKEN, GROUP_ID, message)

    if question and result:
        try:
            save_question(question, result['message_id'])
        except Exception:
            logger.exception('Failed to save ЧГК question to DB')


def _handle_send_answer():
    """Noon flow: send the answer as a reply to the morning question."""
    try:
        pending = get_pending_answer()
        if not pending:
            logger.info('No pending ЧГК answer to send today')
            return

        answer_msg = f'🔑 Ответ: {pending["answer_text"]}'
        if pending['comment_text']:
            answer_msg += f'\n\n💬 Комментарий: {pending["comment_text"]}'

        result = send_message(BOT_TOKEN, GROUP_ID, answer_msg, reply_to_message_id=pending['message_id'])
        if result:
            mark_answer_sent(pending['question_id'])
    except Exception:
        logger.exception('Failed to send ЧГК answer')


def lambda_handler(event=None, context=None):
    """
    Main function. Dispatches based on event action:
    - 'send_question': morning notification + ЧГК question (06:00 UTC trigger)
    - 'send_question_only': only the ЧГК question, no greeting (ad-hoc invocation)
    - 'send_answer': sends the ЧГК answer (12:00 UTC trigger)
    """
    action = (event or {}).get('action', 'send_question')

    if action == 'send_answer':
        _handle_send_answer()
    elif action == 'send_question_only':
        _handle_send_question(question_only=True)
    else:
        _handle_send_question()


if __name__ == '__main__':
    lambda_handler()
