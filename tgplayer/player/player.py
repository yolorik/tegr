"""Окно плеера: mpv, управление, перемотка, клавиши.

Самое большое окно программы. Знает про mpv (через python-обвязку mpv),
про бэкенд (откуда брать байты) и про виджеты панели; само ничего не
качает и не хранит.

Особенности, которые проще понять из кода, чем из названия:

  * картинку рисует mpv прямо в свой виджет, поэтому панель и подсказки
    живут отдельными окнами поверх видео;
  * полный экран выносит видео в собственное окно фуллскрина, а панель —
    в его же отдельное окно (иначе её не видно поверх кадра);
  * «картинка в картинке» — третье окно, маленькое и поверх всех.
"""
import asyncio
import ctypes
import math
import os
import time
from collections import deque

from PyQt6.QtCore import (
    QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, QUrl,
    QCoreApplication, pyqtSignal)
from PyQt6.QtGui import (
    QAction, QActionGroup, QColor, QCursor, QKeySequence, QPainter, QPixmap,
    QShortcut)
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSplitter,
    QVBoxLayout, QWidget)

from ..core import config, history, log, paths, theme, utils, version
from ..core import settings as app_settings
from ..icons import icons
from ..system import mpv_boot

# Кадры mpv рисует Qt: mpv отдаёт их через плагин QtMultimedia
# (libmpv2/qt), а рисует обычный QVideoWidget. Так кадр становится
# обычным виджетом — панель и подсказки ложатся поверх него без
# отдельных окон, а полный экран — это то же самое окно (см. ниже).
# Если QtMultimedia нет, плеер работает как прежде: mpv рисует в окно,
# но тогда и полный экран остаётся отдельным окном.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    QT_VIDEO_OK = bool(QMediaPlayer)
except Exception as _qt_video_err:  # noqa: BLE001
    QAudioOutput = QMediaPlayer = QVideoWidget = None
    QT_VIDEO_OK = False
    print(f"[MPV] QtMultimedia недоступен, кадр рисует сам mpv: "
          f"{_qt_video_err}")


def _install_qt_renderer():
    """Включает у mpv вывод кадров наружу, в QtMultimedia.

    Ставить переменную надо ДО создания MPV: он читает окружение при
    запуске. Куда рисовать, плагин узнаёт от QVideoWidget.
    """
    import os
    os.environ['MPV_RENDER_API'] = 'qt'

from ..widgets import widgets
from ..backend.backend import TelegramBackend
from ..system import telethon_boot as _telethon

def _cursor_pos_win32():
    """Положение курсора прямо от Windows; None — если спросить нельзя."""
    try:
        point = ctypes.wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return QPoint(point.x, point.y)
    except Exception:
        return None


