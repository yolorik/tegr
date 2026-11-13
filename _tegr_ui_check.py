"""Offscreen checks for the current Tegr UI fixes.

Run: python _tegr_ui_check.py
"""
import os
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tegr_runtime as base  # noqa: E402  (paths, Qt, package facade)
from PyQt6.QtCore import QObject, QSize, Qt, pyqtSignal  # noqa: E402
from PyQt6.QtGui import (  # noqa: E402
    QColor, QFont, QFontDatabase, QPalette)
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QListWidgetItem, QLabel, QVBoxLayout)

from tegr.core import settings as app_settings, theme  # noqa: E402
from tegr.windows.tegr_window import TegrMainWindow  # noqa: E402
from tegr.windows.tegr_download_row import TegrDownloadRow  # noqa: E402
from tegr.windows.tegr_history_row import TegrHistoryRow  # noqa: E402
from tegr.windows.tegr_settings import TegrSettingsDialog  # noqa: E402

# Проверки не должны трогать настоящий settings.json. Закрытие окна
# сохраняет геометрию, а вместе с ней раньше перезаписывался весь файл —
# и у пользователя слетали тема, способ подключения и пропорции.
import tempfile  # noqa: E402

_CHECK_SETTINGS_DIR = tempfile.mkdtemp(prefix='tegr_ui_check_')
app_settings.settings_path = lambda: os.path.join(
    _CHECK_SETTINGS_DIR, 'settings.json')
app_settings.save = lambda cfg: None
app_settings.load = lambda: app_settings.defaults()


class FakeBackend(QObject):
    folders_loaded = pyqtSignal(list)
    videos_loaded = pyqtSignal(object, list)
    channels_checked = pyqtSignal(object)
    history_message = pyqtSignal(object, object)
    error_occurred = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    connection_changed = pyqtSignal(str)
    login_step = pyqtSignal(str, str)
    login_cancelled = pyqtSignal()
    login_switch_qr = pyqtSignal()
    search_results = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.settings = app_settings.defaults()
        self.settings['tegr_ui'] = True
        self.settings['theme'] = 'graphite'
        self.settings['close_to_tray'] = False
        self.folders = []
        self.dialogs = []
        self.current_messages = []
        self.active_dialog_id = None
        self.active_dialog_title = ''
        self.cache = {}
        self.loop = None
        self.ready = False
        self._closed = False
        self.set_calls = []

    def set_setting(self, key, value):
        self.set_calls.append(key)
        self.settings[key] = value

    # Пометки «есть ли в канале видео» — как в настоящем бэкенде.
    _dialog_has_video = {}

    @staticmethod
    def _dialog_peer_id(dialog):
        return getattr(dialog, 'id', None)

    def submit_coroutine(self, coro):
        try:
            coro.close()
        except Exception:
            pass

    async def load_videos_from_dialog(self, dialog):
        """Заглушка загрузки: реального Telegram в проверке нет."""
        self.active_dialog_id = getattr(dialog, 'id', None)
        return []

    def answer_login(self, value):
        pass


def load_fonts():
    """Offscreen Qt не видит системные шрифты — подключаем Segoe UI вручную."""
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf'):
        path = os.path.join(os.environ.get('WINDIR', r'C:\Windows'),
                            'Fonts', name)
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)


def check(label, ok, detail=''):
    print(('PASS' if ok else 'FAIL'), label, detail)
    return ok


def fill_history(win):
    win.history_list.clear()
    for i in range(20):
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, TegrHistoryRow.row_height()))
        win.history_list.addItem(item)
        row = TegrHistoryRow(
            'Призрак в доспехах: Синдром одиночки',
            f'1 сезон 1 серия · Основной канал: @anigram_anime · {i + 1}')
        win.history_list.setItemWidget(item, row)


