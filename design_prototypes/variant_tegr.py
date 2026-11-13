"""Вариант Tegr — Studio layout + Paper style.

Три колонки как в Studio, светлый «редакционный» стиль как в Paper.
Есть выбор цветовой темы и масштаба интерфейса. Только внешний вид:
видео, Telegram и кэш не подключены.
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import (
    QPointF, QRect, QSize, Qt, QTimer, pyqtSignal)
from PyQt6.QtGui import QColor, QFont, QPainter, QRadialGradient
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSplitter, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget)

try:
    from .common import (
        Avatar, IconBubble, IconLabel, Poster, ProgressBarLite,
        SearchField, StatusDot, ToggleSwitch, add_shadow, icon, scroll_page)
    from .mock_data import (
        CHANNELS, DOWNLOADS, FOLDERS, HISTORY, VIDEOS)
except ImportError:  # запуск файла напрямую
    from common import (
        Avatar, IconBubble, IconLabel, Poster, ProgressBarLite,
        SearchField, StatusDot, ToggleSwitch, add_shadow, icon, scroll_page)
    from mock_data import (
        CHANNELS, DOWNLOADS, FOLDERS, HISTORY, VIDEOS)


THEMES = {
    'paper': {
        'title': 'Бумага',
        'bg': '#f5f2ec', 'panel': '#fffdf9', 'field': '#faf6ef',
        'border': '#e7dfd2', 'text': '#211d19', 'dim': '#82796f',
        'accent': '#e4572e', 'accent2': '#b8401f', 'soft': '#fdf1eb',
        'good': '#0f766e', 'shadow': '#8b7355', 'on_accent': '#ffffff',
    },
    'sand': {
        'title': 'Песок',
        'bg': '#f2eee4', 'panel': '#fffcf4', 'field': '#f8f3e8',
        'border': '#e2dbcb', 'text': '#22261f', 'dim': '#7d7c6e',
        'accent': '#0f766e', 'accent2': '#0b5b56', 'soft': '#e9f4f1',
        'good': '#0f766e', 'shadow': '#7c7460', 'on_accent': '#ffffff',
    },
    'fog': {
        'title': 'Туман',
        'bg': '#eef1f5', 'panel': '#ffffff', 'field': '#f6f8fb',
        'border': '#dfe5ec', 'text': '#18212c', 'dim': '#74808d',
        'accent': '#3b6fd4', 'accent2': '#2854a8', 'soft': '#eaf0fb',
        'good': '#0f766e', 'shadow': '#6b7c93', 'on_accent': '#ffffff',
    },
    'graphite': {
        'title': 'Графит',
        'bg': '#101214', 'panel': '#171a1d', 'field': '#1f2428',
        'border': '#2b3137', 'text': '#ede9e2', 'dim': '#8e8a82',
        'accent': '#f2b84b', 'accent2': '#c98f2a', 'soft': '#2a2418',
        'good': '#6ee7a8', 'shadow': '#000000', 'on_accent': '#1a1205',
    },
}


def rgba(hex_color, alpha):
    c = QColor(hex_color)
    return f'rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})'


class ElideLabel(QLabel):
    """QLabel, который обрезает текст многоточием под ширину строки."""

    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self._full = text
        self.setText(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = max(0, self.width())
        self.setText(self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, width))


class PaperBackground(QWidget):
    """Тёплый фон с очень слабыми цветными пятнами."""

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self._p = palette

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(self._p['bg']))
        for cx, cy, radius, color, alpha in (
            (0.94, 0.02, 0.55, self._p['accent'], 16),
            (0.06, 1.02, 0.52, self._p['good'], 12),
        ):
            glow = QColor(color)
            glow.setAlpha(alpha)
            clear = QColor(color)
            clear.setAlpha(0)
            grad = QRadialGradient(
                QPointF(self.width() * cx, self.height() * cy),
                self.width() * radius)
            grad.setColorAt(0.0, glow)
            grad.setColorAt(1.0, clear)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRect(self.rect())
        p.end()


class FolderCombo(QComboBox):
    """Комбобокс с явной стрелкой: в светлой теме системная теряется."""

    def __init__(self, palette, scale, parent=None):
        super().__init__(parent)
        self._p = palette
        self._scale = scale

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        size = max(10, int(round(12 * self._scale / 100)))
        icon('chevron', self._p['dim'], size).paint(
            p, QRect(self.width() - size - 10,
                     (self.height() - size) // 2, size, size))
        p.end()


class ChannelRow(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, index, channel, palette, scale, active=False,
                 parent=None):
        super().__init__(parent)
        self.index = index
        self._scale = scale
        self.setObjectName('channelrow')
        self.setProperty('active', bool(active))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)

        def s(value):
            return max(1, int(round(value * scale / 100)))

        lay.addWidget(Avatar(channel['initial'], channel['color'],
                             s(30)))
        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(channel['name'])
        name.setObjectName('channelname')
        count = QLabel(f"{channel['count']} видео")
        count.setObjectName('channelcount')
        text.addWidget(name)
        text.addWidget(count)
        lay.addLayout(text, 1)
        self._bar = QFrame()
        self._bar.setObjectName('activebar')
        self._bar.setFixedWidth(s(3))
        self._bar.setVisible(bool(active))
        lay.addWidget(self._bar)
        self.setMinimumHeight(s(46))

    def set_active(self, active):
        self.setProperty('active', bool(active))
        self._bar.setVisible(bool(active))
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


class VideoRow(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, index, video, palette, scale, active=False,
                 parent=None):
        super().__init__(parent)
        self.index = index
        self.setObjectName('videorow')
        self.setProperty('active', bool(active))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        def s(value):
            return max(1, int(round(value * scale / 100)))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 7, 9, 7)
        lay.setSpacing(10)
        poster = Poster(video['colors'], video.get('duration', ''),
                        radius=s(8))
        poster.setFixedSize(s(94), s(58))
        lay.addWidget(poster)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = ElideLabel(video['title'])
        title.setObjectName('rowtitle')
        meta = ElideLabel(f"{video['channel']}  ·  {video['meta']}")
        meta.setObjectName('rowmeta')
        text.addWidget(title)
        text.addWidget(meta)
        if video.get('progress'):
            text.addWidget(ProgressBarLite(
                video['progress'], s(3), accent=palette['accent']))
        lay.addLayout(text, 1)

        quality = QLabel(video.get('quality', ''))
        quality.setObjectName('quality')
        lay.addWidget(quality)

        btn = QToolButton()
        btn.setObjectName('rowbutton')
        btn.setIcon(icon('download', palette['dim'], s(15)))
        btn.setIconSize(QSize(s(15), s(15)))
        btn.setFixedSize(s(30), s(30))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip('Скачать в кэш')
        lay.addWidget(btn)
        self.setMinimumHeight(s(70))

    def set_active(self, active):
        self.setProperty('active', bool(active))
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


class DownloadRow(QFrame):
    def __init__(self, item, palette, scale, parent=None):
        super().__init__(parent)
        self.setObjectName('downloadrow')

        def s(value):
            return max(1, int(round(value * scale / 100)))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)
        lay.addWidget(IconBubble(
            'download', palette['accent'], palette['accent2'],
            size=s(38), icon_size=s(17)))
        body = QVBoxLayout()
        body.setSpacing(2)
        title = ElideLabel(item['title'])
        title.setObjectName('rowtitle')
        meta = ElideLabel(item['meta'])
        meta.setObjectName('rowmeta')
        body.addWidget(title)
        body.addWidget(meta)
        body.addWidget(ProgressBarLite(
            item['progress'], s(3), accent=palette['accent']))
        lay.addLayout(body, 1)

        right = QVBoxLayout()
        right.setSpacing(2)
        pct = QLabel(f"{int(item['progress'] * 100)}%")
        pct.setObjectName('quality')
        pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(pct)
        buttons = QHBoxLayout()
        buttons.setSpacing(2)
        for name, tip in (('pause', 'Пауза'), ('close', 'Отменить')):
            b = QToolButton()
            b.setObjectName('rowbutton')
            b.setIcon(icon(name, palette['dim'], s(13)))
            b.setIconSize(QSize(s(13), s(13)))
            b.setFixedSize(s(24), s(24))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            buttons.addWidget(b)
        right.addLayout(buttons)
        lay.addLayout(right)


class HistoryRow(QFrame):
    def __init__(self, item, palette, scale, parent=None):
        super().__init__(parent)
        self.setObjectName('historyrow')

        def s(value):
            return max(1, int(round(value * scale / 100)))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)
        dot = QFrame()
        dot.setFixedSize(s(8), s(8))
        dot.setStyleSheet(
            f'background: {item["colors"][0]};'
            f' border-radius: {s(4)}px;')
        lay.addWidget(dot)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = ElideLabel(item['title'])
        title.setObjectName('rowtitle')
        meta = ElideLabel(f"{item['channel']}  ·  {item['when']}")
        meta.setObjectName('rowmeta')
        text.addWidget(title)
        text.addWidget(meta)
        if item.get('progress'):
            text.addWidget(ProgressBarLite(
                item['progress'], s(3), accent=palette['accent']))
        lay.addLayout(text, 1)
        pos = QLabel(item.get('position', ''))
        pos.setObjectName('quality')
        lay.addWidget(pos)
        play = QToolButton()
        play.setObjectName('rowbutton')
        play.setIcon(icon('play', palette['dim'], s(12)))
        play.setIconSize(QSize(s(12), s(12)))
        play.setFixedSize(s(24), s(24))
        lay.addWidget(play)


class ThemeSwatch(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, key, palette, current, scale, parent=None):
        super().__init__(parent)
        self._key = key

        def s(value):
            return max(1, int(round(value * scale / 100)))

        self.setObjectName('themeswatch')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(s(48))
        border = palette['accent'] if current else palette['border']
        self.setStyleSheet(
            f'QFrame#themeswatch {{ background: {palette["bg"]};'
            f' border: 2px solid {border}; border-radius: {s(10)}px; }}'
            f'QFrame#themeswatch:hover {{ border-color: '
            f'{palette["accent"]}; }}')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(7)
        dot = QFrame()
        dot.setFixedSize(s(12), s(12))
        dot.setStyleSheet(
            f'background: {palette["accent"]};'
            f' border-radius: {s(6)}px;')
        lay.addWidget(dot)
        label = QLabel(palette['title'])
        label.setStyleSheet(
            f'color: {palette["text"]}; font-size: {s(11)}px;'
            ' font-weight: 600;')
        lay.addWidget(label, 1)
        if current:
            lay.addWidget(IconLabel('check', palette['accent'], s(14)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)


class TegrWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.theme_key = 'paper'
        self.scale = 100
        self.selected_channel = 0
        self.selected_video = 0
        self.right_tab = 0
        self.prefs = {'tray': True, 'autoplay': True, 'hwdec': True}
        self._scale_timer = QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.timeout.connect(self._apply_pending_scale)

        self.setWindowTitle('Tegr — Paper × Studio (прототип)')
        self.resize(1280, 820)
        self.setMinimumSize(1120, 720)
        self._build_root()

    @property
    def p(self):
        return THEMES[self.theme_key]

    def s(self, value):
        return max(1, int(round(value * self.scale / 100)))

    def _column(self, value):
        """Колонки меняются слабее текста, чтобы центр не схлопывался."""
        factor = 0.90 + 0.10 * self.scale / 100.0
        return max(1, int(round(value * factor)))

    # ------------------------------------------------------------------
    def _build_root(self):
        p = self.p
        self.setStyleSheet(build_style(p, self.scale))
        old = self.takeCentralWidget()
        if old is not None:
            old.deleteLater()
        root = PaperBackground(p)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(self.s(14), self.s(14),
                                 self.s(14), self.s(14))
        outer.setSpacing(self.s(12))
        outer.addWidget(self._build_toolbar())

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setObjectName('main')
        body.setHandleWidth(self.s(12))
        body.setChildrenCollapsible(False)
        body.addWidget(self._build_left())
        body.addWidget(self._build_center())
        body.addWidget(self._build_right())
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setStretchFactor(2, 0)
        body.setSizes([self._column(260), 1, self._column(330)])
        outer.addWidget(body, 1)

    # ------------------------------------------------------------------
    def _build_toolbar(self):
        p = self.p
        bar = QFrame()
        bar.setObjectName('topbar')
        bar.setFixedHeight(self.s(62))
        add_shadow(bar, p['shadow'], self.s(26), self.s(5),
                   55 if p['bg'] != '#101214' else 90)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(self.s(12), 0, self.s(12), 0)
        lay.setSpacing(self.s(8))

        bubble = IconBubble('play', p['accent'], p['accent2'],
                            size=self.s(34), icon_size=self.s(14))
        lay.addWidget(bubble)
        brand = QLabel('Tegr')
        brand.setObjectName('brand')
        lay.addWidget(brand)
        sub = QLabel('Telegram player')
        sub.setObjectName('brandsub')
        lay.addWidget(sub)
        lay.addSpacing(self.s(14))

        self.folder = FolderCombo(p, self.scale)
        self.folder.setObjectName('folder')
        self.folder.addItems(FOLDERS)
        self.folder.setFixedWidth(self.s(150))
        lay.addWidget(self.folder)

        pill = QFrame()
        pill.setObjectName('pill')
        pill_lay = QHBoxLayout(pill)
        pill_lay.setContentsMargins(self.s(8), self.s(3),
                                    self.s(9), self.s(3))
        pill_lay.setSpacing(self.s(6))
        pill_lay.addWidget(StatusDot(p['good'], self.s(7)))
        pill_name = QLabel('Telegram')
        pill_name.setObjectName('pilltext')
        pill_lay.addWidget(pill_name)
        online = QLabel('online')
        online.setObjectName('pillstate')
        pill_lay.addWidget(online)
        lay.addWidget(pill)

        lay.addStretch(1)
        self.search = SearchField('Поиск по каналу и видео…',
                                  p['dim'], height=self.s(36))
        self.search.setMinimumWidth(self.s(150))
        self.search.setMaximumWidth(self.s(340))
        lay.addWidget(self.search, 1)

        gear = QToolButton()
        gear.setObjectName('tool')
        gear.setIcon(icon('settings', p['dim'], self.s(18)))
        gear.setIconSize(QSize(self.s(18), self.s(18)))
        gear.setFixedSize(self.s(34), self.s(34))
        gear.setCursor(Qt.CursorShape.PointingHandCursor)
        gear.setToolTip('Настройки')
        gear.clicked.connect(lambda: self._switch_right_tab(2))
        lay.addWidget(gear)
        lay.addWidget(Avatar('Я', p['good'], self.s(30)))
        return bar

    # ------------------------------------------------------------------
    def _build_left(self):
        p = self.p
        panel = QFrame()
        panel.setObjectName('panel')
        panel.setFixedWidth(self._column(260))
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(self.s(10), self.s(12),
                               self.s(10), self.s(10))
        lay.setSpacing(self.s(8))

        head = QHBoxLayout()
        title = QLabel('КАНАЛЫ')
        title.setObjectName('eyebrow')
        head.addWidget(title)
        head.addStretch(1)
        count = QLabel('12')
        count.setObjectName('countbadge')
        head.addWidget(count)
        lay.addLayout(head)

        self.channel_filter = SearchField('Фильтр каналов', p['dim'],
                                          height=self.s(32))
        lay.addWidget(self.channel_filter)

        self.channel_rows = []
        for index, channel in enumerate(CHANNELS):
            row = ChannelRow(index, channel, p, self.scale,
                             active=(index == self.selected_channel))
            row.clicked.connect(self._select_channel)
            lay.addWidget(row)
            self.channel_rows.append(row)
        lay.addStretch(1)
        foot = QLabel('Telegram · 12 каналов · 3 папки')
        foot.setObjectName('footnote')
        lay.addWidget(foot)
        return panel

    # ------------------------------------------------------------------
    def _build_center(self):
        p = self.p
        panel = QFrame()
        panel.setObjectName('panel')
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(self.s(12), self.s(12),
                               self.s(12), self.s(10))
        lay.setSpacing(self.s(8))

        head = QHBoxLayout()
        title = QLabel('ВИДЕО')
        title.setObjectName('eyebrow')
        head.addWidget(title)
        count = QLabel('128')
        count.setObjectName('countbadge')
        head.addWidget(count)
        head.addStretch(1)
        self.sort_group = QButtonGroup(self)
        self.sort_group.setExclusive(True)
        for index, text in enumerate(('Новые', 'Старые', 'По размеру')):
            btn = QPushButton(text)
            btn.setObjectName('sort')
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.sort_group.addButton(btn, index)
            head.addWidget(btn)
        lay.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            'QScrollArea { background: transparent; border: none; }'
            'QScrollArea > QWidget > QWidget { background: transparent; }')
        scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(self.s(6))
        self.video_rows = []
        for index, video in enumerate(VIDEOS):
            row = VideoRow(index, video, p, self.scale,
                           active=(index == self.selected_video))
            row.clicked.connect(self._select_video)
            inner_lay.addWidget(row)
            self.video_rows.append(row)
        inner_lay.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        foot = QHBoxLayout()
        status = QLabel('12 каналов · 128 видео')
        status.setObjectName('footnote')
        foot.addWidget(status)
        foot.addStretch(1)
        select = QPushButton('Выбрать...')
        select.setObjectName('ghost')
        select.setFixedHeight(self.s(30))
        foot.addWidget(select)
        download = QPushButton('  Скачать все')
        download.setObjectName('primary')
        download.setIcon(icon('download', p['on_accent'], self.s(13)))
        download.setFixedHeight(self.s(30))
        foot.addWidget(download)
        lay.addLayout(foot)
        return panel

    # ------------------------------------------------------------------
    def _build_right(self):
        p = self.p
        panel = QFrame()
        panel.setObjectName('panel')
        panel.setFixedWidth(self._column(330))
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(self.s(10), self.s(12),
                               self.s(10), self.s(10))
        lay.setSpacing(self.s(8))

        self.now_card = QFrame()
        self.now_card.setObjectName('nowcard')
        now = QVBoxLayout(self.now_card)
        now.setContentsMargins(self.s(10), self.s(10),
                               self.s(10), self.s(10))
        now.setSpacing(self.s(6))
        label = QLabel('СЕЙЧАС В ПЛЕЕРЕ')
        label.setObjectName('eyebrow')
        now.addWidget(label)
        poster = Poster(VIDEOS[0]['colors'], VIDEOS[0]['duration'],
                        radius=self.s(8))
        poster.setFixedHeight(self.s(112))
        now.addWidget(poster)
        title = QLabel(VIDEOS[0]['title'])
        title.setObjectName('nowtitle')
        now.addWidget(title)
        meta = QLabel(f"{VIDEOS[0]['channel']} · {VIDEOS[0]['meta']}")
        meta.setObjectName('nowmeta')
        now.addWidget(meta)
        now.addWidget(ProgressBarLite(0.38, self.s(4),
                                      accent=p['accent']))
        actions = QHBoxLayout()
        actions.setSpacing(self.s(6))
        cont = QPushButton('Продолжить')
        cont.setObjectName('primary')
        cont.setFixedHeight(self.s(30))
        actions.addWidget(cont, 1)
        save = QPushButton('Скачать')
        save.setObjectName('ghost')
        save.setFixedHeight(self.s(30))
        actions.addWidget(save)
        now.addLayout(actions)
        lay.addWidget(self.now_card)

        tabs = QHBoxLayout()
        tabs.setSpacing(self.s(2))
        self.right_group = QButtonGroup(self)
        self.right_group.setExclusive(True)
        self.right_tab_buttons = []
        for index, text in enumerate(('Загрузки', 'История',
                                      'Настройки')):
            btn = QPushButton(text)
            btn.setObjectName('segment')
            btn.setCheckable(True)
            btn.setChecked(index == self.right_tab)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.right_group.addButton(btn, index)
            btn.clicked.connect(
                lambda _checked=False, i=index: self._switch_right_tab(i))
            tabs.addWidget(btn, 1)
            self.right_tab_buttons.append(btn)
        lay.addLayout(tabs)

        self.select_bar = QWidget()
        select_lay = QHBoxLayout(self.select_bar)
        select_lay.setContentsMargins(0, 0, 0, 0)
        select_lay.addStretch(1)
        select = QPushButton('Выбрать...')
        select.setObjectName('ghost')
        select.setFixedHeight(self.s(26))
        select_lay.addWidget(select)
        self.clear_btn = QPushButton('Очистить')
        self.clear_btn.setObjectName('ghost')
        self.clear_btn.setFixedHeight(self.s(26))
        select_lay.addWidget(self.clear_btn)
        lay.addWidget(self.select_bar)

        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(self._downloads_page())
        self.right_stack.addWidget(self._history_page())
        self.right_stack.addWidget(self._settings_page())
        lay.addWidget(self.right_stack, 1)
        self._switch_right_tab(self.right_tab)
        return panel

    def _downloads_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self.s(6))
        for item in DOWNLOADS:
            lay.addWidget(DownloadRow(item, self.p, self.scale))
        lay.addStretch(1)
        return scroll_page(page)

    def _history_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self.s(6))
        for item in HISTORY:
            lay.addWidget(HistoryRow(item, self.p, self.scale))
        lay.addStretch(1)
        return scroll_page(page)

    # ------------------------------------------------------------------
    # Настройки: темы и масштаб
    # ------------------------------------------------------------------
    def _settings_page(self):
        p = self.p
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, self.s(4), 0)
        lay.setSpacing(self.s(8))

        title = QLabel('Настройки')
        title.setObjectName('settingshead')
        lay.addWidget(title)
        sub = QLabel('Тема и масштаб применяются сразу.')
        sub.setObjectName('settingssub')
        sub.setWordWrap(True)
        lay.addWidget(sub)

        theme_label = QLabel('ТЕМА')
        theme_label.setObjectName('eyebrow')
        lay.addWidget(theme_label)
        grid = QGridLayout()
        grid.setHorizontalSpacing(self.s(6))
        grid.setVerticalSpacing(self.s(6))
        for index, (key, palette) in enumerate(THEMES.items()):
            swatch = ThemeSwatch(key, palette, key == self.theme_key,
                                 self.scale)
            swatch.clicked.connect(self._set_theme)
            grid.addWidget(swatch, index // 2, index % 2)
        for col in range(2):
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)

        scale_label = QLabel('МАСШТАБ')
        scale_label.setObjectName('eyebrow')
        lay.addWidget(scale_label)
        scale_row = QHBoxLayout()
        scale_row.setSpacing(self.s(8))
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setObjectName('scale')
        self.scale_slider.setRange(75, 150)
        self.scale_slider.setValue(self.scale)
        self.scale_slider.setSingleStep(5)
        self.scale_slider.setPageStep(10)
        self.scale_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        scale_row.addWidget(self.scale_slider, 1)
        self.scale_value = QLabel(f'{self.scale}%')
        self.scale_value.setObjectName('scalevalue')
        scale_row.addWidget(self.scale_value)
        lay.addLayout(scale_row)
        self.scale_slider.valueChanged.connect(self._on_scale_changed)
        hint = QLabel('75–150%. Текст, строки и отступы.')
        hint.setObjectName('settingssub')
        hint.setWordWrap(True)
        lay.addWidget(hint)

        prefs_label = QLabel('ПОВЕДЕНИЕ')
        prefs_label.setObjectName('eyebrow')
        lay.addWidget(prefs_label)
        for key, title_text, desc in (
            ('tray', 'Сворачивать в трей',
             'Крестик скрывает окно.'),
            ('autoplay', 'Автоспроизведение',
             'Следующий ролик сам.'),
            ('hwdec', 'Аппаратное ускорение',
             'Декодировать видео на GPU.'),
        ):
            card = QFrame()
            card.setObjectName('settingscard')
            row = QHBoxLayout(card)
            row.setContentsMargins(self.s(9), self.s(7),
                                   self.s(9), self.s(7))
            text = QVBoxLayout()
            text.setSpacing(1)
            h = QLabel(title_text)
            h.setObjectName('rowtitle')
            d = QLabel(desc)
            d.setObjectName('rowmeta')
            text.addWidget(h)
            text.addWidget(d)
            row.addLayout(text, 1)
            toggle = ToggleSwitch(self.prefs[key], p['accent'],
                                  p['border'])
            toggle.toggled.connect(
                lambda checked, k=key: self.prefs.__setitem__(k, checked))
            row.addWidget(toggle)
            lay.addWidget(card)

        more_label = QLabel('ПРОЧЕЕ')
        more_label.setObjectName('eyebrow')
        lay.addWidget(more_label)
        for title_text, meta_text, action_text in (
            ('Аккаунт', '@telegram · подключён', ''),
            ('Прокси', 'Системный', 'Изменить'),
            ('Кэш', '1,4 ГБ', 'Очистить'),
            ('Обновления', 'Tegr 3.6.2d', 'Проверить'),
        ):
            card = QFrame()
            card.setObjectName('settingscard')
            row = QHBoxLayout(card)
            row.setContentsMargins(self.s(9), self.s(7),
                                   self.s(9), self.s(7))
            text = QVBoxLayout()
            text.setSpacing(1)
            h = QLabel(title_text)
            h.setObjectName('rowtitle')
            m = QLabel(meta_text)
            m.setObjectName('rowmeta')
            text.addWidget(h)
            text.addWidget(m)
            row.addLayout(text, 1)
            if action_text:
                btn = QPushButton(action_text)
                btn.setObjectName('ghost')
                btn.setFixedHeight(self.s(26))
                row.addWidget(btn)
            else:
                row.addWidget(IconLabel('chevron', p['dim'], self.s(13)))
            lay.addWidget(card)

        lay.addStretch(1)
        return scroll_page(page)

    # ------------------------------------------------------------------
    def _switch_right_tab(self, index):
        if not hasattr(self, 'right_stack'):
            self.right_tab = index
            return
        index = max(0, min(2, int(index)))
        self.right_tab = index
        self.right_stack.setCurrentIndex(index)
        for i, btn in enumerate(self.right_tab_buttons):
            btn.setChecked(i == index)
        self.now_card.setVisible(index != 2)
        self.select_bar.setVisible(index != 2)
        self.clear_btn.setText('Удалить все' if index == 0 else 'Очистить')

    def _select_channel(self, index):
        self.selected_channel = index
        for i, row in enumerate(self.channel_rows):
            row.set_active(i == index)

    def _select_video(self, index):
        self.selected_video = index
        for i, row in enumerate(self.video_rows):
            row.set_active(i == index)

    def _on_scale_changed(self, value):
        if hasattr(self, 'scale_value'):
            self.scale_value.setText(f'{value}%')
        self._scale_timer.start(300)

    def _apply_pending_scale(self):
        if not hasattr(self, 'scale_slider'):
            return
        value = int(self.scale_slider.value())
        if value != self.scale:
            self.scale = value
            QTimer.singleShot(0, self._rebuild)

    def _set_theme(self, key):
        if key not in THEMES or key == self.theme_key:
            return
        self.theme_key = key
        QTimer.singleShot(0, self._rebuild)

    def _rebuild(self):
        if hasattr(self, 'right_stack'):
            self.right_tab = self.right_stack.currentIndex()
        self._build_root()


# ----------------------------------------------------------------------
# Стили
# ----------------------------------------------------------------------
def build_style(p, scale):
    def s(value):
        return max(1, int(round(value * scale / 100)))

    return f"""
