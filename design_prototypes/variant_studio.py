"""Вариант 3 — Studio.

Плотный «рабочий» интерфейс для тех, кто часто переключает каналы и
качает видео: три колонки, компактные строки, янтарный акцент.
Только внешний вид.
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import QPointF, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QRadialGradient
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

try:
    from .common import (
        Avatar, DownloadRow, IconLabel, Poster, ProgressBarLite,
        SearchField, StatusDot, VideoRow, add_shadow, icon, scroll_page)
    from .mock_data import (
        CHANNELS, DOWNLOADS, FOLDERS, HISTORY, VIDEOS)
except ImportError:  # запуск файла напрямую
    from common import (
        Avatar, DownloadRow, IconLabel, Poster, ProgressBarLite,
        SearchField, StatusDot, VideoRow, add_shadow, icon, scroll_page)
    from mock_data import (
        CHANNELS, DOWNLOADS, FOLDERS, HISTORY, VIDEOS)


BG = '#0e1012'
PANEL = '#15181b'
PANEL_2 = '#1a1e22'
BORDER = '#282d33'
TEXT = '#e8e4dd'
DIM = '#8b8780'
AMBER = '#f2b84b'
GREEN = '#6ee7a8'
RED = '#ef6b6b'


class StudioBackground(QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(BG))
        grad = QRadialGradient(
            QPointF(self.width() * 0.78, 0), self.width() * 0.62)
        grad.setColorAt(0.0, QColor(242, 184, 75, 18))
        clear = QColor(242, 184, 75, 0)
        grad.setColorAt(1.0, clear)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRect(self.rect())
        p.end()


class FolderCombo(QComboBox):
    """Комбобокс с явной стрелкой: системная в тёмной теме теряется."""

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        icon('chevron', DIM, 12).paint(
            p, QRect(self.width() - 22, (self.height() - 12) // 2,
                     12, 12))
        p.end()


class ChannelRow(QFrame):
    clicked = pyqtSignal()

    def __init__(self, channel, parent=None):
        super().__init__(parent)
        self.channel = dict(channel)
        self.setObjectName('channelrow')
        self.setProperty('active', bool(channel.get('selected')))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(9)
        lay.addWidget(Avatar(channel['initial'], channel['color'], 30))
        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel(channel['name'])
        name.setObjectName('channelname')
        name.setWordWrap(False)
        count = QLabel(f"{channel['count']} видео")
        count.setObjectName('channelcount')
        text.addWidget(name)
        text.addWidget(count)
        lay.addLayout(text, 1)
        self._bar = QFrame()
        self._bar.setObjectName('activebar')
        self._bar.setFixedWidth(3)
        self._bar.setVisible(bool(channel.get('selected')))
        lay.addWidget(self._bar)

    def set_active(self, active):
        self.setProperty('active', bool(active))
        self._bar.setVisible(bool(active))
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StudioWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Tegr — Studio (прототип)')
        self.resize(1280, 820)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet(STYLE)

        root = StudioBackground()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_toolbar())
        outer.addWidget(self._build_body(), 1)

    # ------------------------------------------------------------------
    # Верхняя панель
    # ------------------------------------------------------------------
    def _build_toolbar(self):
        bar = QFrame()
        bar.setObjectName('toolbar')
        bar.setFixedHeight(58)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(10)

        mark = QFrame()
        mark.setObjectName('mark')
        mark.setFixedSize(30, 30)
        mark_lay = QHBoxLayout(mark)
        mark_lay.setContentsMargins(0, 0, 0, 0)
        mark_lay.addWidget(IconLabel('play', '#1a1205', 14),
                          0, Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(mark)
        brand = QLabel('TEGR')
        brand.setObjectName('brand')
        lay.addWidget(brand)
        studio = QLabel('STUDIO')
        studio.setObjectName('studiolabel')
        lay.addWidget(studio)
        lay.addSpacing(14)

        self.folder = FolderCombo()
        self.folder.setObjectName('folder')
        self.folder.addItems(FOLDERS)
        self.folder.setFixedWidth(160)
        lay.addWidget(self.folder)

        source = QFrame()
        source.setObjectName('sourcepill')
        source_lay = QHBoxLayout(source)
        source_lay.setContentsMargins(9, 4, 9, 4)
        source_lay.setSpacing(6)
        source_lay.addWidget(StatusDot(GREEN, 7))
        source_name = QLabel('Telegram')
        source_name.setObjectName('pilltext')
        source_lay.addWidget(source_name)
        lay.addWidget(source)

        lay.addStretch(1)
        self.search = SearchField('Поиск по каналу и видео…', DIM,
                                  height=36)
        self.search.setFixedWidth(330)
        lay.addWidget(self.search)

        self.settings_btn = QToolButton()
        self.settings_btn.setObjectName('tool')
        self.settings_btn.setIcon(icon('settings', '#c9c4bc', 18))
        self.settings_btn.setIconSize(QSize(18, 18))
        self.settings_btn.setFixedSize(34, 34)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setToolTip('Настройки')
        self.settings_btn.clicked.connect(self._show_settings_menu)
        lay.addWidget(self.settings_btn)

        lay.addWidget(Avatar('Я', '#3c6e71', 30))
        return bar

    # ------------------------------------------------------------------
    # Три колонки
    # ------------------------------------------------------------------
    def _build_body(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName('main')
        splitter.setHandleWidth(1)
        splitter.addWidget(self._left_panel())
        splitter.addWidget(self._center_panel())
        splitter.addWidget(self._right_panel())
        splitter.setSizes([260, 700, 320])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        return splitter

    def _left_panel(self):
        panel = QFrame()
        panel.setObjectName('panel')
        panel.setFixedWidth(260)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel('КАНАЛЫ')
        title.setObjectName('eyebrow')
        head.addWidget(title)
        head.addStretch(1)
        count = QLabel('12')
        count.setObjectName('countbadge')
        head.addWidget(count)
        lay.addLayout(head)

        filter_box = SearchField('Фильтр каналов', DIM, height=32)
        lay.addWidget(filter_box)

        self.channel_rows = []
        for channel in CHANNELS:
            row = ChannelRow(channel)
            row.clicked.connect(
                lambda r=row: self._select_channel(r))
            lay.addWidget(row)
            self.channel_rows.append(row)
        lay.addStretch(1)

        foot = QLabel('Telegram · 12 каналов · 3 папки')
        foot.setObjectName('footnote')
        lay.addWidget(foot)
        return panel

    def _center_panel(self):
        panel = QFrame()
        panel.setObjectName('panel')
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel('ВИДЕО')
        title.setObjectName('eyebrow')
        head.addWidget(title)
        count = QLabel('128')
        count.setObjectName('countbadge')
        head.addWidget(count)
        head.addStretch(1)
        for index, text in enumerate(('Новые', 'Старые', 'По размеру')):
            btn = QPushButton(text)
            btn.setObjectName('sort')
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            head.addWidget(btn)
        lay.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(6)
        for video in VIDEOS:
            inner_lay.addWidget(VideoRow(video))
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
        select.setFixedHeight(30)
        foot.addWidget(select)
        download = QPushButton('  Скачать все')
        download.setObjectName('amber')
        download.setIcon(icon('download', '#1a1205', 14))
        download.setFixedHeight(30)
        foot.addWidget(download)
        lay.addLayout(foot)
        return panel

    def _right_panel(self):
        panel = QFrame()
        panel.setObjectName('panel')
        panel.setFixedWidth(320)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(10)

        # Сейчас в плеере
        now = QFrame()
        now.setObjectName('nowplaying')
        now_lay = QVBoxLayout(now)
        now_lay.setContentsMargins(10, 10, 10, 10)
        now_lay.setSpacing(8)
        label = QLabel('СЕЙЧАС В ПЛЕЕРЕ')
        label.setObjectName('eyebrow')
        now_lay.addWidget(label)
        poster = Poster(VIDEOS[0]['colors'], VIDEOS[0]['duration'],
                        radius=8)
        poster.setFixedHeight(114)
        now_lay.addWidget(poster)
        title = QLabel(VIDEOS[0]['title'])
        title.setObjectName('nowtitle')
        now_lay.addWidget(title)
        meta = QLabel(f"{VIDEOS[0]['channel']} · {VIDEOS[0]['meta']}")
        meta.setObjectName('nowmeta')
        now_lay.addWidget(meta)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        cont = QPushButton('Продолжить')
        cont.setObjectName('amber')
        cont.setFixedHeight(30)
        actions.addWidget(cont, 1)
        save = QPushButton('Скачать')
        save.setObjectName('ghost')
        save.setFixedHeight(30)
        actions.addWidget(save)
        now_lay.addLayout(actions)
        lay.addWidget(now)

        # Переключатель «Загрузки / История»
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.right_group = QButtonGroup(self)
        self.right_group.setExclusive(True)
        self.right_stack = QStackedWidget()
        for index, (text, page) in enumerate((
                ('Загрузки', self._downloads_list()),
                ('История', self._history_list()))):
            btn = QPushButton(text)
            btn.setObjectName('segment')
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.right_group.addButton(btn, index)
            btn.clicked.connect(
                lambda _checked=False, i=index: self.right_stack.setCurrentIndex(i))
            tabs.addWidget(btn, 1)
            self.right_stack.addWidget(page)
        lay.addLayout(tabs)
        row = QHBoxLayout()
        row.addStretch(1)
        select = QPushButton('Выбрать...')
        select.setObjectName('ghost')
        select.setFixedHeight(26)
        row.addWidget(select)
        lay.addLayout(row)
        lay.addWidget(self.right_stack, 1)
        return panel

    def _downloads_list(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for item in DOWNLOADS:
            lay.addWidget(DownloadRow(item, AMBER))
        lay.addStretch(1)
        return scroll_page(page)

    def _history_list(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for item in HISTORY:
            lay.addWidget(self._mini_history(item))
        lay.addStretch(1)
        return scroll_page(page)

    def _mini_history(self, item):
        row = QFrame()
        row.setObjectName('historyrow')
        lay = QHBoxLayout(row)
        lay.setContentsMargins(9, 7, 9, 7)
        lay.setSpacing(8)
        dot = QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            f'background: {item["colors"][0]}; border-radius: 4px;')
        lay.addWidget(dot)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(item['title'])
        title.setObjectName('rowtitle')
        meta = QLabel(f"{item['channel']} · {item['when']}")
        meta.setObjectName('rowmeta')
        text.addWidget(title)
        text.addWidget(meta)
        if item.get('progress'):
            text.addWidget(ProgressBarLite(item['progress'], 3,
                                           accent=AMBER))
        lay.addLayout(text, 1)
        pos = QLabel(item.get('position', ''))
        pos.setObjectName('quality')
        lay.addWidget(pos)
        play = QToolButton()
        play.setObjectName('rowbutton')
        play.setIcon(icon('play', '#ffffff', 13))
        play.setIconSize(QSize(13, 13))
        play.setFixedSize(26, 26)
        lay.addWidget(play)
        return row

    def _show_settings_menu(self):
        menu = QMenu(self)
        for text, checked in (('Сворачивать в трей', True),
                              ('Автоспроизведение', True),
                              ('Аппаратное ускорение', True)):
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(checked)
        menu.exec(self.settings_btn.mapToGlobal(
            self.settings_btn.rect().bottomLeft()))

    def _select_channel(self, row):
        for other in self.channel_rows:
            other.set_active(other is row)


STYLE = f"""
QWidget {{
    font-family: 'Segoe UI';
    color: {TEXT};
    font-size: 12px;
}}
QMainWindow {{ background: {BG}; }}

