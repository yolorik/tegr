"""Плеер видео из Telegram — точка входа.

Весь код разложен по пакету `tgplayer/` (см. `tgplayer/__init__.py`:
там карта модулей — что где лежит и за что отвечает). Этот файл
остаётся фасадом:

  * готовит окружение (каталог данных, журнал, libmpv, Qt);
  * реэкспортирует имена из пакета под прежними именами — на этом
    держатся тесты и внешние точки входа;
  * используется `player_tegr.py` при запуске.

Архитектура (подробнее — в `tgplayer/__init__.py`):

  * TelegramBackend — клиент Telethon, кеш роликов и локальный HTTP-сервер,
    который отдаёт mpv уже скачанные байты (с поддержкой Range);
  * MainWindow — список папок, каналов и видео;
  * PlayerWindow — окно mpv: управление, перемотка, качество, клавиши.

Байты качаются по кускам в порядке, который подсказывает позиция mpv:
сначала начало файла, затем хвост (если индекс moov лежит в конце), а
дальше — вслед за воспроизведением.
"""
import os
import sys

# Каталог запуска назначаем ПЕРВЫМ: на нём построены дороги к сессии,
# кешу, настройкам и журналу во всех модулях пакета. В собранном .exe это
# папка с .exe, при запуске из исходника — папка этого файла.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Каталог, рядом с которым живут ДАННЫЕ (сессия, настройки, история, кеш).
#
# Важно: в собранном PyInstaller onefile-приложении __file__ указывает во
# временную папку распаковки (sys._MEIPASS), которая удаляется при выходе.
# Раньше каталог данных брался именно оттуда — то есть из папки, которой
# после закрытия программы нет. Программа каждый раз начинала будто заново,
# а настройки и сессия, положенные рядом с .exe, не подхватывались вовсе.
# В сборке ориентир — только путь к самому .exe.
if getattr(sys, 'frozen', False):
    _APP_HOME = os.path.dirname(os.path.abspath(sys.executable))
else:
    _APP_HOME = _HERE

# Имена папки данных в профиле пользователя. Текущее и прежнее: программа
# раньше называлась TGPlayer, и у тех, кто уже обновился, вход, настройки
# и история лежат в папке со старым именем. См. _profile_dir.
APP_FOLDER = 'Tegr'
OLD_PROFILE_NAME = 'TGPlayer'


def _data_home(app_home):
    """Выбирает папку для данных: рядом с программой или в профиле.

    Раньше данные всегда лежали рядом с .exe. Для переносимой папки это
    удобно, но если программу поставили в Program Files, туда обычному
    пользователю писать нельзя — и плеер падал при запуске на создании
    файла сессии:

        sqlite3.OperationalError: unable to open database file

    Теперь решает простая проба: пробуем записать файл рядом с .exe.
    Получилось — оставляем переносимый режим, как было. Не получилось —
    уходим в профиль пользователя, где писать можно всегда.

    ВАЖНО: проверяем не только «пишется ли сейчас», но и «будет ли
    писаться при обычном запуске». Установщик теперь всегда просит права
    администратора, но САМА ПРОГРАММА запускается без них — кроме одного
    случая: после обновления её запускает сам установщик, то есть всё ещё
    с правами администратора. Если бы выбор папки зависел только от прав,
    в этот момент данные ушли бы в Program Files и разошлись бы с теми,
    что уже лежат в профиле: человек увидел бы окно входа вместо своих
    каналов. Поэтому системная папка определяется по пути, а не по
    текущим правам, — тогда выбор один и тот же при любом запуске.

    Проба дешёвая (один файл), выполняется один раз при старте и не
    затрагивает сами данные.
    """
    if _needs_profile(app_home):
        return _profile_dir(app_home)

    probe = os.path.join(app_home, '.tgplayer_write_test')
    try:
        with open(probe, 'w') as f:
            f.write('ok')
        os.remove(probe)
        return app_home
    except Exception:
        pass

    return _profile_dir(app_home)