QWidget {{
    font-family: 'Segoe UI';
    color: {p['text']};
    font-size: {s(12)}px;
}}
QMainWindow {{ background: {p['bg']}; }}

QFrame#topbar, QFrame#panel {{
    background: {p['panel']};
    border: 1px solid {p['border']};
    border-radius: {s(18)}px;
}}
QSplitter#main::handle {{ background: transparent; }}

QLabel#brand {{
    font-family: Georgia; font-size: {s(21)}px; font-weight: 700;
}}
QLabel#brandsub {{
    color: {p['dim']}; font-size: {s(10)}px; padding-top: {s(4)}px;
}}
QLabel#pilltext {{ font-size: {s(10)}px; font-weight: 600; }}
QLabel#pillstate {{ color: {p['good']}; font-size: {s(9)}px; }}
QFrame#pill {{
    background: {p['soft']};
    border: 1px solid {p['border']};
    border-radius: {s(10)}px;
}}

QComboBox#folder {{
    background: {p['field']};
    border: 1px solid {p['border']};
    border-radius: {s(8)}px;
    padding: {s(5)}px {s(9)}px;
    color: {p['text']};
}}
QComboBox#folder:hover {{ border-color: {p['accent']}; }}
QComboBox#folder::drop-down {{ border: none; width: {s(18)}px; }}
QComboBox#folder QAbstractItemView {{
    background: {p['panel']};
    border: 1px solid {p['border']};
    border-radius: {s(8)}px;
    padding: {s(4)}px;
    outline: none;
    selection-background-color: {p['soft']};
    color: {p['text']};
}}

