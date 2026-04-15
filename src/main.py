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
    Fetches a random ЧГК question from db.chgk.info that has not been sent before.
    Returns a dict with question_id, question_text, answer_text, comment_text or None.
    """
    for attempt in range(1, CHGK_MAX_RETRIES + 1):
        try:
            resp = req.get(CHGK_RANDOM_URL, timeout=20)
            resp.raise_for_status()
        except Exception:
            logger.exception('Failed to fetch random question from db.chgk.info (attempt %d)', attempt)
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

    logger.error('Could not fetch a new unique question after %d attempts', CHGK_MAX_RETRIES)
    return None


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
                    (question_id, question_text, answer_text, comment_text, message_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    question['question_id'],
                    question['question_text'],
                    question['answer_text'],
                    question['comment_text'],
                    message_id,
                ),
            )
        conn.commit()
    logger.info('Saved question %s (message_id=%s)', question['question_id'], message_id)


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
            q_part = f'❓ Вопрос дня:\n\n{question["question_text"]}\n\n🕐 Ответ будет в 16:00'
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
