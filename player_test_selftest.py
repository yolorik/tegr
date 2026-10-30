"""Проверка тестового плеера без экрана (offscreen).

Почему с заглушкой mpv: на этой машине без живого рабочего стола
offscreen-плагин Qt не умеет выводить кадр mpv, и процесс падает при
старте — это ограничение плагина, а не плеера. Поэтому здесь та же
PlayerWindow, но движок — заглушка FakeEngine: проверяем логику
интерфейса (одно окно, полный экран, панель, клавиши, перемотку,
«бесконечный» ролик). Живой ролик проверяется запуском player_test.py.
"""
import os
import sys
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import player_test as pt  # noqa: E402
from PyQt6.QtCore import QEvent, QPointF, QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


class FakeEngine(pt.MpvEngine):
    """Движок без mpv: те же методы, значения в памяти."""

    def __init__(self, *a, **kw):
        self.made = (a, kw)
        self.commands = []
        self._closed = False
        self.paused = True
        self.volume = float(pt.config.DEFAULT_VOLUME)
        self.muted = False
        self.speed = 1.0
        self.props = {'time-pos': 0.0, 'duration': float(pt.DEMO_LENGTH_S)}
        self._obs = {}
        self._observers = self._obs

    def _emit(self, name, value):
        fn = self._obs.get(name)
        if fn:
            fn(value)

    def get(self, name):
        if name in self.props:
            return self.props[name]
        return getattr(self, name.replace('-', '_'), None)

    def set(self, name, value):
        self.props[name] = value

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == 'seek':
            amount = float(args[1])
            if len(args) > 2 and args[2] == 'absolute':
                self._last_pos = amount
            else:
                self._last_pos = (self._last_pos or 0.0) + amount
            self._emit('time-pos', self._last_pos)

    def open(self, pause_at_start=True):
        self.commands = []
        self.paused = bool(pause_at_start)
        return True

    def terminate(self):
        self._closed = True

    # как переключается пауза у настоящего движка
    def toggle_pause(self):
        self.paused = not self.paused
        self._emit('pause', self.paused)

    def seek(self, seconds, mode='relative'):
        self.commands.append(('seek', float(seconds), mode))
        if mode == 'absolute':
            self.props['time-pos'] = float(seconds)
        else:
            self.props['time-pos'] = max(
                0.0, self.props['time-pos'] + float(seconds))

    def set_volume(self, value):
        self.volume = max(0.0, min(100.0, float(value)))

    def toggle_mute(self):
        self.muted = not self.muted

    def set_speed(self, value):
        self.speed = value

    def advance(self, dt):
        if not self.paused:
            self.props['time-pos'] += dt
            self._emit('time-pos', self.props['time-pos'])


