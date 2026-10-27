# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"C:\tgplayer")
from tgplayer.core import config as c

keys = ['PARALLEL_WORKERS', 'PREFETCH_WORKERS', 'PRELOAD_WORKERS',
        'PIECE_STALL_SECS', 'WANTED_TTL', 'SEG_SIZE', 'CHUNK_SIZE',
        'FAST_CHUNK', 'TAIL_BYTES', 'TAIL_PIECE', 'BOOTSTRAP_BYTES',
        'PRIORITY_AHEAD_SEGS', 'PREFETCH_AHEAD_SEC', 'PREFETCH_MIN_BYTES',
        'RAMP_DELAY', 'SEG_RETRIES', 'AUTO_FIX_PERCENT']
for k in keys:
    print("%-22s %s" % (k, getattr(c, k, '?')))
