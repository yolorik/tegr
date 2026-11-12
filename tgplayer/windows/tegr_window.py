"""Главное окно Tegr в оформлении Paper × Studio.

Это рабочий вариант: окно использует тот же TelegramBackend, тот же
PlayerWindow и все методы старого MainWindow. Меняется только раскладка:
каналы слева, видео в центре, загрузки и история справа. Виджеты старого
окна переезжают в новые панели, поэтому поиск, кэш, история, настройки,
обновления и вход продолжают работать без переписывания логики.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation,
    QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QPushButton, QSplitter, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget)

from ..core import config, theme
from ..icons import icons
from . import windows as _windows
from .tegr_download_row import TegrDownloadRow
from .tegr_history_row import TegrHistoryRow
from .tegr_settings import TegrSettingsDialog, style_combo_popup
from .windows import MainWindow


# ----------------------------------------------------------------------
# Темы
# ----------------------------------------------------------------------
# Полные палитры для старого theme.py: он ждёт не только bg/accent, но и
# hover/select/divider. Заодно эти же цвета использует новый QSS.
_THEME_SPECS = {
    'paper': {
        'title': 'Бумага',
        'bg': '#f5f2ec', 'panel': '#fffdf9', 'field': '#faf6ef',
        'border': '#e7dfd2', 'text': '#211d19', 'dim': '#82796f',
        'accent': '#e4572e', 'accent2': '#b8401f', 'soft': '#f6d8cc',
        'good': '#0f766e', 'error': '#d9534f',
    },
    'sand': {
        'title': 'Песок',
        'bg': '#f2eee4', 'panel': '#fffcf4', 'field': '#f8f3e8',
        'border': '#e2dbcb', 'text': '#22261f', 'dim': '#7d7c6e',
        'accent': '#0f766e', 'accent2': '#0b5b56', 'soft': '#d7ebe7',
        'good': '#0f766e', 'error': '#d9534f',
    },
    'fog': {
        'title': 'Туман',
        'bg': '#eef1f5', 'panel': '#ffffff', 'field': '#f6f8fb',
        'border': '#dfe5ec', 'text': '#18212c', 'dim': '#74808d',
        'accent': '#3b6fd4', 'accent2': '#2854a8', 'soft': '#dce7f8',
        'good': '#0f766e', 'error': '#d9534f',
    },
    'graphite': {
        'title': 'Графит',
        'bg': '#101214', 'panel': '#171a1d', 'field': '#1f2428',
        'border': '#2b3137', 'text': '#ede9e2', 'dim': '#8e8a82',
        'accent': '#f2b84b', 'accent2': '#c98f2a', 'soft': '#3a2f1a',
        'good': '#6ee7a8', 'error': '#f85149',
    },
}


def install_tegr_themes():
    """Подменяет список тем на четыре темы нового оформления.

    Вызывается до создания MainWindow: старый SettingsDialog читает
    theme.THEME_TITLES и сразу покажет новые темы. Базовое оформление
    не затрагивается — у него свои темы.
    """
    palettes = {}
    for key, spec in _THEME_SPECS.items():
        palettes[key] = {
            'title': spec['title'],
            'bg': spec['bg'],
            'bg_deep': spec['bg'],
            'panel': spec['panel'],
            'field': spec['field'],
            'hover': spec['field'],
            'select': spec['soft'],
            'accent': spec['accent'],
            'accent_hi': spec['accent2'],
            'link': spec['accent'],
            'text': spec['text'],
            'text_dim': spec['dim'],
            'divider': spec['border'],
            'error': spec['error'],
            'ok': spec['good'],
        }
    theme.TG_THEMES.clear()
    theme.TG_THEMES.update(palettes)
    theme.THEME_TITLES.clear()
    theme.THEME_TITLES.update(
        {key: value['title'] for key, value in palettes.items()})
    theme.THEME_TITLES[theme.CUSTOM_THEME] = 'Своя — из двух цветов'
    theme.DEFAULT_THEME = 'paper'


def _on_accent(palette):
    color = QColor(palette['accent'])
    return '#1a1205' if color.lightnessF() > 0.58 else '#ffffff'


def build_tegr_style():
    """QSS нового окна. Размеры берутся из текущего масштаба интерфейса."""
    p = theme.theme()
    s = theme.scale_px
    on_accent = _on_accent(p)
    field = p['field']
    panel = p['panel']
    border = p['divider']
    select = p['select']
    text = p['text']
    dim = p['text_dim']
    accent = p['accent']
    accent_hi = p['accent_hi']
    good = p['ok']
    error = p['error']
    # Полупрозрачные заливки для плоского выделения строк: акцентная
    # подложка выбранного пункта и едва заметная — под курсором.
    ac = QColor(accent)
    tc = QColor(text)
    accent_tint = f'rgba({ac.red()}, {ac.green()}, {ac.blue()}, 30)'
    hover_tint = f'rgba({tc.red()}, {tc.green()}, {tc.blue()}, 14)'

    return f"""
QWidget {{
    font-family: 'Segoe UI';
    color: {text};
    font-size: {s(12)}px;
}}
QMainWindow, QDialog {{ background: {p['bg']}; }}
QWidget#tegrRoot {{ background: transparent; }}
QWidget#tegrBrandBox {{
    background: transparent; border-radius: {s(6)}px;
}}
QWidget#tegrBrandBox:hover {{ background: {accent_tint}; }}
QWidget#tegrBrandBox:hover QLabel#tegrBrand {{ color: {accent}; }}
/* Страницы внутри стеков прозрачные: фон даёт рамка. Без этого
   QWidget.grab() для анимации рисовал под страницей системный фон. */
QWidget#tegrChannelsPage, QWidget#tegrVideosPage,
QWidget#tegrDownloadsPage, QWidget#tegrHistoryPage {{
    background: transparent;
}}
QLabel {{ background: transparent; }}
QLabel#heading {{
    color: {accent}; font-size: {s(19)}px; font-weight: 600;
}}
QLabel#hint {{ color: {dim}; font-size: {s(11)}px; }}
QLabel#error {{ color: {error}; font-size: {s(11)}px; }}
QLabel#rowtitle {{ color: {text}; }}
QLabel#rowmeta {{ color: {dim}; }}
QLabel#rowmeta:disabled {{ color: {dim}; }}

/* Плоская раскладка: ни у шапки, ни у колонок нет собственной
   подложки. Весь интерфейс лежит на одном фоне, а границы — тонкие
   линии. Разделитель колонок рисует ручка сплиттера. */
QSplitter#tegrSplitter::handle {{
    background: transparent; border-left: 1px solid {border};
}}
QFrame#tegrTopbar {{
    background: transparent; border: none;
    border-bottom: 1px solid {border}; border-radius: 0;
}}
/* Полоса обновления: та же тема, что и остальное окно. */
QWidget#updatebanner {{
    background: {panel};
    border: none;
    border-bottom: 1px solid {border};
    border-radius: 0;
}}
QWidget#updatebanner QLabel {{ color: {text}; }}
QFrame#tegrLeft, QFrame#tegrCenter, QFrame#tegrRight {{
    background: transparent; border: none; border-radius: 0;
}}
QFrame#tegrRule {{
    background: {border}; border: none;
}}
QLabel#tegrBrand {{
    font-family: 'Segoe UI'; font-size: {s(19)}px; font-weight: 700;
}}
/* Сортировка роликов: маленькая кнопка-стрелка рядом с поиском. */
QPushButton#tegrSort {{
    background: transparent; border: none; border-radius: {s(4)}px;
    color: {dim}; font-size: {s(14)}px; padding: 0; font-weight: 700;
}}
QPushButton#tegrSort:hover {{
    background: {accent_tint}; color: {accent};
}}


QPushButton#tegrTab {{
    background: transparent; border: none; border-radius: 0;
    border-bottom: 2px solid transparent;
    color: {dim}; padding: {s(8)}px {s(10)}px {s(8)}px {s(10)}px;
    font-size: {s(12)}px; font-weight: 600;
}}
QPushButton#tegrTab:hover {{ color: {text}; }}
QPushButton#tegrTab:checked {{
    color: {accent}; border-radius: 0;
    border-bottom: 2px solid {accent};
}}
/* Стрелка «назад» в заголовке списка видео: видна, когда видео
   занимает место списка каналов (средняя и узкая раскладка). */
QPushButton#tegrBack {{
    background: transparent; border: none; border-radius: {s(4)}px;
    color: {accent}; font-size: {s(17)}px; font-weight: 800; padding: 0;
}}
QPushButton#tegrBack:hover {{
    background: {accent_tint}; color: {accent_hi};
}}
QPushButton#tegrPillBtn {{
    background: transparent; border: none; padding: 0;
    color: {text}; font-size: {s(10)}px; font-weight: 600;
}}
QPushButton#tegrPillBtn:hover {{
    background: transparent; border: none; color: {accent};
}}
QFrame#tegrPill {{
    background: transparent;
    border: none;
}}

QLineEdit, QComboBox, QSpinBox {{
    background: {field};
    border: 1px solid {border};
    border-radius: {s(8)}px;
    padding: {s(6)}px {s(10)}px;
    color: {text};
    selection-background-color: {select};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {accent};
    background: {panel};
}}
/* Поиск в новом окне — не поле в рамке, а строка с линией снизу. */
QLineEdit#tegrSearch {{
    background: transparent; border: none;
    border-bottom: 1px solid {border}; border-radius: 0;
    padding: {s(5)}px {s(8)}px {s(4)}px {s(14)}px;
    color: {text};
}}
QLineEdit#tegrSearch:focus {{
    background: transparent; border-bottom: 1px solid {accent};
}}
/* Папка в шапке — такой же плоский текст с шевроном. */
QComboBox#tegrFolder {{
    background: transparent; border: none; border-radius: 0;
    border-bottom: 2px solid {accent};
    padding: {s(2)}px {s(18)}px {s(2)}px {s(2)}px;
    color: {dim};
}}
QComboBox#tegrFolder:hover {{ color: {text}; }}
QComboBox#tegrFolder:focus {{
    background: transparent; border: none;
    border-bottom: 2px solid {accent};
}}
QComboBox#tegrFolder::drop-down {{ border: none; width: {s(16)}px; }}
QComboBox::drop-down {{ border: none; width: {s(18)}px; }}
QComboBox QAbstractItemView {{
    background: {panel};
    border: 1px solid {border};
    border-radius: {s(8)}px;
    padding: {s(4)}px;
    outline: none;
    color: {text};
    selection-background-color: {select};
    selection-color: {text};
}}
QComboBox QAbstractItemView::item {{
    padding: {s(6)}px {s(8)}px;
    border-radius: {s(5)}px;
}}
QComboBox QAbstractItemView::item:selected {{
    background: {select};
    color: {text};
}}

QPushButton {{
    background: {field};
    border: 1px solid {border};
    border-radius: {s(8)}px;
    padding: {s(6)}px {s(12)}px;
    color: {text};
}}
QPushButton:hover {{ background: {select}; border-color: {accent}; }}
QPushButton:pressed {{ background: {select}; }}
QPushButton:disabled {{ color: {dim}; }}
QPushButton#primary {{
    background: {accent}; border: none;
    color: {on_accent}; font-weight: 700;
}}
QPushButton#primary:hover {{ background: {accent_hi}; }}
QPushButton#flat {{ background: transparent; border: none; color: {accent}; }}
QProgressBar {{
    background: {field}; border: none; border-radius: {s(3)}px;
}}
QProgressBar::chunk {{
    background: {accent}; border-radius: {s(3)}px;
}}
QToolButton#settingbtn {{
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
}}
QToolButton#settingbtn:hover {{ background: {hover_tint}; }}
QToolButton#rowbutton {{
    background: transparent; border: none;
    border-radius: {s(4)}px;
}}
QToolButton#rowbutton:hover {{ background: {hover_tint}; }}

