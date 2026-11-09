"""Мелкие помощники бэкенда без зависимости от Telegram."""

import asyncio
import os

from ..core import config

def _normalize_phone(text):
    """Приводит номер к виду, который понимает Telegram: плюс и цифры.

    Пользователь вводит номер как привык — с пробелами, скобками и
    дефисами; Telegram такие не принимает.
    """
    digits = ''.join(ch for ch in (text or '') if ch.isdigit())
    return ('+' + digits) if digits else ''

def _remove_session_files():
    """Удаляет файл сессии Telethon вместе со служебными файлами.

    Нужно, когда сессия повреждена: без этого вход невозможен, а
    предлагать пользователю удалить файл руками — плохой выход.
    """
    for suffix in ('', '.session', '.session-journal', '.session-wal',
                   '.session-shm', '-journal'):
        path = config.SESSION_NAME + suffix
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"[Вход] удалён файл сессии: {path}")
        except Exception as ex:
            print(f"[Вход] не удалось удалить {path}: {ex}")

def _is_session_error(ex):
    """Похожа ли ошибка на повреждённый файл сессии, а не на сеть."""
    text = str(ex).lower()
    return any(word in text for word in (
        'database', 'sqlite', 'malformed', 'disk i/o error',
        'file is encrypted', 'not a database',
    ))

def _error_text(ex):
    """Понятное описание ошибки подключения.

    У asyncio.TimeoutError пустой str(), и в окне появлялось
    «Не удалось подключиться: » — без единого слова о причине.
    """
    if isinstance(ex, (asyncio.TimeoutError, TimeoutError)):
        return "истекло время ожидания"
    text = str(ex).strip()
    return text or ex.__class__.__name__

def _session_file_ok():
    """Читается ли файл сессии как база SQLite.

    Telethon открывает сессию прямо в конструкторе клиента, поэтому
    повреждённый файл валит СОЗДАНИЕ клиента, а не подключение — и
    поймать это можно только здесь. Проверяем заранее: тогда файл можно
    удалить, пока его никто не держит. Иначе sqlite успевает открыть
    файл, удаление не проходит («файл занят»), и лечение не срабатывает.
    """
    path = config.SESSION_NAME + '.session'
    if not os.path.exists(path):
        return True
    try:
        import sqlite3
        con = sqlite3.connect(path)
        try:
            con.execute('select name from sqlite_master limit 1').fetchone()
        finally:
            con.close()
        return True
    except Exception as ex:
        print(f"[Вход] файл сессии нечитаем: {ex}")
        return False