def spin(app, sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    app = QApplication([])
    app.setStyleSheet(pt.theme.build_style_scaled())
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))
        print(('OK   ' if ok else 'FAIL ') + name)

    pt.MpvEngine = FakeEngine
    # Кадр здесь не проверяем: его видеоядро Qt на offscreen-плагине
    # падает (ограничение плагина). Живой кадр проверяется запуском.
    pt.QT_VIDEO_OK = False
    win = pt.TestPlayer(mpv_mod=None, engine_class=FakeEngine)
    win.show()
    win.start_demo()
    spin(app, 0.2)
    fake = win.engine

    check('окно одно: видимое окно верхнего уровня — само окно плеера',
          [w for w in app.topLevelWidgets() if w.isVisible()] == [win])
    check('список роликов выбран на первом',
          win.playlist_panel.list.currentRow() == 0)
    check('панель видна в оконном режиме', win.bar.isVisible())
    check('без картинки из Qt карточка поверх кадра не создаётся',
          not win.stage.qt_sink and not win.stage.card.isVisible()
          and not win.stage.fs_button.isVisible())
    check('движку велено открыть ролик', fake.commands == []
          and fake.paused is True)

    # Воспроизведение
    win.toggle_play_pause()
    check('пробел включает воспроизведение', fake.paused is False)
    check('карточка убрана при игре', not win.stage.card.isVisible())

    # Полный экран
    win.toggle_fullscreen()
    spin(app, 0.2)
    check('полный экран включён', win.is_fullscreen())
    check('окно стало полным экраном',
          bool(win.windowState() & Qt.WindowState.WindowFullScreen))
    check('в полном экране по-прежнему одно окно',
          [w for w in app.topLevelWidgets() if w.isVisible()] == [win])
    check('панель видна сразу после входа', win.bar.isVisible())
    check('панель в полном экране — дочерняя виджета, а не окно',
          win.bar.isWindow() is False and win.bar.window() is win)
    check('панель скруглена', win.bar._rounded)

    # Таймер бездействия
    win.hide_bar_idle()
    check('панель прячется по таймеру при игре', not win.bar.isVisible())
    win.poke_bar()
    check('движение мыши возвращает панель', win.bar.isVisible())
    # Движение мыши — панель снова на экране. Настоящее движение
    # распознаётся по смене положения курсора (его читают от Windows), а
    # не по самому событию: Qt шлёт такие же события и без движения.
    # В тесте без экрана настоящей мыши нет, поэтому положение курсора
    # подставляем — как его отдала бы Windows при движении.
    def mouse_pos(xy):
        pt._cursor_pos_win32 = lambda: xy

    def move(cursor_xy):
        mouse_pos(cursor_xy)
        ev = QMouseEvent(QEvent.Type.MouseMove,
                         QPointF(win.width() / 2, win.height() / 2),
                         QPointF(win.mapToGlobal(
                             QPoint(win.width() // 2, win.height() // 2))),
                         Qt.MouseButton.NoButton,
                         Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        win.eventFilter(win.stage, ev)
        return win.bar.isVisible()

    check('движение мыши при игре показывает панель', move((300, 200)))
    win.hide_bar_idle()
    check('и снова прячется по бездействию', not win.bar.isVisible())
    check('событие Qt без движения курсора панель НЕ показывает',
          not move((300, 200)))
    check('после настоящего движения панель снова есть', move((320, 210)))
    # Курсор над панелью: удерживать её надо, даже если панель горит
    # «скоро спрячется».
    win.bar_timer.stop()
    win._cursor_over_bar = lambda: True
    win.hide_bar_idle()
    check('курсор над панелью — бездействие её не прячет',
          win.bar.isVisible() and win.bar_timer.isActive())
    del win._cursor_over_bar

    win.toggle_play_pause()          # пауза
    spin(app, 0.1)
    check('пауза: панель на месте', win.bar.isVisible())
    win.hide_bar_idle()
    check('пауза: таймер бездействия не прячет панель', win.bar.isVisible())
    win.toggle_play_pause()          # снова играет

    # Перемотка и время
    fake.advance(5.0)
    win.seek_rel(10)
    check('перемотка вперёд командой seek',
          ('seek', 10.0, 'relative') in fake.commands)
    check('OSD перемотки показан',
          any(c[0] == 'show-text' and '⏩' in c[1] for c in fake.commands))
    win.reset_pos_ui_for_test() if hasattr(win, 'reset_pos_ui_for_test') else None
    win._last_pos_ui = 0.0
    win.on_pos(15.0)
    check('время на панели обновилось',
          win.bar.label_position.text() == '00:15')

    # Быстрые клавиши: они висят на единственном окне
    seqs = [sc.key().toString() for sc in win._shortcuts]
    check('набор клавиш один, без повторов', len(seqs) == len(set(seqs)))
    check('клавиши из плеера на месте',
          {'Space', 'F', 'Esc', 'Left', 'Right', 'Up', 'Down', 'M', 'N',
           'P', 'L', 'T', 'V', 'H', 'S', 'A', 'F1'} <= set(seqs))
    check('двойной клик по кадру — полный экран',
          win._is_video_area(win.video_widget)
          and win._is_video_area(win.stage)
          and not win._is_video_area(win.bar))

    # Скорость, звук
    win._change_speed(pt.config.SPEED_STEP)
    check('скорость меняется',
          abs(fake.speed - pt.config.SPEED_STEP) < 1e-6)
    win._reset_speed()
    check('скорость сбрасывается', fake.speed == 1.0)
    win._change_volume(-5)
    check('громкость уменьшается', fake.volume == 45.0)
    win.toggle_mute()
    check('звук выключается', fake.muted is True)

    # Следующий ролик
    win.play_video(2)
    check('список и окно согласованы',
          win.current_video_index == 2
          and win.playlist_panel.list.currentRow() == 2)

    # «Бесконечный» ролик
    win._duration = float(pt.DEMO_LENGTH_S)
    fake.props['time-pos'] = pt.DEMO_LOOP_AT_S + 1
    win._last_pos = fake.props['time-pos']
    win._tick()
    check('демо-ролик начинается заново',
          ('seek', 0.0, 'absolute') in fake.commands)

    # Пробный режим: панель отдельным окном, как в настоящем плеере
    win.toggle_overlay_bar()
    spin(app, 0.2)
    check('пробный режим: панель стала окном', win.bar.isWindow() is True)
    win.toggle_overlay_bar()
    spin(app, 0.1)
    check('пробный режим выключается, панель снова внутри окна',
          win.bar.isWindow() is False and win.bar.isVisible())

    # Выход из полного экрана
    size_before = win._windowed_geometry
    win.toggle_fullscreen()
    spin(app, 0.1)
    check('выход из полного экрана', not win.is_fullscreen())
    check('состояние окна обычное',
          not (win.windowState() & Qt.WindowState.WindowFullScreen))
    check('размер прежний', win.size() == size_before.size())
    check('панель на месте', win.bar.isVisible())
    check('панель не скруглена в окне', not win.bar._rounded)
    check('окно по-прежнему одно',
          [w for w in app.topLevelWidgets() if w.isVisible()] == [win])

    win.close()
    bad = [n for n, ok in checks if not ok]
    print(f'\n{len(checks) - len(bad)}/{len(checks)} проверок пройдено')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
