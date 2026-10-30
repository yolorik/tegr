"""Пробный плеер: одно окно вместо трёх.

Отдельная тестовая программа. Повторяет поведение настоящего плеера из
``tgplayer.player.player`` — панель, которая прячется сама, полный экран,
все горячие клавиши, перемотку, скорость, звук, пропорции, скриншот,
справку — но держит всё в ОДНОМ окне.

Почему в настоящем плеере три окна
----------------------------------
При полноэкранном показе Alt+Tab показывает три окна рабочего стола:

    * окно плеера (PlayerWindow) — его не закрывают, а перекрывают
      фуллскрином, и в списке оно остаётся;
    * окно видео (fs_window) — туда переезжает контейнер с кадром;
    * окно панели (bar) — панель нарочно делают отдельным окном
      (WindowStaysOnTopHint | Tool), иначе её не видно поверх кадра.

Панель приходится выносить в окно из-за картинки. mpv рисует кадр в
окно, полученное через ``winId()`` виджета видео: этот виджет становится
отдельным нативным окном, и всё, что лежит поверх него внутри того же
окна Qt, mpv закрашивает. Обычный дочерний виджет над кадром не виден.

Как здесь одного окна хватает
-----------------------------
Кадр рисует не mpv в своё окно, а Qt: mpv отдаёт кадры наружу
(vo=image, плагин «libmpv2/qt»), их принимает QVideoWidget, и он —
обычный виджет в обычном окне. Значит, панель, подпись и кнопки ложатся
поверх кадра без отдельных окон. Полный экран — это тот же самый
QMainWindow с растянутым окном (showFullScreen меняет стиль окна
Windows, а не создаёт новое). В Alt+Tab на любом этапе — одно окно.

Если QtMultimedia нет или кадры до неё не доходят, плеер сам
переключается на вывод mpv в окно: окно всё равно одно, только
подпись и кнопка «на весь экран» поверх кадра пропадают — их слой
перекрывает mpv.

Потока видео нет: mpv играет «бесконечный» демонстрационный ролик —
анимированные песочные часы, — а его можно останавливать, перематывать,
переключать «ролики», менять скорость и звук.

Запуск из корня проекта:

    python player_test.py

Панель управления и значки — настоящие (tgplayer.widgets,
tgplayer.icons), таблица стилей — тоже (tgplayer.core.theme).

Клавиши
-------
Пробел — пауза, F — полный экран, Esc — выйти, стрелки — перемотка и
громкость, L — список роликов, T — автоспроизведение, B — пробный
режим «панель отдельным окном» (та самая раскладка из трёх окон, для
сравнения), F1 — вся справка.
"""
import sys

# libmpv нужно загрузить ДО Qt: модуль кладёт DLL в путь поиска, и от
# порядка загрузки библиотек зависит, поднимется ли mpv на чужой машине
# (ровно как в player_deepseek.py). Пути — тоже до импорта пакета: config
# и paths читают каталог данных уже при импорте.
import os  # noqa: E402
import ctypes  # noqa: E402
import ctypes.wintypes  # noqa: E402

from tgplayer.core import paths  # noqa: E402

# Запуск из исходников — корень проекта — это и данные, и ресурсы.
# В собранном виде каталог запуска рядом с .exe. Без этого mpv не
# найдёт libmpv-2.dll, а иконка — tgplayer.ico.
_LAUNCH = os.path.dirname(os.path.abspath(__file__))
paths.set_launch_dir(_LAUNCH)
paths.set_program_dir(_LAUNCH)
paths.set_base_dir(_LAUNCH)

from tgplayer.system import mpv_boot  # noqa: E402

import time  # noqa: E402

from PyQt6.QtCore import (  # noqa: E402
    QEvent, QPoint, QRect, Qt, QTimer, QUrl, pyqtSignal)
from PyQt6.QtGui import QCursor, QKeySequence, QShortcut  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox,
    QPushButton, QVBoxLayout, QWidget)

from tgplayer.core import config, theme, utils  # noqa: E402
from tgplayer.icons import icons  # noqa: E402
from tgplayer.widgets import widgets  # noqa: E402

# Кадры от mpv рисует Qt: mpv отдаёт их через установленный в его
# окружении плагин (libmpv2/qt). Если QtMultimedia нет, плеер всё равно
# запустится: панель и звук работают, а вместо кадра будет объяснение.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: E402
    from PyQt6.QtMultimediaWidgets import QVideoWidget  # noqa: E402
    QT_VIDEO_OK = bool(
        __import__('PyQt6.QtMultimedia', fromlist=['x']).QMediaPlayer)
except Exception as _qtv_err:  # noqa: BLE001
    QAudioOutput = QMediaPlayer = QVideoWidget = None
    QT_VIDEO_OK = False
    print(f'[Тест] QtMultimedia недоступен: {_qtv_err}')


def install_qt_renderer():
    """Подключает к mpv вывод кадров в QtMultimedia.

    mpv умеет отдавать кадры наружу через libmpv_render_context.
    Готовая обвязка — плагин QtMultimedia `libmpv2/qt`: он лежит рядом с
    Qt и включается одной переменной окружения MPV_RENDER_API=qt. Ставить
    её надо ДО создания MPV, иначе он прочитает окружение без неё. Куда
    кадры рисовать, плагин узнаёт от QVideoWidget — сам виджет сюда
    передавать не нужно.
    """
    os.environ['MPV_RENDER_API'] = 'qt'


# ----------------------------------------------------------------------
# Демонстрационный ролик
# ----------------------------------------------------------------------

# «Песочные часы»: прозрачная чашка, белый песок и струйка. Всё это
# плавно переворачивается туда-обратно — по картинке сразу видно, идёт
# воспроизведение или стоит на паузе.
#
# Про фильтры mpv честно предупреждает, что они медленные. Для тестового
# ролика это то, что нужно: он должен быть медленным, а не тяжёлым.
# Обратный слэш внутри выражения экранирован: запятые в аргументах
# фильтра разделяют параметры, и без этого флажок «повернуть» не прочтя.
HOURGLASS_GRAPH = (
    "color=c=black@0.0:s=640x360:r=25:d=1200,"
    "drawbox=x=220:y=40:w=200:h=280:c=0x8fb7e6@0.85:t=3,"
    "drawbox=x=200:y=26:w=240:h=14:c=0x8fb7e6@1:t=fill,"
    "drawbox=x=200:y=320:w=240:h=14:c=0x8fb7e6@1:t=fill,"
    "drawbox=x=252:y=54:w=136:h=2:c=0xd8dde3@1:t=fill,"
    "drawbox=x=252:y=54:w=22:h=252:c=0xd8dde3@1:t=fill,"
    "drawbox=x=366:y=54:w=22:h=252:c=0xd8dde3@1:t=fill,"
    "drawbox="
    "x=270+((t*45)/2.5)^2*(1-mod(t\\,5)/5):"
    "y=110+((t*45)/2.5)^2*(1-mod(t\\,5)/5):"
    "w=100:h=140:c=0xf0d38a@1:t=fill,"
    "drawbox="
    "x=318:y=54+((t*45)/2.5)^2*(8-mod(t\\,5)/5*8)+182*(1-mod(t\\,5)/5):"
    "w=4:h=6:c=0xf0d38a@1:t=fill"
)

DEMO_SOURCE = 'av://lavfi:' + HOURGLASS_GRAPH
# Длина «бесконечного» ролика в секундах и точка начала нового круга.
DEMO_LENGTH_S = 20 * 60
DEMO_LOOP_AT_S = DEMO_LENGTH_S - 2.0


# ----------------------------------------------------------------------
# Бэкенд-заглушка
# ----------------------------------------------------------------------

class DummyBackend:
    """То, что плеер берёт у Telegram-бэкенда, но без Telegram.

    Настоящий PlayerWindow обращается к бэкенду за настройками и за
    списком роликов. Здесь те же самые поля, но настройки живут только
    в памяти (settings.json программы не трогаем), а в списке — пять
    демонстрационных роликов.

    ПОЧЕМУ настройки не пишутся на диск: это тестовая программа, и
    она не должна менять то, с чем человек смотрит настоящее видео.

    Окно по is_stub понимает, что здесь нет потока из Telegram, и
    подписывает картинку на кадре как демо.
    """

    is_stub = True

    def __init__(self):
        self.active_dialog_title = 'Тестовый плеер (без потока видео)'
        self.settings = {
            'volume': config.DEFAULT_VOLUME,
            'mute': False,
            'speed': 1.0,
            'seek_step': config.DEFAULT_SEEK_STEP,
            'autoplay': True,
            'hwdec': config.HWDEC_INDEX,
            'compat': False,
            'player_geometry': '',
        }
        self.history = {}
        self.current_messages = [DemoMessage(i) for i in range(1, 6)]

    def set_setting(self, key, value):
        self.settings[key] = value

    def flush_history(self):
        pass

    def discard_download(self, doc_id):
        pass

    def pause_download(self, doc_id):
        pass