QLineEdit#search {{
    background: {p['field']};
    border: 1px solid {p['border']};
    border-radius: {s(10)}px;
    padding: {s(5)}px {s(10)}px {s(5)}px {s(5)}px;
    color: {p['text']};
    selection-background-color: {p['soft']};
}}
QLineEdit#search:focus {{
    background: {p['panel']};
    border-color: {p['accent']};
}}

QToolButton#tool {{
    background: {p['field']};
    border: 1px solid {p['border']};
    border-radius: {s(8)}px;
}}
QToolButton#tool:hover {{ border-color: {p['accent']}; }}

QLabel#eyebrow {{
    color: {p['dim']}; font-size: {s(9)}px; font-weight: 700;
    letter-spacing: {s(1)}px;
}}
QLabel#countbadge {{
    background: {p['soft']};
    color: {p['accent']};
    border-radius: {s(8)}px;
    padding: {s(1)}px {s(6)}px;
    font-size: {s(9)}px; font-weight: 700;
}}
QLabel#footnote {{ color: {p['dim']}; font-size: {s(9)}px; }}

QFrame#channelrow {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {s(9)}px;
}}
QFrame#channelrow:hover {{ background: {p['field']}; }}
QFrame#channelrow[active="true"] {{
    background: {p['soft']};
    border: 1px solid {p['border']};
}}
QFrame#activebar {{ background: {p['accent']}; border-radius: {s(2)}px; }}
QLabel#channelname {{ font-size: {s(12)}px; font-weight: 600; }}
QLabel#channelcount {{ color: {p['dim']}; font-size: {s(9)}px; }}

