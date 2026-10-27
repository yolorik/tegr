# -*- coding: utf-8 -*-
"""Точечное сравнение _cache_worker и prioritize_position: 18.10 против сейчас."""
import marshal
import types
import dis

PYC = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe_extracted\player_deepseek_v2.pyc'
code = marshal.loads(open(PYC, 'rb').read()[16:])


def get_fn(co, name):
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            if c.co_name == name:
                return c
            r = get_fn(c, name)
            if r:
                return r
    return None


for fn_name in ('prioritize_position', '_cache_worker'):
    fn = get_fn(code, fn_name)
    print("=" * 66)
    print("18.10 :: %s  (%d инструкций)" % (fn_name, len(list(dis.get_instructions(fn)))))
    print("=" * 66)
    names = []
    for ins in dis.get_instructions(fn):
        if ins.opname in ('LOAD_METHOD', 'LOAD_ATTR', 'LOAD_GLOBAL'):
            v = ins.argval
            if isinstance(v, str) and not v.startswith('__'):
                names.append(v)
    # уникальные имена в порядке появления
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    print("обращается к:", ', '.join(seen))
    print()
    print("строковые константы:")
    for c in fn.co_consts:
        if isinstance(c, str) and len(c) > 3:
            print("   ", c)
    print()
