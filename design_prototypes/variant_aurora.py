"""Вариант 1 — Aurora.

Тёмный неоновый интерфейс: сайдбар, мягкие фиолетовые свечения,
крупный поиск, чипы фильтров и карточки роликов. Только внешний вид.
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QRadialGradient
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QGraphicsOpacityEffect,
    QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget)

try:
    from .common import (
        Avatar, Chip, EmptyState, DownloadRow, HistoryRow, IconBubble,
        IconLabel, NavItem, SearchField, SectionHeader, ToggleSwitch,
        VideoCard, add_shadow, icon, scroll_page)
    from .mock_data import (
        CHANNELS, DOWNLOADS, FOLDERS, HISTORY, VIDEOS)
except ImportError:  # запуск файла напрямую
    from common import (
        Avatar, Chip, EmptyState, DownloadRow, HistoryRow, IconBubble,
        IconLabel, NavItem, SearchField, SectionHeader, ToggleSwitch,
        VideoCard, add_shadow, icon, scroll_page)
    from mock_data import (
        CHANNELS, DOWNLOADS, FOLDERS, HISTORY, VIDEOS)


ACCENT = '#8b5cf6'
ACCENT_2 = '#6d28d9'
TEXT = '#f4f0ff'
DIM = '#9b91b5'
WARN = '#f2b84b'


class AuroraBackground(QWidget):
    """Фон с двумя мягкими цветными пятнами."""

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor('#08050f'))

        for cx, cy, radius, color in (
            (0.18, 0.05, 0.62, QColor(109, 40, 217, 95)),
            (0.82, 0.30, 0.52, QColor(37, 99, 235, 58)),
            (0.55, 1.05, 0.55, QColor(124, 58, 237, 34)),
        ):
            grad = QRadialGradient(
                QPointF(self.width() * cx, self.height() * cy),
                self.width() * radius)
            grad.setColorAt(0.0, color)
            transparent = QColor(color)
            transparent.setAlpha(0)
            grad.setColorAt(1.0, transparent)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRect(self.rect())
        p.end()


class AuroraWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Tegr — Aurora (прототип)')
        self.resize(1220, 800)
        self.setMinimumSize(1040, 700)
        self.setStyleSheet(STYLE)

        root = AuroraBackground()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)
        outer.addWidget(self._build_sidebar())
        outer.addWidget(self._build_content(), 1)

    # ------------------------------------------------------------------
    # Сайдбар
    # ------------------------------------------------------------------
    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName('sidebar')
        side.setFixedWidth(258)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(16, 18, 16, 16)
        lay.setSpacing(8)

        logo_row = QHBoxLayout()
        logo_row.setSpacing(10)
        bubble = IconBubble('play', ACCENT, ACCENT_2, size=42, icon_size=17)
        add_shadow(bubble, ACCENT, 30, 4, 115)
        logo_row.addWidget(bubble)
        logo_text = QVBoxLayout()
        logo_text.setSpacing(0)
        title = QLabel('Tegr')
        title.setObjectName('logoTitle')
        sub = QLabel('медиаплеер')
        sub.setObjectName('logoSub')
        logo_text.addWidget(title)
        logo_text.addWidget(sub)
        logo_row.addLayout(logo_text)
        logo_row.addStretch(1)
        lay.addLayout(logo_row)
        lay.addSpacing(14)

        self.side_search = SearchField(
            'Поиск', DIM, height=38)
        lay.addWidget(self.side_search)
        lay.addSpacing(8)

        self.nav_items = []
        nav_data = [
            ('Поиск', 'search', 0),
            ('Каналы', 'tv', 1),
            ('История', 'history', 2),
            ('Загрузки', 'download', 3),
            ('Настройки', 'settings', 4),
        ]
        for text, icon_name, page in nav_data:
            item = NavItem(
                text, icon_name, DIM, active_color=TEXT,
                badge='3' if text == 'Загрузки' else None)
            item.clicked.connect(
                lambda _checked=False, p=page: self._switch_page(p))
            lay.addWidget(item)
            self.nav_items.append(item)
        self.nav_items[0].set_active(True)

        lay.addSpacing(18)
        label = QLabel('ИЗБРАННЫЕ КАНАЛЫ')
        label.setObjectName('eyebrow')
        lay.addWidget(label)

        for channel in CHANNELS[:3]:
            lay.addWidget(self._favorite_row(channel))
        lay.addStretch(1)

        self.side_status = QLabel('12 каналов · 3 папки')
        self.side_status.setObjectName('sidefoot')
        lay.addWidget(self.side_status)
        return side

    def _favorite_row(self, channel):
        row = QFrame()
        row.setObjectName('sourcerow')
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(9)
        lay.addWidget(Avatar(channel['initial'], channel['color'], 22))
        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel(channel['name'])
        name.setObjectName('sourcename')
        kind = QLabel(f"{channel['count']} видео")
        kind.setObjectName('sourcekind')
        text.addWidget(name)
        text.addWidget(kind)
        lay.addLayout(text, 1)
        return row

    # ------------------------------------------------------------------
    # Основная область
    # ------------------------------------------------------------------
    def _build_content(self):
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        self.search_tools = QWidget()
        tools_lay = QVBoxLayout(self.search_tools)
        tools_lay.setContentsMargins(0, 0, 0, 0)
        tools_lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.main_search = SearchField(
            'Название фильма, сериала или аниме…', DIM, height=48)
        top.addWidget(self.main_search, 1)
        find = QPushButton('Найти')
        find.setObjectName('primary')
        find.setFixedHeight(48)
        find.setCursor(Qt.CursorShape.PointingHandCursor)
        add_shadow(find, ACCENT, 24, 5, 95)
        top.addWidget(find)
        tools_lay.addLayout(top)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        for index, text in enumerate(
                ('Все', 'Фильмы', 'Сериалы', 'Аниме', 'Документальное')):
            chip = Chip(text, checked=index == 0)
            self.chip_group.addButton(chip, index)
            chips.addWidget(chip)
        chips.addStretch(1)
        tools_lay.addLayout(chips)

        lay.addWidget(self.search_tools)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._search_page())
        self.stack.addWidget(self._channels_page())
        self.stack.addWidget(self._history_page())
        self.stack.addWidget(self._downloads_page())
        self.stack.addWidget(self._settings_page())
        lay.addWidget(self.stack, 1)
        return content

    def _search_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 18)
        lay.setSpacing(18)

        empty = EmptyState(
            'film', 'Что посмотрим?',
            'Поиск по всем каналам и папкам Telegram.\n'
            'Выберите канал слева или найдите видео.',
            ACCENT, ACCENT_2)
        empty.setFixedHeight(236)
        lay.addWidget(empty)

        head = SectionHeader('Продолжить просмотр', 'Вся история')
        lay.addWidget(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        for index, video in enumerate(VIDEOS[:3]):
            card = VideoCard(video)
            add_shadow(card, '#000000', 22, 6, 62)
            grid.addWidget(card, 0, index)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)
        lay.addStretch(1)
        return scroll_page(page)

    def _page_head(self, title, subtitle):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(3)
        h = QLabel(title)
        h.setObjectName('pagetitle')
        s = QLabel(subtitle)
        s.setObjectName('pagesub')
        s.setWordWrap(True)
        lay.addWidget(h)
        lay.addWidget(s)
        return box

    def _channels_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 18)
        lay.setSpacing(12)
        lay.addWidget(self._page_head(
            'Каналы', 'Ваши каналы и папки Telegram.'))

        chips = QHBoxLayout()
        chips.setSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, text in enumerate(FOLDERS):
            chip = Chip(text, checked=index == 0)
            group.addButton(chip, index)
            chips.addWidget(chip)
        chips.addStretch(1)
        lay.addLayout(chips)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for index, channel in enumerate(CHANNELS):
            card = QFrame()
            card.setObjectName('sourcecard')
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 12, 14, 12)
            row.setSpacing(10)
            row.addWidget(Avatar(channel['initial'], channel['color'],
                                 40))
            text = QVBoxLayout()
            text.setSpacing(2)
            name = QLabel(channel['name'])
            name.setObjectName('sourcename')
            count = QLabel(f"{channel['count']} видео")
            count.setObjectName('sourcekind')
            text.addWidget(name)
            text.addWidget(count)
            row.addLayout(text, 1)
            row.addWidget(IconLabel('chevron', DIM, 14))
            grid.addWidget(card, index // 3, index % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)
        lay.addStretch(1)
        return scroll_page(page)

    def _history_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(self._page_head(
            'История', 'Последние просмотры — с названием, каналом и '
                       'местом, где остановились.'), 1)
        select = QPushButton('Выбрать...')
        select.setObjectName('ghost')
        select.setFixedHeight(36)
        head.addWidget(select)
        clear = QPushButton('Очистить')
        clear.setObjectName('ghost')
        clear.setFixedHeight(36)
        head.addWidget(clear)
        lay.addLayout(head)
        for item in HISTORY:
            lay.addWidget(HistoryRow(item))
        lay.addStretch(1)
        return scroll_page(page)

    def _downloads_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(self._page_head(
            'Загрузки', 'Активные загрузки и готовые файлы в кэше.'), 1)
        select = QPushButton('Выбрать...')
        select.setObjectName('ghost')
        select.setFixedHeight(36)
        head.addWidget(select)
        clear = QPushButton('Удалить все')
        clear.setObjectName('ghost')
        clear.setFixedHeight(36)
        head.addWidget(clear)
        lay.addLayout(head)
        for item in DOWNLOADS:
            lay.addWidget(DownloadRow(item, ACCENT))
        lay.addStretch(1)
        return scroll_page(page)

    def _settings_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 18)
        lay.setSpacing(12)
        lay.addWidget(self._page_head(
            'Настройки', 'Внешний вид и поведение плеера.'))
        for title, desc, checked in (
            ('Сворачивать в трей', 'Крестик скрывает окно, а не закрывает.',
             True),
            ('Автоспроизведение', 'Переходить к следующему ролику самому.',
             True),
            ('Аппаратное ускорение', 'Декодировать видео на видеокарте.',
             True),
            ('Светлая тема', 'Интерфейс в светлых тонах.', False),
        ):
            card = QFrame()
            card.setObjectName('sourcecard')
            row = QHBoxLayout(card)
            row.setContentsMargins(16, 12, 16, 12)
            text = QVBoxLayout()
            text.setSpacing(2)
            h = QLabel(title)
            h.setObjectName('cardtitle')
            d = QLabel(desc)
            d.setObjectName('cardmeta')
            text.addWidget(h)
            text.addWidget(d)
            row.addLayout(text, 1)
            row.addWidget(ToggleSwitch(checked, ACCENT, '#332d44'))
            lay.addWidget(card)
        lay.addStretch(1)
        return scroll_page(page)

    # ------------------------------------------------------------------
    # Переключение страниц
    # ------------------------------------------------------------------
    def _switch_page(self, index):
        if index < 0 or index >= self.stack.count():
            return
        for i, item in enumerate(self.nav_items):
            item.set_active(i == index)
        self.search_tools.setVisible(index == 0)
        self.stack.setCurrentIndex(index)
        effect = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b'opacity', self)
        anim.setDuration(170)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._fade = anim


STYLE = f"""
QWidget {{
    font-family: 'Segoe UI';
    color: {TEXT};
    font-size: 13px;
}}
QMainWindow {{ background: #08050f; }}

QFrame#sidebar {{
    background: rgba(20, 13, 33, 0.78);
    border: 1px solid rgba(255, 255, 255, 0.075);
    border-radius: 22px;
}}
QLabel#logoTitle {{ font-size: 21px; font-weight: 700; }}
QLabel#logoSub {{ color: {DIM}; font-size: 11px; }}
QLabel#eyebrow {{
    color: #6f6686; font-size: 10px; font-weight: 700;
    letter-spacing: 1px; padding-left: 4px;
}}
QLabel#sidefoot {{ color: #6f6686; font-size: 10px; padding-left: 4px; }}