QPushButton#sort {{
    background: transparent; border: none;
    color: {p['dim']}; font-size: {s(10)}px;
    padding: {s(4)}px {s(5)}px;
}}
QPushButton#sort:hover {{ color: {p['text']}; }}
QPushButton#sort:checked {{ color: {p['accent']}; font-weight: 700; }}

QFrame#videorow, QFrame#downloadrow, QFrame#historyrow {{
    background: {p['field']};
    border: 1px solid {p['border']};
    border-radius: {s(9)}px;
}}
QFrame#videorow:hover, QFrame#downloadrow:hover,
QFrame#historyrow:hover {{
    border-color: {p['accent']};
}}
QFrame#videorow[active="true"] {{
    background: {p['soft']};
    border: 1px solid {p['accent']};
}}
QLabel#rowtitle {{ font-size: {s(12)}px; font-weight: 600; }}
QLabel#rowmeta {{ color: {p['dim']}; font-size: {s(10)}px; }}
QLabel#quality {{
    color: {p['accent']}; font-family: 'Consolas'; font-size: {s(9)}px;
}}

QToolButton#rowbutton {{
    background: {p['panel']};
    border: 1px solid {p['border']};
    border-radius: {s(7)}px;
}}
QToolButton#rowbutton:hover {{
    border-color: {p['accent']};
    background: {p['soft']};
}}

