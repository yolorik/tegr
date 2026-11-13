"""Вариант 2 — Paper.

Светлый «редакционный» интерфейс: тёплый фон, серифные заголовки,
верхняя навигация, карточки с мягкими тенями и правая колонка с очередью.
Только внешний вид.
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QRadialGradient
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QGraphicsOpacityEffect,
    QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

try:
    from .common import (
        Avatar, Chip, DownloadRow, HistoryRow, IconBubble, IconLabel,
        SearchField, SectionHeader, ToggleSwitch, VideoCard,
        VideoRow, add_shadow, icon, scroll_page)
    from .mock_data import (
        CHANNELS, DOWNLOADS, FOLDERS, HISTORY, VIDEOS)
except ImportError:  # запуск файла напрямую
    from common import (
        Avatar, Chip, DownloadRow, HistoryRow, IconBubble, IconLabel,
        SearchField, SectionHeader, ToggleSwitch, VideoCard,
        VideoRow, add_shadow, icon, scroll_page)
    from mock_data import (
        CHANNELS, DOWNLOADS, FOLDERS, HISTORY, VIDEOS)


INK = '#211d19'
DIM = '#82796f'
CORAL = '#e4572e'
TEAL = '#0f766e'
PAPER = '#f5f2ec'
CARD = '#fffdf9'
BORDER = '#e7dfd2'


class PaperBackground(QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(PAPER))
        for cx, cy, radius, color in (
            (0.92, 0.02, 0.52, QColor(228, 87, 46, 20)),
            (0.08, 1.02, 0.50, QColor(15, 118, 110, 15)),
        ):
            grad = QRadialGradient(
                QPointF(self.width() * cx, self.height() * cy),
                self.width() * radius)
            grad.setColorAt(0.0, color)
            clear = QColor(color)
            clear.setAlpha(0)
            grad.setColorAt(1.0, clear)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRect(self.rect())
        p.end()


class PaperWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Tegr — Paper (прототип)')
        self.resize(1260, 820)
        self.setMinimumSize(1080, 720)
        self.setStyleSheet(STYLE)

        root = PaperBackground()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)
        outer.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setSpacing(16)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._search_page())
        self.stack.addWidget(self._channels_page())
        self.stack.addWidget(self._history_page())
        self.stack.addWidget(self._downloads_page())
        self.stack.addWidget(self._settings_page())
        body.addWidget(self.stack, 1)
        self.right_panel = self._right_panel()
        body.addWidget(self.right_panel)
        outer.addLayout(body, 1)

    # ------------------------------------------------------------------
    # Верхняя полоса
    # ------------------------------------------------------------------
    def _build_topbar(self):
        bar = QFrame()
        bar.setObjectName('topbar')
        bar.setFixedHeight(66)
        add_shadow(bar, '#8b7355', 26, 5, 28)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 18, 0)
        lay.setSpacing(8)

        bubble = IconBubble('play', CORAL, '#b8401f', size=36,
                            icon_size=14)
        lay.addWidget(bubble)
        brand = QLabel('Tegr')
        brand.setObjectName('brand')
        lay.addWidget(brand)
        sub = QLabel('кино и видео')
        sub.setObjectName('brandsub')
        lay.addWidget(sub)
        lay.addSpacing(22)

        self.tabs = []
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        for index, text in enumerate(
                ('Поиск', 'Каналы', 'История', 'Загрузки', 'Настройки')):
            tab = QPushButton(text)
            tab.setObjectName('tab')
            tab.setCheckable(True)
            tab.setChecked(index == 0)
            tab.setCursor(Qt.CursorShape.PointingHandCursor)
            tab.clicked.connect(
                lambda _checked=False, i=index: self._switch_page(i))
            self.tab_group.addButton(tab, index)
            self.tabs.append(tab)
            lay.addWidget(tab)

        lay.addStretch(1)
        self.top_search = SearchField('Найти видео…', DIM, height=38)
        self.top_search.setFixedWidth(250)
        lay.addWidget(self.top_search)
        lay.addWidget(Avatar('Я', TEAL, 34))
        return bar

    # ------------------------------------------------------------------
    # Страницы
    # ------------------------------------------------------------------
    def _search_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 18)
        lay.setSpacing(18)

        hero = QWidget()
        hero_lay = QVBoxLayout(hero)
        hero_lay.setContentsMargins(0, 0, 0, 0)
        hero_lay.setSpacing(7)
        title = QLabel('Что посмотрим сегодня?')
        title.setObjectName('heroTitle')
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Preferred)
        hero_lay.addWidget(title)
        sub = QLabel('Один поиск по всем каналам и папкам Telegram.')
        sub.setObjectName('heroSub')
        sub.setWordWrap(True)
        sub.setSizePolicy(QSizePolicy.Policy.Ignored,
                          QSizePolicy.Policy.Preferred)
        hero_lay.addWidget(sub)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search = SearchField('Название фильма, сериала или аниме…',
                             DIM, height=50)
        search_row.addWidget(search, 1)
        find = QPushButton('Найти')
        find.setObjectName('primary')
        find.setFixedHeight(50)
        find.setCursor(Qt.CursorShape.PointingHandCursor)
        search_row.addWidget(find)
        hero_lay.addSpacing(8)
        hero_lay.addLayout(search_row)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, text in enumerate(
                ('Все', 'Фильмы', 'Сериалы', 'Аниме', 'Документальное')):
            chip = Chip(text, checked=index == 0)
            group.addButton(chip, index)
            chips.addWidget(chip)
        chips.addStretch(1)
        hero_lay.addLayout(chips)
        lay.addWidget(hero)

        lay.addWidget(SectionHeader('Продолжить просмотр', 'Вся история'))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        for index, video in enumerate(VIDEOS[:3]):
            card = VideoCard(video, CORAL)
            add_shadow(card, '#8b7355', 22, 6, 28)
            grid.addWidget(card, 0, index)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)

        lay.addWidget(SectionHeader('Новое в каналах', 'Все видео'))
        for video in VIDEOS[3:6]:
            lay.addWidget(VideoRow(video, INK))
        lay.addStretch(1)
        return scroll_page(page)

    def _page_head(self, title, subtitle, action_text=''):
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
        lay.setContentsMargins(4, 8, 4, 18)
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
                                 42))
            text = QVBoxLayout()
            text.setSpacing(2)
            name = QLabel(channel['name'])
            name.setObjectName('cardtitle')
            count = QLabel(f"{channel['count']} видео")
            count.setObjectName('cardmeta')
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
        lay.setContentsMargins(4, 8, 4, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(self._page_head(
            'История', 'Последние просмотры и места, где остановились.'), 1)
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
            lay.addWidget(HistoryRow(item, INK, CORAL))
        lay.addStretch(1)
        return scroll_page(page)

    def _downloads_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 18)
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
            lay.addWidget(DownloadRow(item, CORAL, INK))
        lay.addStretch(1)
        return scroll_page(page)

    def _settings_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 18)
        lay.setSpacing(12)
        lay.addWidget(self._page_head(
            'Настройки', 'Внешний вид, поведение и декодирование.'))
        for title, desc, checked in (
            ('Сворачивать в трей', 'Крестик скрывает окно.', True),
            ('Автоспроизведение', 'Переходить к следующему ролику.', True),
            ('Аппаратное ускорение', 'Декодировать видео на GPU.', True),
            ('Компактные строки', 'Показывать больше роликов на экране.',
             False),
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
            row.addWidget(ToggleSwitch(checked, CORAL, '#ddd4c6'))
            lay.addWidget(card)
        lay.addStretch(1)
        return scroll_page(page)

    # ------------------------------------------------------------------
    # Правая колонка
    # ------------------------------------------------------------------
    def _right_panel(self):
        panel = QFrame()
        panel.setObjectName('queue')
        panel.setFixedWidth(318)
        add_shadow(panel, '#8b7355', 30, 8, 30)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel('Очередь')
        title.setObjectName('queuetitle')
        head.addWidget(title)
        head.addStretch(1)
        count = QLabel('3')
        count.setObjectName('queuecount')
        head.addWidget(count)
        lay.addLayout(head)

        for item in DOWNLOADS[:2]:
            lay.addWidget(DownloadRow(item, CORAL, INK))
        lay.addSpacing(4)

        hist = QLabel('НЕДАВНЕЕ')
        hist.setObjectName('eyebrow')
        lay.addWidget(hist)
        for item in HISTORY[:3]:
            lay.addWidget(self._mini_history(item))

        lay.addStretch(1)
        tip = QFrame()
        tip.setObjectName('tip')
        tip_lay = QVBoxLayout(tip)
        tip_lay.setContentsMargins(12, 10, 12, 10)
        tip_lay.setSpacing(3)
        tip_title = QLabel('Папки Telegram')
        tip_title.setObjectName('tiptitle')
        tip_text = QLabel('Папки, собранные в Telegram, появятся '
                          'в списке каналов автоматически.')
        tip_text.setObjectName('tiptext')
        tip_text.setWordWrap(True)
        tip_lay.addWidget(tip_title)
        tip_lay.addWidget(tip_text)
        lay.addWidget(tip)
        return panel

    def _mini_history(self, item):
        row = QFrame()
        row.setObjectName('qhistory')
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(9)
        dot = QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            f'background: {item["colors"][0]}; border-radius: 4px;')
        lay.addWidget(dot)
        text = QVBoxLayout()
        text.setSpacing(0)
        title = QLabel(item['title'])
        title.setObjectName('qtitle')
        when = QLabel(item['when'])
        when.setObjectName('qwhen')
        text.addWidget(title)
        text.addWidget(when)
        lay.addLayout(text, 1)
        play = QToolButton()
        play.setObjectName('rowbutton')
        play.setIcon(icon('play', INK, 13))
        play.setFixedSize(26, 26)
        lay.addWidget(play)
        return row

    # ------------------------------------------------------------------
    def _switch_page(self, index):
        if index < 0 or index >= self.stack.count():
            return
        self.stack.setCurrentIndex(index)
        self.right_panel.setVisible(index != 4)
        if 0 <= index < len(self.tabs):
            self.tabs[index].setChecked(True)
        effect = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b'opacity', self)
        anim.setDuration(160)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._fade = anim


STYLE = f"""
QWidget {{
    font-family: 'Segoe UI';
    color: {INK};
    font-size: 13px;
}}
QMainWindow {{ background: {PAPER}; }}