QFrame#navitem {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 12px;
}}
QFrame#navitem:hover {{
    background: rgba(255, 255, 255, 0.055);
}}
QFrame#navitem[active="true"] {{
    background: rgba(139, 92, 246, 0.17);
    border: 1px solid rgba(139, 92, 246, 0.38);
}}
QLabel#navtext {{ color: {DIM}; font-size: 13px; }}
QFrame#navitem[active="true"] QLabel#navtext {{ color: #ffffff; }}
QLabel#navbadge {{
    background: {ACCENT}; color: #ffffff; font-size: 10px;
    font-weight: 700; border-radius: 8px; padding: 1px 6px;
}}

QLineEdit#search {{
    background: rgba(255, 255, 255, 0.055);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 13px;
    padding: 8px 12px 8px 8px;
    color: {TEXT};
    selection-background-color: {ACCENT};
}}
QLineEdit#search:focus {{
    border: 1px solid {ACCENT};
    background: rgba(255, 255, 255, 0.075);
}}
QLineEdit#search::placeholder {{ color: #7d7394; }}

QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {ACCENT}, stop:1 {ACCENT_2});
    border: none; border-radius: 13px; padding: 0 22px;
    color: #ffffff; font-weight: 700;
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #9b6dff, stop:1 #7c3aed);
}}
QPushButton#ghost {{
    background: rgba(255, 255, 255, 0.055);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 10px; padding: 0 14px; color: {TEXT};
}}
QPushButton#ghost:hover {{ background: rgba(255, 255, 255, 0.10); }}
QPushButton#linkbutton {{
    background: transparent; border: none; color: {ACCENT};
    padding: 4px 2px; font-weight: 600;
}}

