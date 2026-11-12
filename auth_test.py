"""Отдельная проверка входа по номеру — без GUI и без кэша Tegr.

Запуск из корня проекта:

    python auth_test.py --phone +79991234567
    python auth_test.py --phone +79991234567 --proxy http://127.0.0.1:10808

Скрипт использует те же API_ID/API_HASH, что и плеер, но отдельную
сессию `auth_test_session.session`, поэтому не трогает рабочий вход.

Что показывает:
  * тип доставки кода (приложение / почта / SMS / звонок);
  * следующий доступный способ и таймаут;
  * можно запросить новый код;
  * 2FA-пароль;
  * итоговый аккаунт после входа.
"""
import argparse
import asyncio
import getpass
import os
import sys
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, PhoneCodeExpiredError, PhoneCodeInvalidError,
    PhoneNumberInvalidError, SessionPasswordNeededError)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tgplayer.core import config  # noqa: E402


def normalize_phone(value):
    """8XXXXXXXXXX -> +7XXXXXXXXXX, остальное — просто плюс и цифры."""
    raw = str(value or '').strip()
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ''
    if (not raw.lstrip().startswith('+') and len(digits) == 11
            and digits.startswith('8')):
        digits = '7' + digits[1:]
    return '+' + digits


def parse_proxy(value):
    """http://host:port или socks5://host:port -> словарь Telethon."""
    if not value:
        return None
    parsed = urlparse(value if '://' in value else 'http://' + value)
    kind = 'socks5' if parsed.scheme.startswith('socks') else 'http'
    if not parsed.hostname or not parsed.port:
        raise SystemExit(f'Не разобрать прокси: {value}')
    return {
        'proxy_type': kind,
        'addr': parsed.hostname,
        'port': parsed.port,
        'rdns': True,
        'username': parsed.username,
        'password': parsed.password,
    }


def describe(sent):
    def name(value):
        return type(value).__name__ if value is not None else 'нет'

    print(f"  способ:       {name(getattr(sent, 'type', None))}")
    print(f"  следующий:    {name(getattr(sent, 'next_type', None))}")
    print(f"  таймаут:      {getattr(sent, 'timeout', None)}")
    print(f"  длина кода:   {getattr(getattr(sent, 'type', None), 'length', '?')}")


async def fresh_code(client, phone):
    """Новый SendCodeRequest, а не ResendCodeRequest."""
    hashes = getattr(client, '_phone_code_hash', None)
    if hashes is not None:
        hashes.pop(phone, None)
    return await client.send_code_request(phone)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phone', help='номер, например +79991234567')
    parser.add_argument('--proxy', help='http://127.0.0.1:10808')
    parser.add_argument('--session', default='auth_test_session')
    args = parser.parse_args()

    phone = normalize_phone(args.phone or input('Номер: '))
    if len(phone) < 8:
        raise SystemExit('Номер выглядит неполным')

    proxy = parse_proxy(args.proxy)
    client = TelegramClient(args.session, config.API_ID, config.API_HASH,
                            proxy=proxy, connection_retries=1)
    await client.connect()
    try:
        print(f'Отправляю код на {phone}...')
        try:
            sent = await client.send_code_request(phone)
        except PhoneNumberInvalidError:
            print('Telegram не принял номер.')
            return 2
        except FloodWaitError as ex:
            print(f'FloodWait: подождать {ex.seconds} с.')
            return 3
        describe(sent)

        while True:
            code = input('Код (r — запросить новый): ').strip()
            if code.lower() == 'r':
                try:
                    sent = await fresh_code(client, phone)
                    print('Новый код запрошен.')
                    describe(sent)
                except Exception as ex:
                    print(f'Не удалось запросить новый код: {ex}')
                continue

            code = ''.join(ch for ch in code if ch.isdigit())
            if not code:
                print('Пустой код.')
                continue
            try:
                await client.sign_in(
                    phone=phone, code=code,
                    phone_code_hash=sent.phone_code_hash)
                break
            except SessionPasswordNeededError:
                password = getpass.getpass('Пароль 2FA: ')
                try:
                    await client.sign_in(password=password)
                    break
                except Exception as ex:
                    print(f'Пароль не принят: {ex}')
            except (PhoneCodeInvalidError, PhoneCodeExpiredError) as ex:
                print(f'Код не принят: {ex}')
            except Exception as ex:
                print(f'Вход не выполнен: {ex}')

        me = await client.get_me()
        print(f'Вход выполнен: id={getattr(me, "id", "?")}, '
              f'username={getattr(me, "username", "") or "нет"}')
    finally:
        await client.disconnect()
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