def fill_lists(win):
    try:
        win._dl_timer.stop()
    except Exception:
        pass
    win.channel_list.clear()
    for i in range(40):
        win.channel_list.addItem(f'Канал {i + 1}: название подлиннее')
    win.video_list.clear()
    for i in range(40):
        win.video_list.addItem(
            f'Ролик {i + 1}: демонстрационное название видео')
    win.downloads_view.list.clear()
    for i in range(40):
        item = QListWidgetItem(
            f'Загрузка {i + 1}: демонстрационное название видео')
        item.setSizeHint(QSize(0, 54))
        win.downloads_view.list.addItem(item)
    fill_history(win)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts()
    app.setFont(QFont('Segoe UI', 10))

    backend = FakeBackend()
    win = TegrMainWindow(backend)
    win.resize(1280, 820)
    win.show()
    app.processEvents()
    win._dl_timer.stop()
    win._refresh_downloads_view = lambda: None
    fill_lists(win)
    app.processEvents()
    app.processEvents()

    results = []

    # --- 1. вторая строка текста менее контрастная ---
    row = TegrHistoryRow('Призрак в доспехах: Синдром одиночки',
                         '1 сезон 1 серия · Основной канал: @anigram_anime')
    holder = QFrame()
    holder.setStyleSheet(
        f"background: {theme.theme()['panel']};"
        f" border: 1px solid {theme.theme()['divider']};"
        f" border-radius: {theme.scale_px(10)}px;")
    holder_lay = QVBoxLayout(holder)
    holder_lay.setContentsMargins(4, 4, 4, 4)
    holder_lay.addWidget(row)
    holder.resize(370, row.row_height() + 10)
    holder.show()
    app.processEvents()
    title_color = row._title.palette().color(QPalette.ColorRole.WindowText)
    meta_color = row._meta.palette().color(QPalette.ColorRole.WindowText)
    holder.grab().save('ui_check_rowmeta.png')
    results.append(check(
        'row meta is dimmer than title', title_color != meta_color,
        f'{title_color.name()} vs {meta_color.name()}'))
    row.close()

    # --- 2. в верхней полосе нет «Telegram player» ---
    texts = [w.text() for w in win.findChildren(QLabel)]
    results.append(check('topbar subtitle removed',
                         'Telegram player' not in texts))
    win.grab().copy(0, 0, win.width(), 100).save('ui_check_topbar.png')

    # --- 3. нижнее поле = высота строки статуса ---
    margins = win._tegr_outer.contentsMargins()
    status_h = win._status_bar.height()
    label_h = win._status_bar.label.geometry().height()
    results.append(check(
        'bottom margin collapsed', margins.bottom() == 0,
        f'bottom={margins.bottom()}'))
    results.append(check(
        'status bar follows text height', status_h == label_h and status_h < 24,
        f'bar={status_h} label={label_h}'))
    win.grab().copy(0, win.height() - 90, win.width(), 90).save(
        'ui_check_bottom.png')

    # --- 4. полоса прокрутки отделена от карточек ---
    def check_gap(name, view):
        sb = view.verticalScrollBar()
        container = sb.parentWidget()
        box = container.geometry()
        vp = view.viewport()
        gap = box.left() - vp.geometry().right() - 1
        inset = view.width() - (box.left() + box.width())
        want = theme.scale_px(3)
        results.append(check(
            f'{name} scrollbar has 3px gaps',
            abs(gap - want) <= 1 and abs(inset - want) <= 1,
            f'gap={gap}px right={inset}px'))

    check_gap('video', win.video_list)
    check_gap('channels', win.channel_list)
    check_gap('downloads', win.downloads_view.list)
    video_pos = win.video_list.mapTo(win, win.video_list.rect().topLeft())
    win.grab().copy(video_pos.x(), video_pos.y(),
                    win.video_list.width(),
                    min(330, win.video_list.height())).save(
        'ui_check_scrollbar.png')

    win._tegr_switch_tab(1)
    app.processEvents()
    fill_history(win)
    app.processEvents()
    app.processEvents()
    check_gap('history', win.history_list)
    hist_pos = win.history_list.mapTo(win, win.history_list.rect().topLeft())
    win.grab().copy(hist_pos.x(), hist_pos.y(),
                    win.history_list.width(),
                    min(330, win.history_list.height())).save(
        'ui_check_history.png')
    win._tegr_switch_tab(0)
    app.processEvents()

    # --- 4b. вторая строка в загрузках тоже приглушённая ---
    win.downloads_view.set_entries([
        {'name': 'Сезон 01 серия 02', 'channel': 'Клан Сопрано (гоблины)',
         'state': 'busy', 'progress': 0.03, 'size_text': '114 МБ',
         'speed_text': '8.0 МБ/с', 'file': 'check-a'},
        {'name': '1 сезон 1 серия', 'channel': 'Призрак в доспехах',
         'state': 'done', 'progress': 1.0, 'size_text': '114 МБ',
         'speed_text': '', 'file': 'check-b'},
    ])
    app.processEvents()
    dl_rows = list(win.downloads_view._rows.values())
    dl_ok = bool(dl_rows)
    for row in dl_rows:
        if not (isinstance(row, TegrDownloadRow) and
                row._title.palette().color(
                    QPalette.ColorRole.WindowText) !=
                row._meta.palette().color(
                    QPalette.ColorRole.WindowText)):
            dl_ok = False
    results.append(check('download meta is dimmer than title', dl_ok))
    dl_pos = win.downloads_view.list.mapTo(
        win, win.downloads_view.list.rect().topLeft())
    win.grab().copy(dl_pos.x(), dl_pos.y(),
                    win.downloads_view.list.width(),
                    min(150, win.downloads_view.list.height())).save(
        'ui_check_downloads.png')

    # --- 5. своя тема из двух цветов ---
    dlg = TegrSettingsDialog(win, backend.settings)
    dlg.show()
    app.processEvents()
    custom_pos = dlg.cmb_theme.findData(theme.CUSTOM_THEME)
    results.append(check('custom theme available in settings',
                         custom_pos >= 0,
                         str([dlg.cmb_theme.itemData(i)
                              for i in range(dlg.cmb_theme.count())])))
    dlg.grab().save('ui_check_settings.png')
    dlg.cmb_theme.setCurrentIndex(custom_pos)
    dlg._paint_color_button(dlg.btn_bg, '#1d2733')
    dlg._paint_color_button(dlg.btn_accent, '#f28c4b')
    app.processEvents()
    dlg.grab().save('ui_check_settings_custom.png')
    dlg.close()

    win._apply_setting('theme', theme.CUSTOM_THEME)
    win._apply_setting('custom_bg', '#1d2733')
    win._apply_setting('custom_accent', '#f28c4b')
    app.processEvents()
    pal = theme.theme()
    results.append(check(
        'custom two-color theme applied',
        pal['panel'].lower() == '#1d2733'
        and pal['accent'].lower() == '#f28c4b',
        f"panel={pal['panel']} accent={pal['accent']}"))
    # Панели — ровно выбранный цвет, а подложка окна должна быть темнее.
    results.append(check(
        'custom backdrop darker than main',
        pal['panel'].lower() == '#1d2733'
        and theme._luminance(pal['bg']) < theme._luminance(pal['panel']),
        f"panel={pal['panel']} bg={pal['bg']}"))
    win.grab().copy(0, 0, win.width(), win.height()).save(
        'ui_check_custom_theme.png')

    # --- 6. каналы без видео не показываются в списке ---
    from types import SimpleNamespace
    has_video = SimpleNamespace(id=901, name='С видео', entity=None)
    no_video = SimpleNamespace(id=902, name='Без видео', entity=None)
    unchecked = SimpleNamespace(id=903, name='Не проверен', entity=None)
    win.current_dialogs = [has_video, no_video, unchecked]
    win.backend._dialog_has_video = {901: True, 902: False}
    win.filter_channels()
    shown = [d.name for kind, d in win._search_rows if kind == 'mine']
    results.append(check(
        'channels without video are hidden',
        shown == ['С видео', 'Не проверен'], str(shown)))
    win.current_dialogs = []
    win.backend._dialog_has_video = {}
    win.filter_channels()

    # --- 7. адаптивная раскладка: широкая, средняя, узкая ---
    from types import SimpleNamespace

    def mode_state():
        return (win._tegr_mode, win._tegr_center.isVisible(),
                win._tegr_right.isVisible(),
                win._tegr_narrow_bar.isVisible())

    results.append(check(
        'wide layout at 1280',
        win._tegr_mode == 'wide' and win._tegr_center.isVisible()
        and win._tegr_right.isVisible()
        and win._tegr_narrow_bar.isHidden(),
        str(mode_state())))

    # Индикатор на кнопке списка: одно место, цвет зависит от состояния.
    win.backend.cache = {1: {'complete': False, 'paused': False}}
    win._tegr_update_dl_badges()
    dot = win._tegr_dl_dot
    busy_on = dot.isVisible()
    busy_pos = dot.pos()
    busy_color = '#f2b84b' not in dot.styleSheet()
    win.backend.cache = {2: {'complete': False, 'paused': True}}
    win._tegr_update_dl_badges()
    paused_on = dot.isVisible()
    paused_pos = dot.pos()
    paused_color = '#f2b84b' in dot.styleSheet()
    win.backend.cache = {}
    win._tegr_update_dl_badges()
    badge_off = dot.isHidden()
    results.append(check(
        'download badge on list button',
        busy_on and paused_on and badge_off and busy_pos == paused_pos
        and busy_color and paused_color,
        f'{busy_on} {paused_on} {badge_off} {busy_pos} {paused_pos}'))

    win.resize(1000, 700)
    app.processEvents()
    app.processEvents()
    medium_left = [win._tegr_left_stack.widget(i).objectName()
                   for i in range(win._tegr_left_stack.count())]
    results.append(check(
        'medium shows channels and videos',
        win._tegr_mode == 'medium' and win._tegr_center.isVisible()
        and win._tegr_right.isHidden()
        and win._tegr_narrow_bar.isHidden()
        and medium_left == ['tegrChannelsPage']
        and win._tegr_back_btn.isHidden()
        and win._tegr_right_btn.isVisible(),
        f'{mode_state()} {medium_left}'))

    test_channel = SimpleNamespace(id=777, name='Тестовый канал', entity=None)
    win.current_dialogs = [test_channel]
    win._search_rows = [('mine', test_channel)]
    win.channel_list.blockSignals(True)
    win.channel_list.clear()
    win.channel_list.addItem('Тестовый канал')
    win.channel_list.setCurrentRow(0)
    win.channel_list.blockSignals(False)
    win.on_channel_selected(0)
    app.processEvents()
    results.append(check(
        'channel opens videos beside channels in medium',
        win._tegr_left_stack.currentWidget() is win._tegr_channels_page
        and win._tegr_center_stack.currentWidget()
        is win._tegr_videos_page
        and win._tegr_nav == 'videos'
        and win._tegr_back_btn.isHidden(),
        str(win._tegr_nav)))

    # В среднем режиме загрузки и история открываются плавающим окном.
    win._tegr_right_btn.click()
    app.processEvents()
    medium_right = (getattr(win, '_tegr_right_popup', False)
                    and win._tegr_right.isVisible()
                    and win._tegr_splitter.count() == 2)
    win._tegr_right_btn.click()
    app.processEvents()
    app.processEvents()
    medium_hidden = (not getattr(win, '_tegr_right_popup', False)
                     and win._tegr_right.isHidden())
    results.append(check(
        'medium opens history/downloads as a popup',
        medium_right and medium_hidden,
        f'{medium_right} {medium_hidden}'))

    # ПКМ по каналу не должна открывать его: только меню.
    win._tegr_set_nav('channels')
    win._tegr_channel_right = True
    win.on_channel_selected(0)
    app.processEvents()
    results.append(check(
        'right click does not open channel',
        win._tegr_nav == 'channels', str(win._tegr_nav)))
    win._tegr_channel_right = False

    # Подпись из нескольких строк не должна обрезаться до первого слова.
    class _Msg:
        message = 'Викинги\nСерия 1 серия 1 🔒Lostfilm'
        file = None

    title = win._doc_title(999, {}, {'message': _Msg()})
    results.append(check(
        'download title keeps whole caption',
        title == 'Викинги Серия 1 серия 1 🔒Lostfilm', str(title)))

    win.resize(700, 600)
    app.processEvents()
    app.processEvents()
    left_names = [win._tegr_left_stack.widget(i).objectName()
                  for i in range(win._tegr_left_stack.count())]
    results.append(check(
        'narrow layout at 700',
        win._tegr_mode == 'narrow' and win._tegr_center.isHidden()
        and win._tegr_right.isHidden()
        and win._tegr_narrow_bar.isVisible()
        and left_names == ['tegrChannelsPage', 'tegrVideosPage',
                           'tegrHistoryPage', 'tegrDownloadsPage']
        and win._tegr_left_stack.currentWidget()
        is win._tegr_videos_page
        and win._tegr_back_btn.isVisible(),
        f'{left_names} cur='
        f'{win._tegr_left_stack.currentWidget().objectName()} '
        f'back={win._tegr_back_btn.isVisible()} '
        f'nav={win._tegr_nav} mode={win._tegr_mode}'))
    results.append(check(
        'narrow column uses full window width',
        win._tegr_left.width() >= win.width() - theme.scale_px(40),
        f'left={win._tegr_left.width()} window={win.width()}'))
    win._tegr_back_btn.click()
    app.processEvents()
    results.append(check(
        'back returns to channels in narrow',
        win._tegr_left_stack.currentWidget() is win._tegr_channels_page
        and win._tegr_nav == 'channels'
        and win._tegr_back_btn.isHidden(), ''))
    results.append(check(
        'back clears channel selection',
        win.channel_list.currentRow() == -1,
        str(win.channel_list.currentRow())))
    # Повторный клик по той же строке должен снова открыть канал.
    win.channel_list.setCurrentRow(0)
    app.processEvents()
    results.append(check(
        'same channel opens again after back',
        win._tegr_left_stack.currentWidget() is win._tegr_videos_page
        and win._tegr_nav == 'videos', str(win._tegr_nav)))
    win._tegr_back_btn.click()
    app.processEvents()

    win._tegr_show_narrow_tab(1)
    app.processEvents()
    dl_ok = (win._tegr_left_stack.currentWidget() is win._tegr_downloads_page
             and win.main_tabs.currentIndex() == win._downloads_tab_index())
    win._tegr_show_narrow_tab(2)
    app.processEvents()
    hist_ok = (win._tegr_left_stack.currentWidget() is win._tegr_history_page
               and win.main_tabs.currentIndex() == win._history_tab_index())
    win._tegr_show_narrow_tab(0)
    app.processEvents()
    ch_ok = (win._tegr_left_stack.currentWidget() is win._tegr_channels_page
             and win.main_tabs.currentIndex() == 0
             and not win._dl_timer.isActive())
    results.append(check(
        'narrow tabs switch content',
        hist_ok and dl_ok and ch_ok,
        f'hist={hist_ok} dl={dl_ok} ch={ch_ok}'))

    win.resize(1280, 820)
    app.processEvents()
    app.processEvents()
    win._tegr_switch_tab(1)
    app.processEvents()
    hist_right = (win._tegr_right_stack.currentWidget()
                  is win._tegr_history_page
                  and win.main_tabs.currentIndex() == win._history_tab_index())
    win._tegr_switch_tab(0)
    app.processEvents()
    dl_right = (win._tegr_right_stack.currentWidget()
                is win._tegr_downloads_page
                and win.main_tabs.currentIndex() == win._downloads_tab_index()
                and win._dl_timer.isActive())
    results.append(check(
        'right tabs drive main_tabs in wide',
        hist_right and dl_right, f'{hist_right} {dl_right}'))

    # Каналы не шире 460, правая колонка ровно 450.
    win._tegr_splitter.setSizes([700, 500, 300])
    win._tegr_save_splitter_sizes()
    app.processEvents()
    sizes = win._tegr_splitter.sizes()
    results.append(check(
        'column widths: channels <= 460, right fixed at 450',
        sizes[0] <= theme.scale_px(460) + 1
        and abs(sizes[2] - theme.scale_px(450)) <= 1,
        str(sizes)))
    # Разделитель не может изменить зафиксированную правую колонку.
    win._tegr_splitter.setSizes([400, 600, 700])
    win._tegr_save_splitter_sizes()
    app.processEvents()
    drag_sizes = win._tegr_splitter.sizes()
    results.append(check(
        'right column ignores splitter drag',
        abs(drag_sizes[2] - theme.scale_px(450)) <= 1,
        str(drag_sizes)))

    # Правую колонку можно свернуть и вернуть.
    win._tegr_right_btn.click()
    app.processEvents()
    collapsed = (win._tegr_right.isHidden()
                 and win._tegr_center.isVisible()
                 and win._tegr_left_stack.currentWidget()
                 is win._tegr_channels_page)
    win._tegr_right_btn.click()
    app.processEvents()
    restored = win._tegr_right.isVisible()
    results.append(check(
        'wide right column collapses and returns',
        collapsed and restored, f'{collapsed} {restored}'))

    win.resize(1280, 820)
    app.processEvents()
    win.backend.set_calls.clear()
    win._tegr_splitter.setSizes([240, 610, 330])
    win._tegr_save_splitter_sizes()
    deadline = time.time() + 1.0
    while (time.time() < deadline
           and 'tegr_splitter_sizes' not in win.backend.set_calls):
        app.processEvents()
        time.sleep(0.02)
    results.append(check(
        'column sizes are saved after a drag',
        'tegr_splitter_sizes' in win.backend.set_calls,
        str(win.backend.set_calls)))

    win.video_list.clear()
    for i in range(10):
        win.video_list.addItem(f'Ролик {i + 1}')
    win.video_list.setCurrentRow(7)
    win.resize(1000, 700)
    app.processEvents()
    win.resize(1280, 820)
    app.processEvents()
    app.processEvents()
    results.append(check(
        'video selection survives mode change',
        win.video_list.currentRow() == 7,
        str(win.video_list.currentRow())))

    win.resize(400, 520)
    app.processEvents()
    app.processEvents()
    results.append(check(
        'minimum size stays narrow',
        win._tegr_mode == 'narrow'
        and win.width() >= theme.scale_px(400),
        f'{win.width()}x{win.height()}'))

    # Вкладка «Загрузки» в узком окне переживает расширение и сужение.
    win.resize(700, 600)
    app.processEvents()
    win._tegr_show_narrow_tab(1)
    app.processEvents()
    win.resize(1280, 820)
    app.processEvents()
    wide_dl = (win._tegr_mode == 'wide'
               and win._tegr_right_stack.currentWidget()
               is win._tegr_downloads_page)
    win.resize(700, 600)
    app.processEvents()
    back_dl = (win._tegr_mode == 'narrow'
               and win._tegr_narrow_tab == 1
               and win._tegr_left_stack.currentWidget()
               is win._tegr_downloads_page)
    results.append(check(
        'narrow downloads tab survives resize',
        wide_dl and back_dl, f'{wide_dl} {back_dl}'))

    # Переходы анимированы: смена вкладки проявляет страницу, возврат в
    # широкую раскладку — плавно раздвигает колонки.
    win._tegr_show_narrow_tab(2)
    app.processEvents()
    tab_anim = getattr(win, '_tegr_slide_label', None) is not None
    win.resize(1280, 820)
    app.processEvents()
    win._tegr_splitter.setSizes([300, 500, 300])
    win._tegr_animate_splitter([250, 620, 340])
    split_anim = win._tegr_split_anim is not None
    if win._tegr_split_anim is not None:
        win._tegr_split_anim.stop()
        win._tegr_split_anim = None
    results.append(check(
        'tab and window transitions are animated',
        tab_anim and split_anim, f'tab={tab_anim} split={split_anim}'))

    # Пороги раскладки растут вместе с масштабом интерфейса: иначе на
    # 110–150% широкий режим включается раньше, чем колонки помещаются
    # в окно, и правая колонка уходит под край.
    old_scale = theme.ui_scale()
    theme.set_ui_scale(110)
    mode_1180 = win._tegr_mode_for_width(1180)
    mode_1300 = win._tegr_mode_for_width(1300)
    theme.set_ui_scale(old_scale)
    results.append(check(
        'layout thresholds scale with UI',
        mode_1180 == 'medium' and mode_1300 == 'wide',
        f'{mode_1180}/{mode_1300}'))

    # своя тема не должна сбрасываться при следующем запуске окна
    win.close()
    backend.settings['theme'] = theme.CUSTOM_THEME
    backend.settings['custom_bg'] = '#1d2733'
    backend.settings['custom_accent'] = '#f28c4b'
    win2 = TegrMainWindow(backend)
    results.append(check(
        'custom theme survives window init',
        theme.theme_key() == theme.CUSTOM_THEME,
        f'key={theme.theme_key()}'))
    win2.close()
    app.processEvents()
    return 0 if all(results) else 1

    print('RESULT', 'ALL PASS' if all(results) else 'SOME FAILED')


if __name__ == '__main__':
    raise SystemExit(main())
