import sys
sys.path.insert(0, r"C:\tgplayer")
import types
mod = types.ModuleType("mpv")

class _FakeMPV:
    def __init__(self, *a, **k):
        self._values = {}
    def _get_property(self, name):
        return self._values.get(name)
    def __getitem__(self, k): return self._values.get(k)
    def __setitem__(self, k, v): self._values[k] = v
    def property_observer(self, name):
        def _s(fn): return fn
        return _s
    def __getattr__(self, item):
        def _st(*a, **k): return None
        return _st
mod.MPV = _FakeMPV
sys.modules["mpv"] = mod

from PyQt6.QtWidgets import QApplication
import player_deepseek as P

app = QApplication(sys.argv)
app.setStyleSheet(P.build_style_scaled())

class Signal:
    def __init__(self): self.slots = []
    def connect(self, fn): self.slots.append(fn)
    def emit(self, *a, **k): pass

class Backend:
    cache = {}
    current_messages = []
    settings = {}
    positions = {}
    watched = set()
    def __init__(self):
        for n in ('folders_loaded','status_changed','videos_loaded','channels_checked',
                  'connection_changed','error_occurred','login_step','search_results'):
            setattr(self, n, Signal())
    def set_setting(self, *a, **k): pass
    def mark_watched(self, d): return False
    def ensure_cache(self, *a, **k): return None
    def submit_coroutine(self, *a, **k):
        class T:
            def cancel(self): pass
        return T()
    def __getattr__(self, name):
        def _stub(*a, **k): return None
        return _stub

restarts = []
original = P.PlayerWindow.play_video
def counting_play(self, index):
    restarts.append(index)
    self._play_gen += 1
    self.current_video_index = index
P.PlayerWindow.play_video = counting_play
try:
    pw = P.PlayerWindow(Backend(), [], None)
    pw.current_video_index = 5
    pw.progress_timer.stop()
    # Состояние «видео не пошло»
    pw.player._values['vo-configured'] = True
    pw.player._values['video-params/w'] = None
    pw.player._values['time-pos'] = 0.0
    # Имитируем каскад: проверка зовётся снова и снова (как её ставит play_video)
    for i in range(10):
        pw._verify_vo(pw._play_gen, 0)
    print("перезапусков при 10 проверках:", len(restarts))
    print("ожидали не больше 3")
    print("счётчик:", pw._verify_restarts)
    print("РЕЗУЛЬТАТ:", "ок" if len(restarts) <= 3 else "ПРОВАЛ")
    pw.close()
finally:
    P.PlayerWindow.play_video = original
