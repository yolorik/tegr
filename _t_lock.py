# -*- coding: utf-8 -*-
"""Проверка фазового замка: одновременно качается только ОДИН участок файла.

Запуск:  python _t_lock.py
"""
import sys, os, time, types

sys.path.insert(0, r"C:\tgplayer")

# --- заглушка mpv: настоящая библиотека в тесте не нужна ------------------
mpv_stub = types.ModuleType('mpv')
class _FakeMPV:
    def __init__(self, *a, **k):
        self._p = {}
    def _get_property(self, name):
        return None
    def __setitem__(self, k, v):
        self._p[k] = v
    def __getitem__(self, k):
        return self._p.get(k)
    def __getattr__(self, name):
        return lambda *a, **k: None
mpv_stub.MPV = _FakeMPV
mpv_stub.MpvError = Exception
sys.modules['mpv'] = mpv_stub

os.environ.setdefault('TGPLAYER_TEST', '1')

import player_deepseek as P
from tgplayer.core import config

config.CACHE_DIR = os.path.join(os.environ.get('TEMP', '.'), '_t_lock_cache')
os.makedirs(config.CACHE_DIR, exist_ok=True)


def make_entry(count, done_pieces=()):
    size = count * config.SEG_SIZE
    e = {
        'piece_count': count,
        'piece_done': set(done_pieces),
        'pieces': [(i * config.SEG_SIZE,
                    config.SEG_SIZE if i < count - 1 else size - i * config.SEG_SIZE)
                   for i in range(count)],
        'size': size,
        'queue': object(),          # маркер «запись живая»
        'claims': {},
        'active': set(),
        'started_ts': time.monotonic(),
        'wanted': {},
        'read_gen': 0,
        'piece_done_set': set(),
    }
    return e


class FakeBackend(P.TelegramBackend):
    """Бэкенд без сети: нужны только чистые методы выбора кусков."""
    def __init__(self):
        pass


bk = FakeBackend()
ok_all = True


def check(name, cond, detail=""):
    global ok_all
    print(("OK   " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        ok_all = False


print("=" * 60)
print("1. Старт: первая фаза — хвост (индекс moov нужен раньше тела)")
e = make_entry(55)
phase = bk._next_phase(e)
check("первая фаза = tail", phase == 'tail', f"Got: {phase}")

print()
print("=" * 60)
print("2. Оба воркера работают на ОДНОМ участке (хвост), куски разные")
e = make_entry(55)
i0 = bk._tick_claims(e, 0)
i1 = bk._tick_claims(e, 1)
p0 = bk._phase_of(e, i0) if i0 is not None else None
p1 = bk._phase_of(e, i1) if i1 is not None else None
check("воркер 0 получил кусок", i0 is not None, f"idx={i0} phase={p0}")
check("воркер 1 получил кусок", i1 is not None, f"idx={i1} phase={p1}")
check("фазы совпадают", p0 == p1, f"{p0} vs {p1}")
check("куски разные", i0 != i1, f"{i0} vs {i1}")

print()
print("=" * 60)
print("3. Голова и хвост НИКОГДА не качаются одновременно")
e = make_entry(55)
# эмулируем: читатель просит начало, а хвост ещё не готов
e['wanted'] = {0: time.monotonic()}
i0 = bk._tick_claims(e, 0)
i1 = bk._tick_claims(e, 1)
phases = {bk._phase_of(e, x) for x in (i0, i1) if x is not None}
check("в работе не больше одной фазы", len(phases) <= 1, f"фазы: {phases}")

print()
print("=" * 60)
print("4. Заявка не отдаёт один кусок двоим")
e = make_entry(55)
seen = []
for wid in range(2):
    idx = bk._tick_claims(e, wid)
    if idx is not None:
        seen.append(idx)
check("куски уникальны", len(seen) == len(set(seen)), f"взяты: {seen}")

print()
print("=" * 60)
print("5. Хвост докачан -> переходим к голове")
e = make_entry(55)
tail_first = max(0, 55 - config.TAIL_PIECES)
e['piece_done'] = set(range(tail_first, 55))
e['tail_ready'] = True
phase = bk._next_phase(e)
check("после хвоста фаза = head", phase == 'head', f"Got: {phase}")

print()
print("=" * 60)
print("6. Голова есть -> тело, и оно НЕ хвост")
e = make_entry(55)
e['piece_done'] = {0} | set(range(tail_first, 55))
e['tail_ready'] = True
phase = bk._next_phase(e)
check("фаза = body", phase == 'body', f"Got: {phase}")
idx = bk._pick_for_phase(e)
check("кусок из тела", idx is not None and bk._phase_of(e, idx) == 'body',
      f"idx={idx}")

print()
print("=" * 60)
print("7. Упрямый хвост не держит загрузку дольше предела")
e = make_entry(55)
e['tail_phase_ts'] = time.monotonic() - config.TAIL_PHASE_MAX_SECS - 1
e['wanted'] = {0: time.monotonic()}
e['claims'] = {}
idx = bk._tick_claims(e, 0)
check("tail_given_up выставлен", bool(e.get('tail_given_up')))
phase = bk._next_phase(e)
check("ушли с хвоста", phase != 'tail', f"фаза={phase}")

print()
print("=" * 60)
print("8. Переключение серии снимает заявки старой")
e = make_entry(55)
bk._tick_claims(e, 0)
bk._tick_claims(e, 1)
before = len(e['claims'])
bk.clear_claims(e)
check("заявки сняты", len(e['claims']) == 0, f"было {before}")

print()
print("=" * 60)
print("9. Частично скачанный файл: занятые куски не выдаются заново")
e = make_entry(55, done_pieces=set(range(tail_first, 55)))
e['tail_ready'] = True
e['piece_done'].add(0)
idx = bk._pick_for_phase(e)
check("готовые не выдаются", idx not in e['piece_done'] and idx != 0, f"idx={idx}")

print()
print("=" * 60)
print("ИТОГ: " + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if ok_all else "ЕСТЬ ОШИБКИ"))
sys.exit(0 if ok_all else 1)
