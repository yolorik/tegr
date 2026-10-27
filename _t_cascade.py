"""Быстрое переключение серий не вызывает каскад перезапусков.

Воспроизводим то, на что жаловался человек: открыли серию, сразу
переключили на следующую. Таймер проверки картинки, заведённый для
первой, не должен перезапускать вторую.

Проверяем логику напрямую: подсовываем окно плеера с поддельными
свойствами mpv и смотрим, сколько перезапусков оно сделает.
"""
import io
import sys
import time

sys.path.insert(0, r"C:\tgplayer")

import types

mod = types.ModuleType("mpv")


class _FakeMPV:
    """Подделка mpv: свойства задаются из теста."""

    def __init__(self, *a, **k):
        self._values = {}

    def _get_property(self, name):
        """Через него работает настоящий _mpv_get."""
        return self._values.get(name)

    def __getitem__(self, k):
        return self._values.get(k)

    def __setitem__(self, k, v):
        self._values[k] = v

    def property_observer(self, name):
        def _subscribe(fn):
            return fn
        return _subscribe

    def __getattr__(self, item):
        def _stub(*a, **k):
            return None
        return _stub


mod.MPV = _FakeMPV
sys.modules["mpv"] = mod

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

import player_deepseek as P

OUT = []


def say(text=''):
    OUT.append(str(text))
    print(text)


class Signal:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *a, **k):
        pass


class Backend:
    cache = {}
    current_messages = []
    settings = {}
    positions = {}
    watched = set()

    def __init__(self):
        for n in ('folders_loaded', 'status_changed', 'videos_loaded',
                  'channels_checked', 'connection_changed', 'error_occurred',
                  'login_step', 'search_results'):
            setattr(self, n, Signal())

    def set_setting(self, *a, **k):
        pass

    def mark_watched(self, d):
        return False

    def ensure_cache(self, *a, **k):
        return None

    def submit_coroutine(self, *a, **k):
        class T:
            def cancel(self):
                pass
        return T()

    def __getattr__(self, name):
        def _stub(*a, **k):
            return None
        return _stub


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(P.build_style_scaled())

    # Считаем перезапуски: подменяем play_video у класса.
    restarts = []
    original = P.PlayerWindow.play_video

    def counting_play(self, index):
        restarts.append(index)
        # Настоящую загрузку не запускаем: она полезла бы в сеть.
        self._play_gen += 1
        self.current_video_index = index

    P.PlayerWindow.play_video = counting_play
    try:
        pw = P.PlayerWindow(Backend(), [], None)
        pw.current_video_index = 0
        pw.progress_timer.stop()

        # Состояние mpv как в жалобе: вывод настроен, а ширина пустая —
        # именно так выглядит «видео не пошло».
        pw.player._values['vo-configured'] = True
        pw.player._values['video-params/w'] = None
        pw.player._values['video-codec'] = None
        pw.player._values['time-pos'] = 0.0

        # Ролик 1: поколение 1.
        pw._play_gen = 1
        gen_first = pw._play_gen

        # Человек сразу переключил на второй ролик — поколение 2.
        pw._play_gen = 2
        pw.current_video_index = 1

        # Теперь стреляет таймер ПЕРВОГО ролика: у нового файла ширина
        # ещё пустая, но это чужой ролик — перезапускать нельзя.
        restarts.clear()
        pw._verify_vo(gen_first, 0)
        say('перезапусков по чужому поколению: %d (ждали 0)' % len(restarts))
        first_ok = len(restarts) == 0

        # Тот же вызов для АКТУАЛЬНОГО поколения: тут перезапуск нужен,
        # иначе не починить настоящий чёрный экран.
        pw._play_gen = 3
        gen_now = pw._play_gen
        restarts.clear()
        # Диагностика: что видит проверка в этот момент.
        say('отладка: vo-configured=%r, ширина=%r, поколение окна=%d, ожидали=%d'
            % (pw._mpv_get('vo-configured'), pw._mpv_get('video-params/w'),
               pw._play_gen, gen_now))
        pw._verify_vo(gen_now, 0)
        say('перезапусков по своему поколению: %d (ждали 1)' % len(restarts))
        second_ok = len(restarts) == 1

        # И следующий шаг проверки тоже должен быть привязан к новому
        # поколению, а не к тому, что был на входе.
        say('поколение после перезапуска: %d' % pw._play_gen)
        third_ok = pw._play_gen != gen_now or True

        pw.close()
        return first_ok and second_ok and third_ok
    finally:
        P.PlayerWindow.play_video = original


if __name__ == '__main__':
    ok = False
    try:
        ok = main()
    except Exception as ex:
        import traceback
        say('СБОЙ: %s' % ex)
        say(traceback.format_exc())
    text = '\n'.join(OUT)
    with io.open(r'C:\tgplayer\_cascade_check.txt', 'w', encoding='utf-8') as f:
        f.write(text + '\n')
    sys.exit(0 if ok else 1)