class PlayerWindow(QMainWindow):
    sig_pos = pyqtSignal(float)
    sig_dur = pyqtSignal(float)
    sig_pause = pyqtSignal(bool)
    sig_eof = pyqtSignal()
    sig_vol = pyqtSignal(float)
    sig_mute = pyqtSignal(bool)
    # Пропорции текущего ролика: по ним подгоняется окно PiP.
    sig_aspect = pyqtSignal(float)
    sig_closed = pyqtSignal()
    sig_watched = pyqtSignal()
    def __init__(self, backend, initial_video_index, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.current_video_index = initial_video_index
        # Список роликов, закреплённый за этим окном, и его токен для HTTP.
        # Плеер работает только с ним: смена канала в главном окне не должна
        # подменять файл под уже играющим роликом.
        self.playlist = []
        self._playlist_token = None
        self.set_playlist(self.backend.current_messages)
        # Иконка в заголовке окна: по ней плеер узнаётся на панели задач.
        icon = paths.load_app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self._last_pos_ui = 0.0
        # Где сейчас просмотр: по нему считаем, насколько загрузка ушла
        # вперёд, чтобы вовремя начать качать следующий ролик.
        self._last_pos = 0.0
        self._play_ts = 0.0
        self._closed = False
        self._play_gen = 0
        self._current_doc_id = None
        self._next_doc_id = None
        self._speed_hist = deque()
        self._speed_bps = 0.0
        self._cur_bitrate_mbps = 0.0
        self._warned_slow = False
        # Последние скачанные участки на полосе прокрутки: перерисовываем
        # её только когда они реально изменились (см. update_download_progress).
        self._last_buffered = None
        # Настройки, переживающие перезапуск: громкость, режим декодера,
        # скорость, размер окна.

        # Автоспроизведение: переходить к следующей серии самому. По
        # умолчанию включено — так плеер вёл себя всегда, и выключать
        # привычное поведение по умолчанию было бы странно.
        self._autoplay = bool(self._setting('autoplay', True))
        self._last_pos_save = 0.0
        self._help_window = None

        self.setWindowTitle("Медиаплеер")
        self.resize(960, 580)
        app_settings.restore_window_geometry(self, 'player_geometry', self._settings_dict())

        central = QWidget()
        central.setObjectName("playerroot")
        # У окна плеера фон чёрный, как у самого кадра: иначе по краям
        # видео оставалась кайма цвета темы — на светлой теме она и
        # выглядела белой обводкой вокруг видео.
        central.setStyleSheet(
            "#playerroot { background: #000000; border: none; }")
        self.root_layout = QVBoxLayout(central)
        self.root_layout.setContentsMargins(0, 0, 0, 0)

        self.container = widgets.PlayerContainer()
        # ВИДЕО РИСУЕТ Qt, А НЕ mpv. QVideoWidget встаёт на место виджета
        # mpv и растягивается той же раскладкой. Это и есть то, что даёт
        # одно окно: кадр — обычный виджет, и панель ложится поверх него
        # без отдельного окна, а полный экран больше не нужно выносить в
        # другое окно. Если QtMultimedia нет — остаётся прежний способ
        # (mpv в окно), и тогда полный экран снова отдельное окно.
        self.use_qt_video = bool(QT_VIDEO_OK)
        self._video_mode = 'qt' if self.use_qt_video else 'mpv'
        self._osd_label = None
        self._osd_timer = None
        self.video_out = None
        self.media = None
        self._one_window_fs = False
        self._windowed_geometry = None
        self._sink_qt_video()
        self.bar = widgets.ControlBar()
        # Панель управления — обычный дочерний виджет окна. Раньше она
        # была нативным окном (WA_NativeWindow) и с «слоями окна», чтобы
        # её было видно поверх кадра: кадром владел mpv. Теперь кадром
        # владеет Qt, и это не нужно — панель попадает в то же окно, что
        # и видео.
        # Подложка панели лежит на видео, поэтому рисуем её сами в
        # paintEvent панели (см. ControlBar.paintEvent). Таблице стилей это
        # не по силам: у QWidget фон из стиля без WA_StyledBackground не
        # рисуется, а он у панели снят ради скруглённых углов.
        # Список канала прямо в окне плеера: переключать ролики, не открывая
        # окно с каналами. Скрыт по умолчанию, чтобы не занимать экран.
        # Видимость панели держим отдельным флагом, а не isVisible(): у
        # скрытого окна isVisible() всегда False, и переключатель врал бы.
        self._playlist_shown = bool(self._setting('playlist_panel'))
        self.playlist_panel = self._build_playlist_panel()
        self.video_row = QHBoxLayout()
        self.video_row.setContentsMargins(0, 0, 0, 0)
        self.video_row.setSpacing(4)
        self.video_row.addWidget(self.container, 1)
        self.video_row.addWidget(self.playlist_panel)
        self.root_layout.addLayout(self.video_row, 1)
        self.root_layout.addWidget(self.bar)
        if self._playlist_shown:
            self.playlist_panel.show()
            self.bar.btn_list.setToolTip("Скрыть список канала (L)")

        self.setCentralWidget(central)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Окно закрывается — удаляем его вместе с fs_window и pip_window.
        # Без этого каждый открытый плеер оставался скрытым объектом с
        # родителем MainWindow и жил до конца работы программы.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.fs_window = QMainWindow()
        # Только безрамочное полноэкранное окно, без «поверх всех».
        #
        # WindowStaysOnTopHint висел здесь с самого начала, и из-за него
        # два дела ломались разом. Alt+Tab уводил в другое окно, но
        # фуллскрин продолжал висеть над ним — переключились вроде бы, а
        # на экране по-прежнему видео. И вернуться в плеер с клавиатуры
        # было нельзя: такое окно держится выше любого другого, поэтому
        # показывает его только сам плеер, а не система. Полноэкранному
        # окну «поверх» не нужно — оно и так занимает весь экран.
        self.fs_window.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.fs_window.setStyleSheet("background: black;")
        self.fs_window.installEventFilter(self)

        # Окно «картинка в картинке»: без рамки, поверх всех окон и без
        # записи в панели задач — иначе на СДВГ-случай он не годится.
        # Видео переезжает сюда тем же способом, что и в фуллскрине.
        self.pip_window = QMainWindow()
        self.pip_window.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # Фон окна — обычный чёрный, как был.
        #
        # Прозрачность окна (WA_TranslucentBackground) здесь не годится:
        # видео выводится поверх экрана (WA_PaintOnScreen), и вместе с
        # прозрачным окном оно перестаёт получать мышь совсем — курсор
        # ходит «под» окном, кнопки не нажимаются. Прозрачность нужна
        # только значкам, и её задаём им самим.
        self.pip_window.setStyleSheet("background: black;")
        self.pip_window.installEventFilter(self)

        # Панель управления для PiP: висит поверх видео внизу окна и
        # убирается сама, когда мышь не над окном.
        self.pip_bar = widgets.PipBar(self.pip_window, self)
        self.pip_bar.hide_all()
        self._pip_bar_timer = QTimer(self)
        self._pip_bar_timer.setSingleShot(True)
        self._pip_bar_timer.setInterval(2500)
        self._pip_bar_timer.timeout.connect(self._hide_pip_bar)

        self.video_widget = self.container.video_widget

        # Диагностические флаги и состояние декодера.
        self._logged_play = False
        self._logged_video = False
        self._vdiag = {}
        try:
            self._hwdec_idx = int(self._setting('hwdec', config.HWDEC_INDEX))
        except (TypeError, ValueError):
            self._hwdec_idx = config.HWDEC_INDEX
        if not 0 <= self._hwdec_idx < len(config.HWDEC_MODES):
            self._hwdec_idx = config.HWDEC_INDEX
        self._pending_seek = None
        # Ролики, для которых автолечение картинки уже выполнено.
        self._auto_fixed_docs = set()
        # Автопереход к следующему ролику: флаг, чтобы не сработал дважды.
        self._eof_handled = False
        self._duration = 0.0

        # MPV не должен ждать огромный буфер: локальный HTTP уже отдаёт
        # данные из дискового кеша, а небольшой cache сглаживает сетевые паузы.
        # Режим совместимости: на старых и встроенных адаптерах аппаратный
        # декодер и вывод через DirectX 11 отдают чёрный кадр при живом
        # звуке, не сообщая об ошибке. Тогда декодируем программно.
        self._compat = bool(self._setting('compat'))
        self.player = self._make_mpv()

        @self.player.property_observer('time-pos')
        def _tp(_name, value):
            self.sig_pos.emit(float(value or 0.0))
            if value and not self._logged_play:
                self._logged_play = True
                delay = time.monotonic() - getattr(
                    self, '_play_ts', time.monotonic()
                )
                d = getattr(self, '_vdiag', {})
                print(f"[MPV] видео {d.get('w')}x{d.get('h')}, "
                      f"кодек={d.get('codec')}, формат={d.get('pf')}, "
                      f"primaries={d.get('prim')}, hwdec={d.get('hwdec')}, "
                      f"старт через {delay:.2f} с")

        # Диагностика видео. ВАЖНО: нельзя звать player.get() внутри
        # наблюдателя — он выполняется в потоке mpv, и такой вызов
        # блокирует сам себя. Поэтому значения копим отдельными
        # наблюдателями, а печатаем их из time-pos, когда всё уже известно.
        @self.player.property_observer('video-codec')
        def _d_codec(_name, value):
            self._vdiag['codec'] = value

        @self.player.property_observer('hwdec-current')
        def _d_hwdec(_name, value):
            self._vdiag['hwdec'] = value

        @self.player.property_observer('video-params/w')
        def _d_w(_name, value):
            self._vdiag['w'] = value

        @self.player.property_observer('video-params/h')
        def _d_h(_name, value):
            self._vdiag['h'] = value

        @self.player.property_observer('video-params/pixelformat')
        def _d_pf(_name, value):
            self._vdiag['pf'] = value

        @self.player.property_observer('video-params/primaries')
        def _d_prim(_name, value):
            self._vdiag['prim'] = value

        @self.player.property_observer('duration')
        def _du(_name, value):
            self.sig_dur.emit(float(value or 0.0))

        @self.player.property_observer('pause')
        def _pa(_name, value):
            self.sig_pause.emit(bool(value))

        @self.player.property_observer('eof-reached')
        def _eof(_name, value):
            if value:
                self.sig_eof.emit()

        @self.player.property_observer('volume')
        def _vo(_name, value):
            self.sig_vol.emit(float(value or 0.0))

        @self.player.property_observer('mute')
        def _mu(_name, value):
            self.sig_mute.emit(bool(value))

        # Пропорции ролика. Как только mpv их узнал (после загрузки
        # файла), подгоняем окно PiP: следующий ролик может быть
        # вертикальным после широкого, и окно должно стать вертикальным,
        # иначе картинка встанет в широкое окно с чёрными полосами.
        @self.player.property_observer('video-params/aspect')
        def _asp(_name, value):
            if value:
                self.sig_aspect.emit(float(value))

        self.sig_pos.connect(self.on_pos)
        self.sig_dur.connect(self.on_dur)
        self.sig_pause.connect(self.on_pause)
        # Пауза — повод ПОКАЗАТЬ панель, а не только обновить её вид:
        # см. _on_pause_changed.
        self.sig_pause.connect(self._on_pause_changed)
        self.sig_eof.connect(self.on_eof)
        self.sig_vol.connect(self.on_vol)
        self.sig_mute.connect(self.on_mute)
        self.sig_aspect.connect(self._on_video_aspect)

        # Громкость задаём ПОСЛЕ подписки на наблюдатель: иначе mpv не
        # сообщит о смене значения, и ползунок останется на своём прежнем
        # 80%, показывая не то, что реально играет. Ползунок выставляем
        # сразу же, чтобы он был верным даже до первого сигнала.
        try:
            volume = float(self._setting('volume', config.DEFAULT_VOLUME))
        except (TypeError, ValueError):
            volume = float(config.DEFAULT_VOLUME)
        volume = max(0.0, min(100.0, volume))
        try:
            self.player['volume'] = volume
            self.player['mute'] = bool(self._setting('mute', False))
            self.player['speed'] = self._saved_speed()
        except Exception:
            pass
        self.bar.vol_slider.blockSignals(True)
        self.bar.vol_slider.setValue(int(volume))
        self.bar.vol_slider.blockSignals(False)

        self.bar_timer = QTimer(self)
        self.bar_timer.setSingleShot(True)
        self.bar_timer.timeout.connect(self.hide_bar_idle)

        # Где стоит курсор в фуллскрине — без ожидания событий Qt.
        # От этого зависит, появится ли панель ДО первого нажатия: см.
        # MouseWatch и hide_bar_idle.
        self._mouse_watch = widgets.MouseWatch(
            self.fs_window, self._on_mouse_over, self._on_mouse_left,
            on_move=self._mouse_over_tick)

        # Одиночный клик по видео = play/pause (с задержкой, чтобы отличить
        # от двойного клика = полный экран).
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self.toggle_play_pause)
        self._ignore_next_release = False
        # Перетаскивание окна PiP за область видео: смещение курсора внутри
        # окна на момент нажатия и признак, что окно реально сдвинулось.
        # Сдвиг не должен засчитываться как клик — иначе после каждого
        # переноса окна видео вставало бы на паузу.
        self._pip_drag = None
        self._pip_moved = False
        # Ресайз окна PiP за край: направление (dx, dy), исходная геометрия
        # и исходная точка курсора на момент захвата.
        self._pip_resize = None
        self._pip_resize_geom = None
        self._pip_resize_start = None
        # Текущий курсор окна PiP: setCursor на каждое движение мыши
        # заставляет окно перерисовываться, поэтому ставим его только при
        # смене формы.
        self._pip_cursor = Qt.CursorShape.ArrowCursor
        # Когда панель PiP показана в последний раз. Нужно, чтобы событие
        # «мышь ушла», которое приходит сразу после входа в режим, не
        # прятало панель в тот же миг.
        self._pip_bar_shown_ts = 0.0
        # Последнее настоящее положение курсора (экранные координаты).
        # По нему отличаем движение мыши от событий, которые Qt шлёт сам.
        self._last_mouse_pos = None

        # Таймер обновления индикатора загрузки
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(250)
        self.progress_timer.timeout.connect(self.update_download_progress)
        self.progress_timer.start()

        bar = self.bar
        # Пилюля автоспроизведения показывает сохранённое состояние сразу,
        # ещё до первого ролика.
        self._update_autoplay_button()
        bar.btn_prev.clicked.connect(self.play_prev)
        bar.btn_rew.clicked.connect(lambda: self.seek_rel(-10))
        bar.btn_play.clicked.connect(self.toggle_play_pause)
        bar.btn_ff.clicked.connect(lambda: self.seek_rel(10))
        bar.btn_next.clicked.connect(self.play_next)
        bar.btn_mute.clicked.connect(self.toggle_mute)
        bar.btn_fs.clicked.connect(self.toggle_fullscreen)
        bar.btn_pip.clicked.connect(self.toggle_pip)
        bar.btn_list.clicked.connect(self._toggle_playlist)
        # Пилюля автоспроизведения не кнопка: она не умеет сигналить
        # сама, поэтому ловим нажатие и переключаем состояние в окне.
        bar.btn_auto.mousePressEvent = self._autoplay_clicked
        bar.vol_slider.valueChanged.connect(self.on_vol_slider)
        bar.seek_slider.sliderMoved.connect(self.on_seek_slider)

        # Панель PiP соединять не нужно: она рисует значки сама и зовёт
        # pip_action / pip_seek. Отдельных кнопок и ползунка у неё нет.
        self.pip_bar.installEventFilter(self)

        watched = [self.container, self.video_widget, self.bar, self.pip_bar]
        # Само окно плеера тоже слушаем: Leave от него — сигнал, что мышь
        # ушла в фуллскрине, и панель надо убрать сразу, а не через таймер.
        watched.append(self)
        # Панель в фуллскрине — отдельное окно, кроме себя самой у неё есть
        # подписи и ползунки: курсор над ними тоже «над панелью».
        watched += self.bar.findChildren(QWidget)
        for w in watched:
            w.setMouseTracking(True)
            w.installEventFilter(self)

        self._setup_shortcuts()
        # У набора на панель своё место: его вешает toggle_fullscreen,
        # когда панель становится отдельным окном. Пока она в раскладке,
        # её окно — это окно плеера, и второй набор там сломал бы всё.
        self._setup_bar_shortcuts()

        # Видео здесь НЕ запускаем: окно ещё не показано, и mpv создаст
        # видео-вывод на невидимом окне — картинка останется чёрной до
        # первого перезапуска файла. Запуск делает MainWindow.open_player
        # уже после show().

    def _make_mpv(self):
        """Создаёт mpv с нужным выводом кадра.

        В режиме Qt держим те же опции, что и раньше, кроме вывода:
        кадры отдаются наружу (vo=image), и декодер при этом остаётся
        аппаратным. В режиме без QtMultimedia — всё как прежде: mpv
        рисует прямо в окно своего виджета.
        """
        qt = self._video_mode == 'qt'
        if qt:
            _install_qt_renderer()
        else:
            import os
            os.environ.pop('MPV_RENDER_API', None)
        opts = dict(
            vo=('image' if qt else config.VO_CHAIN),
            hwdec=('no' if self._compat
                   else config.HWDEC_MODES[self._hwdec_idx][0]),
            # Если аппаратный декодер не справился — молча уходим в софт,
            # вместо чёрного экрана при работающем звуке.
            vd_lavc_software_fallback='yes',
            idle='yes',
            # Держим последний кадр на экране после конца файла: без этого
            # mpv мгновенно уходит в idle, и свойство eof-reached не успевает
            # сообщить о конце — автопереход к следующему ролику не срабатывал.
            keep_open='yes',
            # OSD: в режиме Qt надписи рисует окно, в режиме mpv — сам
            # mpv в кадре (уровень 1, без него надписей не видно).
            osd_level=(0 if qt else 1),
            hr_seek='yes',
            cache='yes',
            # При нехватке данных mpv ставит паузу и показывает индикатор
            # буферизации, вместо "замерзания" картинки.
            cache_pause='yes',
            cache_secs=config.MPV_CACHE_SECS,
            # Поток гарантированно seekable (наш сервер отдаёт Range),
            # убираем лишний probe-запрос и исключаем ошибочное
            # определение потока как не-seekable.
            force_seekable='yes',
            demuxer_readahead_secs=config.MPV_DEMUX_READAHEAD_SECS,
            demuxer_max_bytes='64MiB',
            demuxer_max_back_bytes='16MiB',
            # Ошибки и предупреждения mpv должны быть видны: при 'all=no'
            # молчали и сбои декодера (чёрный экран при живом звуке).
            msg_level='all=warn',
            # Клавиатуру обрабатывает Qt, а не mpv.
            input_default_bindings='no',
            input_vo_keyboard='no',
        )
        if not qt:
            opts['wid'] = int(self.video_widget.winId())
        return mpv_boot.mpv.MPV(**opts)

    def _sink_qt_video(self):
        """Ставит QVideoWidget на место виджета mpv (один раз)."""
        if not self.use_qt_video:
            return
        try:
            w = QVideoWidget()
            w.setObjectName('videoframe')
            w.setStyleSheet('background: black;')
            # Наведение выключено нарочно: QtMultimedia ставит окну
            # собственный обработчик, и включённое наведение превращает
            # остальные сообщения мыши в «мышь не моя» — не работают ни
            # клик по кадру, ни двойной клик.
            w.setAttribute(Qt.WidgetAttribute.WA_Hover, False)
            mpv_view = self.container.video_widget
            self.container.layout().replaceWidget(mpv_view, w)
            mpv_view.hide()
            self.video_out = w
            self.media = QMediaPlayer(self)
            self.media.setVideoOutput(w)
            audio = QAudioOutput(self)
            audio.setMuted(True)      # звук играет mpv, второй не нужен
            self.media.setAudioOutput(audio)
        except Exception as ex:
            print(f"[MPV] кадр через QtMultimedia не подключился: {ex}")
            self.use_qt_video = False
            self._video_mode = 'mpv'
            self.video_out = None
            self.media = None

    def update_download_progress(self):
        if (self.current_video_index < 0
                or self.current_video_index >= len(self.playlist)):
            return
        doc = self.playlist[self.current_video_index].media.document
        e = self.backend.cache.get(doc.id)
        if e:
            done_pieces = len(e.get('piece_done', set()))
            total_pieces = e.get('piece_count', 1)
            pct = int((done_pieces / max(1, total_pieces)) * 100)
            if e.get('complete'):
                pct = 100

            # Автолечение картинки. Как только скачано config.AUTO_FIX_PERCENT,
            # перезапускаем файл, чтобы mpv перечитал индекс: без этого он
            # отдаёт чёрный экран при живом звуке до 40+% загрузки.
            # Декодер здесь НЕ перебираем — перебор дело клавиши H, иначе
            # режим уезжал на каждом ролике подряд.
            if (pct >= config.AUTO_FIX_PERCENT
                    and doc.id not in self._auto_fixed_docs
                    and not self._closed):
                self._auto_fixed_docs.add(doc.id)
                QTimer.singleShot(0, self._auto_heal_picture)

            # Скачанные участки на полосе прокрутки (как на YouTube).
            # Считаем только если что-то реально изменилось: таймер бежит
            # 4 раза в секунду, а на слабой машине пересборка полосы на
            # каждом тике отъедала время у видео.
            try:
                size = max(1, e['size'])
                ranges = [(s / size, (en + 1) / size)
                          for s, en in e['tracker'].ranges]
                if ranges != self._last_buffered:
                    self._last_buffered = ranges
                    self.bar.seek_slider.set_buffered_ranges(ranges)
            except Exception:
                pass

            # Честная скорость: среднее по скользящему окну.
            #
            # Окно было 3 секунды, и это давало «скорость в ноль» на ровном
            # канале: данные приходят порциями, и если в окно попала пауза
            # в полсекунды между порциями, среднее падало почти до нуля.
            # Человек видел ноль и сообщение о низкой скорости при живом
            # канале в 4.5 МБ/с.
            #
            # Теперь окно длиннее, а измеряем ещё и по крупному шагу: считаем
            # от самой старой точки, но не меньше чем за секунду, и берём
            # МАКСИМУМ из двух оценок — по текущему окну и по всему времени
            # загрузки. Одна заминка не должна выглядеть как мёртвый канал.
            now = time.monotonic()
            net = e.get('speed_bytes', 0)
            self._speed_hist.append((now, net))
            while (len(self._speed_hist) >= 2
                   and now - self._speed_hist[0][0] > config.SPEED_WINDOW_SECS):
                self._speed_hist.popleft()

            window_bps = 0.0
            if len(self._speed_hist) >= 2:
                t0, b0 = self._speed_hist[0]
                dt = now - t0
                if dt > 1.0:
                    window_bps = max(0.0, (net - b0) / dt)

            # Средняя за всю загрузку: показывает реальную пропускную
            # способность, не реагируя на секундные провалы.
            avg_bps = 0.0
            st = e.get('started_ts')
            if st:
                elapsed = now - st
                if elapsed > 2.0:
                    avg_bps = e.get('downloaded_bytes', 0) / elapsed

            # Скорость для интерфейса и для проверки битрейта — не ниже
            # половины средней: если канал тянет, короткая пауза не должна
            # выглядеть обрывом.
            if avg_bps > 0:
                self._speed_bps = max(window_bps, avg_bps * 0.5)
            else:
                self._speed_bps = window_bps

            # Раз в 10 секунд запоминаем, где остановились: даже если
            # программу закрыть аварийно, позиция останется.
            if now - self._last_pos_save > 10:
                self._last_pos_save = now
                self._save_position()

            if pct >= 100:
                self.bar.lbl_cache.setText("Кэш 100%")
            else:
                avg_txt = f" · ср. {utils.fmt_speed(avg_bps)}" if avg_bps > 0 else ""
                self.bar.lbl_cache.setText(
                    f"Кэш {pct}% · {utils.fmt_speed(self._speed_bps)}{avg_txt}"
                )

            # Проверяем, влезает ли битрейт в канал.
            #
            # Раньше хватало ОДНОГО замера ниже битрейта, и флаг
            # _warned_slow взводился навсегда: сообщение «будут паузы»
            # появлялось после первой же секундной заминки, хотя канал
            # тянул втрое больше нужного. Теперь требуем, чтобы скорость
            # была низкой СТАБИЛЬНО — несколько проверок подряд.
            if self._cur_bitrate_mbps > 0 and self._speed_bps > 0:
                speed_mbps = self._speed_bps * 8 / 1_000_000
                if self._cur_bitrate_mbps > speed_mbps * 1.2:
                    self._slow_checks = getattr(self, '_slow_checks', 0) + 1
                else:
                    # Скорость в норме — счёт начинается заново.
                    self._slow_checks = 0

                if (not self._warned_slow
                        and self._slow_checks >= config.SLOW_CHECKS_BEFORE_WARN):
                    self._warned_slow = True
                    # Переключать на «качество поменьше» больше нечем и
                    # незачем: кнопка убрана, в Telegram у видео обычно
                    # одно качество. Остаётся честное предупреждение.
                    self.show_osd(
                        f"Канал ~{speed_mbps:.1f} Мбит/с, видео "
                        f"{self._cur_bitrate_mbps:.1f} Мбит/с — будут паузы",
                        4500
                    )
        else:
            self.bar.lbl_cache.setText("Подготовка загрузки...")

    def set_playlist(self, messages):
        """Закрепляет за окном список роликов и токен для HTTP-запросов.

        Без этого mpv ходил бы в общий current_messages: пользователь
        переключает канал в главном окне, и следующий Range-запрос
        (перемотка, переподключение) получал бы файл из чужого канала, а
        next/prev шли бы по чужому плейлисту.
        """
        self.playlist = list(messages or [])
        try:
            self._playlist_token = self.backend.pin_playlist(self.playlist)
        except Exception:
            self._playlist_token = None
        # Список канала сменился — панель нужно пересобрать.
        if getattr(self, '_playlist_shown', False):
            self._refresh_playlist()

    def _build_playlist_panel(self):
        """Панель со списком роликов канала.

        Нужна, чтобы переключать серии, не открывая окно с каналами:
        список того же канала, что уже играет, всегда под рукой.

        Ширину можно менять перетаскиванием за левый край, и она
        запоминается: названия серий бывают длинными, и подобрать ширину
        один раз удобнее, чем подтягивать каждый запуск.
        """
        panel = QWidget()
        panel.setObjectName('playlistpanel')
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.lbl_playlist = QLabel("Список канала")
        lay.addWidget(self.lbl_playlist)
        self.list_playlist = QListWidget()
        self.list_playlist.setUniformItemSizes(True)
        self.list_playlist.itemClicked.connect(self._on_playlist_click)
        lay.addWidget(self.list_playlist, 1)

        width = self._saved_playlist_width()
        panel.setFixedWidth(width)
        self._playlist_width = width
        # Тянуть за левый край: панель стоит справа от видео.
        #
        # Фильтр ставим и на дочерние виджеты. Раньше он был только у
        # самой панели, а почти всю её площадь занимает список роликов —
        # события мыши уходили в него, и край не «ловился»: ширина не
        # менялась совсем, а курсор-стрелки залипали на всём окне.
        panel.installEventFilter(self)
        panel.setMouseTracking(True)
        for child in (self.list_playlist, self.lbl_playlist):
            child.installEventFilter(self)
            child.setMouseTracking(True)
        panel.hide()
        return panel

    def _saved_playlist_width(self):
        """Сохранённая ширина панели, либо значение по умолчанию."""
        try:
            value = self._setting('playlist_width', 0)
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        if value < config.PLAYLIST_MIN_WIDTH:
            return config.PLAYLIST_PANEL_WIDTH
        return min(value, config.PLAYLIST_MAX_WIDTH)

    def _save_playlist_width(self):
        """Запоминает текущую ширину панели в настройках."""
        try:
            width = int(self.playlist_panel.width())
            self._playlist_width = width
            self.backend.set_setting('playlist_width', width)
        except Exception as ex:
            print(f"[Плеер] ширина списка не сохранилась: {ex}")

    def _doc_id(self, index):
        """id документа ролика по индексу плейлиста (None, если его нет)."""
        try:
            return self.playlist[index].media.document.id
        except Exception:
            return None

    def _refresh_playlist(self):
        """Пересобирает список роликов текущего канала."""
        watched = getattr(self.backend, 'watched', None) or set()
        self.list_playlist.blockSignals(True)
        self.list_playlist.clear()
        for i in range(len(self.playlist)):
            title = self._video_title(i)
            mark = '✓ ' if self._doc_id(i) in watched else ''
            item = QListWidgetItem(f"{mark}{i + 1}. {title}")
            item.setToolTip(title)
            if i == self.current_video_index:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.list_playlist.addItem(item)
        self.list_playlist.blockSignals(False)
        self.lbl_playlist.setText(f"Список канала — {len(self.playlist)}")
        self._sync_playlist_selection()

    def _sync_playlist_selection(self):
        """Подсвечивает в списке тот ролик, который играет сейчас."""
        row = self.current_video_index
        if row is None or row < 0 or row >= self.list_playlist.count():
            return
        self.list_playlist.setCurrentRow(row)
        self.list_playlist.scrollToItem(self.list_playlist.item(row))

    def _on_playlist_click(self, item):
        row = self.list_playlist.row(item)
        if row != self.current_video_index:
            self.play_video(row)

    def _toggle_playlist(self):
        """Показывает или прячет панель списка канала."""
        show = not self._playlist_shown
        self._playlist_shown = show
        if show:
            self._refresh_playlist()
        self.playlist_panel.setVisible(show)
        self.bar.btn_list.setToolTip(
            "Скрыть список канала (L)" if show else "Список канала (L)")
        # Выбор переживает перезапуск: кто один раз открыл список, обычно
        # хочет видеть его и дальше.
        try:
            self.backend.set_setting('playlist_panel', show)
        except Exception:
            pass

    # Пропорции картинки, перебираемые клавишей A. Первая — «как есть».
    ASPECT_MODES = (
        (None, 'по размеру окна'),
        ('16:9', '16:9'),
        ('4:3', '4:3'),
        ('1.0', 'квадрат'),
        ('21:9', '21:9'),
    )

    HELP_TEXT = (
        "Пробел — пауза\n"
        "← / → — перемотка на 10 с\n"
        "↑ / ↓ — громкость\n"
        "F — полный экран\n"
        "I — картинка в картинке\n"
        "Esc — выйти из полного экрана или PiP\n"
        "H — перебрать режим декодирования\n"
        "M — выключить звук\n"
        "N / P — следующий / предыдущий ролик\n"
        "[ / ] — медленнее / быстрее\n"
        "\\ — обычная скорость\n"
        "A — пропорции картинки\n"
        "S — сохранить кадр\n"
        "L — список канала\n"
        "T — автоспроизведение: включать следующую серию или нет\n"
        "V — режим совместимости (старые видеокарты)\n"
        "F1 — эта справка"
    )

    def _saved_speed(self):
        """Скорость воспроизведения из настроек в допустимых пределах."""
        try:
            value = float(self._setting('speed', 1.0))
        except (TypeError, ValueError):
            value = 1.0
        if not config.SPEED_MIN <= value <= config.SPEED_MAX:
            value = 1.0
        return value

    def _settings_dict(self):
        return app_settings.of_backend(self.backend)

    def _setting(self, key, default=None):
        return self._settings_dict().get(key, default)

    def _saved_position(self, doc_id):
        """Сохранённая позиция просмотра ролика, если она есть."""
        try:
            positions = self.backend.positions
        except Exception:
            return 0.0
        if not isinstance(positions, dict):
            return 0.0
        try:
            return float(positions.get(doc_id) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _save_position(self):
        """Запоминает позицию текущего ролика для «продолжить»."""
        doc_id = self._current_doc_id
        if doc_id is None or self._closed:
            return
        try:
            pos = float(self._mpv_get('time-pos') or 0.0)
        except Exception:
            pos = 0.0
        # Ролик почти досмотрен — возвращаться в конец смысла нет.
        if self._duration > 0 and pos >= self._duration - 15:
            pos = 0.0
        try:
            self.backend.remember_position(doc_id, pos)
        except Exception:
            pass

    def _save_geometry(self):
        """Запоминает размер и место окна плеера (и PiP, если он открыт)."""
        settings = self._settings_dict()
        app_settings.save_window_geometry(self, 'player_geometry', settings)
        if self.pip_window.isVisible():
            app_settings.save_window_geometry(self.pip_window, 'pip_geometry', settings)

    def _change_speed(self, factor):
        """Меняет скорость воспроизведения и запоминает её."""
        try:
            speed = float(self.player['speed'] or 1.0) * factor
        except Exception:
            speed = 1.0
        speed = max(config.SPEED_MIN, min(config.SPEED_MAX, round(speed, 3)))
        try:
            self.player['speed'] = speed
        except Exception:
            return
        try:
            self.backend.set_setting('speed', speed)
        except Exception:
            pass
        self.show_osd(f"Скорость {speed:.2f}x")

    def _reset_speed(self):
        """Возвращает обычную скорость."""
        try:
            self.player['speed'] = 1.0
        except Exception:
            return
        try:
            self.backend.set_setting('speed', 1.0)
        except Exception:
            pass
        self.show_osd("Скорость 1.00x")

    def _screenshot(self):
        """Сохраняет текущий кадр рядом с программой."""
        name = 'shot_' + time.strftime('%Y%m%d_%H%M%S') + '.png'
        path = os.path.join(paths.app_dir(), name)
        try:
            self.player.command('screenshot-to-file', path, 'video')
        except Exception as ex:
            print(f"[Скриншот] {ex}")
            return
        self.show_osd(f"Кадр сохранён: {name}", 2500)

    def _cycle_aspect(self):
        """Перебирает пропорции картинки.

        Пригодится на роликах с неверным соотношением сторон: mpv растягивает
        кадр по окну, и «квадрат» или 4:3 возвращают картинку к норме.
        """
        # Начинаем с нуля, поэтому первое нажатие сразу даёт 16:9, а не
        # «по размеру окна» — иначе первый ход был бы пустым.
        self._aspect_idx = (getattr(self, '_aspect_idx', 0) + 1) % len(
            self.ASPECT_MODES)
        value, label = self.ASPECT_MODES[self._aspect_idx]
        try:
            self.player['video-aspect-override'] = value or 'no'
        except Exception:
            return
        self.show_osd(f"Пропорции: {label}")

    def _show_help(self):
        """Показывает список горячих клавиш поверх видео.

        Отдельным окном, а не QLabel поверх видео: картинку рисует mpv
        через WA_PaintOnScreen, и обычный дочерний виджет на ней не виден.
        """
        if self._help_window is not None:
            self._close_help()
            return
        win = QWidget(None, Qt.WindowType.FramelessWindowHint
                      | Qt.WindowType.WindowStaysOnTopHint
                      | Qt.WindowType.Tool)
        win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        # Фокус не забираем: иначе Esc и остальные клавиши перестанут
        # доходить до плеера, пока справка на экране.
        win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        win.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay = QVBoxLayout(win)
        lay.setContentsMargins(0, 0, 0, 0)
        label = QLabel(self.HELP_TEXT)
        label.setStyleSheet(
            "background: rgba(16,16,16,0.94); color: #eaeaea;"
            "border-radius: 12px; padding: 18px 26px; font-size: 13px;"
        )
        lay.addWidget(label)
        win.adjustSize()
        target = (self.pip_window if self.pip_window.isVisible()
                  else self.fs_window if self.fs_window.isVisible() else self)
        win.move(target.frameGeometry().center() - win.rect().center())
        win.installEventFilter(self)
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

    def play_video(self, index):
        if index < 0 or index >= len(self.playlist):
            return

        # Запоминаем, где остановились на уходящем ролике, пока
        # _current_doc_id ещё указывает на него.
        self._save_position()
        prev_doc_id = self._current_doc_id

        self.current_video_index = index
        self._play_ts = time.monotonic()
        self._speed_hist.clear()
        self._speed_bps = 0.0
        self._logged_play = False
        self._logged_video = False
        self._vdiag = {}
        self._eof_handled = False
        # Длительность нового ролика ещё неизвестна. Без сброса on_pos
        # сравнивал бы позицию нового видео с длительностью ПРЕДЫДУЩЕГО:
        # короткий прошлый ролик запускал ложный автопереход, и при нажатии
        # «назад» плеер тут же возвращался на ролик, с которого переключил.
        self._duration = 0.0
        # Позиция просмотра нужна, чтобы понимать, насколько загрузка ушла
        # вперёд, и вовремя начать качать следующий ролик.
        self._last_pos = 0.0
        self.bar.seek_slider.set_buffered_ranges([])
        self._click_timer.stop()

        # Поколение воспроизведения: старые корутины префетча должны
        # прекратиться, иначе они продолжат качать уже не нужный ролик.
        self._play_gen += 1
        gen = self._play_gen

        document = None
        next_doc = None
        try:
            document = self.playlist[index].media.document
        except Exception:
            document = None

        next_index = index + 1
        if next_index < len(self.playlist):
            try:
                next_doc = self.playlist[next_index].media.document
            except Exception:
                next_doc = None

        # Создаём кеш немедленно, не заставляя MPV ждать инициализации
        # backend при первом HTTP-запросе. Полное число воркеров — чтобы
        # докачать прелоад на полной скорости.
        if document is not None:
            self._current_doc_id = document.id
            # Ролик реально пошёл в плеере — отмечаем его просмотренным
            # и просим главное окно обновить пометки в списке.
            if self.backend.mark_watched(document.id):
                self.sig_watched.emit()
            self._next_doc_id = next_doc.id if next_doc is not None else None
            self.setWindowTitle(self._window_title(index))

            # Битрейт ролика: по нему видно, потянет ли его канал.
            try:
                attr = next(
                    (a for a in document.attributes
                     if isinstance(a, _telethon.DocumentAttributeVideo)),
                    None
                )
                dur = getattr(attr, 'duration', 0) if attr else 0
                self._cur_bitrate_mbps = (
                    document.size * 8 / dur / 1_000_000 if dur else 0.0
                )
            except Exception:
                self._cur_bitrate_mbps = 0.0
            self._warned_slow = False

            self.backend.submit_coroutine(
                self.backend.ensure_cache(
                    self.playlist[index], workers=config.PARALLEL_WORKERS,
                    resume=True)
            )
            self.show_osd(self._video_title(index), 1800)
            # Список канала подсвечивает тот ролик, который играет сейчас.
            self._sync_playlist_selection()
        else:
            self._current_doc_id = None
            self._next_doc_id = None

        # Освобождаем канал: ролик, который больше не играет и не является
        # следующим, не должен отбирать скорость у стартующего видео. Но
        # файлы не выбрасываем, а приостанавливаем: если пользователь
        # вернётся к ролику, докачка пойдёт с того же места.
        if (prev_doc_id is not None
                and prev_doc_id not in (self._current_doc_id, self._next_doc_id)):
            self.backend.pause_download(prev_doc_id)
        # Всё остальное, что осталось от прежних переключений, удаляем:
        # на каждом шаге остаётся ровно один приостановленный ролик, и без
        # этого недокачанные файлы копились бы на диске.
        keep = {i for i in (self._current_doc_id, self._next_doc_id,
                            prev_doc_id) if i is not None}
        self.backend.discard_other_downloads(keep)

        port = getattr(self.backend, 'stream_port', None) or config.STREAM_PORT
        url = (f"http://127.0.0.1:{port}/stream"
               f"?idx={index}&pl={self._playlist_token or ''}")

        # Позиция, с которой надо начать. Приоритет у явной перемотки
        # (_pending_seek: смена декодера, автолечение), иначе —
        # сохранённое место просмотра этого ролика.
        start_at = self._pending_seek
        self._pending_seek = None
        if not start_at and self._current_doc_id is not None:
            saved = self._saved_position(self._current_doc_id)
            if saved > 5:
                start_at = saved
                self.show_osd(
                    f"Продолжаю с {utils.format_time(saved * 1000)}", 2200)

        if start_at:
            self.player['start'] = f"{start_at:.3f}"
        else:
            self.player['start'] = 'none'

        # Снимаем паузу ПЕРЕД показом нового файла.
        #
        # mpv держит свойство pause между файлами. После того как ролик
        # дошёл до конца с выключенным автоспроизведением, mpv оставался
        # с pause=True; следующий файл запускался уже на паузе, и человек
        # видел замерший первый кадр. Кнопка на панели при этом показывала
        # «пауза» — то есть предлагала нажать, хотя видео и не играло.
        # Ставим до play(), чтобы mpv не успел показать кадр и встать.
        try:
            self.player.pause = False
        except Exception:
            pass

        self.player.play(url)
        if self.media is not None:
            # Кадр рисует QtMultimedia; mpv отдаёт его по постоянному
            # адресу вывода, поэтому источник Qt ставим один раз, а на
            # каждый новый ролик лишь запускаем показ.
            if self.media.source().isEmpty():
                self.media.setSource(QUrl.fromLocalFile('image://mpv'))
            self.media.play()

        # Значок на панели — сразу «пауза», потому что ролик пошёл.
        #
        # Иначе кнопка показывала состояние ОТ ПРОШЛОГО ролика. Событие
        # pause от mpv приходит не всегда и не сразу: при запуске нового
        # файла mpv мог не прислать его вовсе (свойство не изменилось —
        # оно и не было включено), и на только что запущенной серии
        # светился значок «play», хотя видео идёт. Человек жал кнопку,
        # думая, что это пауза, — и ставил ролик на паузу.
        self.bar.btn_play.setIcon(icons.get_icon('pause'))
        self.pip_bar.set_player_state(False)

        vw = self.video_out or self.video_widget
        print(f"[MPV] окно видео {vw.width()}x{vw.height()}")

        # Страховка: если видео-дорожка так и не определилась, перезапускаем
        # файл — это ровно то, что делает клавиша H.
        #
        # 1.2 с — как в сборке 18.10. Пробовали 3.5 с: проверка срабатывала
        # позже, и на переключении серии зритель успевал увидеть чёрный экран
        # дольше, чем нужно. Вторая попытка делает _verify_vo сам.
        QTimer.singleShot(1200, lambda: self._verify_vo(gen, 0))

        # Пока пользователь смотрит текущий ролик, тихо начинаем следующий.
        # Ждём не полной докачки текущего (на файлах в сотни мегабайт это
        # почти весь фильм, и следующий ролик стартовал с нуля), а запаса
        # впереди места просмотра: так префетч не отбирает полосу у только
        # что запущенного видео, но успевает подготовить следующий ролик
        # к его концу.
        if document is not None and next_doc is not None:
            async def _prefetch():
                while not self._closed and self._play_gen == gen:
                    cur = self.backend.cache.get(document.id)
                    if cur is None or cur.get('complete'):
                        break
                    if self._buffered_ahead_sec(cur) >= config.PREFETCH_AHEAD_SEC:
                        break
                    if (cur.get('downloaded_bytes', 0) >= config.PREFETCH_MIN_BYTES
                            and self._duration <= 0):
                        # Длительность ещё не пришла от mpv — считаем по
                        # объёму, иначе запас в секундах не посчитать.
                        break
                    await asyncio.sleep(0.5)
                if self._closed or self._play_gen != gen:
                    return
                try:
                    # Передаём сообщение следующего ролика, чтобы Telethon
                    # мог обновить истёкшую ссылку на файл.
                    await self.backend.ensure_cache(
                        self.playlist[next_index],
                        workers=config.PREFETCH_WORKERS,
                        resume=True
                    )
                except Exception as ex:
                    print(f"[Prefetch] {ex}")

            self.backend.submit_coroutine(_prefetch())

    def _buffered_ahead_sec(self, e):
        """Сколько секунд видео скачано впереди места просмотра.

        Именно впереди читателя, а не «сколько всего скачано»: загрузка
        может уйти в хвост за индексом moov, и общий объём тут соврёт.
        """
        try:
            duration = float(self._duration or 0.0)
            size = float(e.get('size') or 0)
            if duration <= 0 or size <= 0:
                return 0.0
            bytes_per_sec = size / duration
            played = min(float(self._last_pos or 0.0), duration)
            pos_byte = int(min(played * bytes_per_sec, size - 1))
            ahead = self.backend._available_from(e, pos_byte)
            return ahead / bytes_per_sec
        except Exception:
            return 0.0

    def on_pos(self, v):
        now = time.monotonic()
        # Позицию запоминаем ДО троттлинга: по ней считается запас загрузки
        # впереди, а он нужен и когда интерфейс не обновляется.
        try:
            self._last_pos = float(v or 0.0)
        except (TypeError, ValueError):
            pass

        # Резервный триггер автоперехода: если mpv не сообщил о конце файла
        # (свойство eof-reached на части сборок не срабатывает), переходим к
        # следующему ролику по достижении конца. Проверяем до троттлинга,
        # чтобы не пропустить момент.
        if (not self._eof_handled
                and self._duration > 0
                and v >= self._duration - 0.35
                and now - self._play_ts > 1.0):
            self._advance_to_next()

        if now - self._last_pos_ui < 0.25:
            return
        self._last_pos_ui = now
        bar = self.bar
        bar.label_position.setText(utils.format_time(v * 1000))
        if bar.seek_slider.isEnabled() and not bar.seek_slider.isSliderDown():
            bar.seek_slider.blockSignals(True)
            bar.seek_slider.setValue(int(v * 1000))
            bar.seek_slider.blockSignals(False)
        # Полоса в окне PiP идёт тем же шагом.
        if self.pip_window.isVisible():
            self.pip_bar.set_position(int(v * 1000),
                                      int((self._duration or 0) * 1000))

    def on_dur(self, v):
        self._duration = float(v or 0.0)
        bar = self.bar
        bar.label_duration.setText(utils.format_time(v * 1000))
        if v > 0:
            bar.seek_slider.setEnabled(True)
            bar.seek_slider.setRange(0, int(v * 1000))
        else:
            bar.seek_slider.setEnabled(False)
        if self.pip_window.isVisible():
            self.pip_bar.set_position(
                int((self._last_pos or 0) * 1000), int(v * 1000))

    def on_pause(self, p):
        self.bar.btn_play.setIcon(icons.get_icon('play' if p else 'pause'))
        self.pip_bar.set_player_state(bool(p))
        # На паузе панель PiP показываем и держим: человек остановился,
        # чтобы что-то сделать, и прятать от него кнопки незачем.
        if self.pip_window.isVisible():
            if p:
                self._pip_bar_timer.stop()
                self._show_pip_bar()
            else:
                self._restart_pip_bar_timer()

    def on_eof(self):
        # Защита от ложного срабатывания: сразу после старта (в том числе
        # после автолечения картинки, которое перезапускает файл) конец
        # файла не обрабатываем.
        if time.monotonic() - self._play_ts < 1.0:
            return
        self._advance_to_next()

    def _advance_to_next(self):
        """Переходит к следующему ролику ровно один раз на текущий.

        Только при включённом автоспроизведении: с выключенным ролик
        останавливается на последнем кадре, и человек сам решает, что
        смотреть дальше. Нажатия на «следующий» кнопкой это не касается —
        там переход делается напрямую.
        """
        if self._eof_handled:
            return
        self._eof_handled = True
        if not self._autoplay:
            self.show_osd("Конец ролика. Автоспроизведение выключено", 2500)
            return
        self.play_next()

    def _autoplay_clicked(self, event):
        """Нажатие по пилюле автоспроизведения.

        Метод подменяет mousePressEvent у самой пилюли: у неё нет
        сигналов, и так нажатие доходит до окна без лишних соединений.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_autoplay()
            event.accept()
            return
        widgets.AutoplaySwitch.mousePressEvent(self.bar.btn_auto, event)

    def toggle_autoplay(self):
        """Включает и выключает переход к следующему ролику."""
        self.set_autoplay(not self._autoplay)

    def set_autoplay(self, enabled):
        """Ставит автоспроизведение и запоминает выбор на будущее."""
        enabled = bool(enabled)
        if enabled == self._autoplay:
            return
        self._autoplay = enabled
        # Пилюля на панели показывает состояние сама, сигнала она не даёт.
        self._update_autoplay_button()
        self.show_osd("Автоспроизведение включено" if enabled
                      else "Автоспроизведение выключено", 2000)
        try:
            self.backend.set_setting('autoplay', enabled)
        except Exception:
            pass

    def _update_autoplay_button(self):
        """Показывает состояние автоспроизведения на панели."""
        switch = getattr(self.bar, 'btn_auto', None)
        if switch is None:
            return
        setter = getattr(switch, 'set_on', None)
        if setter is not None:
            setter(self._autoplay)

    def on_vol(self, v):
        self.bar.vol_slider.blockSignals(True)
        self.bar.vol_slider.setValue(int(v))
        self.bar.vol_slider.blockSignals(False)

    def on_mute(self, m):
        self.bar.btn_mute.setIcon(icons.get_icon('mute' if m else 'vol'))

    def on_vol_slider(self, value):
        try:
            self.player['volume'] = value
            self.show_osd(f"Громкость {int(value)}%")
        except Exception:
            pass
        # Громкость переживает перезапуск: иначе каждый запуск начинался
        # с 50%, хотя пользователь уже выставил своё.
        try:
            self.backend.set_setting('volume', int(value))
        except Exception:
            pass

    def on_seek_slider(self, ms):
        try:
            self.player.seek(ms / 1000.0, reference='absolute')
        except Exception:
            pass

    def play_next(self):
        if self.current_video_index + 1 < len(self.playlist):
            self.play_video(self.current_video_index + 1)
        else:
            self.show_osd("Конец списка")

    def play_prev(self):
        if self.current_video_index > 0:
            self.play_video(self.current_video_index - 1)

    def toggle_play_pause(self):
        try:
            self.player.pause = not self.player.pause
            self.show_osd("⏸ Пауза" if self.player.pause else "▶ Воспроизведение")
        except Exception:
            pass

    def _seek_step(self):
        """Шаг перемотки стрелками из настроек, в секундах.

        Значение проверяется при чтении: в файле настроек могло оказаться
        что угодно после ручной правки, а перемотка обязана оставаться
        предсказуемой.
        """
        try:
            step = int(app_settings.of_backend(self.backend).get(
                'seek_step', config.DEFAULT_SEEK_STEP))
        except Exception:
            return config.DEFAULT_SEEK_STEP
        return step if step in config.SEEK_STEPS else config.DEFAULT_SEEK_STEP

    def seek_rel(self, seconds):
        """Перематывает на seconds и показывает, где оказались.

        Позицию читаем ДО перемотки. Раньше её читали после: mpv успевает
        применить перемотку до чтения свойства, шаг прибавлялся второй раз,
        и на экране было «+20», когда на деле сдвинулись на 10. Ошибка
        копилась с каждым нажатием.

        Сначала показываем ожидаемое место — отклик должен быть
        мгновенным. Когда mpv доедет, подсказку заменяем настоящей
        позицией: ожидание может не совпасть с ней у границ ролика.
        """
        before = self._mpv_get('time-pos')
        try:
            self.player.seek(seconds, reference='relative')
        except Exception:
            return
        if before is None:
            # Ролик ещё не начался — показывать нечего.
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

        # Настоящее место подставим, когда mpv закончит перемотку.
        self._seek_osd_gen = getattr(self, '_seek_osd_gen', 0) + 1
        gen = self._seek_osd_gen
        QTimer.singleShot(
            250, lambda: self._confirm_seek_osd(gen, duration, seconds, 0))

    def _confirm_seek_osd(self, gen, duration, seconds, tries):
        """Заменяет подсказку настоящей позицией после перемотки.

        Ждём, пока mpv перестанет перематывать: сразу после команды
        свойство time-pos ещё показывает старое место.
        """
        # Пока ждали, пользователь нажал ещё раз — эта подсказка устарела.
        if gen != getattr(self, '_seek_osd_gen', 0):
            return
        try:
            if tries < 25 and self._mpv_get('seeking'):
                QTimer.singleShot(
                    120,
                    lambda: self._confirm_seek_osd(
                        gen, duration, seconds, tries + 1))
                return
            raw = self._mpv_get('time-pos')
            if raw is None:
                return
            self._show_seek_osd(float(raw), duration, seconds)
        except Exception:
            pass

    def _show_seek_osd(self, pos, duration, seconds=0):
        """Подсказка о перемотке: место и общая длительность."""
        arrow = '⏪' if seconds < 0 else '⏩'
        text = f"{arrow} {utils.format_time(pos * 1000)}"
        if duration > 0:
            text += f" / {utils.format_time(duration * 1000)}"
        self.show_osd(text, 1500)

    def toggle_mute(self):
        try:
            self.player.mute = not self.player.mute
            self.show_osd("Звук выкл." if self.player.mute else "Звук вкл.")
        except Exception:
            return
        try:
            self.backend.set_setting('mute', bool(self.player.mute))
        except Exception:
            pass

    def _mpv_get(self, name, default=None):
        """Читает свойство mpv.

        У python-mpv нет публичного player.get(): обращение к незнакомому
        атрибуту уходит в __getattr__ и превращается в чтение свойства с
        этим же именем. То есть player.get('time-pos') — это попытка
        прочитать свойство с именем «get», а не позицию воспроизведения, и
        она всегда падает с ошибкой.

        Команда 'get_property' тоже не годится: python-mpv гоняет
        command() через mpv_command_node, и libmpv отвечает на неё
        «Invalid value for mpv parameter» (-4) — проверено запуском.
        Раньше здесь стояла именно она, поэтому функция ВСЕГДА возвращала
        default, а _verify_vo был мёртвым кодом, PiP всегда делал окно
        16:9, а смена декодера перезапускала ролик с нуля.

        Рабочий путь — _get_property: через него работает обычное
        обращение player.volume, и он принимает имена со слэшем
        ('video-params/w'), которых атрибутом не выразить.
        """
        try:
            value = self.player._get_property(name)
        except Exception:
            return default
        # Свойство без значения (файл ещё не открыт) отдаёт None, а не
        # бросает исключение — подставляем default, как обещает сигнатура.
        return default if value is None else value

    def show_osd(self, text, timeout_ms=1200):
        """Показывает текст поверх видео.

        Когда кадр рисует mpv — надписи рисует его OSD (уровень 1
        согласован с osd_level конструктора). Когда кадр рисует Qt
        (один аккуратный кадр), надпись показывается поверх кадра в
        самом окне: OSD mpv там не видно.
        """
        if self.use_qt_video:
            self._show_window_osd(text, timeout_ms)
            return
        try:
            self.player.command('show-text', text, int(timeout_ms), 1)
        except Exception:
            pass

    def _show_window_osd(self, text, timeout_ms=1200):
        """Подсказка поверх кадра — обычная надпись в окне плеера."""
        try:
            if self._osd_label is None:
                label = QLabel(self.container)
                label.setObjectName('osdtext')
                label.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                label.setAttribute(
                    Qt.WidgetAttribute.WA_NoSystemBackground, True)
                label.setStyleSheet(
                    '#osdtext { background: rgba(10, 12, 16, 0.72);'
                    ' color: #eef2f6; border-radius: 10px;'
                    ' padding: 8px 16px; font-size: 16px; }')
                self._osd_label = label
                self._osd_timer = QTimer(self)
                self._osd_timer.setSingleShot(True)
                self._osd_timer.timeout.connect(self._hide_window_osd)
            self._osd_label.setText(text)
            self._osd_label.adjustSize()
            self._place_window_osd()
            self._osd_label.show()
            self._osd_label.raise_()
            self._osd_timer.start(max(300, int(timeout_ms)))
        except Exception:
            pass

    def _place_window_osd(self):
        label = self._osd_label
        host = self.container
        if label is None or host is None:
            return
        label.move(max(0, (host.width() - label.width()) // 2),
                   max(0, host.height() - label.height() - 24))

    def _hide_window_osd(self):
        if self._osd_label is not None:
            self._osd_label.hide()

    def _video_title(self, index):
        try:
            msg = self.playlist[index]
            t = (msg.text or "").replace("\n", " ").strip()[:60]
            return t or f"Видео #{msg.id}"
        except Exception:
            return "Медиаплеер"

    def _window_title(self, index):
        """Заголовок окна: ролик, канал и версия программы.

        Версия в заголовке нужна при разборе полётов: по скриншоту сразу
        видно, какая сборка запущена. Раньше различали по дате файла, и
        это регулярно путало — дата сборки ничего не говорит о том, что
        внутри.
        """
        title = self._video_title(index)
        channel = getattr(self.backend, 'active_dialog_title', '') or ''
        base = f"{title} — {channel}" if channel else title
        return f"{base}  [{version.version_text()}]"


    def _restore_bar_to_layout(self):
        """Возвращает панель управления в раскладку главного окна.

        В фуллскрине панель становится отдельным окном поверх экрана и
        получает фиксированную ширину под размер экрана. При выходе эту
        фиксацию надо снять: иначе панель остаётся шириной 760, а
        QVBoxLayout прижимает нерастяжимый виджет к левому краю — интерфейс
        съезжал вбок и больше не возвращался.
        """
        self.bar_timer.stop()
        self.bar.hide()
        # В раскладке окна панель прижата к нижнему и боковым краям:
        # скруглённые углы оставляли бы в них просветы до кадра.
        self.bar.set_rounded(False)
        self.bar.setMinimumWidth(0)
        self.bar.setMaximumWidth(16777215)
        self.bar.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.bar.setWindowFlags(Qt.WindowType.Widget)
        if self.video_row.indexOf(self.container) < 0:
            # Контейнер обязан идти первым, панель списка — второй: на этот
            # порядок опирается вся раскладка окна. Класть его в
            # root_layout нельзя — тогда видео уезжает под панель.
            self.video_row.insertWidget(0, self.container, 1)
        if self.root_layout.indexOf(self.bar) < 0:
            # Без выравнивания: с ним QVBoxLayout отдаёт панели только
            # sizeHint-ширину, и она снова выглядит узкой полосой.
            self.root_layout.addWidget(self.bar)
        self.container.show()
        self.bar.show()

    def toggle_fullscreen(self):
        # PiP и фуллскрин переносят один и тот же контейнер с видео,
        # поэтому вместе существовать не могут: сначала выходим из PiP,
        # и дальше всё идёт как обычно.
        if self.pip_window.isVisible():
            self._exit_pip()
        if self.use_qt_video:
            # Кадром владеет Qt — полный экран делаем в ТОМ ЖЕ окне.
            if self.is_fullscreen_one_window():
                self._exit_one_window_fullscreen()
            else:
                self._enter_one_window_fullscreen()
            return
        # Без QtMultimedia кадром владеет mpv, и вынести его в то же окно
        # наверх панели нельзя — остаётся прежний способ с отдельным
        # окном фуллскрина.
        if self.fs_window.isVisible():
            self._mouse_watch.stop()
            self.fs_window.hide()
            self.fs_window.takeCentralWidget()
            # Панель возвращается в раскладку — её отдельный набор клавиш
            # теперь совпал бы с набором окна плеера и заглушил его.
            self._setup_bar_shortcuts()
            self._restore_bar_to_layout()
        else:
            self.root_layout.removeWidget(self.bar)
            # Панель уходит из раскладки и становится отдельным окном.
            #
            # Прозрачность и флаги окна выставляем ДО setParent: Qt
            # запоминает их и создаёт нативное окно уже с ними. Если
            # поставить их позже, окно, созданное раньше как дочерний
            # виджет, в слоёное (WS_EX_LAYERED) не переходит — кадр под
            # подложкой не просвечивает. Именно поэтому прозрачность
            # появлялась только со второго входа в фуллскрин: к тому
            # времени окно создавалось заново уже с нужным флагом.
            self.bar.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True)
            # Панель показывается, не забирая активность: иначе при каждом
            # её появлении фокус уезжал с видео, и горячие клавиши
            # переставали работать, пока не щёлкнешь по кадру.
            self.bar.setAttribute(
                Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.bar.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool)
            self.bar.setParent(None)
            # Панель теперь своё окно — вешаем на него набор горячих
            # клавиш. В раскладке он намеренно не висел: там окно панели
            # совпадает с окном плеера, и второй набор глушил основной.
            self._attach_bar_shortcuts()
            self.video_row.removeWidget(self.container)
            self.fs_window.setCentralWidget(self.container)
            self.fs_window.showFullScreen()
            sg = self.fs_window.screen().geometry()
            w = min(760, sg.width() - 60)
            self.bar.setFixedWidth(w)
            # Здесь панель — отдельное окно, висящее над кадром с полями:
            # скругление уместно и показывает, что она ни к чему не
            # прикреплена. В оконном режиме его снимает
            # _restore_bar_to_layout.
            self.bar.set_rounded(True)
            self.bar.adjustSize()
            self.bar.move(sg.x() + (sg.width() - w) // 2, sg.y() + sg.height() - self.bar.height() - 36)
            self.bar.show()
            self.poke_bar()
            # Опрос положения мыши нужен только здесь: без него панель
            # появлялась лишь после того, как событие движения дойдёт до
            # Qt, и первое нажатие по кнопке уходило в видео.
            self._mouse_watch.start()

    # ------------------------------------------------------------------
    # Полный экран в одном окне (кадр через Qt)
    # ------------------------------------------------------------------
    def is_fullscreen_one_window(self):
        """Полный экран сейчас показан в самом окне плеера."""
        return bool(getattr(self, '_one_window_fs', False))

    def _enter_one_window_fullscreen(self):
        """Растягивает окно плеера на весь экран.

        Ничего не переносим и не создаём: это же окно растягивается,
        Windows снимает рамку и заголовок — в Alt+Tab всё время одно
        окно. Панель остаётся в раскладке окна внизу, как в оконном
        режиме: при показе и скрытии по таймеру меняется высота кадра,
        и mpv мгновенно пересчитывает картинку под новый размер.
        """
        if self._one_window_fs:
            return
        self._windowed_geometry = self.geometry()
        self._one_window_fs = True
        # Панель: скругление и прозрачная подложка, как ей положено в
        # полном экране. Размер не трогаем — вид панели прежний.
        self.bar.set_rounded(True)
        self.setWindowState(
            self.windowState() | Qt.WindowState.WindowFullScreen)
        self.show_fullscreen_controls()

    def _exit_one_window_fullscreen(self):
        """Возвращает окно плеера к прежнему размеру."""
        if not self._one_window_fs:
            return
        self._one_window_fs = False
        self.bar_timer.stop()
        self.bar.set_rounded(False)
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowFullScreen)
        if self._windowed_geometry is not None:
            self.setGeometry(self._windowed_geometry)
        self.bar.show()
        if self.video_out is not None:
            self.video_out.setCursor(Qt.CursorShape.ArrowCursor)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def show_fullscreen_controls(self):
        """Показывает панель и курсор в полном экране и запускает таймер."""
        self.poke_bar()
        if not self.player.pause:
            self.bar_timer.start(config.BAR_HIDE_MS)
        self.activateWindow()
        self.setFocus()
        self._mouse_watch.start()

    def _is_fullscreen_view(self):
        """Полный экран сейчас на экране — любым из двух способов."""
        return (self.fs_window.isVisible()
                or self.is_fullscreen_one_window())

    # ------------------------------------------------------------------
    # Картинка в картинке
    # ------------------------------------------------------------------
    def toggle_pip(self):
        """Включает и выключает маленькое окно поверх остальных окон."""
        if self.pip_window.isVisible():
            self._exit_pip()
        else:
            self._enter_pip()

    def _enter_pip(self):
        """Уводит видео в отдельное маленькое окно поверх всех окон."""
        # С фуллскрином не совмещается: оба переносят контейнер с видео.
        if self.fs_window.isVisible():
            self.toggle_fullscreen()

        self.video_row.removeWidget(self.container)
        self.pip_window.setCentralWidget(self.container)
        # Главное окно прячем: смысл режима — маленькая картинка и ничего
        # больше. Панель управления уезжает вместе с ним, но управление
        # остаётся: горячие клавиши продублированы на окно PiP.
        self.hide()
        self.pip_window.resize(*self._pip_size())
        # Панель вешаем на контейнер с видео, а не на окно: QMainWindow
        # рисует центральный виджет поверх детей окна, и панель на окне
        # оказывалась под картинкой — её не было видно.
        self.pip_bar.attach_to(self.container)
        self.pip_window.show()
        # Из сохранённого берём только место: размер всегда задают
        # пропорции ролика. Если сохранённых нет (или они с исчезнувшего
        # монитора) — ставим в правый нижний угол.
        if not app_settings.restore_window_position(
                self.pip_window, 'pip_geometry', self._settings_dict()):
            self._place_pip()
        self._show_pip_bar()
        self._update_pip_bar()
        self.pip_window.raise_()
        self.pip_window.activateWindow()
        self.show_osd("Картинка в картинке: двойной клик или Esc — выход",
                      2500)

    def _on_video_aspect(self, aspect):
        """Пришли пропорции нового ролика: подгоняем окно PiP.

        Сигнал приходит и в обычном окне, где подгонять нечего: там размер
        окна задаёт человек, а размер картинки — сам mpv.
        """
        if not self.pip_window.isVisible():
            return
        # Приходим из наблюдателя mpv, а размер окна меняем в потоке
        # интерфейса — иначе Qt ругается на виджеты из чужого потока.
        try:
            self._fit_pip_to_video()
            self._update_pip_bar()
        except Exception:
            pass

    def _fit_pip_to_video(self):
        """Подгоняет пропорции окна PiP под текущий ролик.

        Масштаб (во сколько раз окно больше исходной картинки) остаётся
        тем, каким его выставил человек или каким он был в прошлый раз, а
        пропорции берутся у ролика. Так вертикальный ролик после широкого
        показывает окно вертикальным, а не вбивает картинку в широкое окно
        с чёрными полосами по бокам.

        Сохранённую площадь окна переносим на новое соотношение сторон:
        при том же масштабе площадь остаётся прежней, а стороны
        пересчитываются под ролик.
        """
        if not self.pip_window.isVisible():
            return
        current = self.pip_window.size()
        if current.width() < 1 or current.height() < 1:
            return
        aspect = self._video_aspect()
        cur_aspect = current.width() / current.height()
        # Пропорции уже те — ничего не трогаем, иначе окно дёргалось бы
        # при каждом обращении.
        if abs(cur_aspect - aspect) < 0.02:
            return
        area = current.width() * current.height()
        width = int(round(math.sqrt(area * aspect)))
        height = int(round(math.sqrt(area / aspect)))
        width, height = max(120, width), max(120, height)
        top_left = self.pip_window.geometry().topLeft()
        self.pip_window.resize(width, height)
        # Место на экране держим прежним: окно, прыгающее в угол при
        # каждом переходе к следующему ролику, раздражает.
        self.pip_window.move(top_left)
        self.pip_bar.place_in_window()

    def _video_aspect(self):
        """Пропорции текущего видео, ограниченные разумными рамками."""
        aspect = config.PIP_DEFAULT_ASPECT
        try:
            value = self._mpv_get('video-params/aspect')
            if value:
                aspect = float(value)
        except Exception:
            pass
        # Совсем экзотические пропорции (панорамы, склейки) ограничиваем,
        # иначе окно выродилось бы в тонкую полоску.
        if not (1 / 3 <= aspect <= 3):
            aspect = config.PIP_DEFAULT_ASPECT
        return aspect

    def pip_action(self, name):
        """Действие по нажатию на значок или крестик в окне PiP.

        Панель рисует значки сама и сообщает сюда, что нажали. Так один
        виджет обходится без кнопок с их рамками и подсветкой.
        """
        if name == 'close':
            self._exit_pip()
        elif name == 'prev':
            self.play_prev()
        elif name == 'next':
            self.play_next()
        elif name == 'play':
            self.toggle_play_pause()
        # После действия значок паузы должен показать новое состояние.
        self._update_pip_bar()

    def pip_seek(self, ms):
        """Перемотка по полосе в окне PiP."""
        try:
            self.player.seek(max(0, ms) / 1000.0, reference='absolute')
        except Exception:
            pass
        self.show_osd(utils.format_time(ms), 800)

    def _show_pip_bar(self):
        """Показывает панель в PiP и ставит таймер на автоскрытие.

        Если панель уже на экране, только продлеваем таймер: повторный
        показ на каждое движение мыши пересобирал и поднимал виджеты
        заново, и панель заметно моргала.
        """
        if not self.pip_window.isVisible():
            return
        if not self.pip_bar.isVisible():
            self.pip_bar.show_all()
        self._pip_bar_shown_ts = time.monotonic()
        self._restart_pip_bar_timer()

    def _hide_pip_bar(self):
        """Убирает панель, если воспроизведение идёт.

        На паузе панель остаётся: человек явно остановился, чтобы что-то
        сделать, и прятать от него кнопки незачем.
        """
        if not self.pip_window.isVisible():
            return
        try:
            paused = bool(self.player.pause)
        except Exception:
            paused = True
        if paused:
            return
        # Панель теперь один виджет: значков, полосы и крестика врозь нет.
        if self.pip_bar.underMouse():
            self._restart_pip_bar_timer()
            return
        # Скрываем, только если панель ещё видна: вызов hide() на каждом
        # движении мыши заставлял окно перерисовываться и моргать.
        if self.pip_bar.isVisible():
            self.pip_bar.hide_all()

    def _restart_pip_bar_timer(self):
        if not self.pip_window.isVisible():
            return
        try:
            paused = bool(self.player.pause)
        except Exception:
            paused = True
        if paused:
            self._pip_bar_timer.stop()
        else:
            self._pip_bar_timer.start()

    def _update_pip_bar(self):
        """Переносит в панель PiP текущую позицию и вид кнопки паузы."""
        if not self.pip_window.isVisible():
            return
        try:
            pos = self._mpv_get('time-pos')
            dur = self._mpv_get('duration')
        except Exception:
            pos, dur = None, None
        ms = int((pos or 0) * 1000)
        total = int((dur or self._duration or 0) * 1000)
        self.pip_bar.set_position(ms, total)
        try:
            self.pip_bar.set_player_state(bool(self.player.pause))
        except Exception:
            pass

    def _pip_size(self):
        """Размер окна PiP при входе в режим.

        Сначала пробуем взять последний размер: человек мог растянуть окно
        мышью, и терять это неудобно. Если сохранённого нет, считаем по
        пропорциям ролика от длинной стороны config.PIP_LONG_SIDE — тогда чёрных
        полей вокруг картинки не остаётся.
        """
        aspect = self._video_aspect()
        saved = self._saved_pip_size()
        if saved is not None:
            # Сохранён масштаб, пропорции берём у ролика.
            area = saved[0] * saved[1]
            width = int(round(math.sqrt(area * aspect)))
            height = int(round(math.sqrt(area / aspect)))
            return max(120, width), max(120, height)
        if aspect >= 1.0:
            width = config.PIP_LONG_SIDE
            height = int(round(config.PIP_LONG_SIDE / aspect))
        else:
            width = int(round(config.PIP_LONG_SIDE * aspect))
            height = config.PIP_LONG_SIDE
        return max(120, width), max(120, height)

    def _saved_pip_size(self):
        """Последний размер окна PiP из настроек, или None.

        Размер хранится дробью сторон: при сохранении геометрии целиком
        вместе с ним сохранились бы и пропорции прошлого ролика, и
        вертикальный ролик открывался бы в широком окне.
        """
        value = self._setting('pip_size')
        try:
            if isinstance(value, (list, tuple)) and len(value) == 2:
                width = int(value[0])
                height = int(value[1])
                if width >= 120 and height >= 120:
                    return width, height
        except (TypeError, ValueError):
            pass
        return None

    def _remember_pip_size(self):
        """Запоминает масштаб окна PiP, чтобы он не терялся между сеансами.

        Храним стороны как есть, а не площадь: по ним видно и масштаб
        (он в произведении), и то, каким окно оставил человек.
        """
        if not self.pip_window.isVisible():
            return
        size = self.pip_window.size()
        if size.width() < 120 or size.height() < 120:
            return
        try:
            self.backend.set_setting('pip_size', [size.width(), size.height()])
        except Exception:
            pass

    def _place_pip(self):
        """Ставит окно PiP в правый нижний угол рабочего стола."""
        screen = self.pip_window.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.pip_window.move(
            area.x() + area.width() - self.pip_window.width() - config.PIP_MARGIN,
            area.y() + area.height() - self.pip_window.height() - config.PIP_MARGIN,
        )

    def _pip_resize_direction(self, pos):
        """Какой край окна PiP тянут по локальной позиции, или None.

        Возвращает (dx, dy), где -1/0/1 означают левый/нет/правый и
        верхний/нет/нижний край. Углы получаются сочетанием двух ненулевых
        значений. Позиция берётся в координатах окна PiP.
        """
        if not self.pip_window.isVisible():
            return None
        w = self.pip_window.width()
        h = self.pip_window.height()
        m = config.PIP_RESIZE_MARGIN
        dx = -1 if pos.x() < m else (1 if pos.x() > w - m else 0)
        dy = -1 if pos.y() < m else (1 if pos.y() > h - m else 0)
        if dx == 0 and dy == 0:
            return None
        return (dx, dy)

    def _pip_cursor_for(self, direction):
        """Курсор под направление ресайза; обычная стрелка, если его нет."""
        if direction is None:
            return Qt.CursorShape.ArrowCursor
        dx, dy = direction
        if dx == 0:
            return Qt.CursorShape.SizeVerCursor
        if dy == 0:
            return Qt.CursorShape.SizeHorCursor
        if (dx, dy) in ((-1, -1), (1, 1)):
            return Qt.CursorShape.SizeFDiagCursor
        return Qt.CursorShape.SizeBDiagCursor

    def _apply_pip_resize(self, global_pos):
        """Меняет размер окна PiP, пока его тянут за край.

        Геометрия считается от исходной на момент захвата, чтобы движение
        не накапливало ошибок округления. Минимальный размер ограничивает
        окно, чтобы его нельзя было сжать в невидимую точку.
        """
        if self._pip_resize is None or self._pip_resize_geom is None:
            return
        dx, dy = self._pip_resize
        delta = global_pos - self._pip_resize_start
        g = QRect(self._pip_resize_geom)

        if dx == -1:
            g.setLeft(g.left() + delta.x())
        elif dx == 1:
            g.setRight(g.right() + delta.x())
        if dy == -1:
            g.setTop(g.top() + delta.y())
        elif dy == 1:
            g.setBottom(g.bottom() + delta.y())

        # Минимальный размер: при растягивании левого/верхнего края влево
        # ширина/высота могла уйти в минус — правим нужный край.
        if g.width() < config.PIP_MIN_SIZE:
            if dx == -1:
                g.setLeft(g.right() - config.PIP_MIN_SIZE + 1)
            else:
                g.setRight(g.left() + config.PIP_MIN_SIZE - 1)
        if g.height() < config.PIP_MIN_SIZE:
            if dy == -1:
                g.setTop(g.bottom() - config.PIP_MIN_SIZE + 1)
            else:
                g.setBottom(g.top() + config.PIP_MIN_SIZE - 1)

        # Пропорции ролика держим при любом перетаскивании за край:
        # иначе в окне оставались бы чёрные полосы сверху или сбоку.
        #
        # За какую сторону ни тянули бы, вторая подстраивается. Тянем за
        # горизонтальный край — по новой ширине считаем высоту. За
        # вертикальный — наоборот. За угол — берём большее из двух
        # изменений, чтобы окно слушалось мыши.
        g = self._pip_keep_aspect(g, dx, dy)

        self.pip_window.setGeometry(g)

    def _pip_keep_aspect(self, g, dx, dy):
        """Возвращает прямоугольник с пропорциями текущего ролика.

        Неподвижным остаётся край, противоположный тому, который тянут:
        тянем правый — левый стоит на месте, тянем левый — стоит правый.
        Так окно слушается мыши и не уезжает в сторону.
        """
        aspect = self._video_aspect()
        if aspect <= 0:
            return g
        width, height = g.width(), g.height()
        if width <= 0 or height <= 0:
            return g

        if dy != 0 and dx == 0:
            # Тянут вертикальный край: высота задаёт ширину.
            width = int(round(height * aspect))
        elif dx != 0 and dy == 0:
            # Тянут горизонтальный край: ширина задаёт высоту.
            height = int(round(width / aspect))
        else:
            # Угол: считаем от той стороны, которая изменилась сильнее,
            # иначе окно отставало бы от мыши.
            want_w = int(round(height * aspect))
            want_h = int(round(width / aspect))
            if abs(want_w - width) <= abs(want_h - height):
                width = want_w
            else:
                height = want_h

        width = max(config.PIP_MIN_SIZE, width)
        height = max(config.PIP_MIN_SIZE, height)

        # Неподвижный край: если тянем левый — стоит правый, и наоборот.
        if dx == -1:
            left = g.right() - width + 1
        else:
            left = g.left()
        if dy == -1:
            top = g.bottom() - height + 1
        else:
            top = g.top()
        return QRect(left, top, width, height)

    def _exit_pip(self):
        """Возвращает видео в главное окно и снова показывает его."""
        if (not self.pip_window.isVisible()
                and self.container.parent() is not self.pip_window):
            return
        # Запоминаем, где пользователь оставил окно PiP и какого оно было
        # размера. Место берётся из геометрии, размер — отдельно: иначе
        # пропорции прошлого ролика вернулись бы при следующем входе.
        app_settings.save_window_geometry(
            self.pip_window, 'pip_geometry', self._settings_dict())
        self._remember_pip_size()
        self._pip_bar_timer.stop()
        self.pip_bar.hide_all()
        self.pip_window.hide()
        self.pip_window.takeCentralWidget()
        # container должен снова идти первым, панель — второй: так окно
        # собиралось при создании, и на этот порядок опирается выход из
        # фуллскрина, который перестраивает раскладку сам.
        self.video_row.insertWidget(0, self.container, 1)
        if self.root_layout.indexOf(self.bar) < 0:
            self.root_layout.addWidget(self.bar)
        self._pip_drag = None
        self._pip_moved = False
        self._pip_resize = None
        self._pip_resize_geom = None
        self._pip_resize_start = None
        self.show()
        self.raise_()
        self.activateWindow()

    def poke_bar(self):
        """Показывает панель управления и продлевает её жизнь."""
        if self._is_fullscreen_view():
            # show() на уже видимой панели заставлял бы окно лишний раз
            # перерисовываться — а вызывается это четыре раза в секунду.
            if not self.bar.isVisible():
                self.bar.show()
            if self.fs_window.cursor().shape() != Qt.CursorShape.ArrowCursor:
                self.fs_window.setCursor(Qt.CursorShape.ArrowCursor)
            if not self.player.pause:
                self.bar_timer.start(config.BAR_HIDE_MS)

    def _on_pause_changed(self, paused):
        """Паузу включили — сразу показать панель.

        Это не то же самое, что on_pause: тот обновляет вид панели, а
        здесь важно ПОЯВЛЕНИЕ. Раньше панель появлялась на паузе только
        через toggle_play_pause, то есть по пробелу или кнопке. А пауза
        бывает и не от них: mpv сам ставит её, когда кончились данные
        (cache-pause), при перемотке, при старте ролика. Тогда панель
        оставалась спрятанной, и видео «висело на паузе» без кнопок.
        """
        if paused and not self._closed and self._is_fullscreen_view():
            self.bar_timer.stop()
            if not self.bar.isVisible():
                self.poke_bar()

    def _mouse_over_tick(self):
        """Опрос MouseWatch: курсор над окном фуллскрина.

        Вызывается раз в config.MOUSE_POLL_MS, пока окно фуллскрина на
        экране. Сдвинулся курсор — панель показываем и жизнь ей
        продлеваем. Не сдвинулся — панель всё равно нужна, если её нет:
        на паузе и в начале ролика события мыши могут вовсе не дойти
        (мышь лежит неподвижно), а кнопки под курсором должны быть.
        Первое нажатие тогда попадёт в кнопку, а не в видео.

        Почему опрос, а не события: кадр в фуллскрине рисует mpv в его
        собственное окно, и события мыши над ним то приходят наведением,
        то не приходят вовсе — на настоящем плеере проверено, что
        при движении мыши окно не получает НИ ОДНОГО события. Опрос
        положения курсора от этого не зависит.
        """
        if not self._is_fullscreen_view():
            return
        pos = _cursor_pos_win32()
        if pos is None:
            return
        moved = pos != self._last_mouse_pos
        self._last_mouse_pos = pos
        if not moved:
            # Курсор не сдвинулся: жизнь стоящей панели по одному
            # опросу не продлеваем — она горит своим таймером. Но на
            # паузе и в самом начале ролика панель должна быть на
            # экране: событий мыши там может не быть вовсе.
            try:
                paused = bool(self.player.pause)
            except Exception:
                paused = False
            if (paused or self._last_pos < 0.5) and not self.bar.isVisible():
                self.poke_bar()
            return
        if not self.bar.isVisible():
            self.poke_bar()
        elif not self.player.pause:
            # Панель видна и мышь сдвинулась: жизнь ей продлеваем.
            # Стоящая панель по одному опросу жизнь не продлевает —
            # она горит своим таймером.
            self.bar_timer.start(config.BAR_HIDE_MS)

    def _hover_moved(self):
        """Мышь сдвинулась над кадром: запомнить и показать панель.

        Вызывается и из события наведения, и из опроса MouseWatch: кадр
        в фуллскрине — виджет-окно внутри окна, и Qt ведёт для него
        наведение (HoverMove) отдельно от MouseMove, а часть событий
        вообще не доходит. Положение курсора запоминаем ВСЕГДА, в любом
        режиме: по нему _mouse_moved отличает настоящее движение от
        событий, которые Qt шлёт само, без движения. А панель показываем
        только в фуллскрине и только при игре.
        """
        pos = _cursor_pos_win32()
        if pos is None:
            return
        if pos == self._last_mouse_pos:
            return
        self._last_mouse_pos = pos
        if self._is_fullscreen_view() and not self.player.pause:
            self.poke_bar()

    def _mouse_moved(self, event=None):
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
            # Спросить Windows не вышло — верим событию, если оно от
            # настоящего устройства: пусть лучше панель лишний раз
            # появится.
            if event is not None and event.spontaneous():
                self.poke_bar()
            return
        if pos == self._last_mouse_pos:
            return
        self._last_mouse_pos = pos
        self.poke_bar()

    def changeEvent(self, event):
        """Возврат в окно плеера.

        Смотрим состояние «окно активно» у главного окна — и когда плеер
        работает в фуллскрине, и когда это просто обычное окно. Alt+Tab
        обратно всегда активирует главное окно плеера, даже если видео
        висит в отдельном окне фуллскрина.
        """
        try:
            if event.type() == QEvent.Type.ActivationChange:
                if self.isActiveWindow():
                    self._on_player_activated()
            elif event.type() == QEvent.Type.WindowStateChange:
                # Полный экран в одном окне включали мы — но выключить
                # его могут и средствами Windows (Win+стрелка). Тогда
                # возвращаем панель на место, как при обычном выходе.
                if (self._one_window_fs
                        and not (self.windowState()
                                 & Qt.WindowState.WindowFullScreen)):
                    self._exit_one_window_fullscreen()
        except Exception:
            pass
        super().changeEvent(event)

    def _on_player_activated(self):
        """Человек вернулся в плеер — показываем управление заново.

        Пришли из другого окна по Alt+Tab. Панель к этому времени почти
        наверняка спрятана (пока смотрели в другое окно, она ушла по
        таймеру), курсор может стоять где угодно. Ждать движения мыши
        незачем: человек только что вернулся, и управление ему нужно
        сейчас.

        Справку по клавишам не трогаем: она висит поверх и живёт своей
        жизнью.
        """
        if not self._is_fullscreen_view():
            return
        if self.is_fullscreen_one_window():
            # Одно окно: Alt+Tab активирует его же — панель сразу на
            # месте, курсор вернуть в кадр.
            self.poke_bar()
            return
        # Опрос начинаем заново: пока курсор был в чужом окне, он вправе
        # был решить, что мышь ушла, и заглушить панель. Сброс заставляет
        # первый же шаг честно сказать, где курсор сейчас.
        self._mouse_watch.start()
        self.poke_bar()
        # Порядок важен: поднимаем окно видео, и только потом активируем
        # его. Если сначала активировать, панель — отдельное окно поверх —
        # остаётся активной, и клавиши уходят ей, а не видео. Так и было:
        # пробел и F не срабатывали, пока не щёлкнешь по кадру.
        self.fs_window.raise_()
        self._return_focus_to_video()

    def _return_focus_to_video(self):
        """Возвращает ввод окну с видео.

        Панель управления в фуллскрине — отдельное окно, и она легко
        становится активной: поднимается поверх видео, показывается при
        движении мыши, а недавно у неё появился WA_NativeWindow. Пока
        активна она, клавиши уходят ей: пробел не ставит паузу, F не
        убирает фуллскрин. Горячие клавиши на панели тоже есть, но
        поведение должно быть прежним — клавиши принадлежат плееру.
        """
        try:
            if self._is_fullscreen_view():
                self.fs_window.activateWindow()
                self.fs_window.setFocus()
            else:
                self.activateWindow()
        except Exception:
            pass

    def _on_mouse_over(self):
        """Опрос нашёл курсор над окном фуллскрина.

        Панели нет — показываем её, не дожидаясь движения. Иначе первое
        нажатие по кнопке досталось бы видео: панель к этому моменту ещё
        скрыта, и под курсором нет виджета, которому можно отдать нажатие.
        Во время воспроизведения она потом уйдёт по таймеру, как обычно:
        этот показ жизнь панели не продлевает, просто выбирает момент.
        """
        if not self._is_fullscreen_view():
            return
        if self.bar.isVisible():
            return
        if self.player.pause:
            # На паузе панель не прячется вовсе — она и так на экране.
            return
        self.poke_bar()

    def _on_mouse_left(self):
        """Курсор ушёл с окна фуллскрина — панель и курсор убираем сразу.

        Без этого панель висела поверх чужих окон, пока таймер не истечёт.
        """
        if not self._is_fullscreen_view():
            return
        self.bar_timer.stop()
        if self.bar.isVisible():
            self.bar.hide()
        target = (self if self.is_fullscreen_one_window()
                  else self.fs_window)
        target.setCursor(Qt.CursorShape.BlankCursor)

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
            if xy is None:
                return False
            where = QPoint(*xy)
            # where — уже экранная точка: панель, даже в фуллскрине,
            # живёт в экранных же координатах, поэтому сравниваем с её
            # прямоугольником на экране.
            return QRect(self.bar.mapToGlobal(QPoint(0, 0)),
                         self.bar.size()).contains(where)
        except Exception:
            return False

    def hide_bar_idle(self):
        """Прячет панель по таймеру бездействия.

        Кроме паузы, есть одна причина не прятать: курсор стоит над самой
        панелью. Раньше кнопки убирались ровно в тот миг, когда до них
        дотянулись, и целиться приходилось наугад.
        """
        if not self._is_fullscreen_view():
            return
        try:
            if self.player.pause:
                return
        except Exception:
            pass
        if self._cursor_over_bar():
            self.bar_timer.start(config.BAR_HIDE_MS)
            return
        self.bar.hide()
        target = (self if self.is_fullscreen_one_window()
                  else self.fs_window)
        target.setCursor(Qt.CursorShape.BlankCursor)

    def _playlist_resize_event(self, event):
        """Перетаскивание левого края панели списка: меняет её ширину.

        Событие приходит и от самой панели, и от списка внутри неё —
        поэтому координату приводим к системе панели, а не берём как
        есть: у дочернего виджета она своя.

        Курсор меняем только у панели, а не у всего окна: раньше стоял
        self.setCursor, и «две стрелки» оставались на всём плеере, в том
        числе над видео.

        Возвращает True, если событие поглощено: тогда клик по краю не
        считается кликом по ролику.
        """
        t = event.type()
        panel = self.playlist_panel

        def near_edge(pos):
            """Точка у левого края панели (в её координатах)."""
            try:
                in_panel = panel.mapFromGlobal(pos)
            except Exception:
                return False
            return 0 <= in_panel.x() <= config.PLAYLIST_RESIZE_MARGIN

        if t == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                if near_edge(event.globalPosition().toPoint()):
                    self._resizing_playlist = True
                    self._resize_start_x = int(event.globalPosition().x())
                    self._resize_start_w = panel.width()
                    panel.setCursor(Qt.CursorShape.SizeHorCursor)
                    return True

        elif t == QEvent.Type.MouseMove:
            if getattr(self, '_resizing_playlist', False):
                # Панель справа от видео: тянем влево — она шире.
                dx = self._resize_start_x - int(event.globalPosition().x())
                width = self._resize_start_w + dx
                width = max(config.PLAYLIST_MIN_WIDTH,
                            min(width, config.PLAYLIST_MAX_WIDTH))
                panel.setFixedWidth(width)
                return True
            # Курсор над краем — показываем, что тут можно тянуть.
            over = near_edge(event.globalPosition().toPoint())
            panel.setCursor(Qt.CursorShape.SizeHorCursor if over
                            else Qt.CursorShape.ArrowCursor)

        elif t == QEvent.Type.MouseButtonRelease:
            if getattr(self, '_resizing_playlist', False):
                self._resizing_playlist = False
                panel.setCursor(Qt.CursorShape.ArrowCursor)
                self._save_playlist_width()
                return True

        elif t == QEvent.Type.Leave:
            # Ушли с панели — возвращаем обычный курсор, иначе стрелки
            # оставались висеть до перезапуска.
            if not getattr(self, '_resizing_playlist', False):
                panel.setCursor(Qt.CursorShape.ArrowCursor)

        return False

    def eventFilter(self, obj, event):
        t = event.type()

        # Ширина панели списка в плеере: тянем за левый край.
        if obj is getattr(self, 'playlist_panel', None):
            handled = self._playlist_resize_event(event)
            if handled:
                return True

        # Окно PiP меняет размер или переезжает — панель следует за ним.
        # Панель отдельное окно, сама она за PiP не едет: без этого
        # значки оставались на прежнем месте и висели поверх чужих окон,
        # пока мышь не уходила с окна и не возвращалась.
        #
        # Обращаемся через getattr: фильтр живёт с самого создания окна,
        # а pip_window появляется позже. Без этого на каждый кадр сыпалось
        # «нет атрибута pip_window» — сотни строк ошибок в журнале.
        pip = getattr(self, 'pip_window', None)
        if pip is not None and obj is pip and t in (QEvent.Type.Resize,
                                                    QEvent.Type.Show,
                                                    QEvent.Type.Move):
            self.pip_bar.follow_window()

        # Справка по клавишам: клик по ней или закрытие убирают её.
        help_win = getattr(self, '_help_window', None)
        if help_win is not None and obj is help_win and t in (
                QEvent.Type.MouseButtonPress, QEvent.Type.Close):
            self._close_help()
            return True

        # Закрытие окна PiP (например, Alt+F4) не должно оставлять
        # приложение с одним скрытым главным окном — выходим из режима.
        if pip is not None and obj is pip and t == QEvent.Type.Close:
            self._exit_pip()
            return True

        # Alt+F4 по окну фуллскрина: закрывать его нельзя — контейнер с
        # видео остался бы в скрытом окне, а панель управления повисла бы
        # поверх экрана. Выходим из режима, само окно не закрываем.
        fs_win = getattr(self, 'fs_window', None)
        if fs_win is not None and obj is fs_win and t == QEvent.Type.Close:
            self._exit_fullscreen()
            event.ignore()
            return True

        # Мышь ушла с окна PiP — панель убирается. Это и есть «навёл
        # мышь — появилась, убрал — убралась».
        #
        # Появление и скрытие ведёт таймер, а Leave только ускоряет
        # скрытие. Сразу после входа в PiP окно сообщает Leave (мышь-то
        # ещё не над ним), и без этой проверки панель исчезала в тот же
        # миг, как появилась: значков не было видно вовсе.
        if (pip is not None and obj is pip and t == QEvent.Type.Leave
                and pip.isVisible()
                and self._pip_bar_shown_ts
                and time.monotonic() - self._pip_bar_shown_ts > 0.5):
            self._pip_bar_timer.stop()
            self.pip_bar.hide_all()
            return False

        # Мышь ушла с главного окна в фуллскрине: курсор возвращаем сразу,
        # не дожидаясь таймера. Опрос положения мыши делает то же самое,
        # но только когда окно теряет мышь надолго; Leave — точный сигнал
        # на случай, когда Qt его всё-таки доставил.
        if (obj is self and t == QEvent.Type.Leave
                and self._is_fullscreen_view()):
            self._on_mouse_left()

        # Наведение на кадр — тоже повод показать панель: в фуллскрине
        # кадр приходит часто именно наведением, а не MouseMove.
        if t == QEvent.Type.HoverMove:
            self._hover_moved()

        if t == QEvent.Type.MouseMove:
            # В PiP окно таскается за картинку: рамки, за которую его
            # можно было бы взять, у него нет.
            if self._pip_resize is not None and self.pip_window.isVisible():
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self._apply_pip_resize(event.globalPosition().toPoint())
                    # При растягивании за край панель должна ехать и
                    # менять размер вместе с окном.
                    self.pip_bar.follow_window()
                    self._pip_moved = True
                return True
            if self._pip_drag is not None and self.pip_window.isVisible():
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self.pip_window.move(
                        event.globalPosition().toPoint() - self._pip_drag)
                    # Панель — отдельное окно, сама за окном PiP она не
                    # поедет: без этого значки оставались на прежнем месте
                    # и висели поверх чужих окон, пока мышь не уходила с
                    # окна и не возвращалась.
                    self.pip_bar.follow_window()
                    self._pip_moved = True
                return True
            # Курсор над краем окна PiP показывает, что его можно тянуть.
            # Ставим его только при смене: setCursor на каждое движение
            # мыши заставляет окно перерисовывать курсор и заметно моргает.
            if self.pip_window.isVisible() and self._is_video_area(obj):
                local = self.pip_window.mapFromGlobal(
                    event.globalPosition().toPoint())
                want = self._pip_cursor_for(
                    self._pip_resize_direction(local))
                if want != self._pip_cursor:
                    self._pip_cursor = want
                    self.pip_window.setCursor(want)
            # В фуллскрине движение мыши снова показывает панель управления.
            # Именно настоящее движение: события без движения (панель
            # спряталась, виджет сменился) панель не будят, иначе она
            # всплывала бы сама и больше не пряталась. Движения над
            # самой панелью не продлевают ей жизнь — иначе она не уйдёт,
            # пока по ней водят курсором.
            if (self._is_fullscreen_view()
                    and not (obj is self.bar or obj.parent() is self.bar)):
                self._mouse_moved(event)
            # В PiP панель тоже появляется от движения мыши и уходит сама.
            if self.pip_window.isVisible():
                self._show_pip_bar()
        elif t == QEvent.Type.MouseButtonPress:
            # Возвращаем фокус окну, чтобы горячие клавиши работали и после
            # клика по области видео.
            self.setFocus()
            if self.fs_window.isVisible() and not self.player.pause:
                # Нажатие мыши — тоже «есть мышь», пусть даже она не
                # шевелилась: панель нужна человеку сейчас.
                self._last_mouse_pos = None
                self.poke_bar()
            if self._is_video_area(obj):
                if (self.pip_window.isVisible()
                        and event.button() == Qt.MouseButton.LeftButton):
                    local = self.pip_window.mapFromGlobal(
                        event.globalPosition().toPoint())
                    direction = self._pip_resize_direction(local)
                    if direction is not None:
                        # Захват за край = ресайз, а не перенос.
                        self._pip_resize = direction
                        self._pip_resize_geom = QRect(
                            self.pip_window.geometry())
                        self._pip_resize_start = (
                            event.globalPosition().toPoint())
                        self._pip_moved = False
                    else:
                        self._pip_drag = (
                            event.globalPosition().toPoint()
                            - self.pip_window.frameGeometry().topLeft()
                        )
                        self._pip_moved = False
                return True
        elif t == QEvent.Type.MouseButtonDblClick:
            if self._is_video_area(obj):
                self._click_timer.stop()
                self._ignore_next_release = True
                if self.pip_window.isVisible():
                    # Из PiP двойной клик возвращает обычное окно.
                    self._exit_pip()
                else:
                    self.toggle_fullscreen()
                # Событие поглощаем: container — родитель video_widget, и
                # фильтр стоит на обоих. Без этого Qt передавал бы событие
                # родителю, обработчик срабатывал второй раз, и фуллскрин
                # включался и тут же выключался — со стороны «не работает».
                return True
        elif t == QEvent.Type.MouseButtonRelease:
            if self._is_video_area(obj) and event.button() == Qt.MouseButton.LeftButton:
                if self._pip_drag is not None or self._pip_resize is not None:
                    # Окно таскали или меняли размер — это не клик, паузу
                    # не переключаем.
                    moved = self._pip_moved
                    self._pip_drag = None
                    self._pip_resize = None
                    self._pip_resize_geom = None
                    self._pip_resize_start = None
                    self._pip_moved = False
                    self._ignore_next_release = False
                    if not moved:
                        self._click_timer.start(self._double_click_interval())
                elif self._ignore_next_release:
                    self._ignore_next_release = False
                else:
                    self._click_timer.start(self._double_click_interval())
                # Тот же двойной проход: без поглощения второй вызов
                # взводил таймер одиночного клика, и видео вставало на
                # паузу после двойного клика.
                return True
        elif t == QEvent.Type.Wheel:
            if self._is_video_area(obj):
                delta = event.angleDelta().y()
                if delta != 0:
                    self._change_volume(5 if delta > 0 else -5)
                    return True
        return super().eventFilter(obj, event)

    def _is_video_area(self, obj):
        return obj in (self.video_widget, self.container)

    def _double_click_interval(self):
        try:
            return max(200, QApplication.styleHints().mouseDoubleClickInterval())
        except Exception:
            return 400

    def _setup_shortcuts(self):
        """Горячие клавиши через QShortcut (WindowShortcut).

        Работают независимо от того, какой дочерний виджет в фокусе;
        дублируются на окне плеера и на полноэкранном окне.
        """
        handlers = [
            ("Space", self.toggle_play_pause),
            # Шаг перемотки берётся из настроек в момент нажатия, поэтому
            # смена значения в окне настроек действует сразу.
            ("Left", lambda: self.seek_rel(-self._seek_step())),
            ("Right", lambda: self.seek_rel(self._seek_step())),
            ("Up", lambda: self._change_volume(5)),
            ("Down", lambda: self._change_volume(-5)),
            ("F", self.toggle_fullscreen),
            ("I", self.toggle_pip),
            ("Escape", self._exit_fullscreen),
            ("H", self.toggle_hwdec),
            # Дополнительные клавиши: в окне плеера нет полей ввода,
            # поэтому одиночные буквы ничему не мешают.
            ("M", self.toggle_mute),
            ("N", self.play_next),
            ("P", self.play_prev),
            # Скорость: [ медленнее, ] быстрее, \ — снова обычная.
            ("[", lambda: self._change_speed(1 / config.SPEED_STEP)),
            ("]", lambda: self._change_speed(config.SPEED_STEP)),
            ("\\", self._reset_speed),
            ("A", self._cycle_aspect),
            ("S", self._screenshot),
            ("L", self._toggle_playlist),
            # Автоспроизведение: та же кнопка, что на панели.
            ("T", self.toggle_autoplay),
            ("V", self.toggle_compat),
            ("F1", self._show_help),
        ]
        # Горячие клавиши вешаем на КАЖДОЕ окно, где может оказаться фокус
        # ввода, — ровно по одному набору на окно.
        #
        # Почему так, а не только на главное окно: панель управления в
        # фуллскрине — отдельное окно поверх видео, и активной бывает она.
        # WindowShortcut срабатывает лишь в активном окне, поэтому без
        # своего набора у панели клавиши там не работали вовсе.
        #
        # Почему по окнам, а не по виджетам: набор на панель и набор на
        # главное окно — это ОДНО и то же окно, пока панель лежит в
        # раскладке (обычный режим). Два одинаковых QShortcut в одном окне
        # Qt считает неоднозначной парой и не срабатывает ни один —
        # хоткеи пропадали в обычном окне. Поэтому окна собираем во
        # множество: по одному набору на каждое, без повторов.
        self._shortcut_handlers = handlers
        self._shortcuts = []
        windows = []
        for parent in (self, self.fs_window, self.pip_window):
            # Окно панели не перечисляем: пока она в раскладке, это окно
            # плеера (уже в списке), а в фуллскрине свой набор ей вешает
            # _setup_bar_shortcuts.
            if parent is None or parent in windows:
                continue
            windows.append(parent)
        for parent in windows:
            for seq, fn in handlers:
                sc = QShortcut(QKeySequence(seq), parent)
                sc.setContext(Qt.ShortcutContext.WindowShortcut)
                sc.activated.connect(fn)
                self._shortcuts.append(sc)

    def _setup_bar_shortcuts(self):
        """Снимает набор горячих клавиш с окна панели.

        Пока панель лежит в раскладке (обычный режим), её окно — это окно
        плеера, где набор уже висит. Второй точно такой же набор Qt считает
        неоднозначной парой и не срабатывает НИ ОДИН — из-за этого
        горячие клавиши пропадали в обычном окне. Свой набор панели вешает
        toggle_fullscreen, когда она становится отдельным окном.
        """
        for sc in getattr(self, '_bar_shortcuts', ()):
            try:
                sc.setEnabled(False)
                sc.deleteLater()
            except Exception:
                pass
        self._bar_shortcuts = []

    def _attach_bar_shortcuts(self):
        """Вешает набор горячих клавиш на окно панели.

        Нужно ровно тогда, когда панель стала отдельным окном: в фуллскрине
        она висит поверх видео, и фокус ввода бывает у неё. Пока панель в
        раскладке, второй набор только мешает — он глушит основной.
        """
        handlers = getattr(self, '_shortcut_handlers', None)
        if not handlers:
            return
        if getattr(self, '_bar_shortcuts', None):
            return
        self._bar_shortcuts = []
        for seq, fn in handlers:
            sc = QShortcut(QKeySequence(seq), self.bar)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(fn)
            self._bar_shortcuts.append(sc)

    def _exit_fullscreen(self):
        # Esc выводит из любого «особого» режима показа: сначала справка,
        # потом PiP, потом фуллскрин.
        if self._help_window is not None:
            self._close_help()
            return
        if self.pip_window.isVisible():
            self._exit_pip()
        elif self._is_fullscreen_view():
            self.toggle_fullscreen()

    def _verify_vo(self, gen, attempt):
        """Проверяет, что видео реально пошло, и чинит чёрный экран.

        Признак поломки — видео-дорожка не определилась (video-params/w
        пуст) или вывод не настроен. Тогда файл перезапускается: к этому
        моменту индекс в хвосте уже скачан, и дорожка находится. Это
        ровно то, что делает клавиша H вручную. Повторяем один раз.
        """
        if self._closed or self._play_gen != gen:
            return

        configured = self._mpv_get('vo-configured')
        vw = self._mpv_get('video-params/w')
        codec = self._mpv_get('video-codec')

        # Свойства не читаются — судить о состоянии видео нельзя, молчим.
        if configured is None and vw is None and codec is None:
            return

        # Картинка пошла: вывод настроен и у дорожки известна ширина.
        if configured and vw:
            return

        # Вывод ещё не настроен — на старте это нормально, файл только
        # начал качаться, и перезапуск здесь только навредил бы. Раньше
        # сюда попадали всегда: свойства не читались вовсе, поэтому
        # проверка молча выходила и на живом видео не срабатывала.
        if not configured:
            return

        print(f"[MPV] видео не пошло: vo-configured={configured}, "
              f"ширина={vw}, кодек={codec}")

        # Позицию читаем до развилки: в ветке «повтор не помог» она тоже
        # нужна, а раньше вычислялась ниже — и обращение к ней падало с
        # NameError прямо в лямбде, вызванной из C++. Такое исключение
        # PyQt6 завершает через qFatal, то есть мгновенным падением без
        # единого слова в логе.
        try:
            pos = float(self._mpv_get('time-pos') or 0.0)
        except Exception:
            pos = 0.0

        if attempt >= 1:
            print("[MPV] повтор не помог — видео-дорожка так и не найдена")
            # Последняя попытка: режим совместимости. На старых и встроенных
            # адаптерах аппаратный декодер и вывод через DirectX 11 отдают
            # чёрный кадр, не сообщая об ошибке, — остаётся программное
            # декодирование. Дальше уже ничего сделать нельзя.
            if self._enable_compat_mode(auto=True):
                self._pending_seek = pos if pos > 0.5 else None
                self.play_video(self.current_video_index)
            return

        print("[MPV] перезапускаю файл, чтобы перечитать индекс")
        self._pending_seek = pos if pos > 0.5 else None
        self.play_video(self.current_video_index)
        QTimer.singleShot(1500, lambda: self._verify_vo(self._play_gen, 1))

    def _auto_heal_picture(self):
        """Перезапускает текущий файл, если картинка так и не появилась.

        Режим декодера не трогаем: раньше здесь вызывался toggle_hwdec, и
        каждый ролик переключал режим на следующий — 1080p/4K уезжали в
        софт-декодирование без видимой причины. Перебор оставлен клавише H.
        """
        if self._closed or self.current_video_index is None:
            return
        # Картинка уже есть — лечить нечего.
        if self._mpv_get('video-params/w'):
            return
        try:
            pos = float(self._mpv_get('time-pos') or 0.0)
        except Exception:
            pos = 0.0
        print("[MPV] картинки нет — перезапускаю файл, чтобы перечитать индекс")
        self._pending_seek = pos if pos > 0.5 else None
        self.play_video(self.current_video_index)

    def toggle_hwdec(self):
        """Перебирает режимы декодирования по кругу.

        На части драйверов аппаратный декодер отдаёт чёрные кадры при
        живом звуке и при этом не сообщает об ошибке, поэтому mpv сам на
        софт не переключается. Порядок перебора задан config.HWDEC_MODES:
        'd3d11va-copy' (аппаратно с копированием, самый совместимый) ->
        'auto-safe' (аппаратно, как решит mpv) -> 'no' (софт, картинка
        гарантирована).
        """
        self._hwdec_idx = (self._hwdec_idx + 1) % len(config.HWDEC_MODES)
        self._apply_hwdec()

    def set_hwdec(self, index):
        """Ставит заданный режим декодирования.

        Нужен окну настроек: там выбирают конкретный режим, а не
        следующий по кругу.
        """
        index = max(0, min(len(config.HWDEC_MODES) - 1, int(index)))
        if index == self._hwdec_idx:
            return
        self._hwdec_idx = index
        self._apply_hwdec()

    def _apply_hwdec(self):
        """Применяет выбранный режим и перезапускает ролик с той же позиции."""
        mode, label = config.HWDEC_MODES[self._hwdec_idx]

        try:
            pos = float(self._mpv_get('time-pos') or 0.0)
        except Exception:
            pos = 0.0

        try:
            self.player['hwdec'] = mode
            if not self.use_qt_video:
                self.player['vo'] = config.VO_CHAIN
        except Exception:
            pass

        self.show_osd(f"Декодирование: {label}", 2500)
        try:
            self.backend.set_setting('hwdec', self._hwdec_idx)
        except Exception:
            pass

        # Смена декодера применяется только к заново открытому файлу,
        # поэтому перезапускаем текущее видео с той же позиции.
        if self.current_video_index is not None:
            self._pending_seek = pos if pos > 0.5 else None
            self.play_video(self.current_video_index)

    def _enable_compat_mode(self, auto=False):
        """Переводит вывод и декодер в самый совместимый режим.

        Возвращает True, если режим включился именно сейчас: вызывающий
        по этому признаку решает, перезапускать ли ролик.
        """
        if self._compat:
            return False
        self._compat = True
        try:
            self.backend.set_setting('compat', True)
        except Exception:
            pass
        # vo берём цепочкой: если 'gpu' не поднимется, mpv сам возьмёт 'sdl'.
        # В режиме Qt вывод — 'image' (кадры отдаются в QtMultimedia),
        # и его менять нельзя, иначе картинка пропадёт.
        vo = 'image' if self.use_qt_video else config.VO_CHAIN
        for name, value in (('vo', vo), ('hwdec', 'no'),
                            ('gpu-context', 'auto')):
            try:
                self.player[name] = value
            except Exception as ex:
                print(f"[MPV] режим совместимости: {name} не применился: {ex}")
        print("[MPV] включён режим совместимости (программное декодирование)")
        self.show_osd("Режим совместимости включён"
                      + (" автоматически" if auto else ""), 3000)
        return True

    def toggle_compat(self):
        """Включает и выключает режим совместимости для старых адаптеров."""
        if self._compat:
            self._compat = False
            try:
                self.backend.set_setting('compat', False)
            except Exception:
                pass
            vo = 'image' if self.use_qt_video else config.VO_CHAIN
            for name, value in (
                    ('hwdec', config.HWDEC_MODES[self._hwdec_idx][0]),
                    ('vo', vo)):
                try:
                    self.player[name] = value
                except Exception:
                    pass
            print("[MPV] режим совместимости выключен")
            self.show_osd("Режим совместимости выключен", 2500)
        else:
            self._enable_compat_mode()

        # Смена вывода и декодера применяется только к заново открытому
        # файлу, поэтому перезапускаем текущее видео с той же позиции.
        if self.current_video_index is not None:
            try:
                pos = float(self._mpv_get('time-pos') or 0.0)
            except Exception:
                pos = 0.0
            self._pending_seek = pos if pos > 0.5 else None
            self.play_video(self.current_video_index)

    def _change_volume(self, delta):
        try:
            v = float(self.player['volume'] or 0)
            v = max(0.0, min(100.0, v + delta))
            self.player['volume'] = v
            self.show_osd(f"Громкость {int(v)}%")
        except Exception:
            pass

    def closeEvent(self, event):
        # Позицию и историю сохраняем ДО флага _closed: _save_position по
        # нему понимает, что плеер закрывается, и молчит.
        self._save_position()
        try:
            self.backend.flush_history()
        except Exception:
            pass
        # Геометрию сохраняем без полного экрана: при закрытии прямо из
        # полного экрана сохранился бы и его размер, и в следующий раз
        # плеер открывался бы во весь экран.
        if self._one_window_fs:
            self._exit_one_window_fullscreen()
        self._save_geometry()

        self._closed = True
        self.progress_timer.stop()
        self.bar_timer.stop()
        self._close_help()
        # Кадр теперь может принадлежать QtMultimedia: закрываем и его,
        # иначе проигрыватель останется держать видео за собой.
        if self.media is not None:
            try:
                self.media.stop()
                self.media.setSource(QUrl())
            except Exception:
                pass
        # Окно PiP живёт отдельно от главного: без этого оно осталось бы
        # висеть на экране после закрытия плеера.
        try:
            self.pip_window.hide()
        except Exception:
            pass
        try:
            self.player.command('stop')
            self.player.terminate()
        except Exception:
            pass
        # Кэш текущего и следующего ролика. Загрузку останавливаем всегда,
        # а вот файлы удаляем только если удаление при выходе включено:
        # иначе на диске остаётся скачанное, и повторный запуск того же
        # ролика идёт сразу, без загрузки.
        clean = bool(self._setting('clean_cache_on_exit', True))
        for doc_id in (self._current_doc_id, self._next_doc_id):
            if doc_id is None:
                continue
            if clean:
                self.backend.discard_download(doc_id)
            else:
                self.backend.pause_download(doc_id)
        self.sig_closed.emit()
        super().closeEvent(event)

