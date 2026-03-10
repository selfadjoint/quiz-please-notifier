import logging
import os
from contextlib import closing

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
        with closing(get_db_connection()) as conn:
            with conn.cursor() as cursor:
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


def send_message(_bot_token, _group_id, _message):
    """
    Sends a message to a channel.
    """
    url = f'https://api.telegram.org/bot{_bot_token}/sendMessage'
    body = {'chat_id': _group_id, 'text': _message}
    response = req.post(url, json=body)

    if response.status_code == 200:
        message_data = response.json()
        logger.info(f'Message sent successfully! Message ID: {message_data["result"]["message_id"]}, '
                    f'Text: {message_data["result"]["text"]}')
        return message_data['result']
    else:
        logger.error(f'Failed to send message. Status code: {response.status_code}')
        logger.info(f'Response: {response.json()}')
        return None


def lambda_handler(event=None, context=None):
    """
    Main function.
    """
    message = f"@applebruin Доброе утро! Сегодня {pdl.today().format('dddd', locale='ru')}."
    game_info = game_today()
    if game_info:
        game_time, game_venue = game_info
        message += f' И мы играем в {game_time} в {game_venue}!'
    else:
        message += ' Квиза сегодня нет :('

    send_message(BOT_TOKEN, GROUP_ID, message)
    return