QListWidget, QListView {{
    background: transparent;
    border: none;
    outline: none;
    /* По 3 px справа: ровно столько же остаётся от пункта до полосы
       прокрутки, которая сама стоит в 3 px от правого края. */
    padding: 0px {s(3)}px 0px 0px;
    selection-background-color: {select};
    selection-color: {text};
}}
/* Строки — не карточки: прозрачный фон и тонкая линия снизу. Слева
   всегда зарезервированы 2 px под акцентную полоску выделения, чтобы
   текст не дёргался при выборе. */
QListWidget::item {{
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    border-bottom: 1px solid {border};
    border-radius: 0;
    margin: 0;
    padding: 0;
}}
QListWidget::item:hover {{
    background: {hover_tint};
    border-left: 2px solid transparent;
    border-bottom: 1px solid {border};
}}
QListWidget::item:selected {{
    background: {accent_tint};
    border-left: 2px solid {accent};
    border-bottom: 1px solid {border};
    color: {text};
}}
/* Кнопки под списками — плоские текстовые действия, без рамок. */
QWidget#tegrDownloadsPage QPushButton,
QWidget#tegrHistoryPage QPushButton {{
    background: transparent; border: none; border-radius: 0;
    color: {dim}; padding: {s(4)}px {s(6)}px;
}}
QWidget#tegrDownloadsPage QPushButton:hover,
QWidget#tegrHistoryPage QPushButton:hover {{ color: {accent}; }}
QWidget#tegrDownloadsPage QPushButton:pressed,
QWidget#tegrHistoryPage QPushButton:pressed {{ color: {text}; }}

#controlbar QLabel {{
    color: {text}; background: transparent;
}}
#controlbar QPushButton {{
    background: transparent; border: none; border-radius: {s(8)}px;
    padding: 0;
}}
#controlbar QPushButton:hover {{
    background: {select};
}}
#controlbar QPushButton:pressed {{
    background: {border};
}}
#controlbar QSlider::groove:horizontal {{
    height: {s(4)}px; background: {select}; border-radius: {s(2)}px;
}}
#controlbar QSlider::sub-page:horizontal {{
    background: {accent}; border-radius: {s(2)}px;
}}
#controlbar QSlider::add-page:horizontal {{
    background: transparent;
}}
/* Форму ползунка повторяем во всех состояниях: если задать только
   фон в :hover, часть стилей рисует вместо круга системный квадрат. */
