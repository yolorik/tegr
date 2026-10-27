# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"C:\tgplayer")
from tgplayer.backend import backend as B
from tgplayer.core import config

# --- стенд: запись кеша на 55 кусков, хвост из 2 ---
config.TAIL_PIECES = 2

class T:
    def __init__(s): s.ranges=[]
    def add_range(s,a,b): s.ranges.append((a,b))
    def available_bytes_from(s,start): return 0

class FakeBackend(B.TelegramBackend):
    def __init__(s):
        pass

e = {
    'fh': object(),
    'rfh': None,
    'size': 55*4*1024*1024,
    'complete': False,
    'error': False,
    'tasks': [],
    'document': None,
    'message': None,
    'path': 'x.part',
    'pieces': [(i*4*1024*1024, 4*1024*1024) for i in range(55)],
    'piece_count': 55,
    'piece_done': set(),
    'downloaded_bytes': 0,
    'tracker': T(),
    'queue': __import__('asyncio').PriorityQueue(),
    'queued': set(),
    'active': set(),
    'boosted': set(),
    'claims': {},
    'claim_seq': 0,
    'started_ts': __import__('time').monotonic(),
    'speed_bytes': 0,
    'last_meta_save': 0.0,
}

fb = FakeBackend()

i0 = fb._tick_claims(e, 0)
i1 = fb._tick_claims(e, 1)
print("воркер 0 ->", i0, "фаза", fb._phase_of(e, i0) if i0 is not None else '-')
print("воркер 1 ->", i1, "фаза", fb._phase_of(e, i1) if i1 is not None else '-')
print("заявок:", {w:(c['idx'], c['phase']) for w,c in e['claims'].items()})

ok = (i0 is not None and i1 is not None and i0 != i1
      and fb._phase_of(e,i0) == fb._phase_of(e,i1) == 'tail')
print("РЕЗУЛЬТАТ:", "OK - оба воркера на хвосте, куски разные" if ok else "ПЛОХО")
