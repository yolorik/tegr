"""Окно плеера: mpv, управление, перемотка, качество, клавиши.

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
    QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer,
    QCoreApplication, pyqtSignal)
from PyQt6.QtGui import (
    QAction, QActionGroup, QColor, QCursor, QKeySequence, QPainter, QPixmap,
    QShortcut)
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSplitter,
    QVBoxLayout, QWidget)

from ..core import config, history, log, paths, theme, utils
from ..core import settings as app_settings
from ..icons import icons
from ..system import mpv_boot
from ..widgets import widgets
from ..backend.backend import TelegramBackend
from ..system import telethon_boot as _telethon

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
    # Список качеств, подготовленный в потоке бэкенда (msg_id, варианты).
    sig_qualities = pyqtSignal(object, list)

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
        # Меню качества готовится в потоке бэкенда, а показать Qt-меню можно
        # только из потока окна: QTimer.singleShot в чужом потоке не
        # срабатывает, поэтому результат приходит сигналом.
        self.sig_qualities.connect(self._display_quality_menu)
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
        # скорость, автовыбор качества, размер окна.
        self._auto_quality = bool(self._setting('auto_quality'))
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
        self.root_layout = QVBoxLayout(central)
        self.root_layout.setContentsMargins(4, 4, 4, 4)

        self.container = widgets.PlayerContainer()
        self.bar = widgets.ControlBar()
        # Панель управления должна стоять на «слоях окна», а не «слоях
        # рабочего стола». С флагами по умолчанию она уходит в отдельный
        # слой, и там её прячут чужие окна — в фуллскрине её не было видно.
        #
        # Это же свойство делает честным getattr(w, 'bar', None) в
        # MouseWatch: панель приходит «не моим» окном, а по типу оказывается
        # такой же дочерью окна фуллскрина, как и полоса состояния.
        self.bar.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
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
        self.player = mpv_boot.mpv.MPV(
            wid=int(self.video_widget.winId()),
            vo=config.VO_CHAIN,
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
            # OSD рисует сам mpv, поэтому надписи "Буферизация/Громкость"
            # гарантированно видны поверх видео без проблем с наложением
            # Qt-виджетов. Уровень именно 1, а не 2: двойка заодно включает
            # автоматический показ времени при перемотке, который появлялся
            # слева сверху и дублировал счётчик в нижней панели. Свои
            # надписи плеер показывает через show-text с тем же уровнем,
            # так что они продолжают работать.
            osd_level=1,
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

            # ВАЖНО: зондирование оставлено по умолчанию. Урезание
            # probesize до 32 КБ ломало определение формата на больших
            # файлах с индексом moov в хвосте: ffmpeg не находил
            # видео-дорожку, и mpv играл только звук (чёрный экран).

            # Ошибки и предупреждения mpv должны быть видны: при 'all=no'
            # молчали и сбои декодера (чёрный экран при живом звуке).
            msg_level='all=warn',

            # Клавиатуру обрабатывает Qt, а не mpv.
            input_default_bindings='no',
            input_vo_keyboard='no',
        )

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
            self.fs_window, self._on_mouse_over, self._on_mouse_left)

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
        bar.btn_quality.clicked.connect(self.show_quality_menu)
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

            # Честная скорость: среднее по скользящему окну в несколько
            # секунд. Мгновенный замер бесполезен — данные приходят кусками
            # по 1 МБ, и точечное значение скачет 1.6 -> 0.4 на ровном канале.
            now = time.monotonic()
            net = e.get('speed_bytes', 0)
            self._speed_hist.append((now, net))
            while (len(self._speed_hist) >= 2
                   and now - self._speed_hist[0][0] > config.SPEED_WINDOW_SECS):
                self._speed_hist.popleft()
            if len(self._speed_hist) >= 2:
                t0, b0 = self._speed_hist[0]
                dt = now - t0
                if dt > 0.5:
                    self._speed_bps = max(0.0, (net - b0) / dt)

            # Раз в 10 секунд запоминаем, где остановились: даже если
            # программу закрыть аварийно, позиция останется.
            if now - self._last_pos_save > 10:
                self._last_pos_save = now
                self._save_position()

            if pct >= 100:
                self.bar.lbl_cache.setText("Кэш 100%")
            else:
                # Средняя за всю загрузку: показывает реальную скорость,
                # в отличие от мгновенной, которая скачет на всплесках.
                avg_bps = 0.0
                st = e.get('started_ts')
                if st:
                    elapsed = time.monotonic() - st
                    if elapsed > 1.0:
                        avg_bps = e.get('downloaded_bytes', 0) / elapsed
                avg_txt = f" · ср. {utils.fmt_speed(avg_bps)}" if avg_bps > 0 else ""
                self.bar.lbl_cache.setText(
                    f"Кэш {pct}% · {utils.fmt_speed(self._speed_bps)}{avg_txt}"
                )

            # Один раз на ролик проверяем, влезает ли битрейт в канал.
            # Иначе видео будет постоянно вставать на буферизацию.
            if (not self._warned_slow
                    and self._cur_bitrate_mbps > 0
                    and self._speed_bps > 0):
                speed_mbps = self._speed_bps * 8 / 1_000_000
                if self._cur_bitrate_mbps > speed_mbps * 1.2:
                    self._warned_slow = True

                    # Автовыбор: переключаем на вариант, который канал тянет.
                    if self._auto_quality:
                        try:
                            msg = self.playlist[self.current_video_index]
                            variant = self.backend.pick_quality_for_speed(
                                msg, speed_mbps * 0.85
                            )
                        except Exception:
                            variant = None

                        if (variant is not None
                                and variant['doc_id'] != self._current_doc_id):
                            self.show_osd(
                                f"Канал ~{speed_mbps:.1f} Мбит/с — "
                                f"переключаю на {variant['quality']}",
                                3500
                            )
                            self.play_selected_quality(variant['message'])
                            return

                    self.show_osd(
                        f"Канал ~{speed_mbps:.1f} Мбит/с, видео "
                        f"{self._cur_bitrate_mbps:.1f} Мбит/с — будут паузы. "
                        f"Кнопка «Качество»",
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
        panel.setFixedWidth(config.PLAYLIST_PANEL_WIDTH)
        panel.hide()
        return panel

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
        "Q — качество\n"
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
        # (_pending_seek: смена декодера, качества, автолечение), иначе —
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

        self.player.play(url)

        print(f"[MPV] окно видео {self.video_widget.width()}x"
              f"{self.video_widget.height()}")

        # Страховка: если видео-дорожка так и не определилась, перезапускаем
        # файл — это ровно то, что делает клавиша H.
        #
        # Ждём 3.5 с, а не 1.2: при переключении серии картинка в норме
        # появляется за 1.5-2 с (нужно скачать начало файла и хвост с
        # индексом). Проверка на 1.2 с успевала сработать раньше времени,
        # перезапускала ещё не разобравшийся ролик, отсчёт шёл заново —
        # и старт растягивался.
        QTimer.singleShot(3500, lambda: self._verify_vo(gen, 0))

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
        """Показывает текст поверх видео через OSD mpv (надёжно при WA_PaintOnScreen).

        Уровень 1 согласован с osd_level в конструкторе MPV: свои надписи
        видны, а автоматическое время перемотки от mpv — нет.
        """
        try:
            self.player.command('show-text', text, int(timeout_ms), 1)
        except Exception:
            pass

    def _video_title(self, index):
        try:
            msg = self.playlist[index]
            t = (msg.text or "").replace("\n", " ").strip()[:60]
            return t or f"Видео #{msg.id}"
        except Exception:
            return "Медиаплеер"

    def _window_title(self, index):
        """Заголовок окна: название ролика + канал, откуда он играет.

        По одному названию видео непонятно, из какого оно канала, а окно
        плеера часто висит поверх других — заголовок читается с ходу.
        """
        title = self._video_title(index)
        channel = getattr(self.backend, 'active_dialog_title', '') or ''
        return f"{title} — {channel}" if channel else title

    def show_quality_menu(self):
        if (self.current_video_index < 0
                or self.current_video_index >= len(self.playlist)):
            return
        message = self.playlist[self.current_video_index]
        self.backend.submit_coroutine(
            self._load_and_show_qualities(message)
        )

    async def _load_and_show_qualities(self, message):
        """Готовит качества в потоке бэкенда и отдаёт их сигналом.

        Раньше здесь стоял QTimer.singleShot(0, ...), но этот код идёт в
        потоке бэкенда, где нет работающего цикла событий Qt: таймер не
        срабатывал никогда, и меню качества просто не появлялось.
        """
        try:
            key = TelegramBackend._msg_key(message)
            qualities = await self.backend.get_video_qualities(message)
            if qualities:
                self.sig_qualities.emit(key, qualities)
        except Exception as e:
            print(f"[Качество] Ошибка: {e}")

    def _display_quality_menu(self, msg_key, qualities):
        if (self.current_video_index < 0
                or self.current_video_index >= len(self.playlist)):
            return
        # Ролик успели переключить, пока грузились качества — меню неактуально.
        if TelegramBackend._msg_key(
                self.playlist[self.current_video_index]) != msg_key:
            return
        menu = QMenu(self)
        current_doc_id = self.playlist[self.current_video_index].media.document.id

        # Переключатель автовыбора: при нехватке скорости плеер сам
        # перейдёт на вариант, который канал реально тянет.
        auto = menu.addAction("Автовыбор качества по скорости")
        auto.setCheckable(True)
        auto.setChecked(self._auto_quality)
        auto.toggled.connect(self._set_auto_quality)
        menu.addSeparator()

        # Ориентир: сколько Мбит/с реально тянет канал (по замеру загрузки).
        speed_mbps = (self._speed_bps * 8) / 1_000_000 if self._speed_bps > 0 else 0.0
        if speed_mbps > 0:
            head = menu.addAction(f"Канал: ~{speed_mbps:.1f} Мбит/с")
            head.setEnabled(False)
            menu.addSeparator()

        for q in qualities:
            size_mb = q['size']
            dur = q.get('duration') or 0
            bitrate = (size_mb * 8 / dur) if dur > 0 else 0.0

            label = f"{q['quality']} · {size_mb:.1f} МБ"
            if bitrate > 0:
                label += f" · {bitrate:.1f} Мбит/с"
                if speed_mbps > 0:
                    if bitrate <= speed_mbps * 0.85:
                        label += "   ✓ пойдёт"
                    else:
                        label += "   ⚠ будут паузы"

            if q['doc_id'] == current_doc_id:
                label = "▶ " + label

            action = menu.addAction(label)
            action.triggered.connect(
                lambda checked, msg=q['message']: self.play_selected_quality(msg)
            )

        btn_pos = self.bar.btn_quality.mapToGlobal(QPoint(0, 0))
        menu.exec(btn_pos)

    def play_selected_quality(self, message):
        """Переключает на выбранный вариант качества с той же позиции."""
        index = None
        for i, msg in enumerate(self.playlist):
            if msg.id == message.id:
                index = i
                break

        if index is None:
            # Вариант нашёлся не в списке канала (например, отдельным поиском
            # по истории сообщений). Раньше клик по нему молча ничего не
            # делал — подставляем его на место текущего ролика.
            index = self.current_video_index
            if index < 0 or index >= len(self.playlist):
                return
            self.playlist[index] = message
            try:
                self._playlist_token = self.backend.pin_playlist(self.playlist)
            except Exception:
                pass

        # Смена качества не должна начинать ролик с нуля.
        try:
            pos = float(self._mpv_get('time-pos') or 0.0)
        except Exception:
            pos = 0.0
        self._pending_seek = pos if pos > 0.5 else None
        self.play_video(index)

    def _set_auto_quality(self, enabled):
        self._auto_quality = bool(enabled)
        try:
            self.backend.set_setting('auto_quality', self._auto_quality)
        except Exception:
            pass
        self.show_osd(
            "Автовыбор качества: вкл" if enabled else "Автовыбор качества: выкл"
        )

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
            self.bar.adjustSize()
            self.bar.move(sg.x() + (sg.width() - w) // 2, sg.y() + sg.height() - self.bar.height() - 36)
            self.bar.show()
            self.poke_bar()
            # Опрос положения мыши нужен только здесь: без него панель
            # появлялась лишь после того, как событие движения дойдёт до
            # Qt, и первое нажатие по кнопке уходило в видео.
            self._mouse_watch.start()

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
        if self.fs_window.isVisible():
            # show() на уже видимой панели заставлял бы окно лишний раз
            # перерисовываться — а вызывается это четыре раза в секунду.
            if not self.bar.isVisible():
                self.bar.show()
            if self.fs_window.cursor().shape() != Qt.CursorShape.ArrowCursor:
                self.fs_window.setCursor(Qt.CursorShape.ArrowCursor)
            if not self.player.pause:
                self.bar_timer.start(config.BAR_HIDE_MS)

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
        if not self.fs_window.isVisible():
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
            if self.fs_window.isVisible():
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
        if not self.fs_window.isVisible():
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
        if not self.fs_window.isVisible():
            return
        self.bar_timer.stop()
        if self.bar.isVisible():
            self.bar.hide()
        self.fs_window.setCursor(Qt.CursorShape.BlankCursor)

    def _cursor_over_bar(self):
        """Курсор сейчас над панелью управления."""
        try:
            if not self.bar.isVisible():
                return False
            local = self._mouse_watch.local_pos()
            if local is None:
                return False
            # local_pos считает от левого верхнего угла окна фуллскрина,
            # а панель живёт в его же координатах: переводим в экранные и
            # смотрим прямоугольник панели на экране.
            base = self.fs_window.mapToGlobal(QPoint(0, 0))
            where = base + local
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
        if not self.fs_window.isVisible():
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
        self.fs_window.setCursor(Qt.CursorShape.BlankCursor)

    def eventFilter(self, obj, event):
        t = event.type()

        # Окно PiP меняет размер или переезжает — панель следует за ним.
        # Панель отдельное окно, сама она за PiP не едет: без этого
        # значки оставались на прежнем месте и висели поверх чужих окон,
        # пока мышь не уходила с окна и не возвращалась.
        if obj is self.pip_window and t in (QEvent.Type.Resize,
                                            QEvent.Type.Show,
                                            QEvent.Type.Move):
            self.pip_bar.follow_window()

        # Справка по клавишам: клик по ней или закрытие убирают её.
        if obj is self._help_window and t in (
                QEvent.Type.MouseButtonPress, QEvent.Type.Close):
            self._close_help()
            return True

        # Закрытие окна PiP (например, Alt+F4) не должно оставлять
        # приложение с одним скрытым главным окном — выходим из режима.
        if obj is self.pip_window and t == QEvent.Type.Close:
            self._exit_pip()
            return True

        # Alt+F4 по окну фуллскрина: закрывать его нельзя — контейнер с
        # видео остался бы в скрытом окне, а панель управления повисла бы
        # поверх экрана. Выходим из режима, само окно не закрываем.
        if obj is self.fs_window and t == QEvent.Type.Close:
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
        if (obj is self.pip_window and t == QEvent.Type.Leave
                and self.pip_window.isVisible()
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
                and self.fs_window.isVisible()):
            self._on_mouse_left()

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
            # В фулскрине движение мыши снова показывает панель управления.
            if self.fs_window.isVisible():
                self.poke_bar()
            # В PiP панель тоже появляется от движения мыши и уходит сама.
            if self.pip_window.isVisible():
                self._show_pip_bar()
        elif t == QEvent.Type.MouseButtonPress:
            # Возвращаем фокус окну, чтобы горячие клавиши работали и после
            # клика по области видео.
            self.setFocus()
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
            ("Q", self.show_quality_menu),
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
        elif self.fs_window.isVisible():
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
        for name, value in (('vo', config.VO_CHAIN), ('hwdec', 'no'),
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
            for name, value in (('hwdec', config.HWDEC_MODES[self._hwdec_idx][0]),
                                ('vo', config.VO_CHAIN)):
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
        self._save_geometry()

        self._closed = True
        self.progress_timer.stop()
        self.bar_timer.stop()
        self._close_help()
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

