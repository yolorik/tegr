"""settings.json: чтение, запись, умолчания и геометрия окон.

Файл лежит рядом с программой. Читается через `cfg.get(key, default)`,
поэтому старый файл без новых полей не ломает запуск: недостающие поля
берутся из `defaults()`.

Геометрия окон тоже здесь: Qt умеет складывать позицию, размер и признак
развёрнутого окна в одну строку base64, и хранить её удобнее рядом с
остальными настройками, чем отдельным файлом.
"""
import json
import os

from PyQt6.QtCore import QByteArray
from PyQt6.QtWidgets import QApplication

from . import config, paths, theme

SETTINGS_FILE = 'settings.json'


def settings_path():
    return os.path.join(paths.app_dir(), SETTINGS_FILE)


def defaults():
    """Значения по умолчанию для всех известных настроек."""
    return {
        'volume': config.DEFAULT_VOLUME,
        'mute': False,
        'hwdec': config.HWDEC_INDEX,
        'auto_quality': False,
        # Переходить к следующему ролику, когда кончился текущий.
        # Выключается кнопкой на панели управления — тогда тот же конец
        # файла просто останавливается на последнем кадре.
        'autoplay': True,
        'speed': 1.0,
        # Шаг перемотки стрелками в секундах.
        'seek_step': config.DEFAULT_SEEK_STEP,
        'main_geometry': '',
        'player_geometry': '',
        'pip_geometry': '',
        # Ширина списка каналов в главном окне (пикселей). Сохраняется,
        # потому что человек подбирает её под себя: длинные названия
        # каналов иначе обрезаются, и каждый запуск приходилось тянуть
        # разделитель заново.
        'channel_list_width': 300,
        # Ширина панели со списком роликов в окне плеера (клавиша L).
        'playlist_width': 0,
        # Последний масштаб окна PiP: [ширина, высота]. Пропорции окна от
        # этого не зависят — они всегда берутся у ролика.
        'pip_size': None,
        # Крестик главного окна: True — свернуть в трей, False — выйти.
        'close_to_tray': True,
        # Панель со списком роликов канала прямо в окне плеера.
        'playlist_panel': False,
        # Где показывать подсказки (громкость, перемотка) в полном
        # экране: над панелью управления (по умолчанию) или под ней,
        # у самого низа экрана.
        'osd_below_bar': False,
        # Режим совместимости: программное декодирование и самый простой
        # видеовыход. Нужен на старых и встроенных адаптерах, где
        # аппаратный декодер отдаёт чёрный кадр при живом звуке.
        'compat': False,
        # Тема оформления — ключ из TG_THEMES (включая свою, 'custom').
        'theme': theme.DEFAULT_THEME,
        # Два цвета своей темы: основной (фон) и акцентный.
        'custom_bg': theme.CUSTOM_BG_DEFAULT,
        'custom_accent': theme.CUSTOM_ACCENT_DEFAULT,
        # Масштаб интерфейса в процентах: 50..150, где 100 — обычный.
        'ui_scale': theme.UI_SCALE_DEFAULT,
        # Удалять файлы кэша при закрытии плеера. Если выключить, кэш
        # остаётся на диске: повторный запуск того же ролика идёт сразу,
        # но место он занимает всегда.
        'clean_cache_on_exit': True,
        # Избранные каналы (отметки id). Показываются сверху списка.
        'favorites': [],
        # Скрытые каналы: id диалога строкой. Из списка они уходят, но не
        # теряются — попадают в папку «Скрытые», откуда их можно вернуть.
        'hidden': [],
        # Способ подключения к Telegram: off / system / custom.
        'proxy_mode': config.DEFAULT_PROXY_MODE,
        # Свой прокси MTProto — хост, порт и секрет.
        'mtproxy_host': config.MT_PROXY_DEFAULT[0],
        'mtproxy_port': config.MT_PROXY_DEFAULT[1],
        'mtproxy_secret': config.MT_PROXY_DEFAULT[2],
    }


def load():
    """Читает settings.json. Отсутствие файла — норма, а не ошибка."""
    cfg = defaults()
    try:
        with open(settings_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    return cfg


def save(cfg):
    """Сохраняет настройки; сбой записи не должен ронять плеер."""
    try:
        with open(settings_path(), 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
    except Exception as ex:
        print(f"[Настройки] не сохранились: {ex}")


def restore_window_geometry(window, key, settings):
    """Возвращает окно на прежнее место и размер.

    saveGeometry кладёт в строку и размер, и позицию, и признак
    развёрнутого окна. Возвращает False, если строки нет или окно
    оказалось бы за пределами всех экранов (монитор отключили с прошлого
    запуска).
    """
    return _restore_geometry(window, key, settings, size=True)


def restore_window_position(window, key, settings):
    """Возвращает окно на прежнее место, но НЕ трогает размер.

    Нужно окну PiP: его размер всегда задаётся пропорциями ролика, и
    сохранённый размер перебил бы расчёт. Из-за этого окно навсегда
    оставалось тех пропорций, что оказались при первом запуске: включили
    на широком ролике — и вертикальный показывался в широком окне с
    чёрными полосами по бокам.
    """
    return _restore_geometry(window, key, settings, size=False)


def _restore_geometry(window, key, settings, size=True):
    """Общая часть восстановления геометрии окна.

    При size=False размер, который restoreGeometry применил вместе с
    позицией, откатывается обратно: этим окно PiP сохраняет пропорции,
    вычисленные по ролику.
    """
    data = (settings or {}).get(key) or ''
    if not data:
        return False
    keep = window.size()
    try:
        ok = bool(window.restoreGeometry(
            QByteArray.fromBase64(data.encode('ascii'))))
    except Exception:
        return False
    if not ok:
        return False
    if not size:
        window.resize(keep)
    frame = window.frameGeometry()
    for screen in QApplication.screens():
        if screen.availableGeometry().intersects(frame):
            return True
    return False


def save_window_geometry(window, key, settings):
    """Запоминает геометрию окна в настройках."""
    try:
        data = bytes(window.saveGeometry().toBase64()).decode('ascii')
    except Exception:
        return
    if settings.get(key) == data:
        return
    settings[key] = data
    save(settings)


def of_backend(backend):
    """Словарь настроек бэкенда.

    Отсутствие настроек не должно ронять окно: у бэкенда-заглушки (или
    при ошибке инициализации) их просто нет.
    """
    try:
        value = backend.settings
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}