QFrame#toolbar {{
    background: rgba(21, 24, 27, 0.96);
    border-bottom: 1px solid {BORDER};
}}
QFrame#mark {{ background: {AMBER}; border-radius: 8px; }}
QLabel#brand {{ font-size: 16px; font-weight: 800; letter-spacing: 1px; }}
QLabel#studiolabel {{
    color: {AMBER}; font-size: 9px; font-weight: 700;
    letter-spacing: 2px; padding-top: 3px;
}}
QComboBox#folder {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 10px;
    color: {TEXT};
}}
QComboBox#folder:hover {{ border-color: #3a4148; }}
QComboBox#folder::drop-down {{ border: none; width: 20px; }}
QComboBox#folder QAbstractItemView {{
    background: {PANEL_2}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 4px; outline: none;
    selection-background-color: {AMBER}; color: {TEXT};
}}
QFrame#sourcepill {{
    background: rgba(110, 231, 168, 0.08);
    border: 1px solid rgba(110, 231, 168, 0.22);
    border-radius: 10px;
}}
QLabel#pilltext {{ color: #b9e8d0; font-size: 10px; }}

QLineEdit#search {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px 6px 6px;
    color: {TEXT};
    selection-background-color: {AMBER};
}}
QLineEdit#search:focus {{ border-color: {AMBER}; }}
QLineEdit#search::placeholder {{ color: #6f6a64; }}

