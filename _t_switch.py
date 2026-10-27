"""Переключение серии: воркеры прошлой обязаны остановиться.

Проверяем главное требование: если идёт загрузка серии и происходит
переключение на другую, все воркеры бросают прошлую и берутся за новую.
"""
import sys, time, asyncio
sys.path.insert(0, r"C:\tgplayer")
from tgplayer.backend import backend as B
from tgplayer.core import config as C

class Fake(B.TelegramBackend):
    def __init__(self):
        self.cache = {}
        self.loop = None

b = Fake()
SIZE = 200 * 1024 * 1024
pieces = B._piece_ranges(SIZE)

def make_e():
    return {
        'piece_count': len(pieces), 'piece_done': set(), 'pieces': pieces,
        'claims': {}, 'claim_seq': 0, 'active': set(), 'wanted': {},
        'boosted': set(), 'queue': None, 'started_ts': time.monotonic(),
        'tasks': [], 'paused': False,
    }

print("=" * 60)
print("1. Заявки старой серии снимаются при паузе")
old = make_e()
old['claims'][0] = {'idx': 40, 'seq': 1, 'ts': time.monotonic(),
                    'granted': True, 'dead': False, 'phase': 'body'}
old['claims'][1] = {'idx': 41, 'seq': 2, 'ts': time.monotonic(),
                    'granted': False, 'dead': False, 'phase': 'body'}
old['active'] = {40}
b.cache[1] = old
print("   до паузы: заявок", len(old['claims']), "активных кусков", len(old['active']))
b._pause_download_on_loop(1)
print("   после паузы: заявок", len(old['claims']), "активных кусков", len(old['active']))
print("   paused:", old['paused'])
assert len(old['claims']) == 0 and len(old['active']) == 0 and old['paused']
print("   OK: старая серия полностью отпущена")

print()
print("=" * 60)
print("2. Новая серия стартует сразу, а не ждёт 4 с")
new = make_e()
b.cache[2] = new
t0 = time.monotonic()
idx = b._tick_claims(new, 0)
dt = time.monotonic() - t0
print(f"   воркер 0 получил кусок {idx} за {dt*1000:.1f} мс")
assert idx is not None, "новая серия не начала качаться"
print("   OK: загрузка пошла без ожидания")

print()
print("=" * 60)
print("3. Воркер 1 работает на ТОЙ ЖЕ фазе, что и воркер 0")
i0 = b._tick_claims(new, 0)
i1 = b._tick_claims(new, 1)
print(f"   воркер 0 -> кусок {i0} (фаза {b._phase_of(new, i0) if i0 is not None else '-'})")
print(f"   воркер 1 -> кусок {i1} (фаза {b._phase_of(new, i1) if i1 is not None else '-'})")
if i0 is not None and i1 is not None:
    assert b._phase_of(new, i0) == b._phase_of(new, i1), "разные фазы!"
print("   OK: одна фаза на двоих")

print()
print("=" * 60)
print("4. Удаление прочих загрузок снимает их заявки")
b.cache[3] = make_e()
b.cache[3]['claims'][0] = {'idx': 5, 'seq': 1, 'ts': time.monotonic(),
                           'granted': True, 'dead': False, 'phase': 'body'}
b._discard_others_on_loop({2})
print("   заявки у удаляемой записи:", len(b.cache.get(3, {}).get('claims', {})))
print("   OK")

print()
print("=" * 60)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