def _needs_profile(app_home):
    """True, если папка программы — системная, и данные туда класть нельзя.

    Проверяем по пути, а не по правам: права при запуске бывают разными.
    Установщик просит администратора и может записать в Program Files,
    но сама программа запускается обычным пользователем и прочитать
    оттуда свои файлы уже не сможет.
    """
    try:
        target = os.path.normcase(os.path.abspath(app_home))
    except Exception:
        return False
    for env in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432'):
        root = os.environ.get(env)
        if root and target.startswith(os.path.normcase(os.path.abspath(root))):
            return True
    # Windows и корень системного диска — тоже не место для данных.
    windir = os.environ.get('windir')
    if windir and target.startswith(os.path.normcase(os.path.abspath(windir))):
        return True
    return False


def _profile_dir(app_home):
    """Папка данных в профиле пользователя, с переносом старых файлов.

    Раньше профиль назывался TGPlayer — по прежнему имени программы.
    Теперь он Tegr, но старую папку тоже проверяем: у всех, кто уже
    обновился, вход, настройки и история лежат именно там. Найти их и
    перенести в новую — единственный способ не заставить человека
    входить заново и заново настраивать тему.

    Сначала смотрим в LOCALAPPDATA по обоим именам, потом в APPDATA:
    старые версии писали в Local, но на всякий случай проверяем и
    Roaming.
    """
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
    if not base:
        base = os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    target = os.path.join(base, APP_FOLDER)
    try:
        os.makedirs(target, exist_ok=True)
        # Сначала — данные из прежнего профиля (TGPlayer), потом то, что
        # лежит рядом с программой. Порядок важен: в профиле свежее.
        _adopt_existing(_old_profile_dir(base), target)
        # Переносим то, что уже лежит рядом с программой: настройки,
        # историю, вход. Иначе после установки в Program Files человек
        # остался бы без своего аккаунта и настроек.
        _adopt_existing(app_home, target)
        return target
    except Exception:
        # Даже профиль недоступен — остаёмся рядом с программой и
        # пытаемся работать как есть. Ошибку покажет обычный путь.
        return app_home


def _old_profile_dir(base):
    """Профиль программы под прежним именем, или пустая строка."""
    old = os.path.join(base, OLD_PROFILE_NAME)
    return old if os.path.isdir(old) else ''


def _adopt_existing(src, dst):
    """Переносит данные из папки программы в профиль при первом запуске.

    Копируем, а не переносим: если программа стоит в папке, откуда её
    потом удалят, оригиналы не нужны, но и терять их не хочется. Уже
    существующие файлы в профиле НЕ перезаписываем — там свежее.
    """
    import shutil
    names = ['my_player_session.session', 'my_player_session.session-journal',
             'settings.json', 'history.json', 'proxy.txt']
    for name in names:
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.exists(s) and not os.path.exists(d):
            try:
                shutil.copy2(s, d)
            except Exception:
                pass


_DATA_HOME = _data_home(_APP_HOME)

from tgplayer.core import paths as _paths
_paths.set_launch_dir(_DATA_HOME)
_paths.set_program_dir(_APP_HOME)
# Пакет ещё не импортирован, а config уже читает app_dir() — передаём
# каталог сразу, иначе он возьмёт каталог пакета.
_paths.set_base_dir(_DATA_HOME)

from tgplayer.core import log as _log
# Печать в журнал — до всех остальных импортов: сообщения о загрузке mpv
# печатаются раньше, и без этого в журнал они бы не попали.
_log.tee_prints()

# libmpv грузим до Qt: модуль кладёт свои DLL в путь поиска, и порядок
# загрузки библиотек на чужой машине от этого зависит.
from tgplayer.system import mpv_boot

from PyQt6.QtCore import (
    QThread, pyqtSignal, QObject, Qt, QTimer, QEvent, QPoint, QRect, QRectF,
    QSize, QByteArray, QPointF, QCoreApplication)
from PyQt6.QtGui import (
    QPainter, QPolygon, QPolygonF, QIcon, QPixmap, QColor, QPen, QShortcut,
    QKeySequence, QAction, QActionGroup, QImage, QFontMetricsF, QCursor)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QLabel, QMessageBox, QPushButton, QSlider,
    QLineEdit, QMenu, QComboBox, QSplitter, QSystemTrayIcon, QDialog,
    QGroupBox, QCheckBox, QFormLayout, QScrollArea, QFrame, QColorDialog,
    QWidgetAction, QStatusBar)