QToolButton#tool {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QToolButton#tool:hover {{ background: #20252a; border-color: #3a4148; }}

QSplitter#main::handle {{ background: {BORDER}; }}
QFrame#panel {{ background: {PANEL}; }}
QFrame#leftpanel {{ background: {PANEL}; }}

QLabel#eyebrow {{
    color: #6f6a64; font-size: 9px; font-weight: 700;
    letter-spacing: 1.5px;
}}
QLabel#countbadge {{
    background: rgba(242, 184, 75, 0.12);
    color: {AMBER};
    border-radius: 8px; padding: 1px 7px;
    font-size: 9px; font-weight: 700;
}}
QLabel#footnote {{ color: #6f6a64; font-size: 10px; }}

QFrame#channelrow {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
}}
QFrame#channelrow:hover {{ background: #1b2024; }}
QFrame#channelrow[active="true"] {{
    background: rgba(242, 184, 75, 0.075);
    border: 1px solid rgba(242, 184, 75, 0.20);
}}
QFrame#activebar {{ background: {AMBER}; border-radius: 2px; }}
QLabel#channelname {{ font-size: 12px; font-weight: 600; }}
QLabel#channelcount {{ color: #6f6a64; font-size: 9px; }}

QPushButton#sort {{
    background: transparent; border: none;
    color: #7c7770; font-size: 10px; padding: 4px 6px;
}}
QPushButton#sort:hover {{ color: {TEXT}; }}
QPushButton#sort:checked {{ color: {AMBER}; font-weight: 700; }}

