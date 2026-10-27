import sys
sys.path.insert(0, r"C:\tgplayer")
from tgplayer.core import paths, config
paths.set_launch_dir(r"C:\tgplayer"); paths.set_base_dir(r"C:\tgplayer")
from tgplayer.widgets.widgets import _piece_ranges
size = 175008633
p = _piece_ranges(size)
print("размер файла: %.1f МБ" % (size/1024/1024))
print("кусков всего:", len(p))
print("первые 3:", p[:3])
tail = [x for x in p if x[0] >= size - config.TAIL_BYTES]
print("хвост (%d кусков):" % len(tail), tail[:6])
print()
print("BOOTSTRAP_BYTES:", config.BOOTSTRAP_BYTES, "BOOTSTRAP_PIECE:", config.BOOTSTRAP_PIECE)
print("TAIL_BYTES:", config.TAIL_BYTES, "TAIL_PIECE:", config.TAIL_PIECE)
print("SEG_SIZE:", config.SEG_SIZE, "CHUNK_SIZE:", config.CHUNK_SIZE)
print("воркеров:", config.PARALLEL_WORKERS)