QFrame#topbar {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 18px;
}}
QLabel#brand {{ font-family: Georgia; font-size: 21px; font-weight: 700; }}
QLabel#brandsub {{ color: {DIM}; font-size: 11px; padding-top: 4px; }}

QPushButton#tab {{
    background: transparent; border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 9px 6px 9px; color: {DIM}; font-size: 12px;
}}
QPushButton#tab:hover {{ color: {INK}; }}
QPushButton#tab:checked {{
    color: {CORAL}; border-bottom: 2px solid {CORAL};
    font-weight: 600;
}}

QLineEdit#search {{
    background: #faf6ef;
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 7px 11px 7px 7px;
    color: {INK};
    selection-background-color: #f3c7b8;
}}
QLineEdit#search:focus {{
    border: 1px solid {CORAL};
    background: {CARD};
}}
QLineEdit#search::placeholder {{ color: #a39a8e; }}

QPushButton#primary {{
    background: {CORAL};
    border: none; border-radius: 12px; padding: 0 22px;
    color: #ffffff; font-weight: 700;
}}
QPushButton#primary:hover {{ background: #c94a26; }}
QPushButton#ghost {{
    background: #faf6ef;
    border: 1px solid {BORDER};
    border-radius: 10px; padding: 0 13px; color: {INK};
}}
QPushButton#ghost:hover {{ background: #f1e9dd; }}
QPushButton#linkbutton {{
    background: transparent; border: none; color: {CORAL};
    padding: 4px 2px; font-weight: 600;
}}

