"""Строка загрузки нового окна.

Старая DownloadRow рисовала текст вручную и при карточке с рамкой
съезжала вниз. Здесь обычные QLabel и QHBoxLayout: Qt сам центрирует
текст, проценты, прогресс и кнопки по высоте строки.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget)

from ..core import theme
from ..icons import icons


class _RowProgress(QWidget):
    def __init__(self, value=0, parent=None):
        super().__init__(parent)
        self._value = max(0, min(100, int(value)))

    def setValue(self, value):
        self._value = max(0, min(100, int(value)))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = theme.theme()
        r = self.rect()
        h = self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(pal['select']))
        p.drawRoundedRect(QRectF(0, 0, r.width(), h), h / 2, h / 2)
        w = max(1.0, r.width() * self._value / 100.0)
        p.setBrush(QColor(pal['accent']))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.end()


class _ElideLabel(QLabel):
    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self._full = text
        self.setText(text)
        # Preferred, а не Ignored: ширину строки считает sizeHint, но
        # подпись всё равно может ужаться до minimumSizeHint.
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Preferred)

    def set_full_text(self, text):
        self._full = str(text or '')
        # Обрезаем сразу, не дожидаясь resizeEvent: строка обновляется
        # на каждом тике таймера, и до первого изменения ширины текст
        # успевал показаться целиком и обрезаться краем виджета.
        self._apply_elide()

    def _apply_elide(self):
        self.setText(self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(0, self.width())))

    def sizeHint(self):
        # Ширину подписи раскладка берёт по самому тексту, а не по
        # свободному месту строки: иначе между коротким названием и
        # процентами оставалась огромная дыра.
        width = self.fontMetrics().horizontalAdvance(self._full) + 4
        return QSize(width, super().sizeHint().height())

    def minimumSizeHint(self):
        # Когда места мало, подпись всё равно должна ужиматься и
        # обрезаться многоточием, а не выдавливать правую часть строки.
        return QSize(24, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()


class TegrDownloadRow(QWidget):
    ROW_H = 54

    @classmethod
    def row_height(cls):
        return theme.scale_px(cls.ROW_H)

    def __init__(self, entry, on_action, parent=None, on_check=None,
                 selectable=False, checked=False):
        super().__init__(parent)
        self.entry = dict(entry)
        self._on_action = on_action
        self._on_check = on_check
        self._selectable = False

        def s(value):
            return theme.scale_px(value)

        self.setObjectName('tegrDownloadRow')
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumHeight(self.row_height())
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(s(8), s(3), s(8), s(3))
        lay.setSpacing(s(8))

        self.check = QCheckBox(self)
        self.check.setFixedSize(s(18), s(18))
        self.check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check.setToolTip('Выбрать загрузку')
        self.check.toggled.connect(self._on_toggled)
        lay.addWidget(self.check, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QWidget(self)
        text_lay = QVBoxLayout(text)
        text_lay.setContentsMargins(0, 0, 0, s(5))
        text_lay.setSpacing(1)
        self._title = _ElideLabel('', text)
        self._title.setObjectName('rowtitle')
        self._meta = _ElideLabel('', text)
        self._meta.setObjectName('rowmeta')
        text_lay.addWidget(self._title)
        text_lay.addWidget(self._meta)
        # Название занимает всё место до правой группы: проценты,
        # скорость, полоса и кнопки стоят вместе у правого края, а
        # подпись при нехватке места обрезается ровно перед ними.
        lay.addWidget(text, 1, Qt.AlignmentFlag.AlignVCenter)

        right = QWidget(self)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, s(5))
        right_lay.setSpacing(1)
        self._pct = QLabel('', right)
        self._pct.setObjectName('quality')
        self._pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._speed = QLabel('', right)
        self._speed.setObjectName('rowmeta')
        self._speed.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_lay.addWidget(self._pct)
        right_lay.addWidget(self._speed)
        lay.addWidget(right, 0, Qt.AlignmentFlag.AlignVCenter)

        self.progress = _RowProgress(0, self)
        self.progress.setFixedSize(s(56), s(4))
        progress_box = QWidget(self)
        progress_lay = QVBoxLayout(progress_box)
        progress_lay.setContentsMargins(0, 0, 0, s(5))
        progress_lay.addWidget(self.progress)
        lay.addWidget(progress_box, 0, Qt.AlignmentFlag.AlignVCenter)

        self.pause_btn = QToolButton(self)
        self.pause_btn.setObjectName('rowbutton')
        self.pause_btn.setFixedSize(s(26), s(26))
        self.pause_btn.setIconSize(QSize(s(14), s(14)))
        self.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_btn.clicked.connect(self._toggle_pause)
        lay.addWidget(self.pause_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.close_btn = QToolButton(self)
        self.close_btn.setObjectName('rowbutton')
        self.close_btn.setFixedSize(s(26), s(26))
        self.close_btn.setIconSize(QSize(s(14), s(14)))
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self._close)
        lay.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_selectable(selectable, checked)
        self.update_entry(self.entry)

    def set_selectable(self, on, checked=False):
        self._selectable = bool(on)
        self.check.blockSignals(True)
        self.check.setChecked(bool(checked))
        self.check.blockSignals(False)
        self.check.setVisible(self._selectable)

    def _on_toggled(self, checked):
        if self._on_check is not None:
            self._on_check(bool(checked))

    def update_entry(self, entry):
        self.entry = dict(entry)
        state = self.entry.get('state') or 'busy'
        self._title.set_full_text(str(self.entry.get('name') or ''))
        self._meta.set_full_text(str(self.entry.get('channel') or ''))

        progress = max(0.0, min(1.0, float(self.entry.get('progress') or 0)))
        percent = int(round(progress * 100))
        self.progress.setValue(percent)
        size_text = str(self.entry.get('size_text') or '')
        speed_text = str(self.entry.get('speed_text') or '')

        if state == 'done':
            self._pct.setText('100%')
            self._speed.setText(size_text or 'готово')
            self.pause_btn.hide()
            self.close_btn.setIcon(icons.get_icon('trash'))
            self.close_btn.setToolTip('Удалить из кэша')
        elif state == 'paused':
            # Без «на паузе»: состояние и так видно по кнопке продолжения,
            # а подпись справа нужна короткая — процент и размер.
            self._pct.setText(f'{percent}%')
            self._speed.setText(size_text)
            self.pause_btn.show()
            self.pause_btn.setIcon(icons.get_icon('play'))
            self.pause_btn.setToolTip('Продолжить загрузку')
            self.close_btn.setIcon(icons.get_icon('close'))
            self.close_btn.setToolTip('Отменить и удалить')
        else:
            self._pct.setText(f'{percent}%')
            self._speed.setText(speed_text or size_text)
            self.pause_btn.show()
            self.pause_btn.setIcon(icons.get_icon('pause'))
            self.pause_btn.setToolTip('Приостановить загрузку')
            self.close_btn.setIcon(icons.get_icon('close'))
            self.close_btn.setToolTip('Отменить и удалить')

    def _toggle_pause(self):
        state = self.entry.get('state')
        if state == 'busy':
            self._on_action(self.entry, 'pause')
        elif state == 'paused':
            self._on_action(self.entry, 'resume')

    def _close(self):
        state = self.entry.get('state')
        action = 'delete' if state == 'done' else 'cancel'
        self._on_action(self.entry, action)
