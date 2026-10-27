import sys
sys.path.insert(0, r"C:\tgplayer")
from tgplayer.backend.backend import _piece_ranges
from tgplayer.core import config

SIZE = 70141023
pieces = _piece_ranges(SIZE)
print("размер файла:", SIZE, "байт =", round(SIZE/1024/1024, 1), "МБ")
print("кусков всего:", len(pieces))
print("TAIL_PIECES =", config.TAIL_PIECES)
print()
print("первые 8 кусков:", [p[0] for p in pieces[:8]])
print("последние 8 кусков:", [p[0] for p in pieces[-8:]])
print()
tail_first = max(0, len(pieces) - config.TAIL_PIECES)
print("хвост начинается с куска:", tail_first)
print("куски 20,21 — это", "ХВОСТ" if 20 >= tail_first else "ТЕЛО")
print()
print("размер первых кусков (байт):", [p[1] for p in pieces[:6]])
