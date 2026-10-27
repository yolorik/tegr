# -*- coding: utf-8 -*-
"""Сравниваем _verify_vo и _auto_heal_picture: 18.10 против текущего."""
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


for fn_name in ('_verify_vo', '_auto_heal_picture'):
    fn = get_fn(code, fn_name)
    print("=" * 66)
    if fn is None:
        print("%s: В 18.10 НЕ БЫЛО" % fn_name)
        print("=" * 66)
        print()
        continue
    print("18.10 :: %s  (%d инструкций)" % (fn_name, len(list(dis.get_instructions(fn)))))
    print("=" * 66)
    print("строковые константы:")
    for c in fn.co_consts:
        if isinstance(c, str):
            print("   ", repr(c))
    print()
    print("имена:")
    seen = []
    for ins in dis.get_instructions(fn):
        if ins.opname in ('LOAD_METHOD', 'LOAD_ATTR', 'LOAD_GLOBAL'):
            v = ins.argval
            if isinstance(v, str) and not v.startswith('__') and v not in seen:
                seen.append(v)
    print("   ", ', '.join(seen))
    print()
