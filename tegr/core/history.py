"""История просмотров: что смотрели и на каком месте остановились.

Лежит в history.json рядом с программой. Формат простой:

::

    {
      "watched":   [id документа, ...],
      "positions": {"id документа": секунды просмотра},
      "entries":   [{название, канал, позиция, дата}, ...]
    }

Позиции нужны для «продолжить с того же места»: без них закрытие плеера
означало начать двухчасовой ролик заново. Записи (entries) питают
вкладку «История»; в старых файлах их нет, и это не ошибка.
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


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_entries():
    """История просмотров: id документа -> запись о ролике.

    Запись нужна вкладке «История»: по ней видно название, канал, сколько
    просмотрено и когда. Старые файлы без этого раздела дают пустой
    словарь — это нормально, история начнёт заполняться заново.
    """
    out = {}
    raw = _read().get('entries')
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        doc_id = _as_int(item.get('doc_id'))
        if doc_id is None:
            continue
        out[doc_id] = {
            'doc_id': doc_id,
            'title': str(item.get('title') or ''),
            'channel': str(item.get('channel') or ''),
            'peer_id': _as_int(item.get('peer_id')),
            'message_id': _as_int(item.get('message_id')),
            'position': max(0.0, _as_float(item.get('position'))),
            'duration': max(0.0, _as_float(item.get('duration'))),
            'size': max(0, _as_int(item.get('size')) or 0),
            'watched_at': max(0.0, _as_float(item.get('watched_at'))),
        }
    return out


def save(watched, positions=None, entries=None):
    """Сохраняет историю просмотров, позиции и записи для вкладки.

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
    if entries:
        clean = []
        for record in entries.values():
            if not isinstance(record, dict):
                continue
            doc_id = _as_int(record.get('doc_id'))
            if doc_id is None:
                continue
            clean.append({
                'doc_id': doc_id,
                'title': str(record.get('title') or ''),
                'channel': str(record.get('channel') or ''),
                'peer_id': _as_int(record.get('peer_id')),
                'message_id': _as_int(record.get('message_id')),
                'position': round(max(0.0, _as_float(
                    record.get('position'))), 1),
                'duration': round(max(0.0, _as_float(
                    record.get('duration'))), 1),
                'size': max(0, _as_int(record.get('size')) or 0),
                'watched_at': round(max(0.0, _as_float(
                    record.get('watched_at'))), 3),
            })
        clean.sort(key=lambda r: r['watched_at'], reverse=True)
        data['entries'] = clean
    try:
        with open(history_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as ex:
        print(f"[История] не сохранилась: {ex}")