def _cursor_pos_win32():
    """Положение курсора прямо от Windows; None — если спросить нельзя."""
    try:
        point = ctypes.wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return (point.x, point.y)
    except Exception:
        return None


def backend_is_stub(backend):
    """Бэкенд — заглушка тестового плеера, а не Telegram."""
    return bool(getattr(backend, 'is_stub', False))


class _Doc:
    def __init__(self, doc_id):
        self.id = doc_id


class _Media:
    def __init__(self, doc_id):
        self.document = _Doc(doc_id)


class DemoMessage:
    """Ролик из списка: плеер читает у него .id, .text и .media.document."""

    def __init__(self, number):
        self.id = number
        self.text = f'Демо-ролик {number} — песочные часы'
        self.media = _Media(number)


# ----------------------------------------------------------------------
# Панель: подложка, кнопка списка и закрытия
# ----------------------------------------------------------------------

class TestControlBar(widgets.ControlBar):
    """Панель управления настоящего плеера плюс то, что нужно тесту.

    Настоящая панель умеет всё нужное, кроме двух вещей: в ней нет
    кнопки «назад из ролика» (в pleере её заменяет главное окно каналов)
    и нет переключателя вида «панель над видео». Обе добавляем здесь,
    ничего не трогая в самом ControlBar: так и видно, что панель —
    подлинная.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        row = self.layout().itemAt(1).layout()
        # Кнопка «Назад» — самая первая, как в телеплеерах. В плеере эту
        # роль играет окно с каналами, которого здесь нет.
        self.btn_back = QPushButton()
        self.btn_back.setText('◀')
        self.btn_back.setToolTip('Назад (Esc в полном экране)')
        self.btn_back.setFixedSize(34, 30)
        row.insertWidget(0, self.btn_back)
        # Подпись «Загрузка» в тесте не нужна: качать нечего.
        self.lbl_cache.setText('Демо без загрузки')
        self.btn_list.setToolTip('Список роликов (L)')


class PlaylistPanel(QWidget):
    """Список роликов, как в окне плеера, но без каналов.

    Настоящая панель списка живёт внутри PlayerWindow и связана с
    бэкендом Telegram. Здесь проще: тот же вид (те же objectName и
    стиль), но список задаётся прямо.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('playlistpanel')
        self.setFixedWidth(config.PLAYLIST_PANEL_WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        title = QLabel('Ролики')
        lay.addWidget(title)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)


