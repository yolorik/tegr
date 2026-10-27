"""П��оверка фазового замка на живой логике backend, без сети.

Импортируем настоящий класс и проверяем главное свойство: в любой момент
времени разрешена только ОДНА фаза, то есть тело и хвост никогда не качаются
одновременно. Именно это душило загрузку.
"""
import sys, os, time, asyncio
sys.path.insert(0, r"C:\tgplayer")

from tgplayer.backend import backend as B
from tgplayer.core import config as C


class Fake(B.TelegramBackend):
    def __init__(self):
        pass


b = Fake()
SIZE = 200 * 1024 * 1024          # 200 МБ -> 50 кусков по 4 МБ


def make_e():
    pieces = B._piece_ranges(SIZE)
    return {
        'piece_count': len(pieces),
        'piece_done': set(),
        'pieces': pieces,
        'claims': {},
        'claim_seq': 0,
        'active': set(),
        'wanted': {},
        'boosted': set(),
        'queue': None,
        'rl': 0,
        'started_ts': time.monotonic(),
    }


print("=" * 62)
print("1. Старт файла: какой участок качаем первым")
e = make_e()
print("   кусков всего:", e['piece_count'])
print("   первая фаза:", b._next_phase(e), "(ожидаем tail)")
print("   первый кусок:", b._pick_for_phase(e), "(должен быть из хвоста)")

print()
print("=" * 62)
print("2. Замок: второй воркер НЕ садится на другую фазу")
e = make_e()
first = b._pick_for_phase(e)
e['claims'][0] = {'idx': first, 'seq': 1, 'ts': time.monotonic(),
                  'granted': True, 'dead': False,
                  'phase': b._phase_of(e, first)}
second = b._pick_for_phase(e)
print("   воркер 0 взял кусок", first, "фаза", b._phase_of(e, first))
print("   воркер 1 получил:", second, "(ожидаем None — фаза занята)")
assert second is None or b._phase_of(e, second) == b._phase_of(e, first), \
    "НАРУШЕНИЕ: два воркера в разных фазах"
print("   OK: разные фазы одновременно не работают")

print()
print("=" * 62)
print("3. Хвост докачан -> замок открывает тело")
e = make_e()
first_tail = e['piece_count'] - C.TAIL_PIECES
e['piece_done'] |= set(range(first_tail, e['piece_count']))
e['tail_ready'] = True
print("   фаза после хвоста:", b._next_phase(e), "(ожидаем head)")
print("   кусок:", b._pick_for_phase(e), "(должен быть 0)")

print()
print("=" * 62)
print("4. Голова есть -> тело, и НЕ хвост")
e = make_e()
e['piece_done'] |= set(range(first_tail, e['piece_count'])) | {0}
e['tail_ready'] = True
print("   фаза:", b._next_phase(e), "(ожидаем body)")
idx = b._pick_for_phase(e)
print("   кусок:", idx, "фаза куска:", b._phase_of(e, idx))
assert b._phase_of(e, idx) == 'body', "взял не из тела"
print("   OK")

print()
print("=" * 62)
print("5. Хвост не отдаётся: через TAIL_PHASE_MAX_SECS пускаем на тело")
e = make_e()
e['tail_phase_ts'] = time.monotonic() - (C.TAIL_PHASE_MAX_SECS + 1)
b._tick_claims(e, 99)
print("   tail_given_up:", e.get('tail_given_up'))
print("   фаза:", b._next_phase(e), "(ожидаем head, не tail)")
assert e.get('tail_given_up') is True
print("   OK: упрямый хвост не держит загрузку")

print()
print("=" * 62)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
