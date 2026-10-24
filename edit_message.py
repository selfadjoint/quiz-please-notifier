"""
Local script to edit a message previously sent by the bot.

Usage:
    python edit_message.py <message_id> <new_text>

Environment variables required:
    BOT_TOKEN  - Telegram bot token
    GROUP_ID   - Telegram chat/group ID
"""

import os
import sys

import requests


def edit_message(bot_token: str, chat_id: str, message_id: int, new_text: str) -> dict | None:
    """Edits a message sent by the bot using the Telegram editMessageText API."""
    url = f'https://api.telegram.org/bot{bot_token}/editMessageText'
    body = {
        'chat_id': chat_id,
        'message_id': message_id,
        'text': new_text,
    }
    response = requests.post(url, json=body)

    if response.status_code == 200 and response.json().get('ok'):
        result = response.json()['result']
        print(f'Message {message_id} edited successfully!')
        print(f'New text: {result["text"]}')
        return result
    else:
        print(f'Failed to edit message. Status code: {response.status_code}')
        print(f'Response: {response.json()}')
        return None


def main():
    if len(sys.argv) < 3:
        print('Usage: python edit_message.py <message_id> <new_text>')
        sys.exit(1)

    bot_token = os.environ.get('BOT_TOKEN')
    group_id = os.environ.get('GROUP_ID')

    if not bot_token or not group_id:
        print('Error: BOT_TOKEN and GROUP_ID environment variables must be set.')
        sys.exit(1)

    message_id = int(sys.argv[1])
    new_text = sys.argv[2]

    edit_message(bot_token, group_id, message_id, new_text)


if __name__ == '__main__':
    main()