class VideoStage(widgets.PlayerContainer):
    """Область кадра: настоящий контейнер плеера плюс карточка «потока нет».

    Кадр здесь рисует Qt (QVideoWidget), а не mpv: mpv отдаёт картинку в
    этот виджет, и обычные дочерние виджеты ложатся поверх неё без
    хитростей — как в любом окне Qt. Поэтому карточка, кнопки и подписи
    над кадром ведут себя предсказуемо, и при полном экране ничего не
    надо переносить в отдельные окна.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.qt_sink = False
        # «Поток видео здесь не воспроизводится» — на самом кадре.
        # mpv (vo=image) отдаёт картинку в QVideoWidget без звука и без
        # всякой связи с Telegram, поэтому подпись честная: это демо,
        # а не поток. Невидима для мыши: клики идут в кадр, как раньше.
        self.badge = QLabel('Поток видео не воспроизводится — идёт демо',
                            self)
        self.badge.setObjectName('demobadge')
        self.badge.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.badge.setStyleSheet(
            '#demobadge { background: rgba(10, 12, 16, 0.62);'
            ' color: #dfe6ee; border: 1px solid rgba(255, 255, 255, 0.12);'
            ' border-radius: 10px; padding: 5px 12px; font-size: 11px; }')
        self.badge.adjustSize()
        # Кнопка «на весь экран» прямо на кадре — и её нельзя
        # потерять: клик по ней не считается кликом по видео (пауза не
        # переключается), а двойной клик по кадру делает то же самое.
        self.fs_button = QPushButton('⛶  На весь экран', self)
        self.fs_button.setObjectName('fsbadge')
        self.fs_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fs_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fs_button.setStyleSheet(
            '#fsbadge { background: rgba(10, 12, 16, 0.62);'
            ' color: #e9edf3; border: 1px solid rgba(255, 255, 255, 0.16);'
            ' border-radius: 10px; padding: 6px 12px; font-size: 12px; }'
            '#fsbadge:hover { background: rgba(40, 48, 60, 0.85);'
            ' border-color: rgba(255, 255, 255, 0.3); }')
        self.fs_button.adjustSize()
        self.card = QLabel(self)
        self.card.setObjectName('nodatacard')
        self.card.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card.setWordWrap(True)
        self.card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.card.setStyleSheet(
            "#nodatacard { background: rgba(10, 12, 16, 0.78);"
            " color: #e9edf3; border: 1px solid rgba(255, 255, 255, 0.14);"
            " border-radius: 16px; padding: 22px 30px;"
            " font-size: 14px; }")
        self.card.hide()

    def show_card(self, text):
        if not self.qt_sink:
            # Кадром владеет mpv: его слой поверх любых дочерних
            # виджетов, и ничего обычного на нём не нарисуется.
            return
        self.card.setText(text)
        self.card.adjustSize()
        self._place_card()
        self.card.show()
        self.card.raise_()

    def hide_card(self):
        self.card.hide()

    def _place_card(self):
        c = self.card
        c.move((self.width() - c.width()) // 2,
               (self.height() - c.height()) // 2)
        b = self.badge
        b.move(12, 12)
        self.fs_button.move(self.width() - self.fs_button.width() - 12, 12)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_card()


# ----------------------------------------------------------------------
# Движок: mpv за одной дверью
# ----------------------------------------------------------------------

class MpvEngine:
    """mpv с тестовым роликом — то, чем окно управляет.

    Окно знает только этот класс: воспроизведение, пауза, перемотка,
    громкость, скорость. Настоящий mpv (libmpv) и заглушка из тестов
    подходят одинаково — поэтому интерфейс тестового плеера проверяется
    без экрана, а запускается он на настоящем видео.

    Ролик «бесконечный»: mpv не умеет источников без конца, поэтому
    источник — это 20 минут песочных часов. Дошло до конца — начинаем
    заново (проверка в _tick окна).
    """

    LENGTH_S = DEMO_LENGTH_S

    def __init__(self, mpv_mod, vo='image', wid=None, hwdec='no',
                 compat=False):
        self.commands = []
        self._observers = {}
        self._closed = False
        self._duration = float(self.LENGTH_S)
        self._last_pos = 0.0
        self.paused = True
        self.volume = float(config.DEFAULT_VOLUME)
        self.muted = False
        self.speed = 1.0
        self.aspect = None
        opts = dict(
            vo=vo,
            # Декодер остаётся аппаратным: кадры отдаются наружу
            # (vo=image), а не рисуются самим mpv, и hwdec-out с этим
            # несовместим.
            hwdec=hwdec,
            vd_lavc_software_fallback='yes',
            idle='yes',
            keep_open='yes',
            osd_level=0,          # OSD рисуем в окне, а не в кадре mpv
            hr_seek='yes',
            cache='yes',
            cache_pause='yes',
            cache_secs=config.MPV_CACHE_SECS,
            force_seekable='yes',
            msg_level='all=warn',
            input_default_bindings='no',
            input_vo_keyboard='no',
        )
        if wid is not None:
            # Вывод в окно (когда QtMultimedia нет): mpv рисует прямо в
            # окно нашего виджета. Окно всё равно одно — это видно по
            # тому, что полный экран растягивает то же окно, а не новое.
            opts['wid'] = int(wid.winId())
            opts['osd_level'] = 1      # тогда OSD рисует сам mpv
        if compat:
            opts['hwdec'] = 'no'
        self._mpv = mpv_mod.MPV(**opts)
        for name in ('time-pos', 'duration', 'pause', 'volume', 'mute',
                     'speed'):
            self._mpv.property_observer(name)(self._make_observer(name))

    def _make_observer(self, name):
        def observer(_name, value):
            if self._closed:
                return
            if name in ('volume', 'mute', 'speed'):
                # Панель только показывает: значения держим здесь же.
                if name == 'volume':
                    self.volume = float(value or 0)
                elif name == 'mute':
                    self.muted = bool(value)
                else:
                    self.speed = float(value or 1.0)
            self.emit(name, value)
        return observer

    def emit(self, name, value):
        """Сообщить окну о значении свойства — как это делает mpv."""
        fn = self._observers.get(name)
        if fn:
            fn(value)

    def get(self, name):
        default = {'duration': self._duration, 'time-pos': self._last_pos,
                   'pause': self.paused, 'volume': self.volume,
                   'mute': self.muted, 'speed': self.speed}.get(name)
        try:
            value = self._mpv._get_property(name)
        except Exception:
            return default
        return default if value is None else value

    def set(self, name, value):
        try:
            self._mpv[name] = value
        except Exception as ex:
            print(f'[Тест] {name} не применился: {ex}')

    def command(self, *args):
        self.commands.append(args)
        try:
            self._mpv.command(*args)
        except Exception as ex:
            if args and args[0] != 'show-text':
                print(f'[Тест] команда {args[0]} не прошла: {ex}')
        # Свойства, которые меняются командами, сообщаем сами: у mpv
        # наблюдатель придёт следующим кадром, а панель должна обновиться
        # сразу — как и с перемоткой ниже.
        if args and args[0] == 'seek':
            try:
                amount = float(args[1])
                mode = args[2] if len(args) > 2 else 'relative'
            except (IndexError, TypeError, ValueError):
                return
            if mode == 'absolute':
                pos = amount
            else:
                pos = float(self._last_pos or 0.0) + amount
            self._last_pos = max(0.0, pos)
            self.emit('time-pos', self._last_pos)

    def open(self, pause_at_start=True):
        self.commands = []
        try:
            self._mpv.play(DEMO_SOURCE)
        except Exception as ex:
            print(f'[Тест] не удалось открыть ролик: {ex}')
            return False
        self.paused = bool(pause_at_start)
        try:
            self._mpv.pause = self.paused
        except Exception:
            pass
        return True

    def terminate(self):
        self._closed = True
        try:
            self._mpv.command('stop')
        except Exception:
            pass
        try:
            self._mpv.terminate()
        except Exception:
            pass

    # --- команды окна, как у настоящего плеера ---
    def toggle_pause(self):
        self.paused = not self.paused
        self.set('pause', self.paused)

    def seek(self, seconds, mode='relative'):
        self.command('seek', float(seconds), mode)

    def set_volume(self, value):
        self.volume = max(0.0, min(100.0, float(value)))
        self.set('volume', self.volume)

    def toggle_mute(self):
        self.muted = not self.muted
        self.set('mute', self.muted)

    def set_speed(self, value):
        self.speed = value
        self.set('speed', value)


class TestPlayer(QMainWindow):
    """Плеер в одном окне.

    Отличия от настоящего PlayerWindow — только в том, как устроено
    показ: никаких fs_window и pip_window. Всё остальное поведение
    (клавиши, панель по таймеру, OSD, скрытие курсора, перемотка,
    скорость, звук, пропорции, скриншот, справка, прерывание тишины
    при возврате в окно) повторено.

    В ПОЛНОМ ЭКРАНЕ ОКНО ОСТАЁТСЯ ОДНИМ. Это достигается не фокусами
    с окнами, а тем, что ничего не переносится: вызов showFullScreen()
    на том же QMainWindow сам меняет стиль окна Windows (WS_POPUP,
    без рамки, на весь экран) — окно то же самое, и в Alt+Tab оно одно.
    """

    sig_pos = pyqtSignal(float)
    sig_dur = pyqtSignal(float)
    sig_pause = pyqtSignal(bool)
    sig_eof = pyqtSignal()
    sig_vol = pyqtSignal(float)
    sig_mute = pyqtSignal(bool)
    sig_closed = pyqtSignal()

    # Движок. В работе — MpvEngine, в тестах без экрана — заглушка.
    ENGINE = MpvEngine

    ASPECT_MODES = (
        (None, 'по размеру окна'),
        ('16:9', '16:9'),
        ('4:3', '4:3'),
        ('1.0', 'квадрат'),
        ('21:9', '21:9'),
    )

    def __init__(self, backend=None, initial_video_index=0, parent=None,
                 mpv_mod=None, engine_class=None):
        # mpv_mod и engine_class — замена движка для тестов без экрана.
        # В работе их нет: берётся настоящий mpv.
        super().__init__(parent)
        self._mpv_mod = mpv_mod if mpv_mod is not None else getattr(
            mpv_boot, 'mpv', None)
        self._engine_class = engine_class or self.ENGINE
        self.backend = backend or DummyBackend()
        self.current_video_index = initial_video_index
        self.playlist = list(self.backend.current_messages)
        # Кадр рисует QtMultimedia. Если её нет — mpv рисует в окно сам:
        # окно всё равно одно, но карточку поверх кадра и «кнопку во
        # весь экран» показать нельзя, потому что тот слой чужой.
        self.use_qt_video = bool(QT_VIDEO_OK)
        self._closed = False
        self._fullscreen = False
        self._duration = 0.0
        self._last_pos = 0.0
        self._last_pos_ui = 0.0
        self._play_ts = time.monotonic()
        # Последнее настоящее положение курсора (в экранных координатах)
        # — по нему отличаем движение мыши от событий Qt без движения.
        self._last_mouse_pos = None
        self._eof_handled = False
        self._pending_seek = None
        self._seek_step_val = int(self._setting(
            'seek_step', config.DEFAULT_SEEK_STEP))
        self._autoplay = bool(self._setting('autoplay', True))
        self._help_window = None
        self._playlist_shown = False
        # Режим «панель поверх видео отдельным окном» — только для
        # сравнения: включённый, он даёт те же три окна, что в плеере.
        self._overlay_bar_mode = False

        icon = paths.load_app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.setWindowTitle('Тестовый плеер — одно окно (без потока видео)')
        self.resize(960, 580)
        # Полный экран включает то же самое окно; в оконный режим
        # возвращаемся ровно к прежнему размеру.
        self._windowed_geometry = None

        # --- раскладка -------------------------------------------------
        central = QWidget()
        central.setObjectName('playerroot')
        central.setStyleSheet(
            '#playerroot { background: #000000; border: none; }')
        self.root_layout = QVBoxLayout(central)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        self.stage = VideoStage()
        self.video_widget = self.stage.video_widget
        self.container = self.stage
        self.bar = TestControlBar()
        self.playlist_panel = PlaylistPanel()
        self.playlist_panel.hide()
        self._fill_playlist()
        # Сигнал вешаем ПОСЛЕ заполнения: выбор строки при заполнении —
        # тоже смена строки, и первая строка стала бы «нажатой».
        self.playlist_panel.list.currentRowChanged.connect(
            self._on_playlist_click)

        self.video_row = QHBoxLayout()
        self.video_row.setContentsMargins(0, 0, 0, 0)
        self.video_row.setSpacing(4)
        self.video_row.addWidget(self.stage, 1)
        self.video_row.addWidget(self.playlist_panel)
        self.root_layout.addLayout(self.video_row, 1)
        # Панель — отдельная строка раскладки с выравниванием ПО ЦЕНТРУ.
        # Без выравнивания QVBoxLayout растягивает виджет на всю ширину,
        # и панель прижималась к левому краю; фиксированная ширина в
        # полном экране из-за этого работала только наполовину.
        self.root_layout.addWidget(self.bar, 0,
                                   Qt.AlignmentFlag.AlignHCenter)
        # Небольшой отступ у панели в полном экране: без рамки окна
        # панель у нижнего края выглядит приклеенной. Отступ задаётся
        # раскладке, а не окну — окно-то одно.
        self._root_margins = (0, 0, 0, 0)
        self.setCentralWidget(central)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # --- кадр:
        # QVideoWidget вместо виджета mpv -------------------------------
        self.video_out = None
        self.media = None
        self.audio_out = None
        self._media_error_shown = False
        # Был ли хоть один кадр по времени: по нему отличаем «ролик
        # только начался» от «картинка не доходит».
        self._frame_shown = False
        if self.use_qt_video:
            self.video_out = QVideoWidget()
            self.video_out.setObjectName('videoframe')
            self.video_out.setStyleSheet('background: black;')
            # ВАЖНО: mouseTracking на QVideoWidget выключает мышь
            # целиком. QtMultimedia ставит его окну СОБСТВЕННЫЙ
            # обработчик (WM_MOUSEMOVE он ждёт и сам), и та же маска
            # превращает остальные сообщения мыши — нажатия, отпускания,
            # колесо — в WM_NCHITTEST с ответом HTTRANSPARENT: окно
            # говорит Windows «мышь не моя», и события не доходят ни до
            # этого виджета, ни до родителя. Ни клик, ни двойной клик по
            # кадру не работали — ни в окне, ни в полном экране.
            # Курсор над кадром и так не меняют, поэтому выключение
            # ничего не теряет.
            self.video_out.setMouseTracking(False)
            self.video_out.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
            # Место настоящего виджета mpv занимает QVideoWidget: он
            # растягивается по карточке раскладки так же.
            self.stage.layout().replaceWidget(self.stage.video_widget,
                                              self.video_out)
            self.video_out.installEventFilter(self)
            self.stage.video_widget.hide()
            self.stage.qt_sink = True
            # Сам видеовыход — без звука: звук играет mpv, а второй
            # источник звука тут ни к чему.
            self.media = QMediaPlayer(self)
            self.media.setVideoOutput(self.video_out)
            self.audio_out = QAudioOutput(self)
            self.audio_out.setMuted(True)
            self.media.setAudioOutput(self.audio_out)
            self.media.errorOccurred.connect(self._on_media_error)

        # --- mpv ------------------------------------------------------
        self._compat = bool(self._setting('compat'))
        self._hwdec_idx = int(self._setting('hwdec', config.HWDEC_INDEX))
        if self.video_out is not None:
            # Кадры mpv отдаёт QtMultimedia: включаем вывод «наружу».
            # Настроек у него нет — кадр рисует Qt, по размеру
            # QVideoWidget.
            install_qt_renderer()
        else:
            # Без QtMultimedia mpv рисует сам: тогда вывод — в окно.
            os.environ.pop('MPV_RENDER_API', None)
        # Вывод кадра: в Qt (vo=image) или прямо в окно (vo-цепочка).
        self._video_mode = ('qt' if self.video_out is not None else 'mpv')
        self._make_engine()
        self.engine._observers.update({
            'time-pos': lambda v: self.sig_pos.emit(float(v or 0.0)),
            'duration': lambda v: self.sig_dur.emit(float(v or 0.0)),
            'pause': lambda v: self.sig_pause.emit(bool(v)),
            'volume': lambda v: self.sig_vol.emit(float(v or 0.0)),
            'mute': lambda v: self.sig_mute.emit(bool(v)),
        })
        # Последнее известное состояние паузы. Наблюдатели mpv приходят
        # из его собственного потока, и опрашивать mpv оттуда нельзя:
        # вызывающий поток начинает ждать сам себя и падает. Поэтому
        # состояние берём у движка, а не у медаплеера.
        self._mpv_paused = True

        self.sig_pos.connect(self.on_pos)
        self.sig_dur.connect(self.on_dur)
        self.sig_pause.connect(self.on_pause)
        self.sig_pause.connect(self._on_pause_changed)
        self.sig_vol.connect(self.on_vol)
        self.sig_mute.connect(self.on_mute)

        volume = self._setting('volume', config.DEFAULT_VOLUME)
        self.engine.set_volume(volume)
        if bool(self._setting('mute', False)):
            self.engine.toggle_mute()
        self.engine.set_speed(self._saved_speed())
        self.bar.vol_slider.blockSignals(True)
        self.bar.vol_slider.setValue(int(volume))
        self.bar.vol_slider.blockSignals(False)

        # --- панель ---------------------------------------------------
        self.bar_timer = QTimer(self)
        self.bar_timer.setSingleShot(True)
        self.bar_timer.timeout.connect(self.hide_bar_idle)
        # Таймер секретности курсора: держит стрелку за краем окна,
        # пока курсор не сдвинется. Нужен, чтобы «спрятать мышь» было
        # не 100 вызовов в секунду, а один.
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(config.MOUSE_POLL_MS)
        self._cursor_timer.timeout.connect(self._maybe_hide_cursor)

        bar = self.bar
        self._update_autoplay_button()
        bar.btn_prev.clicked.connect(self.play_prev)
        bar.btn_rew.clicked.connect(lambda: self.seek_rel(-10))
        bar.btn_play.clicked.connect(self.toggle_play_pause)
        bar.btn_ff.clicked.connect(lambda: self.seek_rel(10))
        bar.btn_next.clicked.connect(self.play_next)
        bar.btn_mute.clicked.connect(self.toggle_mute)
        bar.btn_fs.clicked.connect(self.toggle_fullscreen)
        bar.btn_pip.clicked.connect(self.toggle_fullscreen)
        bar.btn_pip.setToolTip('Полный экран (F) — то же самое')
        bar.btn_list.clicked.connect(self._toggle_playlist)
        bar.btn_back.clicked.connect(self._exit_fullscreen)
        bar.btn_auto.mousePressEvent = self._autoplay_clicked
        bar.vol_slider.valueChanged.connect(self.on_vol_slider)
        bar.seek_slider.sliderMoved.connect(self.on_seek_slider)
        self.stage.fs_button.clicked.connect(self.toggle_fullscreen)
        # Клик мимо кнопки — это клик по кадру (пауза), а не по кнопке.
        self.stage.fs_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Без картинки из Qt кнопка на кадре не появится (её накрывает
        # слой mpv), так что и создавать её нечего.
        self.stage.fs_button.setVisible(self.stage.qt_sink)

        # События мыши: двойной клик — полный экран, одиночный — пауза,
        # колесо — громкость. Ровно как в настоящем плеере.
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self.toggle_play_pause)
        self._ignore_next_release = False
        watched = [self.stage, self.video_widget]
        if self.video_out is not None:
            watched.append(self.video_out)
        watched.append(self)
        watched += self.bar.findChildren(QWidget)
        for w in watched:
            w.setMouseTracking(True)
            w.installEventFilter(self)

        self._setup_shortcuts()

        # --- карточка «потока нет» -----------------------------------
        self.stage.show_card(self._card_text())

        # Часы: их длительность mpv узнаёт только после открытия, и
        # длинный «демо-ролик» нужно зациклить вручную — см. _tick.
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(400)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

        # Смена системной темы: плеер показывает светлую панель на
        # светлой теме, тест — тоже.
        self._system_light = icons.system_uses_light_theme()
        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(2000)
        self._theme_timer.timeout.connect(self._poll_system_theme)
        self._theme_timer.start()

    # ------------------------------------------------------------------
    # Настройки
    # ------------------------------------------------------------------

    def _make_engine(self):
        """Создаёт mpv с нужным выводом кадра и связывает его с окном."""
        self.engine = self._engine_class(
            self._mpv_mod,
            vo='image' if self._video_mode == 'qt' else config.VO_CHAIN,
            # В режиме mpv кадр рисуется в окно этого виджета. Показать
            # его надо ДО winId(), иначе mpv нарисует в скрытое окно.
            wid=None if self._video_mode == 'qt' else self.stage.video_widget,
            hwdec='no' if self._compat
            else config.HWDEC_MODES[self._hwdec_idx][0],
            compat=self._compat)
        self.engine._observers.update({
            'time-pos': lambda v: self.sig_pos.emit(float(v or 0.0)),
            'duration': lambda v: self.sig_dur.emit(float(v or 0.0)),
            'pause': lambda v: self.sig_pause.emit(bool(v)),
            'volume': lambda v: self.sig_vol.emit(float(v or 0.0)),
            'mute': lambda v: self.sig_mute.emit(bool(v)),
        })

    def _settings_dict(self):
        return self.backend.settings

    def _setting(self, key, default=None):
        return self._settings_dict().get(key, default)

    def _saved_speed(self):
        try:
            value = float(self._setting('speed', 1.0))
        except (TypeError, ValueError):
            value = 1.0
        if not config.SPEED_MIN <= value <= config.SPEED_MAX:
            value = 1.0
        return value

    def _seek_step(self):
        step = self._seek_step_val
        return step if step in config.SEEK_STEPS else config.DEFAULT_SEEK_STEP

    def _on_media_error(self, error, message):
        """Qt сообщил об ошибке чтения — это нормально, не пугаем.

        QtMultimedia, получив адрес, проверяет его своим ffmpeg и пишет
        «Could not open file»: он не умеет источник mpv. Кадры при этом
        приходят — mpv рисует мимо этого проигрывателя. Поэтому ошибка
        только в журнал, а то, что кадра нет, видно по времени: если
        оно идёт, а кадр стоит, — вот тогда и объясняем.
        """
        if self._closed:
            return
        print(f'[Тест] QtMultimedia: {error} {message}')

    def _check_frame(self):
        """Часы идут, а кадр стоит — честно говорим, что кадра нет."""
        if not self.stage.qt_sink or self._closed:
            return
        if self._mpv_paused or self._duration <= 0:
            return
        if self._frame_shown:
            return
        if time.monotonic() - self._play_ts < 8.0:
            return
        if self._last_pos <= 2.0:
            return
        print('[Тест] кадр стоит, хотя mpv играет')
        # Значок mpv может идти (мгновенный кадр), а живой картинки не
        # быть: значит, вывод через QtMultimedia не поднялся. Лечится
        # пробным вариантом — mpv рисует в окно сам. Окно при этом
        # остаётся одно, просто без карточки и подписи поверх кадра.
        if self._video_mode == 'qt':
            self._switch_to_mpv_video()
            return
        self.stage.show_card(
            'Кадр не доходит до окна: mpv играет, но ни через '
            'QtMultimedia, ни напрямую в окно картинки нет.\n\n'
            'Звук, панель и управление работают; смотрите консоль.')

    # ------------------------------------------------------------------
    # Ролик: запуск, позиция, конец
    # ------------------------------------------------------------------

    def start_demo(self):
        """Открывает демонстрационный ролик и ставит его на паузу в начале.

        На паузе карточка «потока нет» видна сразу: человек запускает
        программу и понимает, что смотрит. Пробел — и часы пошли.
        """
        self._open_demo(paused=True)

    def _open_demo(self, paused=False, seek_to=None):
        self._eof_handled = False
        self._pending_seek = seek_to
        self._play_ts = time.monotonic()
        if not self.engine.open(pause_at_start=paused):
            self.stage.show_card(
                'Не удалось запустить mpv.\n\n'
                + mpv_boot.diagnosis())
            return
        if self.media is not None and self.media.source().isEmpty():
            # Биндим QtMultimedia к выводу mpv один раз: адрес #video1
            # у плагина mpv постоянный, а Qt проверяет его своим ffmpeg
            # и пишет в журнал «Could not open file» — это ожидаемо,
            # кадры идут с другой стороны. На каждый новый ролик
            # пересоздавать источник незачем: поток тот же.
            self.media.setSource(QUrl.fromLocalFile(DEMO_SOURCE))
        if self.media is not None:
            self.media.play()

    def on_pos(self, v):
        now = time.monotonic()
        self._last_pos = float(v or 0.0)
        if now - self._last_pos_ui < 0.25:
            return
        self._last_pos_ui = now
        self.bar.label_position.setText(utils.format_time(v * 1000))
        if (self.bar.seek_slider.isEnabled()
                and not self.bar.seek_slider.isSliderDown()):
            self.bar.seek_slider.blockSignals(True)
            self.bar.seek_slider.setValue(int(v * 1000))
            self.bar.seek_slider.blockSignals(False)
        # Подсказку «потока нет» прячем, как только ролик пошёл.
        # Признак паузы берём у движка, а не читаем свойство mpv: on_pos
        # вызывается из потока mpv, и обращение к mpv из его же потока —
        # мгновенное падение (проверено на деле).
        if v > 0.3:
            self._frame_shown = True
        if self.stage.card.isVisible() and not self._mpv_paused and v > 0.3:
            self.stage.hide_card()

    def on_dur(self, v):
        self._duration = float(v or 0.0)
        self.bar.label_duration.setText(utils.format_time(v * 1000))
        if v > 0:
            self.bar.seek_slider.setEnabled(True)
            self.bar.seek_slider.setRange(0, int(v * 1000))
        else:
            self.bar.seek_slider.setEnabled(False)
        # Отложенная перемотка: ставим её, как только длина известна.
        if self._pending_seek is not None:
            seek_to, self._pending_seek = self._pending_seek, None
            self.engine.seek(seek_to, 'absolute')

    def on_pause(self, p):
        self._mpv_paused = bool(p)
        self.bar.btn_play.setIcon(icons.get_icon('play' if p else 'pause'))
        # На паузе панель не прячется — человек как раз собирается
        # что-то сделать.
        if p:
            self.bar_timer.stop()
            self._show_bar()
        elif self._fullscreen:
            self.bar_timer.start(config.BAR_HIDE_MS)
        # В начале ролика на паузе видна карточка «потока нет»: кадра
        # ещё нет, и объяснить, что происходит, нечем другим. На
        # середине человек остановил смотреть — ему интересен кадр.
        if not p:
            self.stage.hide_card()
        elif self._last_pos < 0.5 and not self._fullscreen:
            self.stage.show_card(self._card_text())

    def _advance_to_next(self):
        """Конец ролика: по кругу при автоспроизведении, стоп — без него.

        Настоящих серий «следующая» здесь нет, и демо-ролик длинный:
        до конца он доходит, только если перемотать. Тогда по
        включённому автоспроизведению начинаем с начала, а по
        выключенному — стоим на последнем кадре.
        """
        if self._eof_handled:
            return
        self._eof_handled = True
        if not self._autoplay:
            self.show_osd('Конец демо-ролика. Автоспроизведение выключено')
            return
        self._loop_to_start()

    def _loop_to_start(self):
        self.engine.seek(0.0, 'absolute')
        self._eof_handled = False
        self._play_ts = time.monotonic()

    def _switch_to_mpv_video(self):
        """Картинка не пришла через Qt — отдаём кадр самому mpv.

        Окно остаётся ОДНИМ: mpv рисует в окно виджета внутри нашего
        окна, это не новое окно верхнего уровня. Цена — карточка и
        подпись поверх кадра: их слой mpv перекрывает, поэтому они
        убираются, а объяснение показывается один раз подсказкой.
        """
        if self._video_mode != 'qt':
            return
        print('[Тест] вывод переключается на mpv (vo в окно)')
        self._video_mode = 'mpv'
        self.stage.qt_sink = False
        self.stage.card.hide()
        self.stage.badge.hide()
        self.stage.fs_button.hide()
        if self.media is not None:
            self.media.stop()
            self.media.setVideoOutput(None)
            self.media.deleteLater()
            self.media = None
        self.engine.terminate()
        os.environ.pop('MPV_RENDER_API', None)
        self.stage.video_widget.show()
        # Кадр теперь — виджет mpv: наведение для него Qt больше не
        # присылает, а движение мыши — обычный MouseMove. Если следом
        # не сделать это событие наведением, Qt станет слать вместо
        # MouseMove только HoverMove, и панель по мыши перестанет
        # появляться. Держим вид паузы как при наведении.
        self.stage.video_widget.setAttribute(
            Qt.WidgetAttribute.WA_Hover, False)
        self.stage.video_widget.setMouseTracking(True)
        self._make_engine()
        self.engine.set_volume(self.bar.vol_slider.value())
        self.engine.set_speed(self._saved_speed())
        self._open_demo(paused=self._mpv_paused)
        self.show_osd('Картинка выводится напрямую через mpv', 2500)

    def _tick(self):
        """Сторож «бесконечного» ролика.

        Источника без конца у mpv нет, поэтому тестовый ролик — это
        20-минутное видео. Дошло до конца — начинаем заново. Проверяем
        по времени, а не по eof-reached: сколько бы раз ни перематывали,
        конец ролика всегда означает «с начала».
        """
        if self._closed or self._duration <= 0:
            return
        if self._last_pos >= self._duration - 2.5:
            self._loop_to_start()
        # Раз в 400 мс достаточно, чтобы заметить «часы идут, кадра нет».
        self._check_frame()

    def play_video(self, index):
        if not 0 <= index < len(self.playlist):
            return
        self.current_video_index = index
        self._sync_playlist_selection()
        # Демонстрационный ролик у всех пунктов списка один, поэтому
        # «новый ролик» — это тот же ролик с начала.
        self._open_demo(paused=False)
        self.show_osd(f'Ролик {index + 1} из {len(self.playlist)}')

    # ------------------------------------------------------------------
    # Панель: появиться, спрятаться, курсор
    # ------------------------------------------------------------------

    def _show_bar(self):
        if not self.bar.isVisible():
            self.bar.show()
        self._show_cursor()
        if not self._mpv_paused:
            self.bar_timer.start(config.BAR_HIDE_MS)

    def poke_bar(self):
        """Показать панель и продлить её жизнь — по движению мыши."""
        self._show_bar()

    def _on_pause_changed(self, paused):
        """Паузу включили — сразу показать панель.

        Это не то же самое, что on_pause: тот обновляет вид панели, а
        здесь важно ПОЯВЛЕНИЕ. Раньше панель появлялась на паузе только
        через toggle_play_pause, то есть по пробелу или кнопке. А пауза
        бывает и не от них: mpv сам ставит её, когда кончились данные
        (cache-pause), при перемотке, при старте ролика. Тогда панель
        оставалась спрятанной, и видео «висело на паузе» без кнопок.
        """
        if paused and not self._closed:
            self.bar_timer.stop()
            if self._fullscreen and not self.bar.isVisible():
                self._show_bar()

    def _hover_moved(self):
        """Мышь прошла над кадром: запомнить положение, показать панель.

        Кадр — виджет-окно внутри окна, и Qt ведёт для него наведение
        (HoverMove) отдельно от MouseMove. Запоминаем положение курсора
        ВСЕГДА, в любом режиме: по нему _mouse_moved отличает настоящее
        движение от событий, которые Qt шлёт само, без движения. А
        панель показываем только в полном экране и только при игре.
        """
        pos = _cursor_pos_win32()
        if pos is None:
            return
        if pos == self._last_mouse_pos:
            return
        self._last_mouse_pos = pos
        if self._fullscreen and not self._mpv_paused:
            self.poke_bar()

    def _mouse_moved(self, event):
        """Показать панель, если курсор действительно сдвинулся.

        Голое «есть событие MouseMove» движением не считается. Qt
        присылает такие же события само, без всякой мыши: оно
        пересчитывает, что лежит под курсором, когда над ним появился,
        исчез или сдвинулся виджет (например, панель спряталась). По
        такому событию панель показывать нельзя — она всплывала бы сама
        и больше не пряталась.

        А решает всё положение курсора в экранных координатах: у
        настоящего движения оно меняется, у синтетики — нет. Читаем его
        от Windows, а не через QCursor: позиция QCursor обновляется из
        событий Qt, а нам нужна свежая, до того как их обработали.
        """
        pos = _cursor_pos_win32()
        if pos is None:
            # Спросить Windows не вышло — верим, что это настоящее
            # движение: пусть лучше панель лишний раз появится.
            if event.spontaneous():
                self.poke_bar()
            return
        if pos == self._last_mouse_pos:
            return
        self._last_mouse_pos = pos
        self.poke_bar()

    def hide_bar_idle(self):
        """Таймер бездействия: убрать панель и курсор.

        Не прячем в двух случаях: видео на паузе (человек как раз
        собирается нажать кнопку) и курсор стоит над панелью (иначе
        кнопки исчезают ровно тогда, когда до них дотянулись).
        """
        if not self._fullscreen:
            return
        if self._mpv_paused:
            return
        if self._cursor_over_bar():
            self.bar_timer.start(config.BAR_HIDE_MS)
            return
        if self.bar.isVisible():
            self.bar.hide()
        self._hide_cursor()

    def _show_cursor(self):
        if self.cursor().shape() != Qt.CursorShape.ArrowCursor:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _hide_cursor(self):
        if self.cursor().shape() != Qt.CursorShape.BlankCursor:
            self.setCursor(Qt.CursorShape.BlankCursor)

    def _bar_rect_global(self):
        return QRect(self.bar.mapToGlobal(QPoint(0, 0)), self.bar.size())

    def _cursor_over_bar(self):
        """Курсор сейчас над панелью управления.

        Положение берём у Windows, а не у QCursor: внутренняя позиция
        QCursor обновляется по событиям и может отставать от настоящей.
        Мелочь, а без неё панель упрямо не пряталась, стоило курсору
        хоть раз пройти над ней.
        """
        try:
            if not self.bar.isVisible():
                return False
            xy = _cursor_pos_win32()
            where = QPoint(*xy) if xy is not None else QCursor.pos()
            return self._bar_rect_global().contains(where)
        except Exception:
            return False

    def _maybe_hide_cursor(self):
        """Курсор вне панели при идущем видео в полном экране — прячем.

        Настоящий плеер опрашивает положение курсора раз в 100 мс
        отдельным наблюдателем, ради того чтобы панель появлялась ДО
        первого нажатия. Здесь панель под курсор поднимать не надо (она
        внизу и ничем не накрыта), так что опрос нужен только курсору.
        """
        if not self._fullscreen or self._closed:
            return
        if self._mpv_paused or self.bar.isVisible():
            return
        try:
            pos = QCursor.pos()
            if not self.frameGeometry().contains(pos):
                return
            if self._bar_rect_global().contains(pos):
                return
        except Exception:
            return
        self._hide_cursor()

    def _on_player_activated(self):
        """Вернулись в окно по Alt+Tab — панель сразу на месте."""
        if not self._fullscreen:
            return
        self.poke_bar()

    # ------------------------------------------------------------------
    # Полный экран: то же окно, другие флаги
    # ------------------------------------------------------------------

    def is_fullscreen(self):
        return self._fullscreen

    def toggle_fullscreen(self):
        if self._fullscreen:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        if self._fullscreen:
            return
        # Запоминаем окно до растягивания: возвращаясь, ставим ровно
        # прежний размер.
        self._windowed_geometry = self.geometry()
        self._fullscreen = True
        self._overlay_bar_mode = False
        # ЗДЕСЬ ВСЯ РАЗНИЦА С ПЛЕЕРОМ. Ничего не переносим и не
        # создаём: растягиваем это же окно. Windows сама снимет рамку и
        # заголовок (стиль WS_POPUP), и окно то же самое — в Alt+Tab
        # одно.
        self.setWindowState(
            self.windowState() | Qt.WindowState.WindowFullScreen)
        self._root_margins = (24, 12, 24, 18)
        self._apply_margins()
        self._apply_fullscreen_layout(True)
        self._show_bar()
        self.bar_timer.start(config.BAR_HIDE_MS)
        self.activateWindow()
        self.setFocus()
        self._cursor_timer.start()

    def exit_fullscreen(self):
        if not self._fullscreen:
            return
        self._fullscreen = False
        self.bar_timer.stop()
        self._cursor_timer.stop()
        self._leave_overlay_bar()
        self._apply_fullscreen_layout(False)
        self._root_margins = (0, 0, 0, 0)
        self._apply_margins()
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowFullScreen)
        if self._windowed_geometry is not None:
            self.setGeometry(self._windowed_geometry)
        self.bar.show()
        self._show_cursor()
        if self._mpv_paused and self._last_pos < 0.5:
            self.stage.show_card(self._card_text())

    def _apply_fullscreen_layout(self, on):
        """Панель в полном экране: скругление и ширина под экран.

        В настоящем плеере здесь панель выдирается из раскладки и
        становится отдельным окном. Здесь она остаётся в раскладке —
        только скругляется и получает ширину поменьше, чтобы плавать
        посередине экрана, как раньше.
        """
        bar = self.bar
        # На кадре в полном экране ничего не висит: там правит панель.
        self.stage.badge.setVisible(not on and self.stage.qt_sink)
        self.stage.fs_button.setVisible(not on and self.stage.qt_sink)
        bar.set_rounded(on)
        if on:
            # Ширина — по экрану (с полями раскладки): панель читается
            # на 4K-экране и стоит ровно посередине.
            sg = (self.screen() or QApplication.primaryScreen()).geometry()
            left, _t, right, _b = self._root_margins
            bar.setFixedWidth(max(320, sg.width() - left - right - 48))
        else:
            bar.setMinimumWidth(0)
            bar.setMaximumWidth(16777215)

    def _apply_margins(self):
        # Панель в полном экране «плавает» на расстоянии от краёв:
        # отступ у раскладки, а не у отдельной панели. По бокам он
        # почти не виден (фон чёрный, как и кадр), смысл — в нижнем:
        # панель не прилипает к краю экрана.
        left, top, right, bottom = self._root_margins
        self.root_layout.setContentsMargins(left, top, right, bottom)

    def _exit_fullscreen(self):
        """Esc: справка, затем полный экран — как в настоящем плеере."""
        if self._help_window is not None:
            self._close_help()
            return
        if self._fullscreen:
            self.exit_fullscreen()

    # ------------------------------------------------------------------
    # Мышью: клик, двойной клик, колесо
    # ------------------------------------------------------------------

    def _double_click_interval(self):
        try:
            return max(200, QApplication.styleHints().mouseDoubleClickInterval())
        except Exception:
            return 400

    def _is_video_area(self, obj):
        # Панель и кнопки поверх кадра — не кадр: клик по ним не ставит
        # видео на паузу.
        if obj is self.bar or obj.parent() is self.bar:
            return False
        if obj in (self.stage.fs_button, self.stage.badge,
                   self.stage.card):
            return False
        return obj in (self.video_widget, self.video_out, self.stage, self)

    def eventFilter(self, obj, event):
        t = event.type()
        if obj is self:
            # Мышь ушла с окна (например, в чужое окно) — панель и
            # курсор убираем сразу, не дожидаясь таймера.
            if t == QEvent.Type.Leave and self._fullscreen:
                if not self._mpv_paused:
                    self.bar.hide()
                    self._hide_cursor()
            elif t == QEvent.Type.HoverMove:
                # ПРИЧИНА, по которой панель не появлялась по мыши.
                # Фильтр стоит на самом окне, и Qt присылает сюда
                # HoverMove ПЕРВЫМ — раньше, чем событие дойдёт до
                # кадра, — а слово «фильтр» ещё не значит, что этот
                # метод отработает: если обработчик вообще не вызвать
                # здесь, дальше событие до кадра может и не дойти. Поэтому
                # наведение обрабатываем прямо здесь, а не полагаемся на
                # то, что событие доберётся до виджета кадра.
                self._hover_moved()
            elif (t == QEvent.Type.Move and self._fullscreen
                    and not self._mpv_paused):
                # ВНИМАНИЕ. Сюда приходят и СИНТЕЗИРОВАННЫЕ Qt движения —
                # те, что оно шлёт само при смене того, что лежит под
                # неподвижным курсором (например, когда панель
                # спряталась). По ним панель показывать нельзя: она
                # появлялась бы сама и висела бы вечно. Настоящее
                # движение распознаём по смене положения курсора.
                self._mouse_moved(event)
            return super().eventFilter(obj, event)

        if (t == QEvent.Type.MouseMove and self._fullscreen
                and not self._mpv_paused):
            # Движения от виджетов — тоже: и настоящие, и синтетические.
            if obj is self.bar or obj.parent() is self.bar:
                # Панель лежит под курсором и только собирается
                # спрятаться. Мышь тут — не повод продлевать ей жизнь,
                # иначе панель никогда не уйдёт, пока по ней водят.
                return super().eventFilter(obj, event)
            self._mouse_moved(event)
        elif t == QEvent.Type.HoverMove:
            # Та же обработка, но для случаев, когда наведение
            # приходит к виджетам кадра (в оконном режиме так и
            # бывает): положение курсора надо обновлять всегда,
            # иначе настоящее движение потом не отличить от событий
            # без движения.
            self._hover_moved()
        elif t == QEvent.Type.MouseButtonPress:
            self.setFocus()
            if self._fullscreen and not self._mpv_paused:
                # Нажатие мыши — тоже «есть мышь», пусть даже она не
                # шевелилась: панель нужна человеку сейчас.
                self._last_mouse_pos = None
                self.poke_bar()
            if self._is_video_area(obj):
                return True
        elif t == QEvent.Type.MouseButtonDblClick:
            if self._is_video_area(obj):
                self._click_timer.stop()
                self._ignore_next_release = True
                self.toggle_fullscreen()
                return True
        elif t == QEvent.Type.MouseButtonRelease:
            if (self._is_video_area(obj)
                    and event.button() == Qt.MouseButton.LeftButton):
                if self._ignore_next_release:
                    self._ignore_next_release = False
                else:
                    self._click_timer.start(self._double_click_interval())
                return True
        elif t == QEvent.Type.Wheel:
            if self._is_video_area(obj):
                delta = event.angleDelta().y()
                if delta:
                    self._change_volume(5 if delta > 0 else -5)
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Горячие клавиши
    # ------------------------------------------------------------------

    def _setup_shortcuts(self):
        handlers = [
            ('Space', self.toggle_play_pause),
            ('Left', lambda: self.seek_rel(-self._seek_step())),
            ('Right', lambda: self.seek_rel(self._seek_step())),
            ('Up', lambda: self._change_volume(5)),
            ('Down', lambda: self._change_volume(-5)),
            ('F', self.toggle_fullscreen),
            ('Escape', self._exit_fullscreen),
            ('H', self.toggle_hwdec),
            ('M', self.toggle_mute),
            ('N', self.play_next),
            ('P', self.play_prev),
            ('[', lambda: self._change_speed(1 / config.SPEED_STEP)),
            (']', lambda: self._change_speed(config.SPEED_STEP)),
            ('\\', self._reset_speed),
            ('A', self._cycle_aspect),
            ('S', self._screenshot),
            ('L', self._toggle_playlist),
            ('T', self.toggle_autoplay),
            ('V', self.toggle_compat),
            ('B', self.toggle_overlay_bar),
            ('F1', self._show_help),
        ]
        # Окно одно — одного набора и достаточно. Второй набор на то же
        # окно Qt счёл бы неоднозначной парой, и не работал бы ни один.
        self._shortcuts = []
        for seq, fn in handlers:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(fn)
            self._shortcuts.append(sc)

    # ------------------------------------------------------------------
    # Воспроизведение
    # ------------------------------------------------------------------

    def toggle_play_pause(self):
        self.engine.toggle_pause()
        # Состояние — сразу у себя, а не ждём наблюдателя mpv: он
        # приходит своим чередом, а карточку под кадром надо убрать
        # сейчас же, иначе mpv рисует часы под ней.
        self.on_pause(self.engine.paused)
        self.show_osd('⏸ Пауза' if self.engine.paused else '▶ Воспроизведение')

    def seek_rel(self, seconds):
        before = self._mpv_get('time-pos')
        self.engine.seek(seconds, 'relative')
        if before is None:
            return
        try:
            base = float(before)
        except (TypeError, ValueError):
            return
        duration = self._duration or 0.0
        want = max(0.0, base + seconds)
        if duration > 0:
            want = min(want, duration)
        self._show_seek_osd(want, duration, seconds)

    def _show_seek_osd(self, pos, duration, seconds=0):
        arrow = '⏪' if seconds < 0 else '⏩'
        text = f'{arrow} {utils.format_time(pos * 1000)}'
        if duration > 0:
            text += f' / {utils.format_time(duration * 1000)}'
        self.show_osd(text, 1500)

    def on_seek_slider(self, ms):
        self.engine.seek(ms / 1000.0, 'absolute')

    def play_next(self):
        if self.current_video_index + 1 < len(self.playlist):
            self.play_video(self.current_video_index + 1)
        else:
            self.show_osd('Конец списка')

    def play_prev(self):
        if self.current_video_index > 0:
            self.play_video(self.current_video_index - 1)

    def on_vol(self, v):
        self.bar.vol_slider.blockSignals(True)
        self.bar.vol_slider.setValue(int(v))
        self.bar.vol_slider.blockSignals(False)

    def on_mute(self, m):
        self.bar.btn_mute.setIcon(icons.get_icon('mute' if m else 'vol'))

    def on_vol_slider(self, value):
        self.engine.set_volume(value)
        self.show_osd(f'Громкость {int(value)}%')
        self.backend.set_setting('volume', int(value))

    def toggle_mute(self):
        self.engine.toggle_mute()
        self.show_osd('Звук выкл.' if self.engine.muted else 'Звук вкл.')
        self.backend.set_setting('mute', self.engine.muted)

    def _change_volume(self, delta):
        self.engine.set_volume(self.engine.volume + delta)
        self.show_osd(f'Громкость {int(self.engine.volume)}%')

    def _change_speed(self, factor):
        speed = float(self.engine.speed or 1.0) * factor
        speed = max(config.SPEED_MIN, min(config.SPEED_MAX, round(speed, 3)))
        self.engine.set_speed(speed)
        self.backend.set_setting('speed', speed)
        self.show_osd(f'Скорость {speed:.2f}x')

    def _reset_speed(self):
        self.engine.set_speed(1.0)
        self.backend.set_setting('speed', 1.0)
        self.show_osd('Скорость 1.00x')

    def _cycle_aspect(self):
        self._aspect_idx = (getattr(self, '_aspect_idx', 0) + 1) % len(
            self.ASPECT_MODES)
        value, label = self.ASPECT_MODES[self._aspect_idx]
        self.engine.set('video-aspect-override', value or 'no')
        self.show_osd(f'Пропорции: {label}')

    def _screenshot(self):
        name = 'test_shot_' + time.strftime('%Y%m%d_%H%M%S') + '.png'
        path = os.path.join(paths.app_dir(), name)
        self.engine.command('screenshot-to-file', path, 'video')
        self.show_osd(f'Кадр сохранён: {name}', 2500)

    def toggle_hwdec(self):
        self._hwdec_idx = (self._hwdec_idx + 1) % len(config.HWDEC_MODES)
        mode, label = config.HWDEC_MODES[self._hwdec_idx]
        # Вывод (vo=image) не трогаем: его выставляет движок, и от
        # режима декодирования он не зависит. Меняем только декодер.
        self.engine.set('hwdec', mode)
        self.show_osd(f'Декодирование: {label}', 2500)

    def toggle_compat(self):
        self._compat = not self._compat
        self.engine.set(
            'hwdec', 'no' if self._compat
            else config.HWDEC_MODES[self._hwdec_idx][0])
        self.show_osd('Режим совместимости включён' if self._compat
                      else 'Режим совместимости выключен', 2500)

    def toggle_autoplay(self):
        self.set_autoplay(not self._autoplay)

    def set_autoplay(self, enabled):
        enabled = bool(enabled)
        if enabled == self._autoplay:
            return
        self._autoplay = enabled
        self._update_autoplay_button()
        self.show_osd('Автоспроизведение включено' if enabled
                      else 'Автоспроизведение выключено', 2000)
        self.backend.set_setting('autoplay', enabled)

    def _update_autoplay_button(self):
        switch = getattr(self.bar, 'btn_auto', None)
        if switch is not None:
            switch.set_on(self._autoplay)

    def _autoplay_clicked(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_autoplay()
            event.accept()
            return
        widgets.AutoplaySwitch.mousePressEvent(self.bar.btn_auto, event)

    def show_osd(self, text, timeout_ms=1200):
        self.engine.command('show-text', text, int(timeout_ms), 1)

    def _mpv_get(self, name, default=None):
        return self.engine.get(name)

    # ------------------------------------------------------------------
    # Список роликов
    # ------------------------------------------------------------------

    def _fill_playlist(self):
        self.playlist_panel.list.clear()
        for msg in self.playlist:
            self.playlist_panel.list.addItem(msg.text)
        self._sync_playlist_selection()

    def _sync_playlist_selection(self):
        lst = self.playlist_panel.list
        if self.current_video_index < lst.count():
            lst.setCurrentRow(self.current_video_index)

    def _on_playlist_click(self, row):
        if row >= 0:
            self.play_video(row)

    def _toggle_playlist(self):
        self._playlist_shown = not self._playlist_shown
        self.playlist_panel.setVisible(self._playlist_shown)

    # ------------------------------------------------------------------
    # Пробный режим: панель отдельным окном (как в настоящем плеере)
    # ------------------------------------------------------------------

    def toggle_overlay_bar(self):
        """Включает и выключает тот самый режим с тремя окнами.

        Нужен только для сравнения: так делает настоящий плеер, и так
        в Alt+Tab появляются лишние окна. Здесь это не действие игрока,
        а кнопка «посмотрите разницу».
        """
        if self._overlay_bar_mode:
            self._leave_overlay_bar()
            self.show_osd('Панель снова в окне плеера')
            return
        if not self._fullscreen:
            self.enter_fullscreen()
        self._overlay_bar_mode = True
        self.root_layout.removeWidget(self.bar)
        self.bar.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.bar.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.bar.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool)
        self.bar.setParent(None)
        sg = (self.screen() or QApplication.primaryScreen()).geometry()
        w = min(760, sg.width() - 60)
        self.bar.setFixedWidth(w)
        self.bar.set_rounded(True)
        self.bar.adjustSize()
        self.bar.move(sg.x() + (sg.width() - w) // 2,
                      sg.y() + sg.height() - self.bar.height() - 36)
        self.bar.show()
        self.show_osd('Пробный режим: панель отдельным окном (B — назад)', 3000)

    def _leave_overlay_bar(self):
        if not self._overlay_bar_mode:
            return
        self._overlay_bar_mode = False
        self.bar.hide()
        self.bar.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.bar.setWindowFlags(Qt.WindowType.Widget)
        self.bar.setParent(self.centralWidget())
        self.bar.set_rounded(False)
        if self.root_layout.indexOf(self.bar) < 0:
            self.root_layout.addWidget(self.bar)
        self.bar.show()

    # ------------------------------------------------------------------
    # Карточка и справка
    # ------------------------------------------------------------------

    def _card_text(self):
        return (
            '▶ Демонстрационный ролик — потока видео нет\n'
            f'{(self.current_video_index or 0) + 1} из {len(self.playlist)}'
            f' · {self.playlist[self.current_video_index].text}\n\n'
            'Пробел — играть · F или двойной клик — полный экран\n'
            'B — сравнить с вариантом «панель отдельным окном»\n'
            'F1 — все клавиши')

    def _show_help(self):
        if self._help_window is not None:
            self._close_help()
            return
        win = QWidget(None, Qt.WindowType.FramelessWindowHint
                      | Qt.WindowType.Tool)
        win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        win.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay = QVBoxLayout(win)
        lay.setContentsMargins(0, 0, 0, 0)
        label = QLabel(HELP_TEXT)
        label.setStyleSheet(
            'background: rgba(16,16,16,0.94); color: #eaeaea;'
            'border-radius: 12px; padding: 18px 26px; font-size: 13px;')
        lay.addWidget(label)
        win.adjustSize()
        win.move(self.frameGeometry().center() - win.rect().center())
        win.show()
        win.raise_()
        self._help_window = win

    def _close_help(self):
        win, self._help_window = self._help_window, None
        if win is not None:
            try:
                win.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # События окна
    # ------------------------------------------------------------------

    def _poll_system_theme(self):
        if theme.theme_key() != theme.SYSTEM_THEME:
            return
        light = icons.system_uses_light_theme()
        if light == self._system_light:
            return
        self._system_light = light
        QApplication.instance().setStyleSheet(theme.build_style_scaled())
        self.bar.refresh_icons()

    def changeEvent(self, event):
        try:
            if event.type() == QEvent.Type.ActivationChange:
                if self.isActiveWindow():
                    self._on_player_activated()
            elif event.type() == QEvent.Type.WindowStateChange:
                # Если окно вернули из полного экрана средствами
                # Windows (клавиша Win+стрелка, например), панель должна
                # вернуться на прежнее место — свой отсчёт режима ведём
                # по флагу, а не по состоянию окна.
                if (self._fullscreen
                        and not (self.windowState()
                                 & Qt.WindowState.WindowFullScreen)):
                    self.exit_fullscreen()
        except Exception:
            pass
        super().changeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fullscreen and not self._overlay_bar_mode:
            sg = (self.screen() or QApplication.primaryScreen()).geometry()
            self.bar.setFixedWidth(min(760, sg.width() - 60))

    def closeEvent(self, event):
        self._closed = True
        self.bar_timer.stop()
        self._tick_timer.stop()
        self._cursor_timer.stop()
        self._close_help()
        self.engine.terminate()
        if self.media is not None:
            self.media.stop()
            self.media.setSource(QUrl())
        self.sig_closed.emit()
        super().closeEvent(event)


HELP_TEXT = (
    'Пробел — пауза\n'
    '← / → — перемотка (шаг 10 с)\n'
    '↑ / ↓ — громкость\n'
    'F — полный экран (то же окно)\n'
    'Esc — выйти из полного экрана\n'
    'Двойной клик по кадру — полный экран\n'
    'Колесо мыши над кадром — громкость\n'
    'H — перебрать режим декодирования\n'
    'M — выключить звук\n'
    'N / P — следующий / предыдущий ролик\n'
    '[ / ] — медленнее / быстрее\n'
    '\\ — обычная скорость\n'
    'A — пропорции картинки\n'
    'S — сохранить кадр\n'
    'L — список роликов\n'
    'T — автоспроизведение\n'
    'V — режим совместимости (старые видеокарты)\n'
    'B — пробный режим: панель отдельным окном (как в плеере)\n'
    'F1 — эта справка'
)


# ----------------------------------------------------------------------
# Точка входа
# ----------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName('Tegr — тестовый плеер')
    app.setStyle('Fusion')
    theme.apply_theme_settings({})
    app.setStyleSheet(theme.build_style_scaled())

    if mpv_boot.mpv is None:
        QMessageBox.critical(
            None, 'Тестовый плеер',
            'Не удалось загрузить mpv (libmpv-2.dll):\n\n'
            + mpv_boot.diagnosis())
        return 1

    win = TestPlayer()
    win.show()
    # Ролик запускаем ПОСЛЕ show(): mpv, созданный на скрытом окне, не
    # выводит картинку (та же причина, что в настоящем плеере).
    win.start_demo()
    # Окно держим живым до закрытия: иначе Python уберёт его вместе с
    # подписками на mpv, а события оттуда всё ещё приходят.
    app._test_player = win
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())

