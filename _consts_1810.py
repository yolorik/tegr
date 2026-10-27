# -*- coding: utf-8 -*-
"""Достаём из pyc 18.10 значения констант загрузчика.

Константы лежат в co_consts верхнего уровня в том порядке, в котором были
в исходнике. Рядом — co_names, чтобы понять, какая константа чему равна:
в модуле выполняется `LOAD_CONST; STORE_NAME`, и пары идут подряд.
"""
import marshal
import dis
import types

PYC = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe_extracted\player_deepseek_v2.pyc'
code = marshal.loads(open(PYC, 'rb').read()[16:])

print("=== константы верхнего уровня, привязанные к именам ===")
instrs = list(dis.get_instructions(code))
last_const = None
for ins in instrs:
    if ins.opname in ('LOAD_CONST',):
        last_const = ins.argval
    elif ins.opname in ('STORE_NAME', 'STORE_GLOBAL'):
        name = ins.argval
        if isinstance(name, str) and name.isupper():
            print("  %-32s = %r" % (name, last_const))
        last_const = None

print()
print("=== все UPPER-CASE имена модуля ===")
for n in code.co_names:
    if n.isupper():
        print("  ", n)