# Стандартные модули и сторонние библиотеки: импортируются здесь, а не в
# каждом модуле пакета, потому что кода в фасаде ещё много.
import asyncio
import base64
import builtins
import ctypes
import json
import math
import re
import socket
import subprocess
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock as _Lock, Thread as _Thread

from aiohttp import web

# ------------------------------------------------------------------
# Всё, что уже переехало в пакет, берём оттуда.
# ------------------------------------------------------------------
# Фасад нужен не для красоты: на этих именах держатся тесты и внешние
# точки входа (`PlayerWindow`, `TelegramBackend`, `build_style` и
# прочее). Поэтому имена не просто импортируются, а остаются
# доступными отсюда как раньше.
from tgplayer.core import config as _config
from tgplayer.core import history as _history
from tgplayer.core import paths as _paths_mod
from tgplayer.core import proxy as _proxy
from tgplayer.core import settings as _settings_mod
from tgplayer.core import theme as _theme
from tgplayer.core import utils as _utils
from tgplayer.core import version as _version
from tgplayer.system import telethon_boot as _telethon

from tgplayer.core.config import *          # noqa: F401,F403
from tgplayer.core.history import *         # noqa: F401,F403
from tgplayer.core.paths import *           # noqa: F401,F403
from tgplayer.core.theme import *           # noqa: F401,F403
from tgplayer.core.utils import *           # noqa: F401,F403


def _restore_theme_names():
    """Возвращает имена модуля темы после звёздочных импортов.

    `from module import *` приносит не только свои функции, но и чужие
    модули, которые этот модуль импортировал сам: у config в именах
    лежит `theme` (модуль), у виджетов — тоже. Такая подстановка затирает
    функцию «текущая палитра», и вызовы theme()['accent'] падают с
    «'module' object is not callable».

    Звать это нужно ПОСЛЕ всех звёздочных импортов — и снова после тех,
    что идут ниже. Имя функции нельзя взять звёздочкой: у неё то же
    название, что у модуля, и Python его пропускает при экспорте.
    """
    global theme, custom_colors, set_custom_colors, set_theme_key
    global ui_scale, set_ui_scale, scale_css, build_style
    global build_style_scaled, apply_theme_settings, derive_palette
    global _rgba, _hex_to_rgb, _rgb_to_hex, _mix, _luminance
    theme = _theme.theme
    custom_colors = _theme.custom_colors
    set_custom_colors = _theme.set_custom_colors
    set_theme_key = _theme.set_theme_key
    ui_scale = _theme.ui_scale
    set_ui_scale = _theme.set_ui_scale
    scale_css = _theme.scale_css
    build_style = _theme.build_style
    build_style_scaled = _theme.build_style_scaled
    apply_theme_settings = _theme.apply_theme_settings
    derive_palette = _theme.derive_palette
    _rgba = _theme._rgba
    _hex_to_rgb = _theme._hex_to_rgb
    _rgb_to_hex = _theme._rgb_to_hex
    _mix = _theme._mix
    _luminance = _theme._luminance


_restore_theme_names()


# Старые имена: в модуле их звали с подчёркиванием.
_log_dir = _log.log_dir
_log_path = _log.log_path
log_path = _log.log_path
_rotate_log = _log._rotate_log
_open_log = _log._open_log
_tee_prints = _log.tee_prints
_install_log_hooks = _log.install_log_hooks
log = _log.log
log_error = _log.log_error
log_ready = _log.log_ready

_app_dir = _paths_mod.app_dir
_bundle_dir = _paths_mod.bundle_dir
_load_app_icon = _paths_mod.load_app_icon

_history_path = _history.history_path
_read_history = _history._read
load_history = _history.load_watched
load_positions = _history.load_positions
save_history = _history.save

