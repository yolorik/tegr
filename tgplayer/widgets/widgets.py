"""Виджеты плеера: ползунки, панель управления и маленькое окно PiP.

Здесь всё, что рисуется поверх видео и подчиняется человеку мышью:
перемотка с отметками скачанного, кнопки панели, переключатель
автоспроизведения, панель PiP и наблюдатель за положением курсора.

Окна (PlayerWindow, MainWindow) живут выше, в модуле player и windows:
эти виджеты не знают, кто ими управляет, и потому их можно проверять по
отдельности.
"""
import ctypes
import ctypes.wintypes
import time

from PyQt6.QtCore import (
    QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt,
    QTimer)
from PyQt6.QtGui import (
    QColor, QCursor, QIcon, QPainter, QPen, QPixmap, QPolygonF, QShortcut,
    QKeySequence)
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
    QWidget)

from ..core import config, theme, utils
from ..icons import icons

class ClickableSlider(QSlider):
    def _pos_to_value(self, x):
        """Переводит координату клика в значение ползунка."""
        span = self.maximum() - self.minimum()
        if span <= 0:
            return self.minimum()
        ratio = max(0.0, min(1.0, x / max(1, self.width())))
        return int(self.minimum() + span * ratio)

    def mousePressEvent(self, event):
        """Перематывает сразу в точку клика, а не на шаг ползунка."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self.orientation() == Qt.Orientation.Horizontal:
                val = self._pos_to_value(event.position().x())
                val = max(self.minimum(), min(self.maximum(), val))
                self.setValue(val)
                self.sliderMoved.emit(val)
        super().mousePressEvent(event)


class SeekSlider(ClickableSlider):
    """Полоса прокрутки с индикатором скачанного, как на YouTube.

    Серый фон — весь ролик, светлые участки — то, что уже скачано и
    можно смотреть, синяя полоса — просмотренное. Участков может быть
    несколько: при перемотке куски качаются вразнобой, и честнее
    показать каждый, чем одну общую долю.
    """

    TRACK_H = 4
    HANDLE_D = 12

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._buffered = []
        self.setMinimumHeight(self.HANDLE_D + 6)

    def set_buffered_ranges(self, ranges):
        """ranges: список пар (доля_начала, доля_конца) в диапазоне 0..1."""
        norm = []
        for a, b in ranges or ():
            a = max(0.0, min(1.0, float(a)))
            b = max(0.0, min(1.0, float(b)))
            if b > a:
                norm.append((a, b))
        if norm != self._buffered:
            self._buffered = norm
            self.update()

    def _ratio(self):
        span = self.maximum() - self.minimum()
        if span <= 0:
            return 0.0
        return (self.value() - self.minimum()) / span

    def _pos_to_value(self, x):
        """Клик по полосе переводит в позицию с учётом отступа ползунка.

        Дорожка нарисована с отступом HANDLE_D/2 слева и справа, поэтому
        без этой поправки клик у края уезжал на несколько процентов от
        того места, куда попали.
        """
        span = self.maximum() - self.minimum()
        if span <= 0:
            return self.minimum()
        left = self.HANDLE_D / 2
        width = max(1.0, self.width() - self.HANDLE_D)
        ratio = max(0.0, min(1.0, (x - left) / width))
        return int(self.minimum() + span * ratio)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Фон НЕ трогаем: дорожка рисуется поверх того, что уже есть в
        # окне. Попытка залить его прозрачным через CompositionMode_Source
        # стирала пиксели, а виджет без WA_TranslucentBackground показывал
        # на их месте чёрное.
        p.setPen(Qt.PenStyle.NoPen)

        hd = self.HANDLE_D
        left = hd / 2
        width = max(1.0, self.width() - hd)
        y = (self.height() - self.TRACK_H) / 2
        r = self.TRACK_H / 2

        # Фон — весь ролик.
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawRoundedRect(QRectF(left, y, width, self.TRACK_H), r, r)

        # Скачанные участки.
        p.setBrush(QColor(255, 255, 255, 125))
        for a, b in self._buffered:
            x = left + width * a
            w = max(self.TRACK_H, width * (b - a))
            p.drawRoundedRect(QRectF(x, y, w, self.TRACK_H), r, r)

        # Просмотренная часть.
        ratio = max(0.0, min(1.0, self._ratio()))
        if ratio > 0:
            w = max(self.TRACK_H, width * ratio)
            p.setBrush(QColor(theme.theme()['accent']))
            p.drawRoundedRect(QRectF(left, y, w, self.TRACK_H), r, r)

        # Ползунок.
        hx = left + width * ratio
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(
            QRectF(hx - hd / 2, (self.height() - hd) / 2, hd, hd)
        )
        p.end()


class DownloadTracker:
    """Учёт скачанных участков файла.

    Хранит список непрерывных диапазонов (start, end) и умеет отвечать,
    сколько байт подряд доступно начиная с позиции — именно это нужно
    HTTP-серверу, чтобы отдать mpv ровно то, что уже лежит на диске.
    """
    def __init__(self, size):
        self.size = size
        self.ranges = []

    def add_range(self, start, end):
        """Добавляет участок и сливает его с соседними диапазонами."""
        if start >= self.size:
            return
        end = min(end, self.size - 1)
        if start > end:
            return

        new_ranges = []
        inserted = False
        cur_start, cur_end = start, end

        for r_start, r_end in self.ranges:
            if cur_end < r_start - 1:
                if not inserted:
                    new_ranges.append((cur_start, cur_end))
                    inserted = True
                new_ranges.append((r_start, r_end))
            elif cur_start > r_end + 1:
                new_ranges.append((r_start, r_end))
            else:
                cur_start = min(cur_start, r_start)
                cur_end = max(cur_end, r_end)

        if not inserted:
            new_ranges.append((cur_start, cur_end))
        self.ranges = new_ranges

    def available_bytes_from(self, start):
        """Сколько байт подряд доступно начиная с позиции start."""
        for r_start, r_end in self.ranges:
            if r_start <= start <= r_end:
                return r_end - start + 1
        return 0


def _open_read(path):
    """Открывает файл кеша на чтение БЕЗ буфера.

    Буферизованный читатель наполняет буфер целиком (1 МБ), а .part заранее
    заполнен нулями через truncate. Пока кусок не скачан, в буфер попадают
    нули, и последующий seek внутрь уже набранного буфера их НЕ перечитывает:
    mpv получал нули вместо видео. Без буфера каждое чтение идёт с диска.
    """
    return open(path, 'rb', buffering=0)


def _piece_ranges(size):
    """Разбивает файл на куски для скачивания.

    Начало файла (config.BOOTSTRAP_BYTES) нарезается мелкими кусками config.BOOTSTRAP_PIECE,
    чтобы все воркеры параллельно качали именно начало. Середина — крупными
    сегментами config.SEG_SIZE, ради пропускной способности.

    Хвост (config.TAIL_BYTES) снова нарезается мелко: там обычно лежит индекс moov,
    и mpv читает его с конца файла. При одном крупном последнем куске самые
    нужные байты приходили бы последними.
    """
    pieces = []
    tail_start = max(config.BOOTSTRAP_BYTES, size - config.TAIL_BYTES)
    start = 0
    while start < size:
        if start < config.BOOTSTRAP_BYTES:
            length = min(config.BOOTSTRAP_PIECE, size - start, config.BOOTSTRAP_BYTES - start)
        elif start >= tail_start:
            length = min(config.TAIL_PIECE, size - start)
        else:
            length = min(config.SEG_SIZE, size - start, tail_start - start)
        pieces.append((start, length))
        start += length
    return pieces


# ==========================================================
# ИКОНКИ
# ==========================================================
_ICON_CACHE = {}


class AutoplaySwitch(QWidget):
    """Переключатель автоспроизведения — пилюля с ползунком, как на YouTube.

    Кнопка-переключатель с подсветкой здесь читалась плохо: панель лежит
    поверх видео и полупрозрачная, а значок с точками мало кто узнаёт.
    Пилюля говорит сама за себя — сдвинутый вправо белый кружок значит
    «включено», сдвинутый влево и приглушённый — «выключено».

    Цвета берутся у темы: включённый ползунок — акцентный, выключенный —
    приглушённый белый. Оформление системным стилем не занимаемся: панель
    без рамок, и любая рамка кнопки тут выглядела бы чужой.
    """

    WIDTH = 40
    HEIGHT = 20
    KNOB = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = True
        self._hover = False
        self.setObjectName('autoplayswitch')
        # Прозрачный фон: под пилюлей видно кадр, как и у всей панели.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setToolTip("Автоспроизведение включено — выключить")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def is_on(self):
        return self._on

    def set_on(self, on):
        """Ставит положение, не сообщая об этом наружу.

        Сигнала clicked у пилюли нет нарочно: панель только показывает
        состояние, а что делать с ним, решает окно плеера — оно же
        хранит настройку.
        """
        on = bool(on)
        if on != self._on:
            self._on = on
            self._hover = False
            self.setToolTip("Автоспроизведение включено — выключить" if on
                            else "Автоспроизведение выключено — включить")
            self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        # Пилюля нажимается только левой кнопкой и только по себе: у
        # панели поверх видео промах мимо кнопки не должен уходить
        # в паузу по видео.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        r = h / 2

        try:
            accent = QColor(theme.theme()['accent'])
        except Exception:
            accent = QColor('#5288c1')

        # Дорожка: включено — акцентная, выключено — приглушённая белая.
        track = QColor(accent) if self._on else QColor(255, 255, 255, 46)
        if self._hover:
            track = track.lighter(118) if self._on else QColor(255, 255, 255, 70)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)

        # Кружок: справа — включено, слева — выключено.
        d = self.KNOB
        margin = (h - d) / 2
        x = (w - d - margin) if self._on else margin
        p.setBrush(QColor(255, 255, 255)
                   if self._on else QColor(255, 255, 255, 190))
        p.drawEllipse(QRectF(x, margin, d, d))
        p.end()


class ControlBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("controlbar")
        # Полупрозрачную подложку рисуем сами, в paintEvent.
        #
        # Штатный путь — background в таблице стилей — для обычного
        # QWidget не работает: QWidget рисует фон только при
        # WA_StyledBackground, а он по умолчанию снят. Из-за этого
        # правило «#controlbar { background: rgba(...) }» существовало, но
        # не закрашивало ничего: значки и текст лежали прямо на видео, и
        # на светлом кадре панели не было видно совсем, а на тёмном она
        # сливалась с видео в сплошной чёрный прямоугольник.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Фон под панелью видео не портим: QWidget с прозрачной для mouse
        # областью не нужен, а вот авто-заливка мешала бы скруглениям.
        self.setAutoFillBackground(False)
        # Общее правило стиля «QWidget { background: ... }» красит фон
        # каждому виджету без своего правила — и для панели это оборачивалось
        # непрозрачной плашкой цвета темы поверх подложки. Своим
        # прозрачным фоном и скруглением отбиваем его: подложку рисует
        # paintEvent, и она должна быть видна.
        self.setStyleSheet(
            'QWidget#controlbar { background: transparent; }')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(4)

        row1 = QHBoxLayout()
        self.label_position = QLabel("00:00")
        self.seek_slider = SeekSlider()
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setEnabled(False)
        self.label_duration = QLabel("00:00")
        row1.addWidget(self.label_position)
        row1.addWidget(self.seek_slider, 1)
        row1.addWidget(self.label_duration)
        outer.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(2)
        self.btn_prev = QPushButton()
        self.btn_rew = QPushButton()
        self.btn_play = QPushButton()
        self.btn_ff = QPushButton()
        self.btn_next = QPushButton()
        self.btn_mute = QPushButton()
        self.vol_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.btn_pip = QPushButton()
        self.btn_fs = QPushButton()
        self.btn_list = QPushButton()
        # Автоспроизведение: переход к следующей серии сам, когда эта
        # кончилась. Пилюля с ползунком — по ней сразу видно состояние.
        self.btn_auto = AutoplaySwitch()

        self.btn_prev.setIcon(icons.get_icon('prev'))
        self.btn_rew.setIcon(icons.get_icon('rew'))
        self.btn_play.setIcon(icons.get_icon('play'))
        self.btn_ff.setIcon(icons.get_icon('ff'))
        self.btn_next.setIcon(icons.get_icon('next'))
        self.btn_mute.setIcon(icons.get_icon('vol'))
        self.btn_pip.setIcon(icons.get_icon('pip'))
        self.btn_fs.setIcon(icons.get_icon('fs'))
        self.btn_list.setIcon(icons.get_icon('list'))

        self.btn_pip.setToolTip("Картинка в картинке (I)")
        self.btn_list.setToolTip("Список канала (L)")

        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(config.DEFAULT_VOLUME)
        self.vol_slider.setFixedWidth(90)

        for b in (self.btn_prev, self.btn_rew, self.btn_play,
                  self.btn_ff, self.btn_next, self.btn_mute,
                  self.btn_pip, self.btn_fs, self.btn_list):
            b.setFixedSize(34, 30)
            b.setIconSize(QSize(20, 20))

        # О кэше — между кнопками управления и громкостью, по центру
        # свободного места. Полосы под этим нет: скачанные участки и так
        # видны засечками на полосе прокрутки.
        self.lbl_cache = QLabel("Загрузка: 0%")
        self.lbl_cache.setAlignment(Qt.AlignmentFlag.AlignCenter)

        row2.addWidget(self.btn_prev)
        row2.addWidget(self.btn_rew)
        row2.addWidget(self.btn_play)
        row2.addWidget(self.btn_ff)
        row2.addWidget(self.btn_next)
        row2.addWidget(self.btn_list)
        row2.addStretch()
        row2.addWidget(self.lbl_cache)
        row2.addStretch()
        # Автоспроизведение — рядом с громкостью, слева от неё. Кнопки
        # выбора качества больше нет: в Telegram у видео почти всегда одно
        # качество, и меню из одного пункта только мешает.
        row2.addWidget(self.btn_auto)
        row2.addWidget(self.btn_mute)
        row2.addWidget(self.vol_slider)
        row2.addWidget(self.btn_pip)
        row2.addWidget(self.btn_fs)
        outer.addLayout(row2)

    def paintEvent(self, event):
        """Полупрозрачная подложка под панелью.

        Рисуем её здесь, а не правилом background в таблице стилей:
        у обычного QWidget фон из стиля не закрашивается без
        WA_StyledBackground, а полагаться на него при пересборке стиля на
        лету (смена темы) рискованно. Здесь результат один и тот же в
        любом режиме — и в раскладке окна, и отдельным окном поверх видео.

        Цвет берётся у темы, доля непрозрачности — config.PANEL_BG_ALPHA. Кадр
        сквозь подложку виден, но надписи и ползунки остаются разборчивы.
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        try:
            base = QColor(theme.theme()['bg_deep'])
        except Exception:
            base = QColor('#0b0f14')
        base.setAlphaF(config.PANEL_BG_ALPHA)
        p.setBrush(base)
        radius = 12
        # Скруглённый прямоугольник вписан в rect: QRectF(self.rect())
        # тянется от 0 до width-1, и правый нижний край оставался прямым
        # углом. Прямоугольник с рамкой (0, 0, w, h) закрывает всё поле и
        # скругляет все четыре угла.
        p.drawRoundedRect(QRectF(self.rect()), radius, radius)
        p.end()


