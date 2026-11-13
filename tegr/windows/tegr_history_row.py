"""Строка истории нового окна.

Как и строка загрузки, собрана на QHBoxLayout: QLabel и QCheckBox сами
центрируются по высоте карточки, без ручной базовой линии.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget)

from ..core import theme


class _ElideLabel(QLabel):
    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self._full = str(text or '')
        self.setText(self._full)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)

    def set_full_text(self, text):
        self._full = str(text or '')
        self.setText(self._full)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(0, self.width())))


class TegrHistoryRow(QWidget):
    ROW_H = 52

    @classmethod
    def row_height(cls):
        return theme.scale_px(cls.ROW_H)

    def __init__(self, title, meta, parent=None, on_check=None,
                 selectable=False, checked=False):
        super().__init__(parent)
        self.title = str(title or '')
        self.meta = str(meta or '')
        self._on_check = on_check
        self._selectable = False

        def s(value):
            return theme.scale_px(value)

        self.setObjectName('tegrHistoryRow')
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
        self.check.setToolTip('Выбрать запись')
        self.check.toggled.connect(self._on_toggled)
        lay.addWidget(self.check, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QWidget(self)
        text_lay = QVBoxLayout(text)
        text_lay.setContentsMargins(0, 0, 0, s(5))
        text_lay.setSpacing(1)
        self._title = _ElideLabel(self.title, text)
        self._title.setObjectName('rowtitle')
        self._meta = _ElideLabel(self.meta, text)
        self._meta.setObjectName('rowmeta')
        text_lay.addWidget(self._title)
        text_lay.addWidget(self._meta)
        lay.addWidget(text, 1, Qt.AlignmentFlag.AlignVCenter)

        self.set_selectable(selectable, checked)

    def set_selectable(self, on, checked=False):
        self._selectable = bool(on)
        self.check.blockSignals(True)
        self.check.setChecked(bool(checked))
        self.check.blockSignals(False)
        self.check.setVisible(self._selectable)

    def _on_toggled(self, checked):
        if self._on_check is not None:
            self._on_check(bool(checked))