_settings_path = _settings_mod.settings_path
DEFAULT_SETTINGS = _settings_mod.defaults()
load_settings = _settings_mod.load
save_settings = _settings_mod.save
restore_window_geometry = _settings_mod.restore_window_geometry
restore_window_position = _settings_mod.restore_window_position
_restore_geometry = _settings_mod._restore_geometry
save_window_geometry = _settings_mod.save_window_geometry
backend_settings = _settings_mod.of_backend

_fmt_speed = _utils.fmt_speed
# Версия: единый источник — tgplayer/core/version.py. Здесь только имя,
# чтобы её было видно и под прежним именем VERSION.
version = _version
VERSION = _version.VERSION
# Название программы: идёт в заголовок окна и в подписи.
APP_TITLE = _version.APP_NAME

# libmpv и его ошибка загрузки: раньше жили в этом же модуле, теперь в
# tgplayer/system/mpv_boot.py. Держим под прежними именами — на них
# завязаны и точка входа, и тесты.
mpv = mpv_boot.mpv
_MPV_IMPORT_ERROR = mpv_boot.MPV_IMPORT_ERROR
_mpv_diagnosis = mpv_boot.diagnosis

# Имена Telethon: их подставляет ленивый загрузчик. Читаем через модуль
# boot, чтобы подмена (в том числе в тестах) была видна и здесь.
import sys as _sys


class _TelethonName:
    """Имя из загрузчика Telethon, читаемое в момент использования.

    Telethon подтягивается лениво, уже после импорта этого файла. Копия
    значения, снятая при импорте, так и осталась бы None, и первое же
    обращение падало бы с «'NoneType' object is not callable». Поэтому
    здесь не значение, а ссылка на модуль загрузчика: она всегда отдаёт
    то, что там лежит СЕЙЧАС.
    """

    __slots__ = ('_name',)

    def __init__(self, name):
        self._name = name

    def _value(self):
        return getattr(_telethon, self._name, None)

    def __call__(self, *args, **kwargs):
        value = self._value()
        if value is None:
            # Без загруженного Telethon классом пользоваться нельзя —
            # сообщаем об этом внятно, а не «NoneType is not callable».
            raise RuntimeError(
                'Telethon ещё не загружен: сначала вызовите '
                'telethon_boot.load()')
        return value(*args, **kwargs)

    def __getattr__(self, item):
        value = self._value()
        if value is None:
            raise AttributeError(
                'Telethon ещё не загружен (имя %r)' % self._name)
        return getattr(value, item)

    def __repr__(self):
        return '<Telethon %s: %r>' % (self._name, self._value())

    def __bool__(self):
        return self._value() is not None


TelegramClient = _TelethonName('TelegramClient')
telethon_connections = _TelethonName('telethon_connections')
telethon_utils = _TelethonName('telethon_utils')
MessageMediaDocument = _TelethonName('MessageMediaDocument')
DocumentAttributeVideo = _TelethonName('DocumentAttributeVideo')
DialogFilter = _TelethonName('DialogFilter')
Channel = _TelethonName('Channel')
Chat = _TelethonName('Chat')
GetDialogFiltersRequest = _TelethonName('GetDialogFiltersRequest')
UpdateDialogFilterRequest = _TelethonName('UpdateDialogFilterRequest')
SearchContacts = _TelethonName('SearchContacts')
JoinChannelRequest = _TelethonName('JoinChannelRequest')
LeaveChannelRequest = _TelethonName('LeaveChannelRequest')
SessionPasswordNeededError = _TelethonName('SessionPasswordNeededError')
PhoneCodeInvalidError = _TelethonName('PhoneCodeInvalidError')
PhoneCodeExpiredError = _TelethonName('PhoneCodeExpiredError')
PhoneNumberInvalidError = _TelethonName('PhoneNumberInvalidError')
PasswordHashInvalidError = _TelethonName('PasswordHashInvalidError')
FileReferenceExpiredError = _TelethonName('FileReferenceExpiredError')