QPushButton#primary {{
    background: {p['accent']};
    border: none;
    border-radius: {s(8)}px;
    color: {p['on_accent']};
    font-weight: 700;
    padding: 0 {s(12)}px;
}}
QPushButton#primary:hover {{ background: {p['accent2']}; }}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid {p['border']};
    border-radius: {s(8)}px;
    color: {p['text']};
    padding: 0 {s(10)}px;
}}
QPushButton#ghost:hover {{
    border-color: {p['accent']};
    background: {p['soft']};
}}

QFrame#nowcard {{
    background: {p['field']};
    border: 1px solid {p['border']};
    border-radius: {s(10)}px;
}}
QLabel#nowtitle {{ font-size: {s(13)}px; font-weight: 700; }}
QLabel#nowmeta {{ color: {p['dim']}; font-size: {s(10)}px; }}

QPushButton#segment {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: {p['dim']};
    padding: {s(6)}px {s(2)}px {s(4)}px {s(2)}px;
    font-size: {s(10)}px;
    font-weight: 600;
}}
QPushButton#segment:hover {{ color: {p['text']}; }}
QPushButton#segment:checked {{
    color: {p['accent']};
    border-bottom: 2px solid {p['accent']};
}}

QLabel#settingshead {{
    font-family: Georgia; font-size: {s(21)}px; font-weight: 700;
}}
QLabel#settingssub {{ color: {p['dim']}; font-size: {s(9)}px; }}
QLabel#scalevalue {{
    color: {p['accent']}; font-size: {s(10)}px; font-weight: 700;
    min-width: {s(34)}px;
}}
QFrame#settingscard {{
    background: {p['field']};
    border: 1px solid {p['border']};
    border-radius: {s(9)}px;
}}

QSlider#scale::groove:horizontal {{
    height: {s(4)}px;
    background: {p['border']};
    border-radius: {s(2)}px;
}}
QSlider#scale::sub-page:horizontal {{
    background: {p['accent']};
    border-radius: {s(2)}px;
}}
QSlider#scale::handle:horizontal {{
    width: {s(14)}px; height: {s(14)}px;
    margin: -{s(5)}px 0;
    border-radius: {s(7)}px;
    background: {p['panel']};
    border: 2px solid {p['accent']};
}}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: {s(8)}px; margin: {s(2)}px;
}}
QScrollBar::handle:vertical {{
    background: {p['border']};
    border-radius: {s(4)}px; min-height: {s(30)}px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: {p['panel']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: {s(6)}px;
    padding: {s(4)}px {s(7)}px;
}}
"""


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont('Segoe UI', 10))
    window = TegrWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
