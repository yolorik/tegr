import sys
sys.path.insert(0, r"C:\tgplayer")
from tgplayer.core import paths, config
paths.set_launch_dir(r"C:\tgplayer"); paths.set_base_dir(r"C:\tgplayer")
from tgplayer.widgets.widgets import _piece_ranges

print("=== приоритет хвоста: сколько кусков поднимается ===")
for size in (175008633, 256050552, 473794137):
    pieces = _piece_ranges(size)
    n = len(pieces)
    print()
    print("файл %.0f МБ, кусков %d" % (size/1024/1024, n))
    # Что делает prioritize_position(e, size-1): 6 кусков НАЗАД от последнего
    order = [n - 1 - i for i in range(config.PRIORITY_AHEAD_SEGS)]
    print("  поднимаются куски:", order)
    print("  это байты:", [(pieces[i][0]//1024//1024) for i in order], "МБ")
    print("  начало файла (кусок 0) в приоритете:", 0 in order)