#controlbar QSlider::handle:horizontal {{
    width: {s(12)}px; height: {s(12)}px;
    margin: -{s(4)}px 0; border-radius: {s(6)}px;
    background: {text};
}}
#controlbar QSlider::handle:horizontal:hover,
#controlbar QSlider::handle:horizontal:pressed {{
    width: {s(12)}px; height: {s(12)}px;
    margin: -{s(4)}px 0; border-radius: {s(6)}px;
    background: {accent_hi};
}}
#controlbar QSlider::handle:horizontal:disabled {{
    width: {s(12)}px; height: {s(12)}px;
    margin: -{s(4)}px 0; border-radius: {s(6)}px;
    background: {dim};
}}
#controlbar QWidget#autoplayswitch {{
    background: transparent; border: none;
}}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    /* Отступ 3 px от правого края задаёт padding списка. Ползунок
       шириной 6 px — без боковых полей, чтобы не было второго отступа. */
    background: transparent; width: {s(6)}px;
    margin: {s(3)}px 0px {s(3)}px 0px;
}}
QScrollBar::handle:vertical {{
    background: {border}; border-radius: {s(3)}px;
    min-height: {s(30)}px;
}}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar:horizontal {{ background: transparent; height: {s(8)}px; }}
QScrollBar::handle:horizontal {{
    background: {border}; border-radius: {s(4)}px; min-width: {s(30)}px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{
    background: {panel};
    border: 1px solid {border};
    border-radius: {s(10)}px;
    padding: {s(5)}px;
}}
QMenu::item {{ padding: {s(7)}px {s(18)}px; border-radius: {s(6)}px; }}
QMenu::item:selected {{ background: {select}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: {s(4)}px {s(6)}px; }}

QGroupBox {{
    background: {panel};
    border: 1px solid {border};
    border-radius: {s(10)}px;
    margin-top: {s(12)}px;
    padding-top: {s(8)}px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: {s(10)}px;
    padding: 0 {s(4)}px; color: {text}; font-weight: 600;
}}
QCheckBox {{ spacing: {s(7)}px; }}
QCheckBox::indicator {{
    width: {s(16)}px; height: {s(16)}px;
    border: 1px solid {border}; border-radius: {s(4)}px;
    background: {field};
}}
QCheckBox::indicator:checked {{
    background: {accent}; border-color: {accent};
}}
QStatusBar {{
    background: {p['bg']}; color: {dim}; padding: 0;
    border-top: 1px solid {border};
}}
QStatusBar::item {{ border: none; }}
QLabel#statusline {{ font-size: {s(11)}px; }}
QLabel#tegrAboutTitle {{ font-size: {s(13)}px; font-weight: 700; }}
QToolTip {{
    background: {panel}; color: {text};
    border: 1px solid {border}; border-radius: {s(6)}px;
    padding: {s(4)}px {s(7)}px;
}}
"""


# ----------------------------------------------------------------------
# Анимация перехода между страницами
# ----------------------------------------------------------------------
class _TegrBrandButton(QWidget):
    """Надпись Tegr — кнопка подключения; справа сверху точка статуса."""

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _TegrSlideOverlay(QWidget):
    """Кадр перехода: старый и новый снимки едут и перекрёстно тают.

    Рисуем оба снимка сами, без QGraphicsOpacityEffect: эффект на QLabel
    подкладывал под снимок системный светло-серый фон, и во время
    перелистывания панель на мгновение становилась серой.
    """

    def __init__(self, parent, old_pixmap, new_pixmap, shift, color,
                 duration=300):
        super().__init__(parent)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._old = old_pixmap
        self._new = new_pixmap
        self._shift = int(shift)
        self._color = QColor(color)
        self._t = 0.0
        self._on_done = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(max(1, int(duration)))
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self._finished)

    def start(self, on_done=None):
        self._on_done = on_done
        self._anim.start()

    def stop(self):
        try:
            self._anim.stop()
        except Exception:
            pass

    def _step(self, value):
        self._t = float(value)
        self.update()

    def _finished(self):
        callback = self._on_done
        self._on_done = None
        if callback is not None:
            callback()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._color)
        t = max(0.0, min(1.0, self._t))
        # Старая страница уезжает вбок.
        painter.setOpacity(1.0)
        painter.drawPixmap(QPoint(int(-self._shift * t), 0), self._old)
        # Новая приходит с другой стороны и проступает поверх неё.
        painter.setOpacity(t)
        painter.drawPixmap(QPoint(int(self._shift * (1.0 - t)), 0), self._new)
        painter.end()


class _TegrRightPopup(QWidget):
    """Плавающее окно истории/загрузок с непрозрачным фоном.

    Рисуем фон сами: у отдельного окна таблица стилей его не закрашивает,
    и окно получалось прозрачным насквозь.
    """

    RADIUS = 0

    def __init__(self, parent, panel_color, border_color):
        super().__init__(parent, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName('tegrRightPopup')
        self._panel = QColor(panel_color)
        self._border = QColor(border_color)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

    def set_colors(self, panel_color, border_color):
        self._panel = QColor(panel_color)
        self._border = QColor(border_color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._panel)
        radius = theme.scale_px(self.RADIUS)
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
            radius, radius)
        painter.end()


class _TegrUiFadeFilter(QObject):
    """Мягкое затухание кнопок и появление выпадающих списков.

    В панели управления плеера нажатия не трогаем: там своя подсветка,
    и затемнение поверх видео выглядело бы лишним.
    """

    PRESS_OPACITY = 0.55
    PRESS_MS = 80
    RELEASE_MS = 180
    POPUP_MS = 160
    POPUP_OUT_MS = 140

    def eventFilter(self, obj, event):
        try:
            etype = event.type()
            if etype in (QEvent.Type.MouseButtonPress,
                         QEvent.Type.MouseButtonRelease):
                if (isinstance(obj, (QPushButton, QToolButton))
                        and event.button() == Qt.MouseButton.LeftButton
                        and not self._in_player_bar(obj)):
                    if etype == QEvent.Type.MouseButtonPress:
                        self._fade_button(obj, self.PRESS_OPACITY,
                                          self.PRESS_MS)
                    else:
                        self._fade_button(obj, 1.0, self.RELEASE_MS)
            elif etype == QEvent.Type.Show:
                if self._is_popup(obj):
                    obj._tegr_popup_seen = True
                    self._fade_popup(obj)
            elif etype == QEvent.Type.Hide:
                if (self._is_popup(obj)
                        and getattr(obj, '_tegr_popup_seen', False)):
                    # Закрываем сразу. «Призрак» со снимком попапа
                    # показывался уже после его скрытия и давал заметное
                    # мерцание при закрытии.
                    obj._tegr_popup_seen = False
        except Exception:
            pass
        return False

    @staticmethod
    def _is_popup(obj):
        if not (isinstance(obj, QWidget) and obj.isWindow()
                and bool(obj.windowFlags() & Qt.WindowType.Popup)):
            return False
        # Выпадающие списки QComboBox анимируем средствами Qt: окно
        # поп-апа показывается повторно при позиционировании, и
        # windowOpacity-анимация давала моргание.
        try:
            if obj.property('tegrComboPopup'):
                return False
        except Exception:
            pass
        try:
            if 'QComboBox' in obj.metaObject().className():
                return False
        except Exception:
            pass
        return True

    @staticmethod
    def _in_player_bar(widget):
        node = widget
        while node is not None:
            if node.objectName() in ('controlbar', 'pipbar'):
                return True
            node = node.parentWidget()
        return False

    def _fade_button(self, button, target, duration):
        try:
            effect = getattr(button, '_tegr_fade_effect', None)
            if effect is None:
                effect = QGraphicsOpacityEffect(button)
                effect.setOpacity(1.0)
                button.setGraphicsEffect(effect)
                button._tegr_fade_effect = effect
            anim = getattr(button, '_tegr_fade_anim', None)
            if anim is not None:
                anim.stop()
            anim = QPropertyAnimation(effect, b'opacity', button)
            anim.setDuration(int(duration))
            anim.setStartValue(float(effect.opacity()))
            anim.setEndValue(float(target))
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(
                lambda a=anim, b=button, t=target:
                    self._finish_button(b, a, t))
            button._tegr_fade_anim = anim
            anim.start()
        except Exception:
            pass

    @staticmethod
    def _finish_button(button, anim, target):
        if getattr(button, '_tegr_fade_anim', None) is not anim:
            return
        if target < 1.0:
            # Это ещё нажатие — держим затемнение до отпускания.
            return
        try:
            button.setGraphicsEffect(None)
            button._tegr_fade_effect = None
            button._tegr_fade_anim = None
        except Exception:
            pass

    def _fade_popup(self, popup):
        try:
            anim = getattr(popup, '_tegr_popup_anim', None)
            if anim is not None:
                anim.stop()
            popup.setWindowOpacity(0.0)
            anim = QPropertyAnimation(popup, b'windowOpacity', popup)
            anim.setDuration(self.POPUP_MS)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            popup._tegr_popup_anim = anim
            anim.start()
        except Exception:
            try:
                popup.setWindowOpacity(1.0)
            except Exception:
                pass

    @staticmethod
    def _cache_popup(popup):
        """Запоминает вид открытого списка для плавного закрытия."""
        try:
            if popup is None or not popup.isVisible():
                return
            if popup.width() <= 2 or popup.height() <= 2:
                return
            pixmap = popup.grab()
            if not pixmap.isNull():
                popup._tegr_popup_pixmap = pixmap
        except Exception:
            pass

    def _fade_popup_out(self, popup):
        """Плавно гасит закрывающийся список.

        Сам попап Qt прячет мгновенно, поэтому поверх его места
        показываем последний снимок отдельным прозрачным окном и гасим
        уже его — закрытие выглядит так же, как открытие.
        """
        try:
            pixmap = getattr(popup, '_tegr_popup_pixmap', None)
            if pixmap is None or pixmap.isNull():
                return
            ghost = QLabel(None, Qt.WindowType.ToolTip
                           | Qt.WindowType.FramelessWindowHint)
            ghost.setObjectName('tegrPopupGhost')
            ghost.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True)
            ghost.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            ghost.setAttribute(
                Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            try:
                ghost.setWindowFlag(
                    Qt.WindowType.WindowTransparentForInput, True)
            except Exception:
                pass
            ghost.setPixmap(pixmap)
            ghost.resize(pixmap.width(), pixmap.height())
            ghost.move(popup.geometry().topLeft())
            ghost.setWindowOpacity(1.0)
            ghost.show()
            anim = QPropertyAnimation(ghost, b'windowOpacity', ghost)
            anim.setDuration(self.POPUP_OUT_MS)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.Type.InCubic)
            anim.finished.connect(ghost.deleteLater)
            ghost._tegr_popup_anim = anim
            anim.start()
        except Exception:
            pass


# ----------------------------------------------------------------------
# Окно
# ----------------------------------------------------------------------
class TegrMainWindow(MainWindow):
    """MainWindow со старой логикой и новой трёхколоночной раскладкой."""

    # Пороги ширины окна для адаптивной раскладки. Ниже среднего порога
    # окно показывает одну колонку с вкладками, между порогами — две.
    # Широкий порог учитывает минимумы: каналы 350 + видео 320 +
    # история 450 + поля/разделители. Пороги заданы для 100% и
    # масштабируются вместе с колонками: иначе при 110–150% широкий
    # режим включался раньше, чем колонки влезали в окно, и правая
    # колонка уходила под край окна.
    _TEGR_WIDE_MIN = 1180
    _TEGR_MEDIUM_MIN = 780

    def __init__(self, backend):
        # В новой теме строки лежат в карточке с рамкой; сдвигаем
        # содержимое на её поля. Старое окно остаётся без сдвига.
        try:
            # В новом окне загрузки строятся обычным layout'ом:
            # содержимое центрируется по высоте без ручных сдвигов.
            _windows.DownloadRow = TegrDownloadRow
            _windows.HistoryRow = TegrHistoryRow
            # Строкой управляет её собственная отрисовка: название
            # центрируется по фактическим границам букв, поэтому ручной
            # сдвиг больше не нужен (раньше он поднимал текст на 5 px).
            _windows.VideoRow.VERTICAL_NUDGE = 0
            _windows.VideoRow.TEXT_NUDGE = 0
        except Exception:
            pass
        try:
            QApplication.setEffectEnabled(
                Qt.UIEffect.UI_AnimateCombo, False)
        except Exception:
            pass
        self._tegr_ready = False
        install_tegr_themes()
        try:
            first_run = not backend.settings.get('tegr_ui')
            saved_theme = backend.settings.get('theme')
            if (first_run or (saved_theme not in _THEME_SPECS
                              and saved_theme != theme.CUSTOM_THEME)):
                backend.settings['theme'] = 'paper'
            if first_run:
                backend.set_setting('tegr_ui', True)
        except Exception:
            pass
        super().__init__(backend)
        # В новом окне нижняя строка состояния не нужна: она отнимала
        # высоту у списков и дублировала состояние в верхней панели.
        try:
            self._status_bar.hide()
            self._status_bar.setMaximumHeight(0)
        except Exception:
            pass
        self._build_tegr_ui()
        self._tegr_replace_status_label()
        self._tegr_ready = True
        self._apply_tegr_style()
        self._tegr_switch_tab(0)
        # Первичная установка вкладки — не действие пользователя: при
        # первом сужении окна показываем каналы, а не «Загрузки».
        self._tegr_last_area = 'nav'
        try:
            self.folder_combo.clearFocus()
        except Exception:
            pass
        # Минимум теперь определяется раскладкой, а не тремя колонками:
        # в узком режиме хватает одной (он задан в _apply_tegr_style).
        # Размер из настроек не перебиваем, иначе окно, закрытое узким,
        # снова открывалось бы широким.
        if not self._setting('main_geometry'):
            self.resize(1280, 820)
        self._tegr_apply_mode(
            self._tegr_mode_for_width(self.width()), force=True)

    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        try:
            self._status_bar.hide()
            self._status_bar.setMaximumHeight(0)
        except Exception:
            pass
        self._tegr_ensure_fade_filter()
        if not getattr(self, '_tegr_focus_set', False):
            self._tegr_focus_set = True
            QTimer.singleShot(0, self._tegr_focus_target)

    def _tegr_ensure_fade_filter(self):
        """Ставит фильтр затухания кнопок и меню, если его ещё нет."""
        app = QApplication.instance()
        if app is None or getattr(app, '_tegr_fade_filter', None) is not None:
            return
        fade = _TegrUiFadeFilter(app)
        app.installEventFilter(fade)
        app._tegr_fade_filter = fade

    def _tegr_remove_fade_filter(self):
        """Снимает фильтр перед закрытием: иначе падение при выходе."""
        app = QApplication.instance()
        if app is None:
            return
        fade = getattr(app, '_tegr_fade_filter', None)
        if fade is None:
            return
        app.removeEventFilter(fade)
        app._tegr_fade_filter = None
        try:
            fade.deleteLater()
        except Exception:
            pass

    def _tegr_focus_target(self):
        """Фокус на тот список, который виден в текущем режиме."""
        try:
            if (self._tegr_mode == 'narrow'
                    and self._tegr_nav != 'videos'):
                self.channel_list.setFocus()
            else:
                self.video_list.setFocus()
        except Exception:
            pass

    def _apply_theme(self):
        """Сначала даём старому окну пересобрать строки, потом свой QSS."""
        selected = None
        try:
            selected = self._row_index(self.video_list.currentRow())
        except Exception:
            selected = None
        super()._apply_theme()
        if getattr(self, '_tegr_ready', False):
            self._apply_tegr_style()
            self._tegr_restore_selection(selected)

    def _apply_tegr_style(self):
        # Плоские поисковые строки и папка в шапке: имена нужны
        # селекторам QSS, чтобы не менять оформление полей в диалогах.
        try:
            self.channel_search.setObjectName('tegrSearch')
            self.video_search.setObjectName('tegrSearch')
            self.folder_combo.setObjectName('tegrFolder')
            self.channel_search.set_flat_search(True)
            self.video_search.set_flat_search(True)
        except Exception:
            pass
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_tegr_style())
        try:
            # Минимум следует за масштабом интерфейса: при 150% узкая
            # колонка должна быть шире, иначе текст не помещается.
            self.setMinimumSize(theme.scale_px(400), theme.scale_px(520))
            self._tegr_apply_mode_constraints()
            self._tegr_space_scrollbars()
            self._tegr_update_back_icon()
            self._tegr_update_order_button()
            self._tegr_splitter.setHandleWidth(theme.scale_px(9))
            self._tegr_topbar.setFixedHeight(theme.scale_px(56))
            self._tegr_outer.setContentsMargins(0, 0, 0, 0)
            self._tegr_outer.setSpacing(0)
        except Exception:
            pass
        try:
            icon_px = theme.scale_px(18)
            self.btn_settings.setIconSize(QSize(icon_px, icon_px))
            self.btn_settings.setFixedSize(theme.scale_px(32),
                                           theme.scale_px(30))
            self._tegr_right_btn.setIconSize(QSize(icon_px, icon_px))
            self._tegr_right_btn.setFixedSize(theme.scale_px(32),
                                              theme.scale_px(30))
            self._tegr_update_dl_badges()
        except Exception:
            pass
        try:
            self._status_bar.refit()
        except Exception:
            pass
        self._tegr_fit_statusbar()
        try:
            style_combo_popup(self.folder_combo)
        except Exception:
            pass
        try:
            self._tegr_update_conn(getattr(self, '_tegr_state', 'off'))
        except Exception:
            pass

    def _tegr_replace_status_label(self):
        """Обычный QLabel вместо ручной базовой линии.

        Старый StatusLabel рисовал строку по своей формуле и при
        высоте 24 px садился к верхнему краю. QLabel с
        AlignVCenter центрует текст штатно.
        """
        try:
            old = self._status_bar.label
            label = QLabel('', self._status_bar)
            label.setObjectName('statusline')
            label.setAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
            label.setContentsMargins(theme.scale_px(4), 0, 0, 0)
            self._status_bar.label = label
            self.lbl_status = label
            old.deleteLater()
            self._status_bar._place()
        except Exception:
            pass

    def _tegr_space_scrollbars(self):
        """Оставляет 3 px между пунктами списка и полосой прокрутки.

        Сама полоса уже стоит в 3 px от правого края — это задаёт
        padding списка. А этот отступ вьюпорта отделяет от неё
        содержимое, чтобы карточки не касались ползунка.
        """
        views = [self.channel_list, self.video_list, self.history_list]
        try:
            views.append(self.downloads_view.list)
        except Exception:
            pass
        for view in views:
            try:
                view.setViewportMargins(0, 0, theme.scale_px(3), 0)
            except Exception:
                pass

    def _tegr_fit_statusbar(self):
        try:
            # Высоту берём у самой полосы: она считается по шрифту и
            # совпадает с высотой строки текста, а не с прежними 24 px.
            self._status_bar.refit()
            height = self._status_bar.height()
            self._status_bar.label.setGeometry(
                0, 0, self._status_bar.width(), height)
            self._status_bar.label.setContentsMargins(
                theme.scale_px(4), 0, 0, 0)
            self._status_bar.label.update()
        except Exception:
            pass

    def open_settings(self):
        dlg = TegrSettingsDialog(self, self._settings_dict())
        dlg.changed.connect(self._apply_setting)
        try:
            self.backend.status_changed.connect(dlg.set_conn_status)
            dlg.set_conn_status(
                'Текущий способ: '
                + config.PROXY_MODE_TITLES.get(
                    self._setting('proxy_mode',
                                  config.DEFAULT_PROXY_MODE), ''))
        except Exception:
            pass
        try:
            dlg.exec()
        finally:
            try:
                self.backend.status_changed.disconnect(dlg.set_conn_status)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _build_tegr_ui(self):
        # Виджеты, которые переезжают в новую раскладку.
        self.channel_list.blockSignals(True)
        self.video_list.blockSignals(True)

        # Один фильтр на приложение: затухание кнопок и появление меню.
        self._tegr_ensure_fade_filter()

        # main_tabs нужен старым методам (_downloads_tab_index,
        # _history_tab_index, _on_main_tab_changed). Оставляем его живым,
        # но невидимым и без старой полосы вкладок.
        self.main_tabs.setParent(self)
        self.main_tabs.hide()

        root = QWidget()
        root.setObjectName('tegrRoot')
        self.setCentralWidget(root)
        self._tegr_outer = QVBoxLayout(root)
        # Плоская раскладка прижата к краям окна: никаких полей, за
        # которыми виднелась бы вторая подложка.
        self._tegr_outer.setContentsMargins(0, 0, 0, 0)
        self._tegr_outer.setSpacing(0)

        self._tegr_topbar = self._tegr_build_topbar()
        self._tegr_outer.addWidget(self._tegr_topbar)
        self.update_banner.setParent(root)
        self._tegr_outer.addWidget(self.update_banner)

        self._tegr_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._tegr_splitter.setObjectName('tegrSplitter')
        self._tegr_splitter.setHandleWidth(theme.scale_px(9))
        self._tegr_splitter.setChildrenCollapsible(False)
        # Содержимое — четыре страницы, которые переезжают между слотами
        # при смене ширины окна. Страницы создаются один раз: так списки,
        # выделение и прокрутка переживают перестроение раскладки.
        self._tegr_channels_page = self._tegr_build_channels_page()
        self._tegr_videos_page = self._tegr_build_videos_page()
        self._tegr_downloads_page = self._tegr_build_downloads_page()
        self._tegr_history_page = self._tegr_build_history_page()
        self._tegr_left = self._tegr_build_left()
        self._tegr_center = self._tegr_build_center()
        self._tegr_right = self._tegr_build_right()
        self._tegr_splitter.addWidget(self._tegr_left)
        self._tegr_splitter.addWidget(self._tegr_center)
        self._tegr_splitter.addWidget(self._tegr_right)
        self._tegr_splitter.setStretchFactor(0, 0)
        self._tegr_splitter.setStretchFactor(1, 1)
        self._tegr_splitter.setStretchFactor(2, 0)
        factor = 0.90 + 0.10 * theme.ui_scale() / 100.0
        sizes = [int(round(250 * factor)), 620,
                 int(round(340 * factor))]
        saved = self._setting('tegr_splitter_sizes')
        if (isinstance(saved, (list, tuple)) and len(saved) == 3
                and all(isinstance(x, (int, float)) and x > 0
                        for x in saved)):
            sizes = [int(x) for x in saved]
        self._tegr_splitter.setSizes(sizes)
        self._tegr_splitter.splitterMoved.connect(
            self._tegr_save_splitter_sizes)
        self._tegr_outer.addWidget(self._tegr_splitter, 1)

        # Состояние адаптивной раскладки. До первого _tegr_apply_mode
        # окно считается широким — так же, как оно и собрано.
        self._tegr_mode = 'wide'
        self._tegr_nav = 'channels'
        self._tegr_narrow_tab = 0
        self._tegr_right_page = 'downloads'
        # Состояние колонок помним между запусками.
        self._tegr_right_collapsed = bool(
            self._setting('tegr_right_collapsed', False))
        # В среднем режиме правая панель открывается плавающим окном.
        self._tegr_right_popup = False
        self._tegr_right_popup_window = None
        self._tegr_right_popup_just_closed = False
        self._tegr_right_placeholder = None
        self._tegr_last_area = 'nav'
        self._tegr_applying_mode = False
        self._tegr_wide_sizes = list(sizes)
        self._tegr_medium_sizes = []
        # Ширины колонок сохраняем после перетаскивания, но не на
        # каждый пиксель — с небольшой задержкой.
        self._tegr_sizes_timer = QTimer(self)
        self._tegr_sizes_timer.setSingleShot(True)
        self._tegr_sizes_timer.timeout.connect(
            self._tegr_persist_splitter_sizes)
        # Индикаторы загрузок на кнопке списка обновляем раз в секунду.
        self._tegr_dl_badge_timer = QTimer(self)
        self._tegr_dl_badge_timer.setInterval(1000)
        self._tegr_dl_badge_timer.timeout.connect(
            self._tegr_update_dl_badges)
        self._tegr_dl_badge_timer.start()

        self.channel_list.blockSignals(False)
        self.video_list.blockSignals(False)

        # Старое окно больше не нужно: нужные виджеты уже переехали.
        # setCentralWidget сам снимет старое; отдельная ссылка не хранится.

        # ПКМ по каналу: QListWidget делает строку текущей ещё до того,
        # как придёт контекстное меню, и канал открывался вместо меню.
        self._tegr_channel_right = False
        self._tegr_channel_prev_row = -1
        try:
            self.channel_list.viewport().installEventFilter(self)
            self.channel_list.installEventFilter(self)
        except Exception:
            pass
        self._tegr_update_order_button()
        self._tegr_update_dl_badges()

        # Старый значок связи в углу больше не нужен: состояние показывает
        # кнопка в верхней полосе. Само меню подключения остаётся рабочим.
        try:
            self.conn_button.hide()
        except Exception:
            pass
        try:
            self.backend.connection_changed.connect(self._tegr_on_connection)
        except Exception:
            pass
        try:
            self.conn_menu.aboutToHide.connect(self._tegr_conn_menu_closed)
        except Exception:
            pass
        self._tegr_state = 'off'
        self._tegr_update_conn('off')

        # Короткие подписи, чтобы кнопки помещались в правую колонку.
        try:
            self.downloads_view.btn_clear.setText('Удалить все')
            self.downloads_view.btn_delete_selected.setText('Удалить')
            self.btn_select_history.setText('Выбрать...')
            self.btn_delete_history_selected.setText('Удалить')
            self.btn_clear_history.setText('Очистить')
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _tegr_build_topbar(self):
        bar = QFrame()
        bar.setObjectName('tegrTopbar')
        bar.setFixedHeight(theme.scale_px(56))
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(theme.scale_px(12), 0,
                               theme.scale_px(10), 0)
        lay.setSpacing(theme.scale_px(8))

        # Надпись Tegr — это кнопка подключения. Справа сверху от неё
        # точка статуса: зелёная — связь есть, жёлтая — подключаемся,
        # красная — не удалось.
        self._tegr_brand_box = _TegrBrandButton()
        self._tegr_brand_box.setObjectName('tegrBrandBox')
        self._tegr_brand_box.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tegr_brand_box.setFixedHeight(theme.scale_px(34))
        self._tegr_brand_box.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tegr_brand_box.setToolTip('Подключение к Telegram')
        brand_lay = QHBoxLayout(self._tegr_brand_box)
        brand_lay.setContentsMargins(
            theme.scale_px(5), 0, theme.scale_px(5), 0)
        brand_lay.setSpacing(theme.scale_px(4))
        self._tegr_brand = QLabel('Tegr')
        self._tegr_brand.setObjectName('tegrBrand')
        self._tegr_brand.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        brand_lay.addWidget(self._tegr_brand, 0,
                            Qt.AlignmentFlag.AlignVCenter)
        self._tegr_status_dot = QLabel()
        self._tegr_status_dot.setFixedSize(theme.scale_px(8),
                                           theme.scale_px(8))
        self._tegr_status_dot.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        brand_lay.addWidget(self._tegr_status_dot, 0,
                            Qt.AlignmentFlag.AlignTop)
        self._tegr_brand_box.clicked.connect(self._open_connection_menu)
        lay.addWidget(self._tegr_brand_box)
        lay.addSpacing(theme.scale_px(12))

        self.folder_combo.setMinimumWidth(theme.scale_px(140))
        self.folder_combo.setMaximumWidth(theme.scale_px(180))
        lay.addWidget(self.folder_combo)
        lay.addSpacing(theme.scale_px(4))

        lay.addStretch(1)
        # Показать/скрыть правую колонку с загрузками и историей.
        self._tegr_right_btn = QToolButton()
        self._tegr_right_btn.setObjectName('settingbtn')
        self._tegr_right_btn.setIcon(icons.get_icon('list'))
        self._tegr_right_btn.setIconSize(QSize(theme.scale_px(18),
                                              theme.scale_px(18)))
        self._tegr_right_btn.setFixedSize(theme.scale_px(32),
                                          theme.scale_px(30))
        self._tegr_right_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tegr_right_btn.setToolTip('Скрыть загрузки и историю')
        self._tegr_right_btn.clicked.connect(self._tegr_toggle_right)
        # Индикатор загрузок поверх значка списка. Всегда в одном углу,
        # меняется только цвет: акцент — что-то качается, жёлтый — пауза.
        dot_px = theme.scale_px(8)
        self._tegr_dl_dot = QLabel(self._tegr_right_btn)
        self._tegr_dl_dot.setFixedSize(dot_px, dot_px)
        self._tegr_dl_dot.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._tegr_dl_dot.hide()
        lay.addWidget(self._tegr_right_btn)
        self.btn_settings.setToolTip('Настройки')
        lay.addWidget(self.btn_settings)
        return bar

    # ------------------------------------------------------------------
    # Страницы. Одна и та же страница переезжает между слотами при смене
    # режима, поэтому строится ровно один раз.
    def _tegr_build_channels_page(self):
        page = QWidget()
        page.setObjectName('tegrChannelsPage')
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.scale_px(7))

        self.channel_search.setPlaceholderText('Поиск каналов…')
        lay.addWidget(self.channel_search)
        lay.addWidget(self.channel_list, 1)
        return page

    def _tegr_build_videos_page(self):
        page = QWidget()
        page.setObjectName('tegrVideosPage')
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.scale_px(7))

        row = QHBoxLayout()
        row.setSpacing(theme.scale_px(6))
        # В узком окне список видео стоит на месте списка каналов, и
        # вернуться к каналам можно только этой кнопкой.
        self._tegr_back_btn = QPushButton()
        self._tegr_back_btn.setObjectName('tegrBack')
        self._tegr_back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tegr_back_btn.setToolTip('Назад к каналам')
        self._tegr_back_btn.setFixedSize(theme.scale_px(24),
                                         theme.scale_px(24))
        self._tegr_back_btn.clicked.connect(
            lambda: self._tegr_set_nav('channels'))
        self._tegr_back_btn.installEventFilter(self)
        self._tegr_back_btn.hide()
        row.addWidget(self._tegr_back_btn)

        self.video_search.setPlaceholderText('Поиск видео…')
        row.addWidget(self.video_search, 1)

        # Кнопка сортировки — просто стрелка рядом с поиском.
        self.btn_order.setObjectName('tegrSort')
        self.btn_order.setFixedSize(theme.scale_px(26), theme.scale_px(26))
        self.btn_order.setToolTip('Порядок роликов')
        self.btn_order.installEventFilter(self)
        row.addWidget(self.btn_order)
        lay.addLayout(row)

        lay.addWidget(self.video_list, 1)
        lay.addWidget(self.btn_more)
        return page

    def _tegr_build_downloads_page(self):
        page = QWidget()
        page.setObjectName('tegrDownloadsPage')
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        # Сводку «качается · на паузе · скачано» не показываем: в правой
        # колонке она занимала отдельную строку, а счётчики и так видны
        # по самим загрузкам.
        self.downloads_view.hint.hide()
        lay.addWidget(self.downloads_view)
        return page

    def _tegr_build_history_page(self):
        page = QWidget()
        page.setObjectName('tegrHistoryPage')
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.scale_px(5))
        lay.addWidget(self.history_list, 1)
        hist_bottom = QHBoxLayout()
        hist_bottom.setContentsMargins(0, 0, theme.scale_px(8), 0)
        hist_bottom.setSpacing(theme.scale_px(4))
        hist_bottom.addWidget(self.btn_select_history)
        hist_bottom.addWidget(self.btn_delete_history_selected)
        hist_bottom.addStretch(1)
        hist_bottom.addWidget(self.btn_clear_history)
        lay.addLayout(hist_bottom)
        return page

    # ------------------------------------------------------------------
    # Рамки-слоты. В них лежат стеки, содержимое которых меняется по
    # ширине окна; сами рамки — дети сплиттера и просто скрываются.
    def _tegr_build_left(self):
        panel = QFrame()
        panel.setObjectName('tegrLeft')
        panel.setMinimumWidth(theme.scale_px(350))
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.scale_px(7))

        # В узком окне правой колонки нет, поэтому её разделы становятся
        # вкладками над содержимым — как папки в Telegram.
        self._tegr_narrow_bar = QWidget()
        self._tegr_narrow_bar.setObjectName('tegrNarrowTabs')
        nb = QHBoxLayout(self._tegr_narrow_bar)
        nb.setContentsMargins(theme.scale_px(10), 0, 0, 0)
        nb.setSpacing(theme.scale_px(2))
        self._tegr_narrow_group = QButtonGroup(self)
        self._tegr_narrow_group.setExclusive(True)
        self._tegr_narrow_buttons = []
        for index, text in enumerate(('Каналы', 'История', 'Загрузки')):
            btn = QPushButton(text)
            btn.setObjectName('tegrTab')
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._tegr_narrow_group.addButton(btn, index)
            btn.clicked.connect(
                lambda _checked=False, i=index:
                    self._tegr_narrow_tab_clicked(i))
            nb.addWidget(btn)
            self._tegr_narrow_buttons.append(btn)
        nb.addStretch(1)
        self._tegr_narrow_bar.hide()
        lay.addWidget(self._tegr_narrow_bar)
        # Линия под вкладками — общая граница раздела, как в плоском
        # макете, а не рамка вокруг каждой кнопки.
        self._tegr_narrow_rule = QFrame()
        self._tegr_narrow_rule.setObjectName('tegrRule')
        self._tegr_narrow_rule.setFixedHeight(theme.scale_px(1))
        self._tegr_narrow_rule.hide()
        lay.addWidget(self._tegr_narrow_rule)

        self._tegr_left_stack = QStackedWidget()
        self._tegr_left_stack.addWidget(self._tegr_channels_page)
        lay.addWidget(self._tegr_left_stack, 1)
        return panel

    def _tegr_build_center(self):
        panel = QFrame()
        panel.setObjectName('tegrCenter')
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # В среднем и узком режиме видео переезжает в левую колонку и
        # здесь остаётся пустая, скрытая рамка.
        self._tegr_center_stack = QStackedWidget()
        self._tegr_center_stack.addWidget(self._tegr_videos_page)
        lay.addWidget(self._tegr_center_stack, 1)
        return panel

    def _tegr_build_right(self):
        panel = QFrame()
        panel.setObjectName('tegrRight')
        panel.setMinimumWidth(theme.scale_px(450))
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(theme.scale_px(9), 0,
                               theme.scale_px(9), 0)
        lay.setSpacing(theme.scale_px(7))

        tabs = QHBoxLayout()
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(theme.scale_px(2))
        self._tegr_tab_group = QButtonGroup(self)
        self._tegr_tab_group.setExclusive(True)
        self._tegr_tab_buttons = []
        for index, text in enumerate(('Загрузки', 'История')):
            btn = QPushButton(text)
            btn.setObjectName('tegrTab')
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._tegr_tab_group.addButton(btn, index)
            btn.clicked.connect(
                lambda _checked=False, i=index: self._tegr_switch_tab(i))
            tabs.addWidget(btn)
            self._tegr_tab_buttons.append(btn)
        tabs.addStretch(1)
        lay.addLayout(tabs)
        self._tegr_tabs_rule = QFrame()
        self._tegr_tabs_rule.setObjectName('tegrRule')
        self._tegr_tabs_rule.setFixedHeight(theme.scale_px(1))
        lay.addWidget(self._tegr_tabs_rule)

        self._tegr_right_stack = QStackedWidget()
        self._tegr_right_stack.addWidget(self._tegr_downloads_page)
        self._tegr_right_stack.addWidget(self._tegr_history_page)
        # Старое имя: по нему вкладки и проверки находили правый стек.
        self._tegr_stack = self._tegr_right_stack
        lay.addWidget(self._tegr_right_stack, 1)
        return panel

    # ------------------------------------------------------------------
    # Адаптивная раскладка
    # ------------------------------------------------------------------
    @classmethod
    def _tegr_mode_for_width(cls, width):
        # Минимумы колонок заданы через theme.scale_px, значит и пороги
        # раскладки должны расти вместе с масштабом интерфейса.
        if width >= theme.scale_px(cls._TEGR_WIDE_MIN):
            return 'wide'
        if width >= theme.scale_px(cls._TEGR_MEDIUM_MIN):
            return 'medium'
        return 'narrow'

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not getattr(self, '_tegr_ready', False):
            return
        if getattr(self, '_tegr_applying_mode', False) or self.isMinimized():
            return
        # Новый размер окна важнее незаконченных анимаций: иначе они
        # будут возвращать старые пропорции и геометрию снимка.
        self._tegr_slide_stop()
        anim = getattr(self, '_tegr_split_anim', None)
        if anim is not None:
            anim.stop()
            self._tegr_split_anim = None
        mode = self._tegr_mode_for_width(self.width())
        if mode != getattr(self, '_tegr_mode', 'wide'):
            self._tegr_apply_mode(mode)

    def _tegr_page_map(self):
        return {
            'channels': self._tegr_channels_page,
            'videos': self._tegr_videos_page,
            'downloads': self._tegr_downloads_page,
            'history': self._tegr_history_page,
        }

    def _tegr_stack_of(self, page):
        for stack in (self._tegr_left_stack, self._tegr_center_stack,
                      self._tegr_right_stack):
            if stack.indexOf(page) >= 0:
                return stack
        return None

    def _tegr_layout_plan(self, mode):
        """Какая страница в каком слоте живёт в этом режиме."""
        if mode == 'wide':
            return {
                'channels': (self._tegr_left_stack, 0),
                'videos': (self._tegr_center_stack, 0),
                'downloads': (self._tegr_right_stack, 0),
                'history': (self._tegr_right_stack, 1),
            }
        if mode == 'medium':
            # Средний режим — это широкая раскладка без правой колонки:
            # каналы и видео видны одновременно.
            return {
                'channels': (self._tegr_left_stack, 0),
                'videos': (self._tegr_center_stack, 0),
                'downloads': (self._tegr_right_stack, 0),
                'history': (self._tegr_right_stack, 1),
            }
        return {
            'channels': (self._tegr_left_stack, 0),
            'videos': (self._tegr_left_stack, 1),
            'history': (self._tegr_left_stack, 2),
            'downloads': (self._tegr_left_stack, 3),
        }

    def _tegr_place_pages(self, mode):
        """Переносит страницы в слоты нужного режима.

        removeWidget не удаляет страницу и не рвёт её связи с виджетами:
        списки, строки и выделение остаются теми же. Страницу потом
        обязательно нужно показать через setCurrentWidget явно.
        """
        pages = self._tegr_page_map()
        plan = self._tegr_layout_plan(mode)
        stacks = (self._tegr_left_stack, self._tegr_center_stack,
                  self._tegr_right_stack)

        for name, (target, _index) in plan.items():
            page = pages[name]
            owner = self._tegr_stack_of(page)
            if owner is not None and owner is not target:
                owner.removeWidget(page)

        for stack in stacks:
            wanted = sorted(
                (index, name)
                for name, (target, index) in plan.items()
                if target is stack)
            desired = [pages[name] for _index, name in wanted]
            current = [stack.widget(i) for i in range(stack.count())]
            if current == desired:
                continue
            for page in list(current):
                stack.removeWidget(page)
            for page in desired:
                stack.addWidget(page)

    def _tegr_apply_mode(self, mode, force=False):
        """Перестраивает раскладку под ширину окна.

        Порядок важен: сначала страницы и рамки, потом размеры сплиттера.
        Если задать размеры при скрытой рамке, её ширина обнулится.
        """
        if not force and mode == getattr(self, '_tegr_mode', 'wide'):
            return
        if getattr(self, '_tegr_right_popup', False):
            self._tegr_restore_right_panel(reapply=False)
        old = getattr(self, '_tegr_mode', 'wide')
        was_center = self._tegr_center.isVisible()
        was_right = self._tegr_right.isVisible()
        was_bar = self._tegr_narrow_bar.isVisible()
        self._tegr_applying_mode = True
        self._tegr_slide_stop()
        self.setUpdatesEnabled(False)
        try:
            # Пропорции здесь не запоминаем: к моменту resizeEvent
            # сплиттер уже пересчитан под новую ширину, и сохранение
            # записало бы сжатые значения. Широкие пропорции обновляет
            # только перетаскивание (splitterMoved) и закрытие окна.

            # Уход из узкого режима: вкладка «Каналы» возвращает в поток
            # каналов, «История»/«Загрузки» — в правую колонку.
            if old == 'narrow':
                if self._tegr_narrow_tab == 1:
                    self._tegr_right_page = 'history'
                elif self._tegr_narrow_tab == 2:
                    self._tegr_right_page = 'downloads'

            # Вход в узкий режим: если канал открыт, оставляем на экране
            # видео, иначе — список каналов.
            if mode == 'narrow' and old != 'narrow':
                self._tegr_nav = ('videos'
                                  if getattr(self, '_active_dialog_id', None)
                                  else 'channels')

            self._tegr_mode = mode
            self._tegr_place_pages(mode)
            self._tegr_apply_mode_constraints()

            self._tegr_center.setVisible(mode in ('wide', 'medium'))
            self._tegr_right.setVisible(self._tegr_right_visible(mode))
            self._tegr_narrow_bar.setVisible(mode == 'narrow')
            try:
                self._tegr_narrow_rule.setVisible(mode == 'narrow')
            except AttributeError:
                pass
            self._tegr_update_right_toggle()

            if mode == 'wide':
                # При возврате в широкую раскладку размеры доведём
                # плавно уже после перестройки (см. ниже).
                if old == 'wide':
                    self._tegr_splitter.setSizes(
                        self._tegr_splitter_target('wide'))
            elif mode == 'medium':
                self._tegr_splitter.setSizes(
                    self._tegr_splitter_target('medium'))

            # Что показать в левой колонке.
            if mode == 'narrow':
                page = (self._tegr_videos_page if self._tegr_nav == 'videos'
                        else self._tegr_channels_page)
                self._tegr_left_stack.setCurrentWidget(page)
            else:
                self._tegr_left_stack.setCurrentWidget(
                    self._tegr_channels_page)
            right_page = (self._tegr_downloads_page
                          if self._tegr_right_page == 'downloads'
                          else self._tegr_history_page)
            if mode != 'narrow':
                self._tegr_center_stack.setCurrentWidget(
                    self._tegr_videos_page)
                self._tegr_right_stack.setCurrentWidget(right_page)
            self._tegr_update_right_tabs()

            if mode == 'narrow':
                tab = 0
                # Из среднего режима правая колонка не видна, поэтому
                # всегда показываем каналы/видео. Сохраняем вкладку
                # загрузок/истории только при сужении из широкого окна.
                if old == 'wide' and self._tegr_last_area == 'right':
                    tab = 1 if self._tegr_right_page == 'history' else 2
                self._tegr_show_narrow_tab(tab)
            elif self._tegr_right_visible(mode):
                self._tegr_sync_right_driver()
            else:
                # Правая колонка скрыта — таймер загрузок не нужен.
                self._tegr_drive_main_tab('channels')
            self._tegr_update_back_button()
        finally:
            self.setUpdatesEnabled(True)
            self.update()
            self._tegr_applying_mode = False

        # Появившиеся колонки и полосу вкладок проявляем уже после
        # перестройки, когда обновление экрана снова включено.
        if not was_center and self._tegr_center.isVisible():
            self._tegr_fade_in(self._tegr_center)
        if not was_right and self._tegr_right.isVisible():
            self._tegr_fade_in(self._tegr_right)
        if not was_bar and self._tegr_narrow_bar.isVisible():
            self._tegr_fade_in(self._tegr_narrow_bar)
        if mode == 'wide' and old != 'wide':
            QTimer.singleShot(0, self._tegr_animate_wide_sizes)

    def _tegr_apply_mode_constraints(self):
        """Ширины колонок и верхней полосы под текущий режим."""
        mode = getattr(self, '_tegr_mode', 'wide')
        try:
            self._tegr_left.setMinimumWidth(theme.scale_px(350))
            # Каналы не шире 460, история/загрузки не уже 450 — остальное
            # забирает колонка видео. В узком окне левая колонка одна и
            # должна занимать всю ширину, поэтому максимум снимаем.
            if mode == 'narrow':
                self._tegr_left.setMaximumWidth(16777215)
            else:
                self._tegr_left.setMaximumWidth(theme.scale_px(460))
            self._tegr_center.setMinimumWidth(theme.scale_px(320))
            self._tegr_right.setMinimumWidth(theme.scale_px(450))
            # В широкой раскладке ширина истории/загрузок зафиксирована:
            # минимум и максимум совпадают, поэтому ни окно, ни
            # разделитель её не меняют — всё лишнее забирает видео.
            # В среднем режиме эта же рамка живёт в плавающем окне и
            # должна уметь занимать его ширину целиком.
            if mode == 'wide':
                self._tegr_right.setMaximumWidth(theme.scale_px(450))
            else:
                self._tegr_right.setMaximumWidth(16777215)
            if mode == 'narrow':
                self.folder_combo.setMinimumWidth(theme.scale_px(110))
                self.folder_combo.setMaximumWidth(theme.scale_px(150))
            else:
                self.folder_combo.setMinimumWidth(theme.scale_px(140))
                self.folder_combo.setMaximumWidth(theme.scale_px(180))
        except Exception:
            pass
        self._tegr_update_back_button()
        self._tegr_update_right_toggle()

    def _tegr_update_back_icon(self):
        """Рисует толстую стрелку «назад» вместо тонкого символа шрифта."""
        try:
            pal = theme.theme()
            normal = QColor(pal.get('accent') or '#ffffff')
            active = QColor(pal.get('accent_hi') or normal)
            color = active if getattr(self, '_tegr_back_hover', False)                 else normal
            size = theme.scale_px(18)
            icon = QIcon()
            icon.addPixmap(
                self._tegr_back_arrow(color, size), QIcon.Mode.Normal)
            icon.addPixmap(
                self._tegr_back_arrow(color, size), QIcon.Mode.Active)
            self._tegr_back_btn.setIcon(icon)
            self._tegr_back_btn.setIconSize(QSize(size, size))
        except Exception:
            pass

    @staticmethod
    def _tegr_back_arrow(color, size):
        """Стрелка с толстым штрихом и скруглёнными концами."""
        pm = QPixmap(int(size), int(size))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, max(1.8, size * 0.15))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        y = size / 2.0
        left = size * 0.18
        right = size * 0.86
        head = size * 0.30
        p.drawLine(QPointF(left, y), QPointF(right, y))
        p.drawLine(QPointF(left, y), QPointF(left + head, y - head))
        p.drawLine(QPointF(left, y), QPointF(left + head, y + head))
        p.end()
        return pm

    def _tegr_update_back_button(self):
        try:
            visible = (getattr(self, '_tegr_mode', 'wide') == 'narrow'
                       and getattr(self, '_tegr_nav', 'channels') == 'videos')
            self._tegr_back_btn.setVisible(visible)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Анимации
    # ------------------------------------------------------------------
    def _tegr_show_page(self, stack, page, animate=True, direction=1):
        """Показывает страницу со сдвигом.

        Мгновенное переключение списков выглядело как рывок, поэтому
        старую страницу «уводим» вбок, а новая остаётся под ней. Во
        время перестройки раскладки анимация не нужна — там всё и так
        меняется разом.
        """
        if stack is None or page is None:
            return
        try:
            if stack.currentWidget() is page:
                return
        except Exception:
            return
        if animate and not getattr(self, '_tegr_applying_mode', False):
            self._tegr_slide_switch(stack, page, direction)
            return
        try:
            stack.setCurrentWidget(page)
        except Exception:
            pass

    def _tegr_surface_color(self, stack):
        """Цвет поверхности под страницами этого стека.

        В главном окне страницы лежат на фоне окна, а в плавающем окне
        истории/загрузок — на панели поп-апа. Если взять не тот цвет,
        при переключении вкладок фон на мгновение темнеет.
        """
        try:
            if (getattr(self, '_tegr_right_popup', False)
                    and stack is getattr(self, '_tegr_right_stack', None)):
                return theme.theme().get('panel') or '#000000'
            return theme.theme().get('bg') or '#000000'
        except Exception:
            return '#000000'

    def _tegr_snapshot(self, widget, color=None):
        """Снимок страницы на цвете её поверхности.

        Страницы прозрачные: цвет даёт окно или панель поп-апа.
        Накладывать друг на друга два полупрозрачных снимка нельзя —
        они смешаются, поэтому под каждый подкладываем ровный цвет.
        """
        try:
            pixmap = widget.grab()
            base = QPixmap(pixmap.size())
            if color is None:
                try:
                    color = theme.theme().get('bg') or '#000000'
                except Exception:
                    color = '#000000'
            base.fill(QColor(color))
            painter = QPainter(base)
            painter.drawPixmap(0, 0, pixmap)
            painter.end()
            return base
        except Exception:
            return widget.grab()

    def _tegr_slide_stop(self):
        """Убирает незаконченный переход страницы."""
        overlay = getattr(self, '_tegr_slide_overlay', None)
        self._tegr_slide_overlay = None
        self._tegr_slide_label = None
        self._tegr_slide_label2 = None
        self._tegr_slide_anim = None
        if overlay is not None:
            try:
                overlay.stop()
            except Exception:
                pass
            try:
                overlay.deleteLater()
            except Exception:
                pass

    def _tegr_finish_slide(self, overlay):
        if getattr(self, '_tegr_slide_overlay', None) is overlay:
            self._tegr_slide_overlay = None
            # Старое имя оставлено для проверок.
            self._tegr_slide_label = None
        try:
            overlay.deleteLater()
        except Exception:
            pass

    def _tegr_slide_switch(self, stack, page, direction=1, duration=300):
        """Сдвиг с перекрёстным затуханием, как в Telegram.

        Обе страницы едут на небольшую долю ширины: старая уходит и
        тает, новая приходит с другой стороны и проступает.
        """
        try:
            old = stack.currentWidget()
            if old is None or old is page:
                stack.setCurrentWidget(page)
                return
            color = self._tegr_surface_color(stack)
            old_pixmap = self._tegr_snapshot(old, color)
            stack.setCurrentWidget(page)
            new_pixmap = self._tegr_snapshot(page, color)
            self._tegr_slide_stop()

            width = max(1, stack.width())
            height = max(1, stack.height())
            offset = max(theme.scale_px(24), width // 4)
            shift = int(direction) * int(offset)

            overlay = _TegrSlideOverlay(
                stack, old_pixmap, new_pixmap, shift, color, duration)
            overlay.setGeometry(0, 0, width, height)
            overlay.show()
            overlay.raise_()
            self._tegr_slide_overlay = overlay
            # Старое имя оставлено для проверок.
            self._tegr_slide_label = overlay
            overlay.start(lambda: self._tegr_finish_slide(overlay))
        except Exception:
            try:
                stack.setCurrentWidget(page)
            except Exception:
                pass

    def _tegr_fade_in(self, widget, duration=300):
        if widget is None or not getattr(self, '_tegr_ready', False):
            return
        try:
            prev = getattr(widget, '_tegr_fade_anim', None)
            if prev is not None:
                prev.stop()
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.0)
            widget.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b'opacity', widget)
            anim.setDuration(int(duration))
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            def _clear(w=widget):
                try:
                    w.setGraphicsEffect(None)
                except Exception:
                    pass
            anim.finished.connect(_clear)
            widget._tegr_fade_anim = anim
            anim.start()
        except Exception:
            try:
                widget.setGraphicsEffect(None)
            except Exception:
                pass

    def _tegr_animate_splitter(self, target):
        """Плавно ведёт сплиттер к нужным размерам.

        Нужен при возврате в широкую раскладку: средняя и правая
        колонки появляются и раздвигаются, а не встают на место рывком.
        """
        try:
            target = [int(x) for x in target]
            start = [int(x) for x in self._tegr_splitter.sizes()]
            if len(start) != 3 or len(target) != 3 or start == target:
                self._tegr_splitter.setSizes(target)
                return
            old = getattr(self, '_tegr_split_anim', None)
            if old is not None:
                old.stop()
            anim = QVariantAnimation(self)
            anim.setDuration(300)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            def _step(value, start=start, target=target):
                try:
                    self._tegr_splitter.setSizes([
                        int(round(a + (b - a) * float(value)))
                        for a, b in zip(start, target)])
                except Exception:
                    pass

            def _done(a=anim):
                if getattr(self, '_tegr_split_anim', None) is a:
                    self._tegr_split_anim = None
                try:
                    a.deleteLater()
                except Exception:
                    pass

            anim.valueChanged.connect(_step)
            anim.finished.connect(_done)
            self._tegr_split_anim = anim
            anim.start()
        except Exception:
            try:
                self._tegr_splitter.setSizes(target)
            except Exception:
                pass

    def _tegr_right_visible(self, mode=None):
        """Показана ли колонка загрузок/истории в этом режиме."""
        mode = mode or getattr(self, '_tegr_mode', 'wide')
        if mode == 'wide':
            return not getattr(self, '_tegr_right_collapsed', False)
        # В среднем это не колонка, а плавающее окно (см.
        # _tegr_show_right_popup), поэтому в сплиттере её нет.
        return False

    def _tegr_splitter_target(self, mode):
        """Пропорциональные размеры колонок под текущую ширину окна.

        Сохранённые размеры — пиксели при прежней ширине. Если подставить
        их как есть в более узкое окно, каналы остаются широкими, а
        видео сжимается. Поэтому масштабируем пропорции под ширину окна.
        """
        saved = list(getattr(self, '_tegr_wide_sizes', None) or [])
        if len(saved) != 3 or any(x <= 0 for x in saved):
            saved = [250, 620, 340]
        show_right = self._tegr_right_visible(mode)
        left_min = theme.scale_px(350)
        left_max = theme.scale_px(460)
        center_min = theme.scale_px(320)
        right_min = theme.scale_px(450)
        handles = 2 if show_right else 1
        try:
            margins = self._tegr_outer.contentsMargins()
            margins = margins.left() + margins.right()
        except Exception:
            margins = theme.scale_px(24)
        avail = max(1, self.width() - margins
                    - self._tegr_splitter.handleWidth() * handles)
        needed = left_min + center_min + (right_min if show_right else 0)
        avail = max(needed, int(avail))

        # Ширина истории/загрузок зафиксирована на 450: она не зависит
        # от прежних размеров и не растягивается вместе с окном.
        right = right_min if show_right else 0
        # Каналы делят оставшееся место с видео по прежним пропорциям.
        rest = avail - right
        wl = float(saved[0])
        wc = float(saved[1])
        total = (wl + wc) or 1.0
        left = int(round(rest * wl / total))
        left = max(left_min, min(left_max, left))
        center = avail - left - right
        # Видео всегда получает остаток. Если места совсем мало,
        # ужимаем каналы до минимума.
        if center < center_min:
            left = max(left_min, rest - center_min)
            center = avail - left - right
        return [left, center, right]

    def _tegr_animate_wide_sizes(self):
        if getattr(self, '_tegr_mode', 'wide') != 'wide':
            return
        self._tegr_animate_splitter(self._tegr_splitter_target('wide'))

    def _tegr_update_right_toggle(self):
        """Кнопка загрузок/истории: видна в среднем и широком окне."""
        try:
            mode = getattr(self, '_tegr_mode', 'wide')
            self._tegr_right_btn.setVisible(mode in ('wide', 'medium'))
            if mode == 'medium':
                opened = getattr(self, '_tegr_right_popup', False)
                tip = ('Скрыть историю и загрузки' if opened
                       else 'История и загрузки')
            else:
                shown = self._tegr_right_visible('wide')
                tip = ('Скрыть загрузки и историю' if shown
                       else 'Показать загрузки и историю')
            self._tegr_right_btn.setToolTip(tip)
        except Exception:
            pass

    def _tegr_update_dl_badges(self):
        """Точки на кнопке списка: идёт загрузка / загрузка на паузе."""
        try:
            cache = getattr(self.backend, 'cache', {}) or {}
            busy = paused = 0
            for entry in cache.values():
                if not isinstance(entry, dict):
                    continue
                if entry.get('removed') or entry.get('complete'):
                    continue
                if entry.get('paused'):
                    paused += 1
                else:
                    busy += 1
            btn = self._tegr_right_btn
            dot = self._tegr_dl_dot.width()
            x = max(0, btn.width() - dot - 3)
            self._tegr_dl_dot.move(x, 3)
            self._tegr_dl_dot.raise_()
            color = (theme.theme()['accent'] if busy > 0 else '#f2b84b')
            self._tegr_dl_dot.setStyleSheet(
                f'background: {color}; border-radius: {dot // 2}px;')
            self._tegr_dl_dot.setVisible(busy > 0 or paused > 0)
        except Exception:
            pass

    def _tegr_toggle_right(self):
        """В широком окне — колонка, в среднем — плавающее окно."""
        mode = getattr(self, '_tegr_mode', 'wide')
        if mode == 'wide':
            self._tegr_right_collapsed = not getattr(
                self, '_tegr_right_collapsed', False)
            self._set_setting_quiet('tegr_right_collapsed',
                                    self._tegr_right_collapsed)
            self._tegr_apply_mode('wide', force=True)
        elif mode == 'medium':
            # Клик по кнопке сначала закрывает попап (клик мимо окна),
            # и только потом доходит до кнопки. Второй раз не открываем.
            if getattr(self, '_tegr_right_popup_just_closed', False):
                self._tegr_right_popup_just_closed = False
                return
            if getattr(self, '_tegr_right_popup', False):
                self._tegr_close_right_popup()
            else:
                self._tegr_show_right_popup()

    def _tegr_clear_popup_flag(self):
        self._tegr_right_popup_just_closed = False

    def _tegr_restore_splitter_sizes(self, sizes):
        """Возвращает прежние ширины колонок после переезда правой панели."""
        try:
            values = [int(x) for x in (sizes or [])]
            count = self._tegr_splitter.count()
            values = values[:count]
            if values and sum(values) > 0:
                self._tegr_splitter.setSizes(values)
        except Exception:
            pass

    def _tegr_show_right_popup(self):
        """Показывает историю/загрузки плавающим окном в среднем режиме."""
        if (getattr(self, '_tegr_right_popup', False)
                or getattr(self, '_tegr_mode', 'wide') != 'medium'):
            return
        saved_sizes = []
        try:
            saved_sizes = [int(x) for x in self._tegr_splitter.sizes()]
        except Exception:
            pass
        try:
            # Переезд правой панели в отдельное окно заставляет QSplitter
            # пересчитывать ширины. На один кадр колонка видео менялась,
            # и правые подписи длительности/размера дёргались. Сохраняем
            # размеры и не даём сплиттеру перерисоваться между делом.
            self._tegr_splitter.setUpdatesEnabled(False)
            index = self._tegr_splitter.indexOf(self._tegr_right)
            if index < 0:
                index = 2
            pal = theme.theme()
            wrapper = getattr(self, '_tegr_right_popup_window', None)
            if wrapper is None:
                wrapper = _TegrRightPopup(
                    self, pal['panel'], pal['divider'])
                self._tegr_right_popup_window = wrapper
                wrapper.installEventFilter(self)
            else:
                wrapper.set_colors(pal['panel'], pal['divider'])
            screen = self.screen()
            area = screen.availableGeometry() if screen else self.geometry()
            width = min(theme.scale_px(480),
                        max(theme.scale_px(300),
                            area.width() - theme.scale_px(16)))
            height = min(max(theme.scale_px(360),
                             int(self.height() * 0.72)),
                         area.height() - theme.scale_px(16))
            btn = self._tegr_right_btn
            anchor = btn.mapToGlobal(
                QPoint(btn.width(), btn.height() + theme.scale_px(4)))
            x = max(area.left(), min(anchor.x() - width,
                                     area.right() - width))
            y = max(area.top(), min(anchor.y(), area.bottom() - height))
            # Сначала задаём окну конечный размер, и только потом кладём
            # в него панель. Иначе строки на миг раскладывались под
            # старый размер окна и правые подписи дёргались.
            wrapper.resize(width, height)
            wrapper.move(x, y)
            # Внутри окна фон рисует обёртка, рамке он не нужен.
            self._tegr_right.setStyleSheet(
                'QFrame#tegrRight { background: transparent; border: none; }')
            wrapper.layout().addWidget(self._tegr_right)
            self._tegr_right.show()
            self._tegr_right_popup = True
            # На время окна панель забирается из сплиттера: там остаются
            # каналы и видео, а сетка размеров не трогается.
            self._tegr_right_placeholder = None
            wrapper.show()
            wrapper.raise_()
            wrapper.setFocus()
            self._tegr_update_right_toggle()
        except Exception:
            self._tegr_right_popup = False
            self._tegr_restore_right_panel()
        finally:
            try:
                self._tegr_restore_splitter_sizes(saved_sizes)
            finally:
                self._tegr_splitter.setUpdatesEnabled(True)

    def _tegr_close_right_popup(self):
        if not getattr(self, '_tegr_right_popup', False):
            return
        self._tegr_restore_right_panel()

    def _tegr_restore_right_panel(self, reapply=True):
        """Возвращает правую панель на место в сплиттере."""
        if not getattr(self, '_tegr_right_popup', False):
            return
        self._tegr_right_popup = False
        try:
            saved_sizes = [int(x) for x in self._tegr_splitter.sizes()]
        except Exception:
            saved_sizes = []
        wrapper = getattr(self, '_tegr_right_popup_window', None)
        self._tegr_right_popup_window = None
        try:
            self._tegr_splitter.setUpdatesEnabled(False)
            # Сначала гасим окно, потом разбираем содержимое: иначе
            # строки успевали переразложиться на глазах.
            if wrapper is not None:
                wrapper.hide()
            self._tegr_right.hide()
            self._tegr_right.setStyleSheet('')
            if wrapper is not None:
                wrapper.layout().removeWidget(self._tegr_right)
            self._tegr_right.setParent(None)
        except Exception:
            pass
        try:
            if self._tegr_splitter.indexOf(self._tegr_right) < 0:
                self._tegr_splitter.insertWidget(2, self._tegr_right)
        except Exception:
            pass
        try:
            self._tegr_right.hide()
        except Exception:
            pass
        if wrapper is not None:
            try:
                wrapper.hide()
            except Exception:
                pass
        if reapply:
            try:
                self._tegr_apply_mode(self._tegr_mode, force=True)
            except Exception:
                pass
        try:
            self._tegr_restore_splitter_sizes(saved_sizes)
        finally:
            self._tegr_splitter.setUpdatesEnabled(True)

    # ------------------------------------------------------------------
    # Вкладки и навигация
    # ------------------------------------------------------------------
    def _tegr_drive_main_tab(self, name):
        """Держит main_tabs в согласии с видимым разделом.

        Именно main_tabs включает таймер загрузок и обновление истории:
        вкладки нового окна — только внешний вид, поэтому каждый переход
        идёт через этот метод.
        """
        try:
            if name == 'channels':
                target = 0
            elif name == 'history':
                target = self._history_tab_index()
            else:
                target = self._downloads_tab_index()
            if target < 0:
                return
            if self.main_tabs.currentIndex() != target:
                self.main_tabs.setCurrentIndex(target)
                return
            # Индекс уже нужный — сигнал не придёт, повторяем побочные
            # действия вручную.
            if name == 'history':
                self._refresh_history()
            elif name == 'downloads':
                self._refresh_downloads_view()
                self._dl_timer.start()
            else:
                self._dl_timer.stop()
        except Exception:
            pass

    def _tegr_update_right_tabs(self):
        try:
            for i, btn in enumerate(self._tegr_tab_buttons):
                btn.setChecked(
                    (i == 0 and self._tegr_right_page == 'downloads') or
                    (i == 1 and self._tegr_right_page == 'history'))
        except Exception:
            pass

    def _tegr_update_narrow_tabs(self):
        try:
            for i, btn in enumerate(self._tegr_narrow_buttons):
                btn.setChecked(i == self._tegr_narrow_tab)
        except Exception:
            pass

    def _tegr_sync_right_driver(self):
        """В wide/medium правый стек задаёт, что сейчас активно."""
        try:
            page = self._tegr_right_stack.currentWidget()
        except Exception:
            page = None
        if page is self._tegr_downloads_page:
            name = 'downloads'
        elif page is self._tegr_history_page:
            name = 'history'
        else:
            name = self._tegr_right_page
        self._tegr_right_page = name
        self._tegr_update_right_tabs()
        self._tegr_drive_main_tab(name)

    def _tegr_switch_tab(self, index):
        """Вкладки правой колонки: Загрузки (0) и История (1)."""
        index = 0 if int(index) == 0 else 1
        name = 'downloads' if index == 0 else 'history'
        self._tegr_right_page = name
        self._tegr_last_area = 'right'
        if getattr(self, '_tegr_mode', 'wide') == 'narrow':
            self._tegr_show_narrow_tab(2 if index == 0 else 1)
            return
        page = (self._tegr_downloads_page if index == 0
                else self._tegr_history_page)
        # История лежит правее загрузок: туда — влево, обратно — вправо.
        self._tegr_show_page(self._tegr_right_stack, page,
                             direction=1 if index == 1 else -1)
        self._tegr_update_right_tabs()
        self._tegr_drive_main_tab(name)

    def _tegr_narrow_tab_clicked(self, index):
        """Клик по вкладке узкого окна.

        «Каналы» — не просто показать последнюю страницу, а вернуться к
        списку каналов: вкладка ведёт себя как папка в Telegram, даже
        если до этого был открыт канал с видео.
        """
        if int(index) == 0:
            self._tegr_set_nav('channels')
            return
        self._tegr_show_narrow_tab(index)

    def _tegr_show_narrow_tab(self, index):
        """Одна колонка: Каналы (0), История (1), Загрузки (2)."""
        prev = getattr(self, '_tegr_narrow_tab', 0)
        index = max(0, min(2, int(index)))
        direction = 1 if index >= prev else -1
        self._tegr_narrow_tab = index
        self._tegr_last_area = 'nav' if index == 0 else 'right'
        try:
            if index == 0:
                page = (self._tegr_videos_page
                        if self._tegr_nav == 'videos'
                        else self._tegr_channels_page)
                self._tegr_show_page(self._tegr_left_stack, page,
                                     direction=direction)
                self._tegr_drive_main_tab('channels')
            elif index == 1:
                self._tegr_show_page(self._tegr_left_stack,
                                     self._tegr_history_page,
                                     direction=direction)
                self._tegr_right_page = 'history'
                self._tegr_drive_main_tab('history')
            else:
                self._tegr_show_page(self._tegr_left_stack,
                                     self._tegr_downloads_page,
                                     direction=direction)
                self._tegr_right_page = 'downloads'
                self._tegr_drive_main_tab('downloads')
        except Exception:
            pass
        self._tegr_update_narrow_tabs()
        self._tegr_update_back_button()

    def _tegr_set_nav(self, nav):
        """В узком окне меняет левую колонку: каналы или видео."""
        self._tegr_nav = 'videos' if nav == 'videos' else 'channels'
        if getattr(self, '_tegr_mode', 'wide') == 'narrow':
            page = (self._tegr_videos_page if self._tegr_nav == 'videos'
                    else self._tegr_channels_page)
            # Каналы → видео сдвигаем влево, обратно — вправо.
            self._tegr_show_page(
                self._tegr_left_stack, page,
                direction=1 if self._tegr_nav == 'videos' else -1)
            self._tegr_last_area = 'nav'
            if self._tegr_nav == 'channels':
                # Возврат к списку снимает выделение: иначе строка
                # открытого канала остаётся текущей, повторный клик по ней
                # не меняет currentRow и канал не открывается снова.
                try:
                    self.channel_list.blockSignals(True)
                    self.channel_list.setCurrentRow(-1)
                finally:
                    self.channel_list.blockSignals(False)
            self._tegr_narrow_tab = 0
            self._tegr_update_narrow_tabs()
            try:
                self._tegr_drive_main_tab('channels')
            except Exception:
                pass
        self._tegr_update_back_button()

    def _open_channel(self, dialog):
        """Выбор канала: в узком окне показываем видео на месте каналов."""
        super()._open_channel(dialog)
        self._tegr_set_nav('videos')

    def _open_download_item(self, entry):
        super()._open_download_item(entry)
        self._tegr_close_right_popup()

    def _open_history_item(self, item):
        selecting = bool(getattr(self, '_history_selecting', False))
        super()._open_history_item(item)
        if not selecting:
            self._tegr_close_right_popup()

    def _goto_video(self, entry):
        super()._goto_video(entry)
        self._tegr_close_right_popup()

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        # Ширины колонок и видимость правой панели помним между запусками.
        if getattr(self, '_tegr_right_popup', False):
            self._tegr_close_right_popup()
        try:
            self._tegr_sizes_timer.stop()
        except Exception:
            pass
        self._tegr_save_splitter_sizes(persist=True)
        self._set_setting_quiet('tegr_right_collapsed',
                                bool(self._tegr_right_collapsed))
        self._tegr_remove_fade_filter()
        super().closeEvent(event)

    def _tegr_enforce_limits(self):
        """Каналы не шире 460, правая панель ровно 450."""
        try:
            if getattr(self, '_tegr_mode', 'wide') == 'narrow':
                return
            sizes = [int(x) for x in self._tegr_splitter.sizes()]
            if len(sizes) != 3:
                return
            changed = False
            max_left = theme.scale_px(460)
            if sizes[0] > max_left:
                sizes[1] += sizes[0] - max_left
                sizes[0] = max_left
                changed = True
            if self._tegr_right_visible() and sizes[2] > 0:
                # Правая колонка ровно 450: разделитель её не двигает.
                fixed_right = theme.scale_px(450)
                if sizes[2] != fixed_right:
                    sizes[1] += sizes[2] - fixed_right
                    sizes[2] = fixed_right
                    changed = True
            if changed:
                sizes[1] = max(theme.scale_px(320), sizes[1])
                self._tegr_splitter.setSizes(sizes)
        except Exception:
            pass

    def _tegr_save_splitter_sizes(self, *_args, persist=False):
        """Запоминает ширины колонок для всех раскладок."""
        self._tegr_enforce_limits()
        try:
            sizes = [int(x) for x in self._tegr_splitter.sizes()]
        except Exception:
            return
        if len(sizes) != 3 or sum(sizes) <= 0:
            return
        mode = getattr(self, '_tegr_mode', 'wide')
        if mode == 'wide':
            if sizes[0] <= 0 or sizes[1] <= 0:
                return
            if sizes[2] > 0:
                self._tegr_wide_sizes = sizes
            else:
                # Правая колонка свёрнута: запоминаем соотношение каналов
                # и видео, сохраняя прежнюю долю правой колонки.
                pair = sizes[0] + sizes[1]
                base = max(1, self._tegr_wide_sizes[0]
                           + self._tegr_wide_sizes[1])
                left = int(round(base * sizes[0] / pair))
                self._tegr_wide_sizes = [left, base - left,
                                         self._tegr_wide_sizes[2]]
        elif mode == 'medium':
            self._tegr_medium_sizes = sizes
            if sizes[0] <= 0 or sizes[1] <= 0:
                return
            # В среднем колонки всего две: запоминаем соотношение
            # каналов и видео в общих пропорциях широкой раскладки.
            pair = sizes[0] + sizes[1]
            base = max(1, self._tegr_wide_sizes[0]
                       + self._tegr_wide_sizes[1])
            left = int(round(base * sizes[0] / pair))
            self._tegr_wide_sizes = [left, base - left,
                                     self._tegr_wide_sizes[2]]
        else:
            return
        if persist:
            self._tegr_persist_splitter_sizes()
        else:
            self._tegr_sizes_timer.start(350)

    def _tegr_persist_splitter_sizes(self):
        sizes = getattr(self, '_tegr_wide_sizes', None)
        try:
            if not sizes or len(sizes) != 3 or any(x <= 0 for x in sizes):
                return
            self._set_setting_quiet(
                'tegr_splitter_sizes', [int(x) for x in sizes])
        except Exception:
            pass

    def _tegr_restore_selection(self, index):
        if index is None:
            return
        try:
            for row in range(self.video_list.count()):
                item = self.video_list.item(row)
                if item is None:
                    continue
                if int(item.data(Qt.ItemDataRole.UserRole)) == int(index):
                    self.video_list.setCurrentRow(row)
                    return
        except Exception:
            pass

    def _tegr_update_order_button(self):
        """Толстая стрелка сортировки вместо тонкого символа шрифта."""
        try:
            newest = getattr(self, '_order_newest_first', True)
            pal = theme.theme()
            normal = QColor(pal.get('text_dim') or '#8e8a82')
            active = QColor(pal.get('accent') or normal)
            color = active if getattr(self, '_tegr_sort_hover', False)                 else normal
            size = theme.scale_px(16)
            icon = QIcon()
            icon.addPixmap(
                self._tegr_sort_arrow(newest, color, size),
                QIcon.Mode.Normal)
            icon.addPixmap(
                self._tegr_sort_arrow(newest, color, size),
                QIcon.Mode.Active)
            self.btn_order.setText('')
            self.btn_order.setIcon(icon)
            self.btn_order.setIconSize(QSize(size, size))
            self.btn_order.setToolTip(
                'Сначала новые' if newest else 'Сначала старые')
        except Exception:
            pass

    @staticmethod
    def _tegr_sort_arrow(down, color, size):
        """Толстая стрелка вниз/вверх, в стиле кнопки «назад». """
        pm = QPixmap(int(size), int(size))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, max(1.8, size * 0.15))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        x = size / 2.0
        top = size * 0.16
        bottom = size * 0.84
        head = size * 0.30
        if down:
            p.drawLine(QPointF(x, top), QPointF(x, bottom))
            p.drawLine(QPointF(x, bottom),
                       QPointF(x - head, bottom - head))
            p.drawLine(QPointF(x, bottom),
                       QPointF(x + head, bottom - head))
        else:
            p.drawLine(QPointF(x, bottom), QPointF(x, top))
            p.drawLine(QPointF(x, top), QPointF(x - head, top + head))
            p.drawLine(QPointF(x, top), QPointF(x + head, top + head))
        p.end()
        return pm

    def _toggle_order(self):
        super()._toggle_order()
        self._tegr_update_order_button()

    def eventFilter(self, obj, event):
        """Запоминает, что по списку каналов щёлкнули правой кнопкой.

        QListWidget на нажатие ПКМ делает строку текущей, и
        currentRowChanged открывал канал ещё до контекстного меню. В
        узком окне список при этом успевал скрыться — меню не появлялось.
        """
        try:
            if (obj is getattr(self, '_tegr_right_popup_window', None)
                    and event.type() == QEvent.Type.Hide
                    and getattr(self, '_tegr_right_popup', False)):
                # Плавающее окно закрылось (клик мимо) — возвращаем
                # панель в сплиттер. Клик, который его закрыл, не должен
                # тут же открыть окно заново.
                self._tegr_right_popup_just_closed = True
                QTimer.singleShot(300, self._tegr_clear_popup_flag)
                QTimer.singleShot(0, self._tegr_restore_right_panel)
            # При возврате фокуса в список каналов Qt сам делает
            # текущей первую строку. После кнопки «назад» выделение
            # снято, и это самовыделение открывало канал, хотя его никто
            # не выбирал. Флаг гасит ровно такой переход; настоящий клик
            # его снимает.
            if obj is getattr(self, 'btn_order', None):
                if event.type() == QEvent.Type.Enter:
                    self._tegr_sort_hover = True
                    self._tegr_update_order_button()
                elif event.type() == QEvent.Type.Leave:
                    self._tegr_sort_hover = False
                    self._tegr_update_order_button()
            if obj is getattr(self, '_tegr_back_btn', None):
                if event.type() == QEvent.Type.Enter:
                    self._tegr_back_hover = True
                    self._tegr_update_back_icon()
                elif event.type() == QEvent.Type.Leave:
                    self._tegr_back_hover = False
                    self._tegr_update_back_icon()
            if (obj is self.channel_list
                    and event.type() == QEvent.Type.FocusIn
                    and self.channel_list.currentRow() < 0):
                self._tegr_channel_focus_guard = True
            if obj is self.channel_list.viewport():
                if event.type() == QEvent.Type.MouseButtonPress:
                    if event.button() == Qt.MouseButton.RightButton:
                        self._tegr_channel_right = True
                        self._tegr_channel_prev_row = \
                            self.channel_list.currentRow()
                    elif event.button() == Qt.MouseButton.LeftButton:
                        # Левый клик всегда главнее: страховка от
                        # «залипшего» флага, если меню почему-то не открылось.
                        self._tegr_channel_right = False
                        self._tegr_channel_left_press = True
                        QTimer.singleShot(
                            0, self._tegr_clear_channel_left_press)
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def on_channel_selected(self, row):
        # ПКМ по каналу показывает меню, а не открывает канал.
        if getattr(self, '_tegr_channel_right', False):
            return
        # Автовыделение первой строки при возврате фокуса окна — не
        # выбор пользователя. Снимаем его и остаёмся на списке каналов.
        guard = getattr(self, '_tegr_channel_focus_guard', False)
        pressed = getattr(self, '_tegr_channel_left_press', False)
        self._tegr_channel_focus_guard = False
        if row >= 0 and guard and not pressed:
            try:
                self.channel_list.blockSignals(True)
                self.channel_list.setCurrentRow(-1)
            finally:
                self.channel_list.blockSignals(False)
            return
        super().on_channel_selected(row)

    def _tegr_clear_channel_left_press(self):
        self._tegr_channel_left_press = False

    def _on_channel_menu(self, pos):
        """Контекстное меню канала; после него возвращаем выделение."""
        right_click = getattr(self, '_tegr_channel_right', False)
        try:
            super()._on_channel_menu(pos)
        finally:
            self._tegr_channel_right = False
            if right_click:
                prev = getattr(self, '_tegr_channel_prev_row', -1)
                try:
                    self.channel_list.blockSignals(True)
                    self.channel_list.setCurrentRow(prev)
                finally:
                    self.channel_list.blockSignals(False)
            self._tegr_channel_prev_row = -1

    def on_folder_changed(self, index):
        """Смена папки в узком окне показывает список каналов."""
        super().on_folder_changed(index)
        if getattr(self, '_tegr_mode', 'wide') == 'narrow':
            self._tegr_set_nav('channels')

    def filter_channels(self):
        super().filter_channels()

    def display_videos(self, dialog_id, videos):
        super().display_videos(dialog_id, videos)
        try:
            if self._list_has_videos and self.video_list.count():
                self.video_list.setCurrentRow(0)
        except Exception:
            pass

    def _tegr_on_connection(self, state):
        self._tegr_state = state
        self._tegr_update_conn(state)

    def _tegr_update_conn(self, state):
        if not hasattr(self, '_tegr_status_dot'):
            return
        p = theme.theme()
        if state == 'ok':
            color = p['ok']
            text = 'Подключено'
        elif state == 'connecting':
            color = '#f2b84b'
            text = 'Подключение…'
        else:
            color = p['error']
            text = 'Не удалось подключиться'
        size = theme.scale_px(8)
        self._tegr_status_dot.setStyleSheet(
            f'background: {color}; border-radius: {size // 2}px;')
        try:
            self._tegr_brand_box.setToolTip(
                f'Подключение к Telegram · {text}')
        except Exception:
            pass

    def _open_connection_menu(self):
        """Кнопка соединения: первое нажатие открывает меню, второе закрывает.

        Меню — обычный поп-ап, а не модальный exec(). Оно само
        закрывается от нажатия мимо, в том числе по самой кнопке; флаг
        недавнего закрытия не даёт этому же нажатию открыть его снова.
        """
        if not hasattr(self, '_tegr_brand_box'):
            return super()._open_connection_menu()
        menu = self.conn_menu
        if menu.isVisible():
            menu.hide()
            return
        if time.monotonic() - getattr(
                self, '_tegr_conn_closed_at', 0.0) < 0.30:
            return
        menu.set_connected(getattr(self, '_connected', False))
        pos = self._tegr_brand_box.mapToGlobal(
            QPoint(0, self._tegr_brand_box.height()))
        menu.popup(pos)

    def _tegr_conn_menu_closed(self):
        self._tegr_conn_closed_at = time.monotonic()

    # ------------------------------------------------------------------
    def _on_main_tab_changed(self, index):
        super()._on_main_tab_changed(index)
        try:
            self.conn_button.hide()
        except Exception:
            pass
