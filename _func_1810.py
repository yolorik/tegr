# -*- coding: utf-8 -*-
"""Смотрим, что именно делает загрузчик 18.10: строки и константы методов."""
import marshal
import types
import dis

PYC = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe_extracted\player_deepseek_v2.pyc'
code = marshal.loads(open(PYC, 'rb').read()[16:])

TARGET = '_maybe_prioritize_tail'


def find(co, name):
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            if c.co_name == name:
                return c
            r = find(c, name)
            if r:
                return r
    return None


fn = find(code, TARGET)
print("=== %s в сборке 18.10 ===" % TARGET)
print("строк в функции:", len(list(dis.get_instructions(fn))))
print()
print("--- константы ---")
for c in fn.co_consts:
    if isinstance(c, str):
        print("  строка:", c)
    else:
        print("  значение:", repr(c))
print()
print("--- короткая дизассемблерия (имена вызовов) ---")
for ins in dis.get_instructions(fn):
    if ins.opname in ('LOAD_METHOD', 'LOAD_ATTR', 'LOAD_GLOBAL', 'LOAD_CONST'):
        v = ins.argval
        if isinstance(v, str) and not v.startswith('__'):
            print("   %-16s %s" % (ins.opname, v))
