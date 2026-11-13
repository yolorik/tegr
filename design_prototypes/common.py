"""Общие детали прототипов оформления Tegr.

Только интерфейс: ни Telegram, ни mpv, ни кэша здесь нет. Модуль даёт
набор простых виджетов и иконок, чтобы три варианта дизайна не копировали
одну и ту же вёрстку по три раза.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QByteArray, QPointF, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath,
    QPixmap, QPolygonF)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)


# --------------------------------------------------------------------------
# Иконки
# --------------------------------------------------------------------------

_ICON_PATHS = {
    'search': '<circle cx="11" cy="11" r="6.5"/>'
              '<path d="M16 16l4.5 4.5"/>',
    'history': '<circle cx="12" cy="12" r="8.5"/>'
               '<path d="M12 7v5l3.5 2"/>',
    'download': '<path d="M12 3v11"/><path d="M7.5 9.5L12 14l4.5-4.5"/>'
                '<path d="M4 20h16"/>',
    'settings': '<path d="M4 7h16M4 12h16M4 17h16"/>'
                '<circle cx="9" cy="7" r="2.2"/>'
                '<circle cx="15" cy="12" r="2.2"/>'
                '<circle cx="10" cy="17" r="2.2"/>',
    'play': '<path d="M8 5.5v13l11-6.5z" fill="{color}" stroke="none"/>',
    'pause': '<path d="M9 5v14M15 5v14"/>',
    'link': '<path d="M10.5 13.5a4.5 4.5 0 0 0 6.4 0l2.1-2.1a4.5 4.5 0 '
            '0 0-6.4-6.4l-1.2 1.2"/>'
            '<path d="M13.5 10.5a4.5 4.5 0 0 0-6.4 0l-2.1 2.1a4.5 4.5 0 '
            '0 0 6.4 6.4l1.2-1.2"/>',
    'folder': '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8'
              'a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    'film': '<rect x="3" y="4" width="18" height="16" rx="2"/>'
            '<path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    'tv': '<rect x="3" y="5" width="18" height="12" rx="2"/>'
          '<path d="M8 21h8M12 17v4"/>',
    'spark': '<path d="M12 2.5l2.1 6.4 6.4 2.1-6.4 2.1L12 19.5'
             'l-2.1-6.4L3.5 11l6.4-2.1z"/>',
    'close': '<path d="M6 6l12 12M18 6L6 18"/>',
    'check': '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    'chevron': '<path d="M9 5l7 7-7 7"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'star': '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8'
            '-5.2-2.7-5.2 2.7 1-5.8L3.5 9.7l5.9-.9z"/>',
    'globe': '<circle cx="12" cy="12" r="8.5"/>'
             '<path d="M3.5 12h17M12 3.5c2.2 2.3 3.3 5.2 3.3 8.5'
             'S14.2 18.2 12 20.5c-2.2-2.3-3.3-5.2-3.3-8.5'
             'S9.8 5.8 12 3.5z"/>',
    'clock': '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/>',
    'user': '<circle cx="12" cy="8" r="3.5"/>'
            '<path d="M5 20c0-3.4 3.1-5.5 7-5.5s7 2.1 7 5.5"/>',
    'list': '<path d="M8 6h12M8 12h12M8 18h12"/>'
            '<circle cx="4.5" cy="6" r="1" fill="{color}" stroke="none"/>'
            '<circle cx="4.5" cy="12" r="1" fill="{color}" stroke="none"/>'
            '<circle cx="4.5" cy="18" r="1" fill="{color}" stroke="none"/>',
}

_ICON_CACHE = {}


def icon(name, color, size=20):
    """SVG-иконка нужного цвета. Результат кэшируется."""
    key = (name, color, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    body = _ICON_PATHS.get(name, _ICON_PATHS['spark'])
    body = body.replace('{color}', color)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
        f'height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode('utf-8')))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    result = QIcon(pixmap)
    _ICON_CACHE[key] = result
    return result


class IconLabel(QLabel):
    """QLabel с иконкой, которую можно перекрасить."""

    def __init__(self, name, color, size=20, parent=None):
        super().__init__(parent)
        self._name = name
        self._color = color
        self._size = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.set_icon(name, color)

    def set_icon(self, name, color):
        self._name, self._color = name, color
        self.setPixmap(icon(name, color, self._size).pixmap(
            self._size, self._size))


# --------------------------------------------------------------------------
# Мелкие декоративные элементы
# --------------------------------------------------------------------------

class Avatar(QWidget):
    """Круглая «аватарка» с инициалом канала."""

    def __init__(self, text, color, size=34, parent=None):
        super().__init__(parent)
        self._text = (text or '?')[:1].upper()
        self._color = QColor(color)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self._size - 1, self._size - 1)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, self._color.lighter(112))
        grad.setColorAt(1.0, self._color.darker(140))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(rect)
        p.setPen(QColor('#ffffff'))
        font = QFont(self.font())
        font.setPixelSize(max(11, int(self._size * 0.38)))
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


class StatusDot(QWidget):
    """Маленькая цветная точка состояния."""

    def __init__(self, color, size=8, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(0, 0, self._size, self._size))
        p.end()


class IconBubble(QWidget):
    """Градиентный кружок с иконкой для пустых состояний."""

    def __init__(self, name, color_a, color_b, size=88, icon_size=34,
                 parent=None):
        super().__init__(parent)
        self._name = name
        self._a = QColor(color_a)
        self._b = QColor(color_b)
        self._icon_size = icon_size
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, self._a)
        grad.setColorAt(1.0, self._b)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(rect, self.width() / 2, self.height() / 2)
        icon_pm = icon(self._name, '#ffffff', self._icon_size).pixmap(
            self._icon_size, self._icon_size)
        x = (self.width() - self._icon_size) / 2
        y = (self.height() - self._icon_size) / 2
        p.drawPixmap(QPointF(x, y), icon_pm)
        p.end()


class Poster(QWidget):
    """Абстрактная «обложка» ролика: градиент, круги и кнопка Play."""

    def __init__(self, colors, badge='', radius=12, parent=None):
        super().__init__(parent)
        self._a = QColor(colors[0])
        self._b = QColor(colors[1])
        self._badge = badge
        self._radius = radius
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        p.setClipPath(path)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, self._a)
        grad.setColorAt(1.0, self._b)
        p.fillPath(path, QBrush(grad))

        # Мягкие круги — вместо постеров, которых в прототипе нет.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawEllipse(QPointF(rect.width() * 0.16, rect.height() * 0.10),
                      rect.width() * 0.42, rect.width() * 0.42)
        p.setBrush(QColor(0, 0, 0, 22))
        p.drawEllipse(QPointF(rect.width() * 0.88, rect.height() * 0.95),
                      rect.width() * 0.50, rect.width() * 0.50)

        # Кнопка Play по центру.
        cx, cy = rect.center().x(), rect.center().y()
        r = min(20.0, rect.height() * 0.20)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setBrush(QColor(255, 255, 255, 235))
        tri = QPolygonF([
            QPointF(cx - r * 0.28, cy - r * 0.42),
            QPointF(cx + r * 0.46, cy),
            QPointF(cx - r * 0.28, cy + r * 0.42),
        ])
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tri)

        if self._badge:
            font = QFont(self.font())
            font.setPixelSize(9)
            p.setFont(font)
            fm = p.fontMetrics()
            bw = fm.horizontalAdvance(self._badge) + 12
            bh = 18
            br = QRectF(rect.width() - bw - 8, rect.height() - bh - 8,
                        bw, bh)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(br, 6, 6)
            p.setPen(QColor('#ffffff'))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, self._badge)
        p.end()


class ToggleSwitch(QWidget):
    """Переключатель для настроек."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked=False, accent='#8b5cf6',
                 track_off='#3a3645', parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._accent = QColor(accent)
        self._track_off = QColor(track_off)
        self._pos = 1.0 if checked else 0.0
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.valueChanged.connect(self._animate)

    def _animate(self, value):
        self._pos = float(value)
        self.update()

    def isChecked(self):
        return self._checked

    def setChecked(self, checked, emit=False):
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()
        if emit:
            self.toggled.emit(checked)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked, emit=True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        p.setPen(Qt.PenStyle.NoPen)
        track = QColor(self._accent) if self._checked else self._track_off
        p.setBrush(track)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        d = self.height() - 6
        x = 3 + self._pos * (self.width() - d - 6)
        knob = QRectF(x, 3, d, d)
        p.setBrush(QColor('#ffffff'))
        p.drawEllipse(knob)
        p.end()