QFrame#videorow, QFrame#downloadrow, QFrame#historyrow {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#videorow:hover, QFrame#downloadrow:hover,
QFrame#historyrow:hover {{
    border-color: #3b4249;
    background: #1c2125;
}}
QLabel#rowtitle {{ font-size: 12px; font-weight: 600; }}
QLabel#rowmeta {{ color: #77726b; font-size: 10px; }}
QLabel#quality {{
    color: {AMBER}; font-family: 'Consolas'; font-size: 10px;
}}
QToolButton#rowbutton {{
    background: #22272c;
    border: 1px solid {BORDER};
    border-radius: 7px;
}}
QToolButton#rowbutton:hover {{
    background: #2a3036; border-color: {AMBER};
}}

QPushButton#amber {{
    background: {AMBER}; border: none; border-radius: 7px;
    color: #1a1205; font-weight: 700; padding: 0 12px;
}}
QPushButton#amber:hover {{ background: #ffc75f; }}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 7px; color: {TEXT}; padding: 0 10px;
}}
QPushButton#ghost:hover {{ background: #22272c; }}

QFrame#nowplaying {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLabel#nowtitle {{ font-size: 14px; font-weight: 700; }}
QLabel#nowmeta {{ color: #77726b; font-size: 10px; }}

QPushButton#segment {{
    background: transparent; border: none;
    border-bottom: 2px solid transparent;
    color: #77726b; padding: 7px 4px 5px 4px;
    font-size: 10px; font-weight: 600;
}}
QPushButton#segment:hover {{ color: {TEXT}; }}
QPushButton#segment:checked {{
    color: {AMBER}; border-bottom: 2px solid {AMBER};
}}

QMenu {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 24px 6px 12px;
    border-radius: 5px;
    color: {TEXT};
}}
QMenu::item:selected {{ background: rgba(242, 184, 75, 0.15); }}
QMenu::indicator {{ width: 14px; height: 14px; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 7px; margin: 1px; }}
QScrollBar::handle:vertical {{
    background: #2d3339; border-radius: 3px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {AMBER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont('Segoe UI', 9))
    window = StudioWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