def _setup_logging():
    """Открывает журнал и ставит перехват необработанных исключений.

    Печать в файл уже включена при импорте модуля (см. _tee_prints):
    сообщения о загрузке mpv печатаются раньше, чем сюда дошло бы дело.
    Здесь только шапка о запуске и перехватчик.
    """
    log('=' * 60)
    log('Запуск %s %s' % (version.APP_NAME, version.version_text()))
    log('версия: %s' % version.full_version_text())
    log('версия Python %s' % sys.version.split()[0])
    log('каталог программы: %s' % _app_dir())
    log('файл журнала: %s' % log_path())
    try:
        _install_log_hooks()
    except Exception:
        pass


# Ленивая загрузка Telethon: фасад держит под прежними именами и функции,
# и сами классы — их читает backend через этот же модуль.
preload_telethon = _telethon.preload
_load_telethon = _telethon.load


def _telethon_name(name):
    """Текущее значение имени из загрузчика Telethon.

    Копии, снятые однажды при импорте, остались бы пустыми: Telethon
    подтягивается позже, и объект-класс появляется уже после. Поэтому
    backend спрашивает значение в момент использования, а не на старте.
    """
    return getattr(_telethon, name, None)


_port_open = _utils.port_open
_proxy_mode = _proxy.mode_slug
_parse_proxy_mode = _proxy.parse_mode
_mode_label = _proxy.mode_label
_telegram_direct_ok = _proxy.telegram_direct_ok
_list_listening_ports = _proxy.list_listening_ports
_detect_proxy_protocol = _proxy.detect_proxy_protocol
_discover_local_proxies = _proxy.discover_local_proxies
_modes_from_proxy_string = _proxy.modes_from_proxy_string
_proxy_port_alive = _proxy.proxy_port_alive
_system_proxy_modes = _proxy.system_proxy_modes
_load_proxy_config = _proxy.load_proxy_config
_proxy_config_modes = _proxy.proxy_config_modes

# Виджеты и значки: панель управления, ползунки, PiP, наблюдатель за
# мышью и рисование иконок.
#
# Берём по именам, а не звёздочкой: звёздочный импорт принёс бы сюда
# служебные модули виджетов (theme, config, icons) и затёр бы функцию
# «текущая палитра».
from tgplayer.icons import icons as _icons          # noqa: F401
from tgplayer.widgets import widgets as _widgets

SeekSlider = _widgets.SeekSlider
ClickableSlider = _widgets.ClickableSlider
DownloadTracker = _widgets.DownloadTracker
AutoplaySwitch = _widgets.AutoplaySwitch
ControlBar = _widgets.ControlBar
PipBar = _widgets.PipBar
PlayerContainer = _widgets.PlayerContainer
MouseWatch = _widgets.MouseWatch

get_icon = _icons.get_icon
get_avatar_icon = _icons.get_avatar_icon
app_glyph = _icons.app_glyph
paint_connection = _icons.paint_connection
connection_pixmap = _icons.connection_pixmap
connection_icon = _icons.connection_icon
connection_colors = _icons.connection_colors
cursor_size = _icons.cursor_size
CONNECTION_OK_COLOR = _icons.CONNECTION_OK_COLOR
CONNECTION_BAD_COLOR = _icons.CONNECTION_BAD_COLOR
RING_WIDTH_SHARE = _icons.RING_WIDTH_SHARE
STATUS_BAR_HEIGHT = _icons.STATUS_BAR_HEIGHT
SCROLLBAR_THICKNESS = _icons.SCROLLBAR_THICKNESS
TRAY_ICON_SIZE = _icons.TRAY_ICON_SIZE
_ring_line = _icons._ring_line
_draw_connection_mark = _icons._draw_connection_mark

# Прежние подчёркнутые имена трекера загрузки.
_open_read = _widgets._open_read
_piece_ranges = _widgets._piece_ranges

# Бэкенд: подключение к Telegram, кеш роликов и HTTP-сервер для mpv.
from tgplayer.backend import backend as _backend_mod

TelegramBackend = _backend_mod.TelegramBackend
_normalize_phone = _backend_mod._normalize_phone
_remove_session_files = _backend_mod._remove_session_files
_is_session_error = _backend_mod._is_session_error
_error_text = _backend_mod._error_text
_session_file_ok = _backend_mod._session_file_ok

