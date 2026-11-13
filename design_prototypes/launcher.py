"""Мини-лаунчер трёх вариантов оформления."""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QLinearGradient
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QVBoxLayout, QWidget)

try:
    from .common import IconBubble, add_shadow, icon
    from .variant_aurora import AuroraWindow
    from .variant_paper import PaperWindow
    from .variant_studio import StudioWindow
    from .variant_tegr import TegrWindow
except ImportError:  # запуск файла напрямую
    from common import IconBubble, add_shadow, icon
    from variant_aurora import AuroraWindow
    from variant_paper import PaperWindow
    from variant_studio import StudioWindow
    from variant_tegr import TegrWindow


class LauncherBackground(QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor('#0b0713'))
        grad.setColorAt(1.0, QColor('#15101f'))
        p.fillRect(self.rect(), grad)
        p.end()


class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Tegr — варианты оформления')
        self.resize(1120, 560)
        self.setMinimumSize(720, 480)
        self.setStyleSheet(STYLE)
        self.windows = []

        root = LauncherBackground()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(28, 26, 28, 26)
        lay.setSpacing(18)

        head = QLabel('Варианты оформления')
        head.setObjectName('head')
        lay.addWidget(head)
        sub = QLabel('Четыре независимых прототипа. Только интерфейс: '
                     'функции плеера, Telegram и кэша не подключены.')
        sub.setObjectName('sub')
        sub.setWordWrap(True)
        lay.addWidget(sub)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        variants = (
            ('Tegr', 'Paper × Studio', 'Три колонки, светлый стиль, '
             'выбор темы и масштаба.', '#e4572e', '#b8401f',
             TegrWindow),
            ('Aurora', 'Тёмный неон', 'Сайдбар, фиолетовое свечение, '
             'крупный поиск и карточки.', '#8b5cf6', '#6d28d9',
             AuroraWindow),
            ('Paper', 'Светлый редакционный', 'Тёплый фон, серифные '
             'заголовки, верхняя навигация и очередь.', '#e4572e',
             '#b8401f', PaperWindow),
            ('Studio', 'Плотный рабочий', 'Три колонки, компактные строки '
             'и янтарный акцент.', '#f2b84b', '#a8791d', StudioWindow),
        )
        for name, kind, desc, color_a, color_b, cls in variants:
            cards.addWidget(self._card(name, kind, desc, color_a, color_b,
                                       cls))
        lay.addLayout(cards, 1)
        foot = QLabel('Прототипы не связаны с текущим приложением.')
        foot.setObjectName('foot')
        lay.addWidget(foot)

    def _card(self, name, kind, desc, color_a, color_b, cls):
        card = QFrame()
        card.setObjectName('card')
        add_shadow(card, '#000000', 30, 8, 90)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        bubble = IconBubble('play', color_a, color_b, size=42, icon_size=17)
        lay.addWidget(bubble)
        title = QLabel(name)
        title.setObjectName('cardtitle')
        lay.addWidget(title)
        subtitle = QLabel(kind)
        subtitle.setObjectName('cardkind')
        lay.addWidget(subtitle)
        text = QLabel(desc)
        text.setObjectName('cardtext')
        text.setWordWrap(True)
        lay.addWidget(text, 1)
        button = QPushButton('Открыть')
        button.setObjectName('open')
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedHeight(36)
        button.setStyleSheet(
            f'QPushButton#open {{ background: {color_a}; }}'
            'QPushButton#open:hover { background: #ffffff; color: #17121f; }')
        button.clicked.connect(lambda _checked=False, c=cls: self._open(c))
        lay.addWidget(button)
        return card

    def _open(self, cls):
        window = cls()
        window.show()
        window.raise_()
        window.activateWindow()
        self.windows.append(window)


STYLE = """
QWidget {
    font-family: 'Segoe UI';
    color: #f4f0ff;
    font-size: 13px;
}
QLabel#head { font-size: 28px; font-weight: 700; }
QLabel#sub { color: #9b91b5; font-size: 12px; }
QLabel#foot { color: #6f6686; font-size: 10px; }
QFrame#card {
    background: rgba(255, 255, 255, 0.045);
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 18px;
}
QFrame#card:hover { border: 1px solid rgba(255, 255, 255, 0.18); }
QLabel#cardtitle { font-size: 20px; font-weight: 700; }
QLabel#cardkind { color: #b9b0ce; font-size: 11px; font-weight: 600; }
QLabel#cardtext { color: #8f86a3; font-size: 11px; }
QPushButton#open {
    border: none; border-radius: 10px;
    color: #17121f; font-weight: 700;
}
"""


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont('Segoe UI', 10))
    window = LauncherWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