QPushButton#chip {{
    background: rgba(255, 255, 255, 0.055);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 16px; padding: 6px 14px; color: #c9c0dc;
}}
QPushButton#chip:hover {{ background: rgba(255, 255, 255, 0.10); }}
QPushButton#chip:checked {{
    background: {ACCENT}; border: 1px solid {ACCENT};
    color: #ffffff; font-weight: 600;
}}

QLabel#pagetitle {{ font-size: 24px; font-weight: 700; }}
QLabel#pagesub {{ color: {DIM}; font-size: 12px; }}
QLabel#sectiontitle {{ font-size: 15px; font-weight: 700; }}

QFrame#card {{
    background: rgba(255, 255, 255, 0.045);
    border: 1px solid rgba(255, 255, 255, 0.085);
    border-radius: 16px;
}}
QFrame#card:hover {{ border: 1px solid rgba(139, 92, 246, 0.65); }}
QLabel#cardtitle {{ font-size: 13px; font-weight: 600; }}
QLabel#cardmeta {{ color: {DIM}; font-size: 11px; }}

QFrame#sourcecard {{
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
}}
QFrame#sourcecard:hover {{ border: 1px solid rgba(139, 92, 246, 0.42); }}

QLabel#sourcename {{ color: #ded7ee; font-size: 12px; }}
QLabel#sourcekind {{ color: #6f6686; font-size: 10px; }}
QLabel#sourcecount {{ color: #6f6686; font-size: 10px; }}
QFrame#sourcerow {{ border-radius: 10px; }}
QFrame#sourcerow:hover {{ background: rgba(255, 255, 255, 0.045); }}

QFrame#historyrow, QFrame#downloadrow, QFrame#videorow {{
    background: rgba(255, 255, 255, 0.035);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 12px;
}}
QFrame#historyrow:hover, QFrame#downloadrow:hover,
QFrame#videorow:hover {{
    border: 1px solid rgba(139, 92, 246, 0.42);
    background: rgba(255, 255, 255, 0.055);
}}
QLabel#rowtitle {{ font-size: 13px; font-weight: 600; }}
QLabel#rowmeta {{ color: {DIM}; font-size: 11px; }}
QLabel#quality {{ color: #9187a6; font-size: 10px; }}

QToolButton#rowbutton {{
    background: rgba(255, 255, 255, 0.06);
    border: none; border-radius: 9px;
}}
QToolButton#rowbutton:hover {{ background: rgba(139, 92, 246, 0.55); }}

QLabel#emptytitle {{ font-size: 24px; font-weight: 700; }}
QLabel#emptysub {{ color: {DIM}; font-size: 12px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: rgba(255, 255, 255, 0.12);
    border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont('Segoe UI', 10))
    window = AuroraWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
