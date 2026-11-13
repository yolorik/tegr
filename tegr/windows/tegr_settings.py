"""Настройки Tegr в новом оформлении.

Содержит те же настройки, что старый SettingsDialog, но без тёмных
групп и розовых «своих цветов»: четыре темы, масштаб, подключение,
воспроизведение, окно, кэш и обновления.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget)

from ..core import config, theme, version


THEME_ORDER = ('paper', 'sand', 'fog', 'graphite')


def style_combo_popup(combo):
    """Красит список QComboBox: системный попап иначе остаётся тёмным."""
    try:
        p = theme.theme()
        view = combo.view()
        view.setStyleSheet(
            f'QListView {{ background: {p["panel"]};'
            f' color: {p["text"]};'
            f' border: 1px solid {p["divider"]};'
            f' selection-background-color: {p["select"]};'
            f' selection-color: {p["text"]}; outline: none;'
            ' padding: 4px; }'
            'QListView::item { padding: 6px 8px; border-radius: 5px; }'
            f'QListView::item:selected {{'
            f' background: {p["select"]}; color: {p["text"]}; }}')
        pal = view.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(p['panel']))
        pal.setColor(QPalette.ColorRole.Text, QColor(p['text']))
        pal.setColor(QPalette.ColorRole.Highlight,
                     QColor(p['select']))
        pal.setColor(QPalette.ColorRole.HighlightedText,
                     QColor(p['text']))
        view.setPalette(pal)
        view.viewport().setAutoFillBackground(True)
        # Помечаем окно выпадающего списка: глобальный фильтр анимации
        # не должен трогать поп-апы QComboBox. Qt показывает их
        # несколько раз подряд (позиционирование, размер), и анимация
        # прозрачности с нуля давала заметное моргание при открытии.
        win = view.window()
        if win is not None:
            win.setProperty('tegrComboPopup', True)
    except Exception:
        pass


class TegrCombo(QComboBox):
    def showPopup(self):
        style_combo_popup(self)
        super().showPopup()

    def wheelEvent(self, event):
        # Закрытый список не должен менять значение от колеса: оно
        # предназначено прокрутке окна настроек.
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


class TegrSettingsDialog(QDialog):
    changed = pyqtSignal(str, object)

    def __init__(self, parent, values):
        super().__init__(parent)
        self._loading = True
        self.setObjectName('tegrSettings')
        self.setWindowTitle('Настройки')
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setMaximumWidth(620)

        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setMaximumHeight(
                max(360, screen.availableGeometry().height() - 80))

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        title = QLabel('Настройки')
        title.setObjectName('heading')
        root.addWidget(title)
        sub = QLabel('Изменения применяются сразу')
        sub.setObjectName('hint')
        root.addWidget(sub)

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
        inner.setStyleSheet('background: transparent;')
        body = QVBoxLayout(inner)
        body.setContentsMargins(0, 16, 6, 16)
        body.setSpacing(10)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # --- Подключение ---
        conn = self._group('Подключение')
        conn_lay = conn.layout()
        self.lbl_conn = QLabel('')
        self.lbl_conn.setObjectName('hint')
        self.lbl_conn.setWordWrap(True)
        conn_lay.addWidget(self.lbl_conn)
        btn_conn = QPushButton('Настроить подключение…')
        btn_conn.clicked.connect(
            lambda: self.changed.emit('open_connection', True))
        conn_lay.addWidget(btn_conn, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(conn)

        # --- Оформление ---
        look = self._group('Оформление')
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(9)
        look.layout().addLayout(form)
        self.cmb_theme = TegrCombo()
        for key in THEME_ORDER:
            pal = theme.TG_THEMES.get(key)
            if pal:
                self.cmb_theme.addItem(pal.get('title', key), key)
        self.cmb_theme.addItem(
            theme.THEME_TITLES.get(theme.CUSTOM_THEME,
                                   'Своя — из двух цветов'),
            theme.CUSTOM_THEME)
        pos = self.cmb_theme.findData(
            values.get('theme', theme.DEFAULT_THEME))
        self.cmb_theme.setCurrentIndex(pos if pos >= 0 else 0)
        self.cmb_theme.currentIndexChanged.connect(
            lambda *_: self._emit('theme', self.cmb_theme.currentData()))
        form.addRow('Тема:', self.cmb_theme)

        # Своя тема собирается из двух цветов: фона и акцента. Все
        # остальные оттенки выводятся из них автоматически.
        self.btn_bg = QPushButton()
        self.btn_bg.setObjectName('color')
        self.btn_accent = QPushButton()
        self.btn_accent.setObjectName('color')
        self._paint_color_button(
            self.btn_bg,
            values.get('custom_bg') or theme.CUSTOM_BG_DEFAULT)
        self._paint_color_button(
            self.btn_accent,
            values.get('custom_accent') or theme.CUSTOM_ACCENT_DEFAULT)
        self.btn_bg.clicked.connect(
            lambda: self._pick_color('custom_bg', self.btn_bg))
        self.btn_accent.clicked.connect(
            lambda: self._pick_color('custom_accent', self.btn_accent))
        form.addRow('Свой фон:', self.btn_bg)
        form.addRow('Свой акцент:', self.btn_accent)
        hint_custom = QLabel(
            'Свои цвета применяются, когда выбрана тема '
            '«Своя — из двух цветов».')
        hint_custom.setObjectName('hint')
        hint_custom.setWordWrap(True)
        form.addRow('', hint_custom)

        self.cmb_scale = TegrCombo()
        for percent in range(theme.UI_SCALE_RANGE[0],
                             theme.UI_SCALE_RANGE[1] + 1, 10):
            self.cmb_scale.addItem(f'{percent}%', percent)
        try:
            cur_scale = int(values.get('ui_scale', theme.UI_SCALE_DEFAULT))
        except (TypeError, ValueError):
            cur_scale = theme.UI_SCALE_DEFAULT
        pos_scale = self.cmb_scale.findData(cur_scale)
        if pos_scale < 0:
            pos_scale = self.cmb_scale.findData(theme.UI_SCALE_DEFAULT)
        self.cmb_scale.setCurrentIndex(max(0, pos_scale))
        self.cmb_scale.currentIndexChanged.connect(
            lambda *_: self._emit('ui_scale',
                                  self.cmb_scale.currentData()))
        form.addRow('Масштаб:', self.cmb_scale)
        body.addWidget(look)

        # --- Воспроизведение ---
        play = self._group('Воспроизведение')
        play_form = QFormLayout()
        play_form.setContentsMargins(0, 0, 0, 0)
        play_form.setHorizontalSpacing(12)
        play_form.setVerticalSpacing(8)
        play.layout().addLayout(play_form)

        self.cmb_hwdec = TegrCombo()
        for i, mode in enumerate(config.HWDEC_MODES):
            self.cmb_hwdec.addItem(mode[1], i)
        self.cmb_hwdec.setCurrentIndex(max(
            0, min(len(config.HWDEC_MODES) - 1,
                   int(values.get('hwdec', config.HWDEC_INDEX)))))
        self.cmb_hwdec.currentIndexChanged.connect(
            lambda *_: self._emit('hwdec', self.cmb_hwdec.currentIndex()))
        play_form.addRow('Декодер:', self.cmb_hwdec)

        self.cmb_seek = TegrCombo()
        for step in config.SEEK_STEPS:
            self.cmb_seek.addItem(f'{step} с', step)
        try:
            cur_step = int(values.get('seek_step',
                                      config.DEFAULT_SEEK_STEP))
        except (TypeError, ValueError):
            cur_step = config.DEFAULT_SEEK_STEP
        if cur_step not in config.SEEK_STEPS:
            cur_step = config.DEFAULT_SEEK_STEP
        self.cmb_seek.setCurrentIndex(config.SEEK_STEPS.index(cur_step))
        self.cmb_seek.currentIndexChanged.connect(
            lambda *_: self._emit('seek_step',
                                  self.cmb_seek.currentData()))
        play_form.addRow('Шаг перемотки:', self.cmb_seek)

        self.chk_compat = self._check(
            play, 'compat', 'Режим совместимости (старые видеокарты)',
            values.get('compat'))
        self.chk_auto = self._check(
            play, 'auto_quality', 'Подбирать качество по скорости канала',
            values.get('auto_quality'))
        body.addWidget(play)

        # --- Окно и список ---
        win = self._group('Окно и список')
        win_lay = win.layout()
        self.chk_tray = self._check(
            win, 'close_to_tray', 'Сворачивать в трей',
            values.get('close_to_tray'))
        self.chk_list = self._check(
            win, 'playlist_panel', 'Показывать список роликов канала',
            values.get('playlist_panel'))
        body.addWidget(win)

        # --- Кэш ---
        cache = self._group('Кэш')
        cache_lay = cache.layout()
        self.chk_cache = self._check(
            cache, 'clean_cache_on_exit', 'Удалять кэш при выходе',
            values.get('clean_cache_on_exit'))
        hint = QLabel('Если выключить, скачанное остаётся на диске.')
        hint.setObjectName('hint')
        hint.setWordWrap(True)
        cache_lay.addWidget(hint)
        btn_clear = QPushButton('Очистить кэш сейчас')
        btn_clear.clicked.connect(
            lambda: self.changed.emit('clear_cache', True))
        cache_lay.addWidget(btn_clear, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(cache)

        # --- О программе ---
        about = self._group('О программе')
        about_lay = about.layout()
        for index, line in enumerate(version.about_text()):
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            if index == 0:
                lbl.setObjectName('tegrAboutTitle')
            else:
                lbl.setObjectName('hint')
            about_lay.addWidget(lbl)

        upd = QHBoxLayout()
        self.cmb_channel = TegrCombo()
        self.cmb_channel.addItem('Только проверенные (stable)', 'stable')
        self.cmb_channel.addItem('Все сборки (beta)', 'beta')
        pos_ch = self.cmb_channel.findData(
            values.get('update_channel', 'stable'))
        self.cmb_channel.setCurrentIndex(max(0, pos_ch))
        self.cmb_channel.currentIndexChanged.connect(
            lambda *_: self._emit('update_channel',
                                  self.cmb_channel.currentData()))
        upd.addWidget(QLabel('Канал обновлений:'))
        upd.addWidget(self.cmb_channel, 1)
        about_lay.addLayout(upd)

        self.chk_upd = QCheckBox('Проверять обновления при запуске')
        self.chk_upd.setChecked(bool(values.get('check_updates', True)))
        self.chk_upd.toggled.connect(
            lambda on: self._emit('check_updates', bool(on)))
        about_lay.addWidget(self.chk_upd)

        btn_upd = QPushButton('Проверить обновления сейчас')
        btn_upd.clicked.connect(
            lambda: self.changed.emit('check_updates_now', True))
        about_lay.addWidget(btn_upd, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(about)

        body.addStretch(1)

        done = QPushButton('Готово')
        done.setObjectName('primary')
        done.setMinimumHeight(38)
        done.clicked.connect(self.accept)
        root.addWidget(done)
        self._loading = False

    def _paint_color_button(self, btn, color):
        """Красит кнопку в выбранный цвет и подписывает его кодом."""
        color = str(color or theme.CUSTOM_BG_DEFAULT)
        if not color.startswith('#'):
            color = theme.CUSTOM_BG_DEFAULT
        fg = '#000000' if theme._luminance(color) > 0.55 else '#ffffff'
        p = theme.theme()
        radius = theme.scale_px(8)
        btn.setText(color)
        btn.setMinimumHeight(theme.scale_px(28))
        btn.setStyleSheet(
            f'QPushButton {{ background: {color}; color: {fg};'
            f' border: 1px solid {p["divider"]};'
            f' border-radius: {radius}px; font-weight: 600; }}'
            f'QPushButton:hover {{ border-color: {p["accent"]}; }}')

    def _pick_color(self, key, btn):
        """Системный выбор цвета; выбранный цвет включает свою тему."""
        picked = QColorDialog.getColor(
            QColor(btn.text()), self, 'Выберите цвет')
        if not picked.isValid():
            return
        value = picked.name()
        self._paint_color_button(btn, value)
        pos = self.cmb_theme.findData(theme.CUSTOM_THEME)
        if pos >= 0 and self.cmb_theme.currentIndex() != pos:
            self.cmb_theme.blockSignals(True)
            self.cmb_theme.setCurrentIndex(pos)
            self.cmb_theme.blockSignals(False)
            self._emit('theme', theme.CUSTOM_THEME)
        self._emit(key, value)

    @staticmethod
    def _group(title):
        box = QGroupBox(title)
        box.setObjectName('tegrGroup')
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(8)
        return box

    def _check(self, parent, key, text, value):
        # QGroupBox уже имеет layout; для play/win используем его.
        box = QCheckBox(text)
        box.setChecked(bool(value))
        box.toggled.connect(lambda on, k=key: self._emit(k, bool(on)))
        parent.layout().addWidget(box)
        return box

    def _emit(self, key, value):
        if self._loading:
            return
        self.changed.emit(key, value)

    def set_conn_status(self, text):
        try:
            self.lbl_conn.setText(str(text or ''))
        except Exception:
            pass