class _PipSeek(QWidget):
    """Полоса перемотки в окне PiP.

    Отдельный виджет, а не часть панели: панель занимает только полосу
    значков по центру, и накрывать ею весь кадр незачем. Здесь рисуется
    только сама дорожка, фона нет.
    """

    def __init__(self, bar, parent):
        super().__init__(parent)
        self._bar = bar
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._bar._draw_seek(p, self.rect(), QColor(255, 255, 255))
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._bar._seek_dragging = True
            self._bar._seek_from_x(event.position().x())

    def mouseMoveEvent(self, event):
        if self._bar._seek_dragging:
            self._bar._seek_from_x(event.position().x())

    def mouseReleaseEvent(self, event):
        self._bar._seek_dragging = False


class _PipClose(QWidget):
    """Крестик выхода из PiP.

    Отдельный маленький виджет в правом верхнем углу: панель сюда не
    достаёт, а накрывать кадр целиком ради одного крестика незачем.
    """

    def __init__(self, bar, parent):
        super().__init__(parent)
        self._bar = bar
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('Выйти из маленького окна')
        self._hover = False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 55))
            p.drawEllipse(rect)
        self._bar._draw_close(p, rect, QColor(255, 255, 255))
        p.end()

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._bar._window.pip_action('close')


class PipBar(QWidget):
    """Панель управления в маленьком окне PiP.

    В окне PiP основной панели нет: контейнер с видео переезжает туда
    один, и управлять приходилось только горячими клавишами. Это не
    очевидно — человек в маленьком окне ищет кнопки, а их нет.

    Здесь только то, что нужно в маленьком окне: пауза, соседние ролики
    и полоса перемотки. Громкость, качество и полноэкранный режим
    оставлены основному окну: в окне сорок на сорок точек для них нет
    места, а горячие клавиши продолжают работать.

    Раскладка как в крупных плеерах:

    * по центру окна — только пауза, предыдущий и следующий ролик;
    * внизу — полоса перемотки во всю ширину окна;
    * в правом верхнем углу — крестик выхода.

    Появляется это при наведении мыши: постоянные элементы поверх видео
    мешали бы смотреть. Убрали мышь — всё убралось.
    """

    def __init__(self, parent, window):
        super().__init__(parent)
        self.setObjectName('pipbar')
        self._window = window
        self._owner = parent

        # Всё рисуется вручную: значки, крестик и полоса.
        #
        # Отдельные QPushButton и QSlider тянут за собой системную рамку и
        # подложку — на видео появлялись чёрные прямоугольники, а
        # подсветка при наведении заставляла окно перерисовываться.
        #
        # Панель накрывает только полосу значков по центру. Полоса
        # перемотки и крестик — свои маленькие виджеты: любой виджет
        # поверх видео закрашивает свою площадь фоном, и одно полотно на
        # всё окно закрывало кадр целиком.
        self._seek_widget = None
        self._close_widget = None
        self.seek_slider = None
        self.btn_close = None

        self._btn_rects = {}
        self._seek_rect = QRect()
        self._close_rect = QRect()
        self._seek_max = 0
        self._seek_value = 0
        self._seek_dragging = False
        self._seek_buffered = []
        self._paused = True
        self._hover = None
        self._pressed = None
        self.setMouseTracking(True)

        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._buttons = ('prev', 'play', 'next')
        self._button_w = 34
        self._button_h = 30
        # Расстояние между значками. Было 10 — значки читались как одна
        # плотная группа, между ними не хватало воздуха.
        self._gap = 22
        self._seek_h = 16
        self._close_size = 22

        # Полоса перемотки и крестик живут отдельными виджетами поверх
        # видео: так панель занимает только полосу значков.
        if parent is not None:
            self._seek_widget = _PipSeek(self, parent)
            self._close_widget = _PipClose(self, parent)
            self._seek_widget.hide()
            self._close_widget.hide()
        self._layout_rects()

    # ---------- раскладка ----------

    def _layout_rects(self):
        """Считает прямоугольники значков внутри панели.

        Панель занимает ровно полосу значков, поэтому их координаты
        начинаются с нуля.
        """
        self._btn_rects = {}
        x = 0
        for name in self._buttons:
            self._btn_rects[name] = QRect(x, 0, self._button_w,
                                          self._button_h)
            x += self._button_w + self._gap
        # Полоса и крестик живут в координатах окна, а не панели.
        self._seek_rect = QRect(0, 0, 100, self._seek_h)
        self._close_rect = QRect(0, 0, self._close_size, self._close_size)

    def _panel_size(self):
        n = len(self._buttons)
        return (n * self._button_w + (n - 1) * self._gap, self._button_h)

    def attach_to(self, host):
        """Переводит панель в отдельное прозрачное окно поверх видео.

        Дочерним виджетом её делать нельзя: картинку рисует mpv прямо в
        окно видео-виджета, и всё внутри него остаётся под кадром.
        Проверено снимком экрана на играющем видео — панель «видима», а
        на экране ни одного её пикселя.

        Тем же способом сделана справка по клавишам (см. _show_help).
        """
        self._owner = host
        self._make_popup()

    def _make_popup(self):
        """Создаёт один раз прозрачное окно без рамки для панели.

        Панель, полоса и крестик живут в нём обычными виджетами. Окно
        ставится по окну PiP и следует за ним при переезде.
        """
        if self.isWindow():
            return
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if self._seek_widget is not None:
            self._seek_widget.setParent(self)
        if self._close_widget is not None:
            self._close_widget.setParent(self)

    def _video_host(self):
        """Виджет с видео: на него и вешаем панель.

        Ищем его от переданного контейнера вверх по дереву, а если по
        какой-то причине не нашли — берём сам контейнер.
        """
        window = self._window
        if window is not None and getattr(window, 'video_widget', None):
            return window.video_widget
        return self._owner

    def follow_window(self):
        """Ставит окно панели на текущее место окна PiP.

        Панель — отдельное окно, поэтому при перетаскивании PiP она сама
        за ним не едет: значки оставались на прежнем месте и висели
        поверх других окон, пока мышь не уходила с окна и не
        возвращалась. Вызывается на каждое движение при перетаскивании и
        при каждом изменении размера.
        """
        window = self._window
        host = window.pip_window if window is not None else None
        if host is None or not host.isVisible():
            return
        origin = host.mapToGlobal(QPoint(0, 0))
        # Размер обычно тот же — setGeometry всё равно дешёвый, но
        # лишнюю перерисовку без нужды не затеваем.
        if (self.geometry().topLeft() != origin
                or self.width() != host.width()
                or self.height() != host.height()):
            self.setGeometry(origin.x(), origin.y(),
                             host.width(), host.height())
            self.place_elements()

    def place_in_window(self):
        """Ставит окно панели ровно поверх окна PiP и раскладывает элементы.

        Размеры берём у окна PiP, а не у контейнера с видео: контейнер
        бывает больше окна, пока оно пересчитывается, и панель уезжала за
        видимую область.
        """
        window = self._window
        host = window.pip_window if window is not None else None
        if host is None:
            return
        pw, ph = host.width(), host.height()

        # Окно панели — на всё окно PiP. Фона у него нет (прозрачность
        # включена), поэтому видеопоток под ним виден как есть.
        origin = host.mapToGlobal(QPoint(0, 0))
        self.setGeometry(origin.x(), origin.y(), pw, ph)
        self.place_elements()

    def place_elements(self):
        """Раскладывает значки, полосу и крестик внутри окна панели."""
        pw, ph = self.width(), self.height()

        # Значки — по центру.
        n = len(self._buttons)
        total = n * self._button_w + (n - 1) * self._gap
        self._btn_rects = {}
        x = (pw - total) // 2
        y = (ph - self._button_h) // 2
        for name in self._buttons:
            self._btn_rects[name] = QRect(x, y, self._button_w,
                                          self._button_h)
            x += self._button_w + self._gap

        # Полоса — во всю ширину у нижнего края.
        margin = 8
        self._seek_rect = QRect(margin, max(0, ph - self._seek_h - 3),
                                max(20, pw - 2 * margin), self._seek_h)
        # Крестик — в правом верхнем углу.
        s = self._close_size
        self._close_rect = QRect(max(0, pw - s - 4), 4, s, s)

        if self._seek_widget is not None:
            self._seek_widget.setGeometry(self._seek_rect)
        if self._close_widget is not None:
            self._close_widget.setGeometry(self._close_rect)

    # ---------- вид ----------

    def set_player_state(self, paused):
        """Переключает значок паузы под состояние воспроизведения."""
        paused = bool(paused)
        if paused != self._paused:
            self._paused = paused
            self.update()

    def set_position(self, ms, duration_ms):
        """Ставит ползунок на место, не мешая перетаскиванию."""
        if self._seek_dragging:
            return
        self._seek_max = max(0, int(duration_ms))
        self._seek_value = max(0, int(ms))
        self.update()

    def set_buffered(self, ranges):
        """Скачанные участки ролика: список пар (доля_нач, доля_конца)."""
        self._seek_buffered = list(ranges or ())
        self.update()

    def _icon_color(self):
        return QColor(255, 255, 255)

    def paintEvent(self, event):
        """Рисует значки прямо на видео.

        Фона нет совсем: под значками видно кадр. Только лёгкая
        подсветка у значка под курсором — без прямоугольной рамки.

        Крестик и полоса перемотки рисуются своими виджетами: панель
        занимает только полосу значков по центру окна.
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = self._icon_color()
        for name, r in self._btn_rects.items():
            self._draw_glyph(p, name, r, color,
                             QColor(255, 255, 255, 55)
                             if self._hover == name else None)
        p.end()

    def _draw_glyph(self, p, name, rect, color, hover_bg=None):
        """Рисует значок в прямоугольнике."""
        if hover_bg is not None:
            # Лёгкая подсветка только у значка, без прямоугольной рамки.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hover_bg)
            p.drawRoundedRect(QRectF(rect), 6, 6)

        cx = rect.center().x() + 0.5
        cy = rect.center().y() + 0.5
        s = min(rect.width(), rect.height()) * 0.5

        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        if name == 'prev':
            p.drawRect(QRectF(cx - s, cy - s, s * 0.28, s * 2))
            p.drawPolygon(QPolygonF([
                QPointF(cx + s, cy - s), QPointF(cx + s, cy + s),
                QPointF(cx - s * 0.35, cy)]))
        elif name == 'next':
            p.drawRect(QRectF(cx + s * 0.72, cy - s, s * 0.28, s * 2))
            p.drawPolygon(QPolygonF([
                QPointF(cx - s, cy - s), QPointF(cx - s, cy + s),
                QPointF(cx + s * 0.35, cy)]))
        elif self._paused:
            # На паузе показываем «играть».
            p.drawPolygon(QPolygonF([
                QPointF(cx - s * 0.5, cy - s),
                QPointF(cx - s * 0.5, cy + s),
                QPointF(cx + s * 0.8, cy)]))
        else:
            p.drawRect(QRectF(cx - s * 0.6, cy - s, s * 0.42, s * 2))
            p.drawRect(QRectF(cx + s * 0.18, cy - s, s * 0.42, s * 2))

    def _draw_close(self, p, rect, color):
        """Крестик двумя линиями — без кнопки и рамки."""
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 2.4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        m = 7
        p.drawLine(rect.left() + m, rect.top() + m,
                   rect.right() - m, rect.bottom() - m)
        p.drawLine(rect.right() - m, rect.top() + m,
                   rect.left() + m, rect.bottom() - m)

    def _draw_seek(self, p, rect, color):
        """Полоса перемотки: дорожка, скачанное и просмотренное.

        Дорожку делаем плотной, а не еле заметной: она лежит на видео,
        где под ней может быть и светлый, и тёмный кадр. Полупрозрачная
        заливка на светлом кадре пропадала совсем.
        """
        if rect.width() < 10:
            return
        h = 5
        y = rect.center().y() - h / 2
        r = h / 2
        p.setPen(Qt.PenStyle.NoPen)

        # Весь ролик.
        p.setBrush(QColor(255, 255, 255, 150))
        p.drawRoundedRect(QRectF(rect.left(), y, rect.width(), h), r, r)

        # Скачанные участки.
        p.setBrush(QColor(255, 255, 255, 210))
        for a, b in self._seek_buffered:
            x = rect.left() + rect.width() * a
            w = max(h, rect.width() * (b - a))
            p.drawRoundedRect(QRectF(x, y, w, h), r, r)

        # Просмотренное.
        if self._seek_max > 0:
            ratio = max(0.0, min(1.0, self._seek_value / self._seek_max))
            if ratio > 0:
                p.setBrush(QColor(theme.theme()['accent']))
                p.drawRoundedRect(
                    QRectF(rect.left(), y, max(h, rect.width() * ratio), h),
                    r, r)
            # Ползунок.
            hx = rect.left() + rect.width() * ratio
            p.setBrush(QColor(255, 255, 255))
            d = 12
            p.drawEllipse(QRectF(hx - d / 2,
                                 rect.center().y() - d / 2, d, d))

    # ---------- показ и мышь ----------

    def show_all(self):
        """Показывает панель, полосу перемотки и крестик."""
        self.place_in_window()
        self.show()
        self.raise_()
        if self._seek_widget is not None:
            self._seek_widget.show()
            self._seek_widget.raise_()
        if self._close_widget is not None:
            self._close_widget.show()
            self._close_widget.raise_()
        self.update()

    def hide_all(self):
        """Убирает всё разом."""
        self.hide()
        if self._seek_widget is not None:
            self._seek_widget.hide()
        if self._close_widget is not None:
            self._close_widget.hide()

    def _hit(self, pos):
        """Что под курсором внутри панели: имя значка или None.

        Полоса и крестик — отдельные виджеты, у них свои обработчики.
        """
        for name, r in self._btn_rects.items():
            if r.contains(pos):
                return name
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit(event.position().toPoint())
        if hit is not None:
            self._pressed = hit
        event.accept()

    def mouseMoveEvent(self, event):
        hit = self._hit(event.position().toPoint())
        cur = (Qt.CursorShape.PointingHandCursor
               if hit is not None else Qt.CursorShape.ArrowCursor)
        if self.cursor().shape() != cur:
            self.setCursor(cur)
        if hit != self._hover:
            self._hover = hit
            self.update()

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._pressed
        self._pressed = None
        pos = event.position().toPoint()
        if hit is not None and self._hit(pos) == hit and self._window:
            self._window.pip_action(hit)

    def _seek_from_x(self, x):
        """Перематывает по горизонтали внутри полосы."""
        rect = self._seek_rect
        if rect.width() <= 0 or self._seek_max <= 0:
            return
        ratio = (x - rect.left()) / rect.width()
        ratio = max(0.0, min(1.0, ratio))
        self._seek_value = int(self._seek_max * ratio)
        self.update()
        if self._window:
            self._window.pip_seek(self._seek_value)


class PlayerContainer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.video_widget = QWidget()
        self.video_widget.setStyleSheet("background: black;")
        self.video_widget.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen, True)
        self.video_widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        lay.addWidget(self.video_widget)


class MouseWatch(QObject):
    """Сообщает, где сейчас курсор, не дожидаясь событий Qt.

    Без этого в фуллскрине всё работает со второго раза. Панель
    управления прячется сама, когда воспроизведение идёт. Если человек
    остановил курсор там, где панель появится, первое нажатие уходит мимо
    кнопки: виджета под курсором ещё нет, и Qt отдаёт событие видео.
    Только ПОСЛЕ этого приходит движение мыши, панель показывается, и
    следующее нажатие наконец попадает в кнопку.

    Лечится это опросом положения курсора: панель появляется ДО того,
    как человек нажмёт, а не после первого промаха.

    Опрос дешёвый — раз в config.MOUSE_POLL_MS, пока окно фуллскрина на экране.
    Своё состояние запоминается: `over` — был ли курсор над окном на
    прошлом шаге, `inset` — сколько миллисекунд назад он там появился.
    По `inset` таймер автоскрытия отличает «человек ведёт мышь к панели»
    от «панель всплыла под неподвижным курсором».
    """

    def __init__(self, window, on_over, on_leave):
        super().__init__(window)
        self._window = window
        self._on_over = on_over
        self._on_leave = on_leave
        self._over = False
        self._inset = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(config.MOUSE_POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self):
        # Сбрасываем состояние: первая же проверка должна говорить правду
        # о текущем месте курсора, а не помнить прошлый режим.
        self._over = False
        self._inset = 0.0
        self._timer.start()
        self._poll()

    def stop(self):
        self._timer.stop()
        self._over = False
        self._inset = 0.0

    def over(self):
        """Курсор был над окном на последней проверке."""
        return self._over

    def inset(self):
        """Сколько прошло с тех пор, как курсор оказался над окном, с."""
        if not self._over:
            return 0.0
        return time.monotonic() - self._inset

    def local_pos(self):
        """Курсор в координатах окна — без проверки, над ним ли он.

        Считается от прямоугольника окна из GetWindowRect, а не через
        mapFromGlobal: тот возвращает выдуманную точку, если Qt ещё не
        знает о позиции мыши (в покое она не обновляется).
        """
        w = self._window
        if w is None:
            return None
        try:
            point = ctypes.wintypes.POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                return None
            rect = ctypes.wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(
                    int(w.winId()), ctypes.byref(rect)):
                return None
            return QPoint(point.x - rect.left, point.y - rect.top)
        except Exception:
            return None

    def probe(self):
        """Где курсор: (над окном, точка в координатах окна)."""
        w = self._window
        if w is None or not w.isVisible():
            return (False, None)
        try:
            # Позицию берём у Windows, а не у Qt: Qt обновляет её только
            # по событиям, а нам нужна именно свежая — за неё и боремся.
            point = ctypes.wintypes.POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                return (False, None)
            # Ручку окна надо брать до GetWindowRect: у скрытого окна её
            # нет, и сам winId() может создавать окно заново.
            hwnd = int(w.winId())
            rect = ctypes.wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(hwnd,
                                                     ctypes.byref(rect)):
                return (False, None)
        except Exception:
            # Без Win32 остаётся то, что знает Qt. Хуже, но не смертельно.
            try:
                pos = QCursor.pos()
                local = w.mapFromGlobal(pos)
                return (w.rect().contains(local), local)
            except Exception:
                return (False, None)
        if not (rect.left <= point.x <= rect.right
                and rect.top <= point.y <= rect.bottom):
            return (False, None)
        local = QPoint(point.x - rect.left, point.y - rect.top)
        if not w.rect().contains(local):
            return (False, None)
        # Точка внутри прямоугольника ещё не значит, что мышь наша: окно
        # может быть чем-то накрыто (или это вовсе копия экрана раньше
        # висела здесь). Спрашиваем Windows, чьё окно под курсором.
        # Панель управления — отдельное окно поверх видео, поэтому она
        # законно оказывается «не нашим» окном; постороннее окно — повод
        # считать, что мышь ушла.
        try:
            hit = ctypes.windll.user32.WindowFromPoint(point)
            if hit:
                if hit == hwnd or self._belongs_to_us(hit, w, hwnd):
                    pass
                # Скрытое окно мышь не получает, и если курсор «на» нём,
                # настоящим хозяином будет то, что его накрывает. Такое
                # окно — не повод считать, что мышь ушла.
                elif ctypes.windll.user32.IsWindowVisible(hit):
                    return (False, None)
        except Exception:
            # Не смогли спросить — доверяем прямоугольнику: лучше показать
            # панель лишний раз, чем спрятать её под курсором.
            pass
        return (True, local)

    @staticmethod
    def _belongs_to_us(hit, w, hwnd):
        """Окно под курсором принадлежит плееру, а не чужой программе."""
        try:
            panel = getattr(w, 'bar', None)
            if panel is not None and panel.isVisible():
                if hit == int(panel.winId()):
                    return True
            # Дети окна фуллскрина (полоса состояния и прочее).
            if ctypes.windll.user32.GetAncestor(hit, 1) == hwnd:
                return True
        except Exception:
            pass
        return False

    def _poll(self):
        # Окно ушло с экрана — опрос больше не нужен, панель за ним
        # прятать и показывать нечего.
        if self._window is None or not self._window.isVisible():
            self._over = False
            return
        inside, _ = self.probe()
        if inside:
            if not self._over:
                self._inset = time.monotonic()
            self._over = True
            self._on_over()
        elif self._over:
            # Ушла с окна: один раз сообщаем. Дальше ждём, пока вернётся.
            self._over = False
            self._inset = 0.0
            self._on_leave()

