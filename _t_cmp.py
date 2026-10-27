# -*- coding: utf-8 -*-
"""Сравнение параметров загрузчика: 18.10 (из pyc) против текущего кода."""
import sys
sys.path.insert(0, r'C:\tgplayer')
from tgplayer.core import config as c

V1810 = {
    'CHUNK_SIZE': 1048576, 'SEG_SIZE': 4194304,
    'PARALLEL_WORKERS': 2, 'PREFETCH_WORKERS': 2, 'PRELOAD_WORKERS': 2,
    'PREFETCH_AHEAD_SEC': 90.0, 'PREFETCH_MIN_BYTES': 33554432,
    'CONNECT_TIMEOUT': 15.0, 'CONNECT_BUDGET': 75.0,
    'PIECE_STALL_SECS': 12.0, 'WANTED_TTL': 4.0, 'RAMP_DELAY': 0.0,
    'READ_CAP': 2097152, 'SEG_RETRIES': 3,
    'RECONNECT_MIN_GAP': 5.0, 'WATCHDOG_INTERVAL': 5.0,
    'WATCHDOG_GRACE': 6.0, 'STALL_TIMEOUT': 15.0,
    'BOOTSTRAP_BYTES': 2097152, 'BOOTSTRAP_PIECE': 1048576,
    'FAST_HEAD_BYTES': 2097152, 'FAST_CHUNK': 262144,
    'TAIL_BYTES': 4194304, 'TAIL_PIECE': 1048576,
    'PRIORITY_AHEAD_SEGS': 6, 'AUTO_FIX_PERCENT': 3,
    'SPEED_WINDOW_SECS': 3.0, 'MPV_CACHE_SECS': 8,
    'MPV_DEMUX_READAHEAD_SECS': 4, 'DEFAULT_VOLUME': 50,
}

print("%-26s %-14s %-14s %s" % ('параметр', '18.10', 'сейчас', ''))
print("-" * 66)
for k, v in V1810.items():
    cur = getattr(c, k, '<НЕТ В КОДЕ>')
    same = (cur == v)
    mark = '' if same else '  <-- РАЗЛИЧИЕ'
    print("%-26s %-14s %-14s%s" % (k, v, cur, mark))

print()
print("=== параметры, которых в 18.10 НЕ БЫЛО ===")
for k in sorted(dir(c)):
    if k.isupper() and k not in V1810:
        print("  %-30s = %r" % (k, getattr(c, k)))