QPushButton#chip {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 16px; padding: 6px 14px; color: #5f574e;
}}
QPushButton#chip:hover {{ background: #faf6ef; border-color: #d6cbbb; }}
QPushButton#chip:checked {{
    background: {INK}; border: 1px solid {INK};
    color: #ffffff; font-weight: 600;
}}

QLabel#heroTitle {{
    font-family: Georgia; font-size: 34px; font-weight: 700;
    color: {INK};
}}
QLabel#heroSub {{ color: {DIM}; font-size: 13px; }}
QLabel#pagetitle {{ font-family: Georgia; font-size: 25px; font-weight: 700; }}
QLabel#pagesub {{ color: {DIM}; font-size: 12px; }}
QLabel#sectiontitle {{
    font-family: Georgia; font-size: 18px; font-weight: 700;
}}
QLabel#eyebrow {{
    color: #a39a8e; font-size: 10px; font-weight: 700;
    letter-spacing: 1px;
}}

QFrame#card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
}}
QFrame#card:hover {{ border: 1px solid #d9b7a8; }}
QLabel#cardtitle {{ font-family: Georgia; font-size: 14px; font-weight: 700; }}
QLabel#cardmeta {{ color: {DIM}; font-size: 11px; }}

QFrame#sourcecard {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#sourcecard:hover {{ border: 1px solid #d9b7a8; }}

QFrame#historyrow, QFrame#downloadrow, QFrame#videorow {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#historyrow:hover, QFrame#downloadrow:hover,
QFrame#videorow:hover {{
    border: 1px solid #d9b7a8;
    background: #fffaf3;
}}
QLabel#rowtitle {{ font-size: 13px; font-weight: 600; }}
QLabel#rowmeta {{ color: {DIM}; font-size: 11px; }}
QLabel#quality {{ color: #9a9084; font-size: 10px; }}

QToolButton#rowbutton {{
    background: #f5efe6;
    border: none; border-radius: 9px;
}}
QToolButton#rowbutton:hover {{ background: #f6d8cc; }}

QFrame#queue {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 18px;
}}
QLabel#queuetitle {{ font-family: Georgia; font-size: 19px; font-weight: 700; }}
QLabel#queuecount {{
    background: #f6d8cc; color: {CORAL}; font-size: 10px;
    font-weight: 700; border-radius: 9px; padding: 2px 8px;
}}
QFrame#qhistory {{ border-radius: 9px; }}
QFrame#qhistory:hover {{ background: #faf6ef; }}
QLabel#qtitle {{ font-size: 12px; font-weight: 600; }}
QLabel#qwhen {{ color: {DIM}; font-size: 10px; }}
QFrame#tip {{
    background: #fdf1eb;
    border: 1px solid #f0d2c4;
    border-radius: 12px;
}}
QLabel#tiptitle {{ font-size: 12px; font-weight: 700; color: #a64022; }}
QLabel#tiptext {{ color: #8d6b5e; font-size: 10px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: #d9d0c3;
    border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {CORAL}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont('Segoe UI', 10))
    window = PaperWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