# --------------------------------------------------------------------------
# Элементы навигации и полей
# --------------------------------------------------------------------------

class NavItem(QFrame):
    """Пункт бокового меню: иконка, подпись и необязательный счётчик."""

    clicked = pyqtSignal()

    def __init__(self, text, icon_name, color, active_color='#ffffff',
                 badge=None, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._color = color
        self._active_color = active_color
        self.setObjectName('navitem')
        self.setProperty('active', False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(10)
        self._icon = IconLabel(icon_name, color, 18)
        self._label = QLabel(text)
        self._label.setObjectName('navtext')
        lay.addWidget(self._icon)
        lay.addWidget(self._label)
        lay.addStretch(1)
        if badge:
            self._badge = QLabel(str(badge))
            self._badge.setObjectName('navbadge')
            self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(self._badge)

    def set_active(self, active):
        active = bool(active)
        self.setProperty('active', active)
        self._icon.set_icon(self._icon_name,
                            self._active_color if active else self._color)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class Chip(QPushButton):
    """Кнопка-фильтр."""

    def __init__(self, text, checked=False, parent=None):
        super().__init__(text, parent)
        self.setObjectName('chip')
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class SearchField(QLineEdit):
    """Поле поиска со значком слева."""

    def __init__(self, placeholder, color='#9b91b5', parent=None,
                 height=42):
        super().__init__(parent)
        self.setObjectName('search')
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(icon('search', color, 18),
                       QLineEdit.ActionPosition.LeadingPosition)
        self.setFixedHeight(height)


class SectionHeader(QWidget):
    """Заголовок раздела с необязательной ссылкой справа."""

    def __init__(self, title, action_text='', parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(title)
        self.title.setObjectName('sectiontitle')
        lay.addWidget(self.title)
        lay.addStretch(1)
        self.action = None
        if action_text:
            self.action = QPushButton(action_text)
            self.action.setObjectName('linkbutton')
            self.action.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(self.action)


def scroll_page(widget):
    """Помещает страницу в прозрачную прокручиваемую область."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(widget)
    area.setStyleSheet('QScrollArea { background: transparent; }'
                       'QScrollArea > QWidget > QWidget '
                       '{ background: transparent; }')
    area.viewport().setAutoFillBackground(False)
    return area


def add_shadow(widget, color='#000000', blur=28, y=8, alpha=70):
    """Мягкая тень для карточек и плавающих панелей."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    c = QColor(color)
    c.setAlpha(alpha)
    effect.setColor(c)
    widget.setGraphicsEffect(effect)
    return effect


# --------------------------------------------------------------------------
# Карточки и строки
# --------------------------------------------------------------------------

class VideoCard(QFrame):
    """Карточка ролика для сетки."""

    clicked = pyqtSignal()

    def __init__(self, video, accent='#8b5cf6', parent=None):
        super().__init__(parent)
        self.video = dict(video)
        self.setObjectName('card')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.poster = Poster(video['colors'], video.get('duration', ''),
                             radius=14)
        self.poster.setFixedHeight(126)
        lay.addWidget(self.poster)

        body = QVBoxLayout()
        body.setContentsMargins(12, 10, 12, 12)
        body.setSpacing(4)
        title = QLabel(video['title'])
        title.setObjectName('cardtitle')
        title.setWordWrap(True)
        title.setMinimumHeight(36)
        title.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Preferred)
        body.addWidget(title)
        meta = QLabel(f"{video['channel']}  ·  {video['meta']}")
        meta.setObjectName('cardmeta')
        meta.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        body.addWidget(meta)
        if video.get('progress'):
            bar = ProgressBarLite(video['progress'], 4, accent=accent)
            body.addSpacing(4)
            body.addWidget(bar)
        lay.addLayout(body)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class VideoRow(QFrame):
    """Компактная строка ролика для списков."""

    clicked = pyqtSignal()

    def __init__(self, video, icon_color='#ffffff', parent=None):
        super().__init__(parent)
        self.video = dict(video)
        self.setObjectName('videorow')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(12)
        poster = Poster(video['colors'], video.get('duration', ''), radius=9)
        poster.setFixedSize(92, 56)
        lay.addWidget(poster)

        text = QVBoxLayout()
        text.setSpacing(3)
        title = QLabel(video['title'])
        title.setObjectName('rowtitle')
        title.setWordWrap(False)
        title.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Preferred)
        text.addWidget(title)
        meta = QLabel(f"{video['channel']}  ·  {video['meta']}")
        meta.setObjectName('rowmeta')
        meta.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        text.addWidget(meta)
        lay.addLayout(text, 1)

        quality = QLabel(video.get('quality', ''))
        quality.setObjectName('quality')
        lay.addWidget(quality)

        btn = QToolButton()
        btn.setObjectName('rowbutton')
        btn.setIcon(icon('download', icon_color, 17))
        btn.setIconSize(QSize(17, 17))
        btn.setFixedSize(32, 32)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip('Скачать в кэш')
        lay.addWidget(btn)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class HistoryRow(QFrame):
    """Строка истории просмотров."""

    def __init__(self, item, icon_color='#ffffff', accent='#8b5cf6',
                 parent=None):
        super().__init__(parent)
        self.setObjectName('historyrow')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(12)
        poster = Poster(item['colors'], '', radius=8)
        poster.setFixedSize(72, 46)
        lay.addWidget(poster)
        text = QVBoxLayout()
        text.setSpacing(3)
        title = QLabel(item['title'])
        title.setObjectName('rowtitle')
        title.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Preferred)
        text.addWidget(title)
        meta = QLabel(f"{item['channel']}  ·  {item['when']}")
        meta.setObjectName('rowmeta')
        meta.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        text.addWidget(meta)
        if item.get('progress'):
            text.addWidget(ProgressBarLite(item['progress'], 3,
                                           accent=accent))
        lay.addLayout(text, 1)
        pos = QLabel(item.get('position', ''))
        pos.setObjectName('quality')
        lay.addWidget(pos)
        play = QToolButton()
        play.setObjectName('rowbutton')
        play.setIcon(icon('play', icon_color, 15))
        play.setIconSize(QSize(15, 15))
        play.setFixedSize(32, 32)
        play.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(play)


