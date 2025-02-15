import logging
import os

import boto3
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
DYNAMODB_REG_TABLE = os.environ['DYNAMODB_REG_TABLE']

# Initialize a DynamoDB client
dynamodb = boto3.client('dynamodb')

def game_today(_table):
    """
    Loads today's game time and venue if we have a game today.
    """
    try:
        response = dynamodb.query(
            TableName=_table,
            IndexName='game_date_index',
            KeyConditionExpression='game_date = :val',
            ProjectionExpression='game_time, game_venue, is_poll_created',
            ExpressionAttributeValues={':val': {'S': pdl.today().to_date_string()}}
        )
        attrs = [[x['game_time']['S'],
                  x['game_venue']['S']]
                 for x in response['Items'] if x['is_poll_created']['N'] == '1'
                 ]
        if attrs:
            logger.info(f'Today we play at {attrs[0][0]} at {attrs[0][1]}')
            return attrs[0]
        else:
            logger.info('No game today')
            return None

    except Exception as e:
        logger.error(f'Failed to load game info: {e}')
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
        logger.info(f'Message sent successfully! Message: {message_data["result"]["text"]}')
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
    game_info = game_today(DYNAMODB_REG_TABLE)
    if game_info:
        game_time, game_venue = game_info
        message += f' И мы играем в {game_time} в {game_venue}!'
    else:
        message += ' Квиза сегодня нет :('

    send_message(BOT_TOKEN, GROUP_ID, message)
    return