# Окно плеера и окна программы.
from tgplayer.player import player as _player_mod
from tgplayer.windows import windows as _windows_mod

PlayerWindow = _player_mod.PlayerWindow
MainWindow = _windows_mod.MainWindow
LoginDialog = _windows_mod.LoginDialog
SettingsDialog = _windows_mod.SettingsDialog
FoundDialog = _windows_mod.FoundDialog
StatusLabel = _windows_mod.StatusLabel
ThinStatusBar = _windows_mod.ThinStatusBar
ConnectionMenu = _windows_mod.ConnectionMenu
CornerConnectionButton = _windows_mod.CornerConnectionButton
WorkerThread = _windows_mod.WorkerThread
_single_instance_guard = _windows_mod._single_instance_guard
STATUS_INK_NUDGE = _windows_mod.STATUS_INK_NUDGE

# ==========================================================
# ТОЧКА ВХОДА
# ==========================================================
# ==========================================================
# ТОЧКА ВХОДА
# ==========================================================
if __name__ == '__main__':
    # В собранном .exe консоли нет — уводим диагностику в файл.
    _setup_logging()

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app = QApplication(sys.argv)
    # Тема из настроек применяется до создания окон: иначе первое окно
    # успело бы мелькнуть системными цветами.
    try:
        app.setStyleSheet(apply_theme_settings(load_settings()))
    except Exception:
        app.setStyleSheet(build_style())

    _guard = _single_instance_guard()
    if _guard is None:
        QMessageBox.information(
            None, "Уже запущено",
            "Плеер уже работает.\n\nВторой экземпляр испортил бы файл "
            "сессии Telegram и занял бы тот же локальный порт."
        )
        sys.exit(0)

    if mpv is None:
        # Подробный отчёт важнее краткой фразы: «не удалось загрузить» на
        # чужом ПК одинаково выглядит и при пропавшем файле, и при
        # нехватке зависимости, и при слишком старом процессоре.
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Не запустилось")
        box.setText("Не удалось загрузить видеодвижок (libmpv-2.dll).")
        box.setInformativeText(
            "Подробный отчёт — по кнопке ниже. Он же записан в файл "
            "tgplayer.log рядом с программой.")
        box.setDetailedText(_mpv_diagnosis())
        box.exec()
        sys.exit(1)

    backend = TelegramBackend()
    main_win = MainWindow(backend)
    # Окно показываем НЕ сразу: сначала идёт вход в аккаунт.
    #
    # Раньше плеер открывался первым, и окно входа всплывало поверх него.
    # Выглядело это как два окна разом, а если человек закрывал вход, то
    # оставался пустой плеер без каналов — и чтобы попробовать снова,
    # нужно было перезапускать программу. Теперь порядок такой: вход →
    # потом окно. MainWindow сам откроется после успешного входа.
    main_win.start_login()

    # Вход отменили — программа закрывается. Внутри неё делать нечего:
    # без аккаунта нет ни каналов, ни видео. Раньше в этом случае
    # оставалось пустое окно, и вернуться к входу можно было только
    # перезапуском.
    def _login_cancelled():
        if getattr(main_win, '_shown_after_login', False):
            return
        print("[Вход] отменён — закрываю программу")
        QApplication.instance().quit()

    backend.login_cancelled.connect(_login_cancelled)

    # Telethon грузим в фоне уже после появления окна: импорт занимает
    # секунды, а окно к нему не относится. К моменту входа библиотека
    # обычно уже на месте, и ожидания не видно.
    _preload = _Thread(target=preload_telethon, daemon=True,
                       name='telethon-preload')
    _preload.start()

    thread = WorkerThread(backend)
    thread.start()

    def stop_all():
        # Флаг ставим до остановки цикла: цикл подключения может ждать
        # очередной попытки, и без флага он бы её начал уже при выходе.
        backend._closed = True
        if backend.loop and backend.loop.is_running():
            backend.loop.call_soon_threadsafe(backend.loop.stop)

    app.aboutToQuit.connect(stop_all)
    code = app.exec()
    stop_all()
    thread.wait(2000)
    sys.exit(code)
