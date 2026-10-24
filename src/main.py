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
CHGK_IMAGE_BASE_URL = 'https://db.chgk.info/images/db/'
CHGK_MAX_RETRIES = 10

# gotquestions.online: ~250k questions with sequential IDs; we pick a random ID from 1..MAX_GQ_QUESTION_ID
GQ_QUESTION_URL = 'https://gotquestions.online/question/{qid}'
GQ_MAX_QUESTION_ID = 240_000
GQ_MAX_RETRIES = 5

POLL_OPTIONS = ['Иду', '+1', 'Не иду']
POLL_OPTION_GOING = 0
POLL_OPTION_PLUS_ONE = 1


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
                SELECT game_id, game_time, game_venue
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
            logger.info(f'Today we play at {game_info[1]} at {game_info[2]}')
            return game_info  # (game_id, game_time, game_venue)

        logger.info('No game today')
        return None
    except Exception:
        logger.exception('Failed to load game info')
        return None


def send_message(_bot_token, _group_id, _message, reply_to_message_id=None):
    """
    Sends a text message to a channel.
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


def send_photo(_bot_token, _group_id, photo_url, caption=None, reply_to_message_id=None):
    """Send a single photo with an optional caption."""
    url = f'https://api.telegram.org/bot{_bot_token}/sendPhoto'
    body = {'chat_id': _group_id, 'photo': photo_url}
    if caption:
        body['caption'] = caption
    if reply_to_message_id:
        body['reply_parameters'] = {'message_id': reply_to_message_id}
    response = req.post(url, json=body)
    if response.status_code == 200:
        result = response.json()['result']
        logger.info('Photo sent successfully! Message ID: %s', result['message_id'])
        return result
    else:
        logger.error('Failed to send photo. Status code: %d, response: %s', response.status_code, response.json())
        return None


def send_media_group(_bot_token, _group_id, photo_urls, reply_to_message_id=None):
    """Send multiple photos as a media group. Returns the first message result."""
    url = f'https://api.telegram.org/bot{_bot_token}/sendMediaGroup'
    media = [{'type': 'photo', 'media': u} for u in photo_urls]
    body = {'chat_id': _group_id, 'media': media}
    if reply_to_message_id:
        body['reply_parameters'] = {'message_id': reply_to_message_id}
    response = req.post(url, json=body)
    if response.status_code == 200:
        results = response.json()['result']
        logger.info('Media group sent successfully! %d photos, first message ID: %s', len(results), results[0]['message_id'])
        return results[0]
    else:
        logger.error('Failed to send media group. Status code: %d, response: %s', response.status_code, response.json())
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


def _extract_pics(text):
    """Extract (pic: filename) references from question text.

    Returns (clean_text, list_of_image_urls).
    Images are resolved against CHGK_IMAGE_BASE_URL.
    """
    if not text:
        return text, []
    pic_pattern = re.compile(r'\(pic:\s*([^)]+)\)', re.IGNORECASE)
    image_urls = [CHGK_IMAGE_BASE_URL + m.group(1).strip() for m in pic_pattern.finditer(text)]
    clean = pic_pattern.sub('', text)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean, image_urls


TELEGRAM_CAPTION_LIMIT = 1024


def send_question_message(_bot_token, _group_id, full_message, question_text, image_urls):
    """Send the question part of a message, handling inline images.

    *full_message* is the complete text (greeting + question).
    *question_text* is only the question portion (used to measure caption fit).
    *image_urls*    is a list of image URLs extracted from the question.

    Strategy:
    - No images → plain sendMessage with full_message.
    - 1 image + full_message fits in caption (≤1024 chars) → sendPhoto with caption.
    - Otherwise → send image(s) first (sendPhoto / sendMediaGroup), then sendMessage.

    Returns the result dict of the *question text* message (for message_id storage).
    """
    if not image_urls:
        return send_message(_bot_token, _group_id, full_message)

    if len(image_urls) == 1 and len(full_message) <= TELEGRAM_CAPTION_LIMIT:
        return send_photo(_bot_token, _group_id, image_urls[0], caption=full_message)

    # Send image(s) first, then text
    if len(image_urls) == 1:
        send_photo(_bot_token, _group_id, image_urls[0])
    else:
        send_media_group(_bot_token, _group_id, image_urls)

    return send_message(_bot_token, _group_id, full_message)


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
        raw_question_text = _clean_html(q_el.findtext('Question') or '')
        answer_text = _clean_html(q_el.findtext('Answer') or '')

        if not text_id or not raw_question_text or not answer_text:
            logger.warning('Incomplete question data (attempt %d)', attempt)
            continue
        raw_comment_text = _clean_html(q_el.findtext('Comments') or '') or None
        question_text, image_urls = _extract_pics(raw_question_text)
        answer_text, answer_image_urls = _extract_pics(answer_text)
        comment_text, comment_image_urls = _extract_pics(raw_comment_text or '')
        comment_text = comment_text or None
        all_answer_image_urls = answer_image_urls + comment_image_urls

        if _is_question_sent(text_id):
            logger.info('Question %s already sent, retrying (attempt %d)', text_id, attempt)
            continue

        return {
            'question_id': text_id,
            'question_text': question_text,
            'answer_text': answer_text,
            'comment_text': comment_text,
            'image_urls': image_urls,
            'answer_image_urls': all_answer_image_urls,
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

    # Locate the question object by its characteristic key sequence. The page also embeds an unrelated
    # promo question (always answered "404") before the real one, so match on the requested ID.
    marker = next(
        (
            m
            for m in re.finditer(r'\\?"question\\?":\{\\?"id\\?":(\d+),\\?"number\\?":', html)
            if m.group(1) == str(qid)
        ),
        None,
    )
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

    # razdatkaPic is the handout image shown to players — include it if present
    GQ_BASE = 'https://gotquestions.online'

    def _gq_url(path):
        path = (path or '').strip()
        if not path:
            return None
        return GQ_BASE + path if path.startswith('/') else path

    image_urls = [u for u in [_gq_url(obj.get('razdatkaPic'))] if u]
    answer_image_urls = [u for u in [_gq_url(obj.get('answerPic')), _gq_url(obj.get('commentPic'))] if u]

    return {
        'question_id': f'gq-{gq_id}',
        'question_text': question_text,
        'answer_text': answer_text,
        'comment_text': comment_text,
        'image_urls': image_urls,
        'answer_image_urls': answer_image_urls,
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
    import json as _json

    answer_image_urls = question.get('answer_image_urls') or []
    answer_image_urls_json = _json.dumps(answer_image_urls) if answer_image_urls else None

    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO quizplease.chgk_sent_questions
                    (question_id, question_text, answer_text, comment_text, message_id, question_source, answer_image_urls)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (question_id) DO UPDATE
                    SET message_id = EXCLUDED.message_id,
                        sent_at = now(),
                        answer_sent = FALSE,
                        question_source = EXCLUDED.question_source,
                        answer_image_urls = EXCLUDED.answer_image_urls
                """,
                (
                    question['question_id'],
                    question['question_text'],
                    question['answer_text'],
                    question['comment_text'],
                    message_id,
                    question.get('source', 'db.chgk.info'),
                    answer_image_urls_json,
                ),
            )
        conn.commit()
    logger.info('Saved question %s (message_id=%s, source=%s)', question['question_id'], message_id, question.get('source'))


