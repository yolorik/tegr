"""История просмотров: что смотрели и на каком месте остановились.

Лежит в history.json рядом с программой. Формат простой:

::

    {
      "watched":   [id документа, ...],
      "positions": {"id документа": секунды просмотра}
    }

Позиции нужны для «продолжить с того же места»: без них закрытие плеера
означало начать двухчасовой ролик заново.
"""
import json
import os

from . import paths

HISTORY_FILE = 'history.json'


def history_path():
    """Файл истории просмотров рядом с программой."""
    return os.path.join(paths.app_dir(), HISTORY_FILE)


def _read():
    """Содержимое history.json как словарь. Ошибки не критичны."""
    try:
        with open(history_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def load_watched():
    """Множество id документов, которые уже смотрели."""
    return set(_read().get('watched') or [])


def load_positions():
    """Позиции просмотра: id документа -> секунды.

    Мусор в файле пропускаем молча: история — удобство, а не данные, без
    которых программа не работает.
    """
    out = {}
    for key, value in (_read().get('positions') or {}).items():
        try:
            seconds = float(value)
            doc_id = int(key)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            out[doc_id] = seconds
    return out


def save(watched, positions=None):
    """Сохраняет историю просмотров и позиции.

    Сбой записи не должен ронять плеер: история — удобство, а не данные,
    без которых программа не работает.
    """
    data = {'watched': sorted(watched)}
    if positions:
        # Позиции меньше пяти секунд смысла не имеют: ролик почти не начали,
        # и «продолжить» с двух секунд только раздражает.
        data['positions'] = {
            str(k): round(float(v), 1)
            for k, v in positions.items() if v and v > 5
        }
    try:
        with open(history_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception as ex:
        print(f"[История] не сохранилась: {ex}")
