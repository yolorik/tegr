import os, sys, time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tegr_runtime as base  # noqa
from PyQt6.QtWidgets import QApplication
from tegr.player.player import PlayerWindow
from _tegr_ui_check import FakeBackend, load_fonts

app = QApplication.instance() or QApplication(sys.argv)
load_fonts()
b = FakeBackend(); b.settings['tegr_ui'] = True
w = PlayerWindow(b, 0, playlist=[])
w.resize(900, 600); w.show()
for _ in range(10): app.processEvents(); time.sleep(0.02)
w.show_osd('Test hint', 3000)
for _ in range(5): app.processEvents(); time.sleep(0.02)
o = w._osd_window
print('osd:', o is not None, 'visible', o.isVisible() if o else None,
      'geom', o.geometry().getRect() if o else None,
      'label', w._osd_label.text() if o else None)
w.close(); app.processEvents()
print('done', flush=True)