def get_pending_answer():
    """
    Get today's unanswered question. Returns a dict with answer_text, comment_text,
    question_id, message_id, answer_image_urls or None if nothing is pending.
    """
    import json as _json

    with closing(get_db_connection()) as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT question_id, answer_text, comment_text, message_id, answer_image_urls
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

    question_id, answer_text, comment_text, message_id, answer_image_urls_json = row
    answer_image_urls = _json.loads(answer_image_urls_json) if answer_image_urls_json else []
    return {
        'question_id': question_id,
        'answer_text': answer_text,
        'comment_text': comment_text,
        'message_id': message_id,
        'answer_image_urls': answer_image_urls,
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
# Poll helpers
# ---------------------------------------------------------------------------


def _get_telegram_updates(offset=None):
    """Fetch pending poll_answer updates from Telegram."""
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/getUpdates'
    params = {'allowed_updates': ['message', 'poll_answer'], 'timeout': 0}
    if offset is not None:
        params['offset'] = offset
    try:
        response = req.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json().get('result', [])
    except Exception:
        logger.exception('Failed to fetch Telegram updates')
        return []


def _get_poll_offset():
    """Read the stored getUpdates offset from DB."""
    with closing(get_db_connection()) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT value FROM quizplease.telegram_state WHERE key = 'poll_updates_offset'")
        row = cursor.fetchone()
        return int(row[0]) if row else None


def _save_poll_offset(offset):
    """Persist the getUpdates offset."""
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO quizplease.telegram_state (key, value)
                VALUES ('poll_updates_offset', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                (str(offset),),
            )
        conn.commit()


def _get_active_poll_ids():
    """Return poll_ids for games today or later."""
    with closing(get_db_connection()) as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.poll_id
            FROM quizplease.polls p
            JOIN quizplease.game_registration_overview g ON g.game_id = p.game_id
            WHERE g.game_date >= %s
            """,
            (pdl.today().to_date_string(),),
        )
        return {row[0] for row in cursor.fetchall()}


def _save_poll_answers(answers):
    """Bulk-insert poll answer events into poll_answers."""
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            for a in answers:
                cursor.execute(
                    """
                    INSERT INTO quizplease.poll_answers
                        (poll_id, user_id, username, first_name, last_name, option_ids, update_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (a['poll_id'], a['user_id'], a.get('username'), a['first_name'], a.get('last_name'), a['option_ids'], a.get('update_id')),
                )
        conn.commit()
    logger.info('Saved %d poll answer event(s)', len(answers))


def collect_poll_results():
    """Fetch poll_answer updates from Telegram and write to poll_answers."""
    offset = _get_poll_offset()
    active_poll_ids = _get_active_poll_ids()
    logger.info('Collecting poll results; active polls: %s, offset: %s', active_poll_ids, offset)

    updates = _get_telegram_updates(offset)
    if not updates:
        logger.info('No new Telegram updates')
        return

    answers = []
    for update in updates:
        pa = update.get('poll_answer')
        if not pa or pa['poll_id'] not in active_poll_ids:
            continue
        user = pa.get('user')
        if not user:
            continue
        answers.append({
            'poll_id': pa['poll_id'],
            'user_id': user['id'],
            'username': user.get('username'),
            'first_name': user.get('first_name', ''),
            'last_name': user.get('last_name'),
            'option_ids': pa.get('option_ids', []),
            'update_id': update['update_id'],
        })

    if answers:
        _save_poll_answers(answers)

    new_offset = max(u['update_id'] for u in updates) + 1
    _save_poll_offset(new_offset)
    logger.info('Processed %d update(s), saved %d answer(s), new offset: %d', len(updates), len(answers), new_offset)


def _get_today_participants(game_id):
    """Return formatted attendee strings for game_id from current poll results."""
    with closing(get_db_connection()) as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT pac.username, pac.first_name, pac.last_name, pac.option_ids
            FROM quizplease.poll_answers_current pac
            JOIN quizplease.polls p ON p.poll_id = pac.poll_id
            WHERE p.game_id = %s
              AND (0 = ANY(pac.option_ids) OR 1 = ANY(pac.option_ids))
            ORDER BY pac.update_id NULLS LAST, pac.voted_at, pac.id ASC
            """,
            (game_id,),
        )
        rows = cursor.fetchall()

    attendees = []
    total_seats = 0
    for username, first_name, last_name, option_ids in rows:
        going = POLL_OPTION_GOING in option_ids
        plus_one = POLL_OPTION_PLUS_ONE in option_ids
        seats = (1 if going else 0) + (1 if plus_one else 0)
        total_seats += seats
        if going and plus_one:
            name = f'@{username}' if username else first_name
            label = f'{name} +1'
        elif going:
            label = f'@{username}' if username else first_name
        else:
            full_name = ' '.join(filter(None, [first_name, last_name]))
            label = f'+1 от {full_name}'
        attendees.append((label, seats))
    return attendees, total_seats


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
            game_id, game_time, game_venue = game_info
            message += f' И мы играем в {game_time} в {game_venue}!'
            try:
                participants, total_seats = _get_today_participants(game_id)
                if participants:
                    if total_seats <= 9:
                        message += f'\n\nПроголосовали: {", ".join(l for l, _ in participants)}'
                    else:
                        entries = []
                        running = 0
                        for label, seats in participants:
                            if running >= 9:
                                break
                            remaining = 9 - running
                            if seats <= remaining:
                                entries.append(label)
                                running += seats
                            elif seats == 2 and remaining >= 1:
                                entries.append(label.removesuffix(' +1'))
                                running += 1
                        message += f'\n\nУ нас {total_seats} желающих! Первые 9 проголосовавших: {", ".join(entries)}'
            except Exception:
                logger.exception('Failed to get poll participants')
        else:
            message += ' Квиза сегодня нет :('

    # Try to append a ЧГК question to the notification
    question = None
    image_urls = []
    try:
        question = fetch_random_question()
        if question:
            image_urls = question.get('image_urls') or []
            q_part = f'❓ Вопрос дня:\n\n{question["question_text"]}\n\n🕐 Ответ будет в 16:00.'
            message = f'{message}\n\n{q_part}'.strip()
        else:
            logger.warning('No ЧГК question fetched today')
    except Exception:
        logger.exception('Failed to fetch ЧГК question, sending notification only')

    if not message:
        logger.warning('Nothing to send')
        return

    result = send_question_message(BOT_TOKEN, GROUP_ID, message, question['question_text'] if question else '', image_urls)

    if question and result:
        try:
            save_question(question, result['message_id'])
        except Exception:
            logger.exception('Failed to save ЧГК question to DB')


def send_answer_message(_bot_token, _group_id, answer_msg, image_urls, reply_to_message_id):
    """Send the answer, handling optional images.

    Strategy mirrors send_question_message:
    - No images → plain sendMessage with reply.
    - 1 image + answer_msg fits in caption → sendPhoto with caption as reply.
    - Otherwise → send image(s) first, then sendMessage as reply.
    """
    if not image_urls:
        return send_message(_bot_token, _group_id, answer_msg, reply_to_message_id=reply_to_message_id)

    if len(image_urls) == 1 and len(answer_msg) <= TELEGRAM_CAPTION_LIMIT:
        return send_photo(_bot_token, _group_id, image_urls[0], caption=answer_msg, reply_to_message_id=reply_to_message_id)

    # Send images first (without reply), then text as the reply
    if len(image_urls) == 1:
        send_photo(_bot_token, _group_id, image_urls[0], reply_to_message_id=reply_to_message_id)
    else:
        send_media_group(_bot_token, _group_id, image_urls, reply_to_message_id=reply_to_message_id)

    return send_message(_bot_token, _group_id, answer_msg, reply_to_message_id=reply_to_message_id)


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

        answer_image_urls = pending.get('answer_image_urls') or []
        result = send_answer_message(BOT_TOKEN, GROUP_ID, answer_msg, answer_image_urls, reply_to_message_id=pending['message_id'])
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
    - 'collect_poll_results': fetch Telegram poll_answer updates and persist (ad-hoc)
    """
    action = (event or {}).get('action', 'send_question')

    if action == 'collect_poll_results':
        collect_poll_results()
        return

    if action in ('send_question', 'send_answer'):
        try:
            collect_poll_results()
        except Exception:
            logger.exception('Failed to collect poll results')

    if action == 'send_answer':
        _handle_send_answer()
    elif action == 'send_question_only':
        _handle_send_question(question_only=True)
    else:
        _handle_send_question()


if __name__ == '__main__':
    lambda_handler()
