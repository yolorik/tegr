# -*- coding: utf-8 -*-
"""Сравниваем загрузчик 18.10 и текущий по каждой функции.

Для каждой функции берём: размер байткода, строковые константы и имена
вызываемых методов. Где набор резко расходится — там и правки.
"""
import marshal
import types
import dis
import re
import os

PYC = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe_extracted\player_deepseek_v2.pyc'
code = marshal.loads(open(PYC, 'rb').read()[16:])

LOADER = ['ensure_cache', '_cache_worker', 'prioritize_position',
          '_maybe_prioritize_tail', '_index_at_end', '_piece_wanted',
          '_finalize_cache', 'handle_stream', '_serve_from_cache',
          '_available_from', '_existing_partial_pieces',
          '_save_partial_state', '_revive_workers', '_piece_index_for']


def profile(co, name):
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            if c.co_name == name:
                instrs = list(dis.get_instructions(c))
                strs = {x for x in c.co_consts if isinstance(x, str)}
                calls = set()
                for ins in instrs:
                    if ins.opname in ('LOAD_METHOD', 'LOAD_GLOBAL') and isinstance(ins.argval, str):
                        calls.add(ins.argval)
                return len(instrs), strs, calls
            r = profile(c, name)
            if r:
                return r
    return None


# текущий backend
src = open(r'C:\tgplayer\tgplayer\backend\backend.py', encoding='utf-8').read()

print("%-28s %-10s %-10s %s" % ('функция', '18.10', 'сейчас', 'вывод'))
print("-" * 74)
for fn in LOADER:
    old = profile(code, fn)
    o_instr = old[0] if old else 0

    # грубая оценка размера текущей: от def до следующего def того же уровня
    m = re.search(r'\n    (?:async )?def %s\(' % re.escape(fn), src)
    if m:
        rest = src[m.start() + 1:]
        m2 = re.search(r'\n    (?:async )?def ', rest)
        body = rest[:m2.start()] if m2 else rest
        n_new = len(body.splitlines())
    else:
        n_new = 0

    if o_instr == 0:
        verdict = 'добавлена после 18.10'
    elif n_new == 0:
        verdict = 'ПРОПАЛА'
    else:
        verdict = ''
    print("%-28s %-10s %-10s %s" % (fn, o_instr, n_new, verdict))