class DownloadRow(QFrame):
    """Строка загрузки с прогрессом и кнопкой паузы."""

    def __init__(self, item, accent='#8b5cf6', icon_color='#ffffff',
                 parent=None):
        super().__init__(parent)
        self.setObjectName('downloadrow')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(12)
        bubble = IconBubble('download', accent, accent, size=42,
                            icon_size=19)
        lay.addWidget(bubble)

        body = QVBoxLayout()
        body.setSpacing(4)
        title = QLabel(item['title'])
        title.setObjectName('rowtitle')
        title.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Preferred)
        body.addWidget(title)
        meta = QLabel(item['meta'])
        meta.setObjectName('rowmeta')
        meta.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        body.addWidget(meta)
        body.addWidget(ProgressBarLite(item['progress'], 4, accent=accent))
        lay.addLayout(body, 1)

        right = QVBoxLayout()
        right.setSpacing(4)
        pct = QLabel(f"{int(item['progress'] * 100)}%")
        pct.setObjectName('quality')
        pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(pct)
        buttons = QHBoxLayout()
        buttons.setSpacing(2)
        for name, tip in (('pause', 'Пауза'), ('close', 'Отменить')):
            b = QToolButton()
            b.setObjectName('rowbutton')
            b.setIcon(icon(name, icon_color, 15))
            b.setIconSize(QSize(15, 15))
            b.setFixedSize(28, 28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            buttons.addWidget(b)
        right.addLayout(buttons)
        lay.addLayout(right)


class ProgressBarLite(QWidget):
    """Тонкая полоса прогресса без текста."""

    def __init__(self, value, height=4, accent='#8b5cf6', parent=None):
        super().__init__(parent)
        self._value = max(0.0, min(1.0, float(value)))
        self._accent = QColor(accent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect()
        h = self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(128, 128, 128, 55))
        p.drawRoundedRect(QRectF(0, 0, r.width(), h), h / 2, h / 2)
        p.setBrush(self._accent)
        p.drawRoundedRect(
            QRectF(0, 0, max(2.0, r.width() * self._value), h),
            h / 2, h / 2)
        p.end()


class EmptyState(QWidget):
    """Пустое состояние: кружок с иконкой, заголовок и подпись."""

    def __init__(self, icon_name, title, subtitle, accent_a, accent_b,
                 parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 28, 20, 28)
        lay.setSpacing(10)
        lay.addStretch(1)
        bubble = IconBubble(icon_name, accent_a, accent_b, size=86,
                            icon_size=34)
        lay.addWidget(bubble, 0, Qt.AlignmentFlag.AlignHCenter)
        head = QLabel(title)
        head.setObjectName('emptytitle')
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(head)
        text = QLabel(subtitle)
        text.setObjectName('emptysub')
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setWordWrap(True)
        lay.addWidget(text)
        lay.addStretch(1)
